"""Cloud graceful-update — container side of the control channel.

The webui container is hardened (no docker.sock, cap_drop ALL) so it cannot
recreate itself. The host apply-watcher (`apply-update.sh`) runs the real
`docker compose up -d`. The two sides talk through a bind-mounted control dir:

    <control>/activity.json      container→host  {"ts": <unix>, "busy": <bool>}
    <control>/apply-requested    container→host  touch — user clicked「立即更新」
    <control>/update-available   host→container  {"image": ...} — a newer image is staged

This module is the container side: write activity, record an apply request,
read the host's update-available signal, plus the pure `should_apply` decision
the watcher mirrors in shell.

No-op safe off-cloud: when the control dir doesn't exist (desktop installs),
writes are swallowed and reads return None.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

CONTROL_DIR_ENV = "HERMES_CONTROL_DIR"
_DEFAULT_CONTROL_DIR = "/opt/hermes/control"
IDLE_SECS_DEFAULT = 600

_ACTIVITY_FILE = "activity.json"
_APPLY_REQUESTED_FILE = "apply-requested"
_UPDATE_AVAILABLE_FILE = "update-available"


def control_dir() -> Path:
    return Path(os.getenv(CONTROL_DIR_ENV) or _DEFAULT_CONTROL_DIR)


# ── In-process activity tracking (fed by the request log hook) ───────────────
# `_last_activity_ts` is the wall-clock of the most recent meaningful request.
# Health probes and the update-available poll don't count — they'd keep an
# unattended instance looking "active" forever. Seeded to process start so a
# freshly-booted idle instance only looks idle after IDLE_SECS, not instantly.
_last_activity_ts: float = time.time()
_IGNORED_ACTIVITY_PATHS = ("/health", "/api/neowow/update-available")


def note_activity(path: str, now: float | None = None) -> None:
    global _last_activity_ts
    p = (path or "").split("?", 1)[0]
    if p in _IGNORED_ACTIVITY_PATHS:
        return
    _last_activity_ts = time.time() if now is None else now


def last_activity_ts() -> float:
    return _last_activity_ts


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError, OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def write_activity(ts: float, busy: bool, *, control: Path | None = None) -> None:
    """Record this instance's activity for the host idle check. No-op if the
    control dir can't be written (non-cloud install)."""
    d = control or control_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / _ACTIVITY_FILE).write_text(
            json.dumps({"ts": int(ts), "busy": bool(busy)}), encoding="utf-8",
        )
    except OSError:
        pass


def read_activity(*, control: Path | None = None) -> dict | None:
    return _read_json((control or control_dir()) / _ACTIVITY_FILE)


def request_apply(*, control: Path | None = None) -> dict:
    """Record the user's「立即更新」request — the host watcher applies it within
    its next tick."""
    d = control or control_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / _APPLY_REQUESTED_FILE).write_text(str(int(time.time())), encoding="utf-8")
        return {"ok": True, "message": "更新中，约 1–2 分钟生效"}
    except OSError as e:
        return {"ok": False, "error": f"control dir unavailable: {e}"}


def read_update_available(*, control: Path | None = None) -> dict | None:
    """The host writes this when a newer image is staged (pulled but not yet
    applied). Returns None when no update is staged."""
    return _read_json((control or control_dir()) / _UPDATE_AVAILABLE_FILE)


def should_apply(
    now: float,
    activity: dict | None,
    apply_requested: bool,
    idle_secs: int = IDLE_SECS_DEFAULT,
) -> bool:
    """Whether the host should recreate the container now.

    Truth table (the shell watcher mirrors this):
      - user clicked「立即更新」            → yes
      - no activity signal at all           → no (conservative: a missing
        signal might mean the writer is broken, not that nobody's here —
        don't risk interrupting; the user can still force via「立即更新」)
      - an agent/background task is running → no
      - else: applied iff quiet ≥ idle_secs
    """
    if apply_requested:
        return True
    if activity is None:
        return False
    if activity.get("busy"):
        return False
    try:
        ts = float(activity.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0.0
    return (now - ts) >= idle_secs
