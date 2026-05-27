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
def report(
    phase: str,
    error: str,
    *,
    traceback: str | None = None,
    log_path: str | None = None,
    extra: dict | None = None,
) -> bool:
    """Send a crash report. Returns True on confirmed HTTP 2xx, False otherwise."""
    raise NotImplementedError  # filled in by later tasks


# ── Queue management ─────────────────────────────────────────────────────────
def flush_queue() -> int:
    """Re-send all pending crash reports. Called from main.py at startup."""
    raise NotImplementedError  # filled in by Task 7
