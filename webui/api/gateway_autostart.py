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


def build_supervisor_argv(root_home: Path) -> list[str]:
    """Argv for the supervised gateway loop — same shape as the manual
    while-true loop operators have used, but launched automatically.
    `bash -lic` loads the login env (PATH etc.) the agent venv expects.
    """
    agent_dir = root_home / "hermes-agent"
    inner = (
        f"cd {shlex.quote(str(agent_dir))} && "
        "while true; do "
        "source venv/bin/activate && hermes gateway run 2>&1; "
        'echo "[gateway-supervisor] gateway exited at $(date) - restarting in 5s"; '
        "sleep 5; "
        "done"
    )
    return ["bash", "-lic", inner]


def gateway_running() -> bool:
    """True iff a `hermes gateway run` process is alive in this container.

    Uses pgrep (same PID namespace as the WebUI process). Any failure to
    probe is treated as "not running" so we err toward (re)starting.
    """
    import subprocess
    try:
        rc = subprocess.run(
            ["pgrep", "-f", "hermes gateway run"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode
        return rc == 0
    except Exception:
        return False


def maybe_start_gateway(
    root_home: Path,
    *,
    running_check: Callable[[], bool],
    spawn: Callable[[list[str]], None],
    log: Callable[..., None],
) -> str:
    """Start the supervised gateway loop iff the instance is configured for
    one and it isn't already running. Returns a short status string. Never
    raises — orchestration errors are caught and returned as 'error:...'.
    """
    try:
        if not should_autostart(root_home):
            return "skipped:not-configured"
        if running_check():
            return "skipped:already-running"
        spawn(build_supervisor_argv(root_home))
        log("[gateway-autostart] supervised gateway loop started")
        return "started"
    except Exception as e:  # never let startup break
        log("[gateway-autostart] error: %s", e)
        return f"error:{e}"
