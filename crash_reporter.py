"""Neowow Studio — client-side crash reporter.

Imported by both main.py (PyInstaller frozen) and webui/server.py (venv
subprocess). The webui side finds this module via the HERMES_INSTALLER_BASE_DIR
env var set by main.py.

Design spec: docs/superpowers/specs/2026-05-27-crash-reporter-design.md
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
ENDPOINT = "https://app.neowow.studio/api/client-log"
TIMEOUT_SECONDS = 8
JOIN_BUDGET_SECONDS = 0.5
MAX_LOG_TAIL_BYTES = 150_000
MAX_QUEUE_ENTRIES = 20
MAX_QUEUE_ENTRY_BYTES = 200_000
FLUSH_TIME_BUDGET_SECONDS = 5.0
MAX_ATTEMPTS_BEFORE_DLQ = 5

QUEUE_DIR = Path.home() / ".hermes" / "pending-crash-reports"
DLQ_DIR = QUEUE_DIR / "quarantine"

_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Windows: C:\Users\Alice\foo  →  C:\Users\<USER>\foo
    (re.compile(r'([A-Za-z]:[\\/])Users[\\/][^\\/\s\"\']+', re.IGNORECASE),
     r'\1Users\\<USER>'),
    # macOS: /Users/alice/foo  →  /Users/<USER>/foo
    (re.compile(r'/Users/[^/\s\"\']+'), '/Users/<USER>'),
    # Linux: /home/alice/foo  →  /home/<USER>/foo
    (re.compile(r'/home/[^/\s\"\']+'), '/home/<USER>'),
    # API keys (prefix sk-)
    (re.compile(r'sk-[A-Za-z0-9_-]{20,}'), 'sk-***REDACTED***'),
    # api_key= or api-key=
    (re.compile(r'api[_-]?key[=:][\"\']?[^\s\"\',;)]+', re.IGNORECASE),
     'api_key=***REDACTED***'),
    # Authorization: Bearer ...
    (re.compile(r'Authorization:\s*Bearer\s+\S+', re.IGNORECASE),
     'Authorization: Bearer ***REDACTED***'),
    # Bearer <token> (loose)
    (re.compile(r'Bearer\s+[A-Za-z0-9._-]{20,}'), 'Bearer ***REDACTED***'),
    # neoToken cookie
    (re.compile(r'neoToken=[^;\s]+'), 'neoToken=***REDACTED***'),
    # JWT fallback (3 base64url segments)
    (re.compile(r'\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b'),
     '<JWT_REDACTED>'),
]


def _sanitize_pii(text: str) -> str:
    """Apply all PII redaction patterns to a string."""
    if not text:
        return text
    out = text
    for pat, repl in _PII_PATTERNS:
        out = pat.sub(repl, out)
    return out


PHASES = frozenset({
    # main.py — existing
    "startup_webview2_missing",
    "startup_pywebview_missing",
    "startup_pywebview_failed",
    "windows_install_failed",
    "main_unhandled",
    # main.py — new
    "wait_for_server_timeout",
    "venv_health_check_failed",
    "windows_install_dir_wiped",
    "webui_subprocess_exit_unexpected",
    # webui/server.py — new
    "webui_pre_main_import_error",
    "webui_startup_crash",
    "webui_runtime_exception",
})


# ── Public API ───────────────────────────────────────────────────────────────
def _collect_metadata() -> dict:
    """Return non-PII metadata about the running process."""
    return {
        "pid":            os.getpid(),
        "python_version": sys.version.split()[0],
    }


def _build_payload(phase: str, error: str, traceback: str | None,
                   log_tail: str | None, extra: dict | None) -> dict:
    """Build the wire payload. PII filtering happens in caller."""
    try:
        from main import _get_app_version  # local import; main.py may not be importable in webui ctx
        version = _get_app_version()
    except Exception:
        version = os.environ.get("HERMES_INSTALLER_VERSION", "unknown")
    payload = {
        "app":      "hermes-installer",
        "version":  str(version)[:32],
        "platform": sys.platform[:32],
        "phase":    phase[:64],
        "error":    str(error)[:500],
    }
    if traceback:
        payload["traceback"] = str(traceback)[:5000]
    if log_tail:
        payload["logTail"] = str(log_tail)[:MAX_LOG_TAIL_BYTES]
    merged_extra = _collect_metadata()
    if extra:
        merged_extra.update(extra)
    payload["extra"] = merged_extra
    return payload


def _post(payload: dict, headers: dict) -> bool:
    """POST the payload. Returns True on HTTP 2xx, raises on network error."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return 200 <= resp.status < 300


def _enqueue(payload: dict, attempt: int = 1) -> Path | None:
    """Persist payload to the queue for later retry. Returns the file path or None on failure."""
    try:
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        _drop_oldest_if_full()
        body = json.dumps(payload).encode("utf-8")
        if len(body) > MAX_QUEUE_ENTRY_BYTES:
            logger.warning("crash_reporter: payload too large (%d B), truncating", len(body))
            body = body[:MAX_QUEUE_ENTRY_BYTES]
        # Filename: <epoch_ns>.attempt-<N>.json
        path = QUEUE_DIR / f"{time.time_ns()}.attempt-{attempt}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # Windows: ignore chmod failure
        os.replace(tmp, path)  # atomic
        return path
    except Exception as exc:
        logger.error("crash_reporter: enqueue failed: %s", exc)
        return None


def _drop_oldest_if_full() -> None:
    """If queue at capacity, remove oldest entry to make room (FIFO)."""
    try:
        entries = sorted(QUEUE_DIR.glob("*.json"))
        while len(entries) >= MAX_QUEUE_ENTRIES:
            oldest = entries.pop(0)
            try:
                oldest.unlink()
            except OSError:
                pass
    except FileNotFoundError:
        pass


def _sanitize_payload(payload: dict) -> dict:
    """Apply PII filter to text fields (error, traceback, logTail, extra values)."""
    out = dict(payload)
    for k in ("error", "traceback", "logTail"):
        if k in out and isinstance(out[k], str):
            out[k] = _sanitize_pii(out[k])
    extra = out.get("extra")
    if isinstance(extra, dict):
        out["extra"] = {
            k: _sanitize_pii(v) if isinstance(v, str) else v
            for k, v in extra.items()
        }
    return out


def _read_log_tail(path: str | None) -> str | None:
    """Return the last MAX_LOG_TAIL_BYTES bytes of a log file, decoded as UTF-8."""
    if not path:
        return None
    try:
        p = Path(path)
        if not p.is_file():
            return None
        size = p.stat().st_size
        with p.open("rb") as f:
            if size > MAX_LOG_TAIL_BYTES:
                f.seek(size - MAX_LOG_TAIL_BYTES)
                # Drop the (likely partial) first line for clean boundary
                _ = f.readline()
            raw = f.read()
        return raw.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("crash_reporter: _read_log_tail(%s) failed: %s", path, exc)
        return None


def _attach_jwt(headers: dict) -> None:
    """Read JWT from ~/.hermes/webui/neowow.json and add Authorization header."""
    try:
        jwt_path = Path.home() / ".hermes" / "webui" / "neowow.json"
        if not jwt_path.is_file():
            return
        data = json.loads(jwt_path.read_text(encoding="utf-8"))
        jwt = (data.get("jwt") or data.get("accessToken")
               or data.get("authorization") or "")
        if isinstance(jwt, str) and jwt.count(".") == 2:
            headers["Authorization"] = f"Bearer {jwt}"
    except Exception as exc:
        logger.debug("crash_reporter: _attach_jwt failed: %s", exc)


def report(phase, error, *, traceback=None, log_path=None, extra=None) -> bool:
    """Send a crash report. Non-blocking — main thread returns within JOIN_BUDGET_SECONDS."""
    if phase not in PHASES:
        logger.warning("crash_reporter: unknown phase %r — sending anyway", phase)

    log_tail = _read_log_tail(log_path) if log_path else None
    payload = _build_payload(phase, error, traceback, log_tail, extra)
    payload = _sanitize_payload(payload)
    # User-Agent must not be Python's default "Python-urllib/3.X" — Cloudflare
    # blocks it with error 1010 ("bad bot"). Use a benign identifier that
    # also helps backend log parsers attribute reports to the right release.
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"hermes-installer-crash-reporter/{os.environ.get('HERMES_INSTALLER_VERSION', '1.x')} ({sys.platform})",
    }
    _attach_jwt(headers)

    # Shared state between main thread and worker: was it a clean success?
    result = {"success": False}

    def _worker():
        try:
            if _post(payload, headers):
                result["success"] = True
                return
        except Exception as exc:
            logger.debug("crash_reporter: post failed: %s", exc)
        _enqueue(payload)

    t = threading.Thread(target=_worker, name="crash-reporter", daemon=True)
    t.start()
    t.join(timeout=JOIN_BUDGET_SECONDS)
    # If the thread is still running, it'll continue in background.
    # We can only report definitive success if it finished AND set the flag.
    return bool(result["success"])


# ── Queue management ─────────────────────────────────────────────────────────
_ATTEMPT_RE = re.compile(r"\.attempt-(\d+)\.json$")


def _parse_attempt(path: Path) -> int:
    """Extract attempt-N from filename; default 1 if absent."""
    m = _ATTEMPT_RE.search(path.name)
    return int(m.group(1)) if m else 1


def _move_to_dlq(path: Path) -> None:
    """Move a payload file to the dead-letter quarantine."""
    try:
        DLQ_DIR.mkdir(parents=True, exist_ok=True)
        os.replace(path, DLQ_DIR / path.name)
        logger.warning("crash_reporter: moved %s to DLQ after %d attempts",
                       path.name, MAX_ATTEMPTS_BEFORE_DLQ)
    except Exception as exc:
        logger.error("crash_reporter: DLQ move failed: %s", exc)


def _bump_attempt(path: Path) -> Path:
    """Rename a queue file to increment its attempt counter. Returns new path."""
    cur = _parse_attempt(path)
    base = _ATTEMPT_RE.sub("", path.name)
    new_path = path.with_name(f"{base}.attempt-{cur + 1}.json")
    try:
        os.replace(path, new_path)
        return new_path
    except Exception:
        return path  # best-effort; leave as-is


def flush_queue() -> int:
    """Re-send all pending reports. Returns the number successfully sent.

    Budget: FLUSH_TIME_BUDGET_SECONDS. Entries that exceed MAX_ATTEMPTS_BEFORE_DLQ
    are moved to quarantine. Called from main.py at startup.
    """
    if not QUEUE_DIR.is_dir():
        return 0
    dlq_count = len(list(DLQ_DIR.glob("*.json"))) if DLQ_DIR.is_dir() else 0
    if dlq_count:
        logger.warning("crash_reporter: %d dead-letter payloads in %s", dlq_count, DLQ_DIR)

    entries = sorted(QUEUE_DIR.glob("*.json"))  # exclude subdirs (quarantine/)
    entries = [p for p in entries if p.is_file()]
    sent = 0
    deadline = time.monotonic() + FLUSH_TIME_BUDGET_SECONDS

    for path in entries:
        if time.monotonic() >= deadline:
            logger.info("crash_reporter: flush budget exceeded, %d entries remain", len(entries) - sent)
            break

        attempt = _parse_attempt(path)
        if attempt >= MAX_ATTEMPTS_BEFORE_DLQ:
            _move_to_dlq(path)
            continue

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("crash_reporter: malformed queue file %s — dropping", path)
            try: path.unlink()
            except OSError: pass
            continue

        # See _USER_AGENT comment near the other call site.
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"hermes-installer-crash-reporter/{os.environ.get('HERMES_INSTALLER_VERSION', '1.x')} ({sys.platform})",
        }
        _attach_jwt(headers)
        try:
            if _post(payload, headers):
                path.unlink()
                sent += 1
                continue
        except Exception as exc:
            logger.debug("crash_reporter: flush retry failed: %s", exc)
        # Bump attempt counter and move on (will retry next startup)
        _bump_attempt(path)

    return sent
