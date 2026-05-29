"""Auto-(re)start the platform gateway on WebUI container boot.

Cloud single-container deployments run `hermes gateway run` as a foreground
process wrapped in a manual while-true loop INSIDE the container. That loop
survives gateway crashes but NOT container recreation (the hourly
`docker compose pull && up -d` SIGKILLs the whole container). This module,
driven from server.py's startup, relaunches a supervised gateway loop on
every container boot when the instance is configured to run one.

Persistent "should it run?" signal: gateway_state.json (written by the
running gateway). running → relaunch; stopped/missing → don't.
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Callable

GATEWAY_RUNTIME_STATUS_FILE = "gateway_state.json"


def should_autostart(root_home: Path) -> bool:
    """True iff <root_home>/gateway_state.json exists and reports running.

    Container recreation SIGKILLs the gateway before it can write "stopped",
    so a persisted "running" means "was running, should be brought back".
    A user-initiated stop persists "stopped" → we honor it. Missing file
    (never configured) or unparseable JSON → don't start.
    """
    path = root_home / GATEWAY_RUNTIME_STATUS_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return False
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("gateway_state") == "running"
