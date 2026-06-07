"""Local Gemma (via Ollama) onboarding helper — desktop-only.

Lets a desktop user run a fully local Gemma model instead of subscribing to the
cloud Coding Plan. Flow: detect/require Ollama → `ollama pull <gemma>` (size
auto-picked by RAM) → configure Hermes to the local Ollama provider.

HARD constraint: NEVER on cloud. `local_llm_available()` is False when
HERMES_NEOWOW_ONLY is set (cloud ECS) or the OS isn't macOS/Linux, and the
route layer 403s install attempts in that case too.

Install of Ollama itself is NOT force-automated (`curl|sh` needs root on Linux,
and macOS/Windows differ): if Ollama is absent the job reports
`need_manual_install` with guidance; once installed, pull+configure is automatic.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

logger = logging.getLogger(__name__)

RAM_THRESHOLD_BYTES = 16 * 1024 ** 3   # ≥16 GiB → bigger model
OLLAMA_BASE_URL = "http://localhost:11434/v1"
_OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"

# Official one-liner shown to the user when Ollama is absent (macOS/Linux).
OLLAMA_INSTALL_HINT = "curl -fsSL https://ollama.com/install.sh | sh"
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"


# ── Pure helpers ────────────────────────────────────────────────────────────

def pick_gemma_model(total_ram_bytes: int) -> str:
    """≥16 GiB → gemma4:e4b (9.6GB, 4.5B eff). Else → gemma4:e2b (7.2GB, 2.3B).
    Unknown/0 falls to the smaller, safest option."""
    return "gemma4:e4b" if (total_ram_bytes or 0) >= RAM_THRESHOLD_BYTES else "gemma4:e2b"


def detect_total_ram_bytes() -> int:
    """Total physical RAM (macOS/Linux via sysconf). 0 on failure."""
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return 0


def _neowow_only() -> bool:
    return os.getenv("HERMES_NEOWOW_ONLY", "").strip().lower() in {"1", "true", "yes"}


def local_llm_available() -> bool:
    """True only on a desktop macOS/Linux box (never cloud / Windows in v1)."""
    if _neowow_only():
        return False
    return sys.platform in ("darwin", "linux")


def ollama_installed() -> bool:
    return shutil.which("ollama") is not None


def ollama_running(timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(_OLLAMA_TAGS_URL, timeout=timeout) as r:
            return getattr(r, "status", 200) == 200
    except Exception:
        return False


# ── Install job (in-memory store + thread; core is sync + DI for testing) ────

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _set(job: dict, **patch) -> None:
    job.update(patch)


def _run_install(job: dict, model: str, deps: dict) -> None:
    """Synchronous core of the install job. `deps` keys:
       ollama_installed() -> bool, pull(model, on_progress=None), configure(model).
    Mutates `job["state"]` to one of need_manual_install | error | done."""
    if not deps["ollama_installed"]():
        _set(job, state="need_manual_install",
             hint=OLLAMA_INSTALL_HINT, download_url=OLLAMA_DOWNLOAD_URL)
        return
    try:
        def on_progress(pct: int, line: str) -> None:
            job["percent"] = pct
            job["log"].append(line)
            job["log"][:] = job["log"][-50:]   # keep tail only
        deps["pull"](model, on_progress=on_progress)
    except Exception as e:   # noqa: BLE001 — surface any pull failure to the UI
        _set(job, state="error", error=f"拉取模型失败: {e}")
        return
    try:
        deps["configure"](model)
    except Exception as e:   # noqa: BLE001
        _set(job, state="error", error=f"配置失败: {e}")
        return
    _set(job, state="done", percent=100)


# ── Production dependency implementations ────────────────────────────────────

def _pull_model(model: str, on_progress=None) -> None:
    """Run `ollama pull <model>`, parsing percentage lines for progress."""
    proc = subprocess.Popen(
        ["ollama", "pull", model],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        pct = 0
        if "%" in line:
            try:
                pct = int(line.split("%")[0].split()[-1])
            except (ValueError, IndexError):
                pct = 0
        if on_progress:
            on_progress(pct, line)
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"ollama pull exited {code}")


def _configure_ollama(model: str) -> None:
    """Point Hermes at the local Ollama provider via the standard apply path."""
    from api.onboarding import apply_onboarding_setup
    apply_onboarding_setup({
        "provider":          "ollama",
        "model":             model,
        "api_key":           "",
        "base_url":          OLLAMA_BASE_URL,
        "confirm_overwrite": True,
    })


def _production_deps() -> dict:
    return {
        "ollama_installed": ollama_installed,
        "pull":             _pull_model,
        "configure":        _configure_ollama,
    }


def start_install_job(model: str | None = None, deps: dict | None = None) -> str:
    """Spawn the install job in a background thread. Returns a job id to poll."""
    chosen = model or pick_gemma_model(detect_total_ram_bytes())
    job_id = f"gemma-{int(time.time() * 1000)}-{len(_jobs)}"
    job = {"state": "running", "model": chosen, "percent": 0, "log": [], "error": None}
    with _jobs_lock:
        _jobs[job_id] = job
    use_deps = deps or _production_deps()
    threading.Thread(
        target=_run_install, args=(job, chosen, use_deps), daemon=True,
    ).start()
    return job_id


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def status_payload() -> dict:
    """For GET /status — drives whether the UI renders the card."""
    ram = detect_total_ram_bytes()
    return {
        "available":         local_llm_available(),
        "ollama_installed":  ollama_installed(),
        "ollama_running":    ollama_running(),
        "ram_bytes":         ram,
        "recommended_model": pick_gemma_model(ram),
        "models":            ["gemma4:e2b", "gemma4:e4b"],
        "install_hint":      OLLAMA_INSTALL_HINT,
        "download_url":      OLLAMA_DOWNLOAD_URL,
    }
