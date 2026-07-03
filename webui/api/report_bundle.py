"""Build + upload the user-initiated diagnostic bundle for "报告问题".

Fired when a STUCK (not crashed) user clicks 报告问题 in the webui. Gathers the
active-profile logs, a health snapshot, version, and a REDACTED config summary,
applies client-side PII redaction (mirrors crash_reporter._PII_PATTERNS — the
server re-sanitizes as defence-in-depth), and POSTs to /api/client-report.

On upload failure the bundle is written to ~/.hermes/pending-reports/ so a
stuck/offline user never loses it.
"""
from __future__ import annotations

import base64
import json
import os
import platform
import re
import sys
import time
import urllib.request
from pathlib import Path

REPORT_ENDPOINT = "https://app.neowow.studio/api/client-report"
UPLOAD_TIMEOUT = 20  # seconds — user is waiting, keep it snappy

_LOG_FILES = {"agent": "agent.log", "errors": "errors.log", "gateway": "gateway.log"}
_MAX_BYTES = 2 * 1024 * 1024  # per-file read window
_MAX_LINES = 2000             # per-file tail

# ── PII redaction (mirrors crash_reporter._PII_PATTERNS) ────────────────────
_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'([A-Za-z]:[\\/])Users[\\/][^\\/\s"\']+', re.IGNORECASE), r'\1Users\\<USER>'),
    (re.compile(r'/Users/[^/\s"\']+'), '/Users/<USER>'),
    (re.compile(r'/home/[^/\s"\']+'), '/home/<USER>'),
    (re.compile(r'sk-[A-Za-z0-9_-]{20,}'), 'sk-***REDACTED***'),
    (re.compile(r'Authorization:\s*Bearer\s+\S+', re.IGNORECASE), 'Authorization: Bearer ***REDACTED***'),
    (re.compile(r'Bearer\s+[A-Za-z0-9._-]{20,}'), 'Bearer ***REDACTED***'),
    (re.compile(r'neoToken=[^;\s]+'), 'neoToken=***REDACTED***'),
    (re.compile(r'\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b'), '<JWT_REDACTED>'),
]


def _sanitize_pii(text: str) -> str:
    if not text:
        return text
    out = text
    for pat, repl in _PII_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _sanitize_bundle(obj):
    if isinstance(obj, str):
        return _sanitize_pii(obj)
    if isinstance(obj, list):
        return [_sanitize_bundle(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _sanitize_bundle(v) for k, v in obj.items()}
    return obj


# ── Diagnostics ─────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _app_version() -> str:
    v = (os.environ.get("HERMES_INSTALLER_VERSION") or "").strip()
    if v:
        return v
    for base in (Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2],):
        try:
            vf = Path(base) / "version.txt"
            if vf.is_file():
                return vf.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return "unknown"


def _logs_dir() -> Path:
    try:
        from api.profiles import get_active_hermes_home
        return Path(get_active_hermes_home()).expanduser() / "logs"
    except Exception:
        home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
        return Path(home).expanduser() / "logs"


def _startup_log_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", os.environ.get("TEMP", "C:\\Temp"))) / "Hermes"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs" / "Hermes"
    else:
        base = Path(os.environ.get("TMPDIR", "/tmp")) / "hermes"
    return base / "hermes-startup.log"


def _read_tail(path: Path) -> dict:
    try:
        if not path.is_file():
            return {"tail": [], "bytes": 0, "truncated": False}
        size = path.stat().st_size
        read = min(size, _MAX_BYTES)
        with path.open("rb") as fh:
            if size > read:
                fh.seek(size - read)
            raw = fh.read(read)
        lines = raw.decode("utf-8", "replace").splitlines()
        tail = lines[-_MAX_LINES:]
        return {"tail": tail, "bytes": size, "truncated": size > read or len(lines) > _MAX_LINES}
    except Exception as exc:  # never let one bad file sink the whole report
        return {"tail": [f"<read error: {exc}>"], "bytes": 0, "truncated": False}


def _collect_logs() -> dict:
    d = _logs_dir()
    out = {key: _read_tail(d / fn) for key, fn in _LOG_FILES.items()}
    out["startup"] = _read_tail(_startup_log_path())
    return out


def _jwt_exp(jwt: str):
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        exp = claims.get("exp")
        return int(exp) if exp is not None else None
    except Exception:
        return None


def _collect_config() -> dict:
    cred = (os.environ.get("NEOWOW_CODING_PLAN_API_KEY") or "").strip()
    if cred.startswith("nws_dt_"):
        kind = "deploy_token"
    elif cred.count(".") == 2:
        kind = "jwt"
    elif not cred:
        kind = "none"
    else:
        kind = "other"
    jwt_exp = None
    try:
        import api.neowow as neowow
        j = neowow.get_jwt()
        if j and j.count(".") == 2:
            jwt_exp = _jwt_exp(j)
    except Exception:
        pass
    return {
        "provider": (os.environ.get("NEOWOW_CODING_PLAN_PROVIDER") or "neowow-coding-plan"),
        "base_url": "app.neowow.studio",
        "hasCodingPlanCred": bool(cred),
        "codingPlanCredKind": kind,
        "jwtExp": jwt_exp,
        "disableLazyInstalls": (os.environ.get("HERMES_DISABLE_LAZY_INSTALLS") or "").lower() in ("1", "true", "yes", "on"),
    }


def build_report_bundle(description: str, health: dict | None = None) -> dict:
    bundle = {
        "kind": "user_report",
        "app": "neowow-studio",
        "version": _app_version(),
        "platform": f"{sys.platform} {platform.release()}",
        "createdAt": _now_iso(),
        "description": (description or "")[:2000],
        "health": health or {},
        "config": _collect_config(),
        "logs": _collect_logs(),
    }
    return _sanitize_bundle(bundle)


# ── Upload ──────────────────────────────────────────────────────────────────
def _attach_jwt(headers: dict) -> None:
    """Attach the current JWT (or a deploy token) so the server can attribute
    the report. Optional — upload still works unauthenticated."""
    tok = ""
    try:
        import api.neowow as neowow
        tok = (neowow.get_jwt() or "").strip()
    except Exception:
        tok = ""
    if not tok:
        dt = (os.environ.get("NEOWOW_CODING_PLAN_API_KEY") or "").strip()
        if dt.startswith("nws_dt_"):
            tok = dt
    if tok:
        headers["Authorization"] = f"Bearer {tok}"


def _pending_dir() -> Path:
    return Path.home() / ".hermes" / "pending-reports"


def _save_pending(bundle: dict) -> str:
    try:
        d = _pending_dir()
        d.mkdir(parents=True, exist_ok=True)
        stamp = _now_iso().replace(":", "-").replace(".", "-")
        p = d / f"report-{stamp}.json"
        p.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(p)
    except Exception:
        return ""


def upload_report(bundle: dict) -> dict:
    """POST the bundle. Returns {ok:True, reportId} or, on any failure,
    {ok:False, saved:<path>, error:<str>} after persisting locally."""
    body = json.dumps(bundle, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        # Non-default UA — Python-urllib/* trips Cloudflare error 1010.
        "User-Agent": f"hermes-installer-report/{_app_version()} ({sys.platform})",
    }
    _attach_jwt(headers)
    try:
        req = urllib.request.Request(REPORT_ENDPOINT, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=UPLOAD_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        report_id = (data or {}).get("reportId")
        if report_id:
            return {"ok": True, "reportId": report_id}
        return {"ok": False, "saved": _save_pending(bundle), "error": "server returned no reportId"}
    except Exception as exc:
        return {"ok": False, "saved": _save_pending(bundle), "error": str(exc)[:200]}
