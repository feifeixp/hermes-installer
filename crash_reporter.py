"""Hermes Installer — client-side crash reporter.

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


def report(phase, error, *, traceback=None, log_path=None, extra=None) -> bool:
    if phase not in PHASES:
        logger.warning("crash_reporter: unknown phase %r — sending anyway", phase)
    payload = _build_payload(phase, error, traceback, None, extra)
    headers = {"Content-Type": "application/json"}
    try:
        if _post(payload, headers):
            return True
        _enqueue(payload)
        return False
    except Exception as exc:
        logger.debug("crash_reporter: _post failed (%s), enqueueing", exc)
        _enqueue(payload)
        return False


# ── Queue management ─────────────────────────────────────────────────────────
def flush_queue() -> int:
    """Re-send all pending crash reports. Called from main.py at startup."""
    raise NotImplementedError  # filled in by Task 7
