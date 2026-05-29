# Gateway Auto-Start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the WebUI container auto-(re)start the platform gateway (`hermes gateway run`) on boot so it survives container recreation (hourly auto-update), not just process crashes.

**Architecture:** A new pure-logic module `webui/api/gateway_autostart.py` decides whether to start the gateway (reads `gateway_state.json`) and orchestrates a supervised relaunch via injectable `running_check`/`spawn` callbacks (so the decision is unit-testable without real subprocesses). `server.py` runs it in a daemon thread at startup — same spot as `_startup_skill_sync` — passing real implementations.

**Tech Stack:** Python 3.11 (stdlib only), pytest, the existing `webui/server.py` startup flow, `hermes_constants.get_default_hermes_root()`.

**Scope boundary:** hermes-installer `webui/` only. Does not change `hermes gateway run`, docker-compose, cloud-init, or the hourly auto-update. No UI.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `webui/api/gateway_autostart.py` | Decide + orchestrate gateway (re)start. Pure logic + injectable side-effects. | Create |
| `webui/tests/test_gateway_autostart.py` | Unit tests for the decision + orchestration (no real subprocess). | Create |
| `webui/server.py` | Run `_startup_gateway_supervisor()` daemon thread at startup. | Modify |

**Module surface (`gateway_autostart.py`):**
- `GATEWAY_RUNTIME_STATUS_FILE = "gateway_state.json"` (mirrors agent_health).
- `should_autostart(root_home: Path) -> bool` — True iff `<root_home>/gateway_state.json` exists and its JSON `gateway_state == "running"`.
- `gateway_running() -> bool` — True iff a `hermes gateway run` process is alive (`pgrep -f`).
- `build_supervisor_argv(root_home: Path) -> list[str]` — the bash supervised-loop command.
- `maybe_start_gateway(root_home, *, running_check, spawn, log) -> str` — orchestration; returns a short status string (`"started" | "skipped:not-configured" | "skipped:already-running" | "error:<msg>"`). Injectable `running_check`/`spawn`/`log` for tests.

---

## Task 1: Decision logic — `should_autostart` (TDD)

**Files:**
- Create: `webui/api/gateway_autostart.py`
- Create: `webui/tests/test_gateway_autostart.py`

- [ ] **Step 1: Write the failing test**

Create `webui/tests/test_gateway_autostart.py`:

```python
# Unit tests for gateway_autostart — pure decision logic, no real subprocess.
#
# Run via (from webui/):
#   python3 -m pytest tests/test_gateway_autostart.py -v

import json
from pathlib import Path

from api.gateway_autostart import should_autostart


def _write_state(root: Path, obj) -> None:
    (root / "gateway_state.json").write_text(json.dumps(obj), encoding="utf-8")


def test_running_state_should_autostart(tmp_path):
    _write_state(tmp_path, {"gateway_state": "running", "updated_at": "x"})
    assert should_autostart(tmp_path) is True


def test_stopped_state_should_not_autostart(tmp_path):
    _write_state(tmp_path, {"gateway_state": "stopped"})
    assert should_autostart(tmp_path) is False


def test_missing_file_should_not_autostart(tmp_path):
    assert should_autostart(tmp_path) is False


def test_corrupt_json_should_not_autostart(tmp_path):
    (tmp_path / "gateway_state.json").write_text("{not json", encoding="utf-8")
    assert should_autostart(tmp_path) is False
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd webui && python3 -m pytest tests/test_gateway_autostart.py -v 2>&1 | tail -15`
Expected: collection/import error — `ModuleNotFoundError: No module named 'api.gateway_autostart'`.

- [ ] **Step 3: Create the module with `should_autostart`**

Create `webui/api/gateway_autostart.py`:

```python
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
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `cd webui && python3 -m pytest tests/test_gateway_autostart.py -v 2>&1 | tail -15`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add webui/api/gateway_autostart.py webui/tests/test_gateway_autostart.py
git commit -m "feat(gateway): should_autostart decision from gateway_state.json"
```

---

## Task 2: Orchestration — `gateway_running`, `build_supervisor_argv`, `maybe_start_gateway` (TDD)

**Files:**
- Modify: `webui/api/gateway_autostart.py`
- Modify: `webui/tests/test_gateway_autostart.py`

- [ ] **Step 1: Add failing tests for the orchestration**

Append to `webui/tests/test_gateway_autostart.py`:

```python
from api.gateway_autostart import build_supervisor_argv, maybe_start_gateway


def test_build_supervisor_argv_contains_loop_and_dir(tmp_path):
    argv = build_supervisor_argv(tmp_path)
    assert argv[0] == "bash"
    joined = " ".join(argv)
    assert "hermes gateway run" in joined
    assert "while true" in joined
    assert str(tmp_path / "hermes-agent") in joined  # cd into the agent dir


def test_maybe_start_skips_when_not_configured(tmp_path):
    spawned = []
    status = maybe_start_gateway(
        tmp_path,
        running_check=lambda: False,
        spawn=lambda argv: spawned.append(argv),
        log=lambda *_: None,
    )
    assert status == "skipped:not-configured"
    assert spawned == []


def test_maybe_start_skips_when_already_running(tmp_path):
    (tmp_path / "gateway_state.json").write_text('{"gateway_state":"running"}', encoding="utf-8")
    spawned = []
    status = maybe_start_gateway(
        tmp_path,
        running_check=lambda: True,   # already alive
        spawn=lambda argv: spawned.append(argv),
        log=lambda *_: None,
    )
    assert status == "skipped:already-running"
    assert spawned == []


def test_maybe_start_spawns_when_configured_and_not_running(tmp_path):
    (tmp_path / "gateway_state.json").write_text('{"gateway_state":"running"}', encoding="utf-8")
    spawned = []
    status = maybe_start_gateway(
        tmp_path,
        running_check=lambda: False,
        spawn=lambda argv: spawned.append(argv),
        log=lambda *_: None,
    )
    assert status == "started"
    assert len(spawned) == 1
    assert "hermes gateway run" in " ".join(spawned[0])
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd webui && python3 -m pytest tests/test_gateway_autostart.py -v 2>&1 | tail -20`
Expected: `ImportError: cannot import name 'build_supervisor_argv'` (and `maybe_start_gateway`).

- [ ] **Step 3: Implement the orchestration in `gateway_autostart.py`**

Append to `webui/api/gateway_autostart.py`:

```python
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
        'echo \"[gateway-supervisor] gateway exited at $(date) - restarting in 5s\"; '
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
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd webui && python3 -m pytest tests/test_gateway_autostart.py -v 2>&1 | tail -20`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add webui/api/gateway_autostart.py webui/tests/test_gateway_autostart.py
git commit -m "feat(gateway): supervisor argv + maybe_start orchestration"
```

---

## Task 3: Wire the supervisor into server.py startup

**Files:**
- Modify: `webui/server.py`

Context: `server.py` already starts a daemon thread `_startup_skill_sync` right after the "listening" prints (around the `import threading as _threading` / `_threading.Thread(target=_startup_skill_sync, ...).start()` lines). Add the gateway supervisor thread immediately after that `.start()` call, mirroring its structure.

- [ ] **Step 1: Add the supervisor thread**

In `webui/server.py`, immediately AFTER the line:

```python
    _threading.Thread(target=_startup_skill_sync, daemon=True, name="startup-skill-sync").start()
```

insert:

```python
    # ── Startup gateway supervisor (background, non-blocking) ──────────────
    # Cloud single-container gateways run as a manual while-true `hermes
    # gateway run` loop INSIDE the container — it survives crashes but not
    # container recreation (the hourly auto-update SIGKILLs the container).
    # On every container boot, if this instance is configured to run a
    # gateway (gateway_state.json == running) and one isn't already alive,
    # relaunch the supervised loop. Daemon thread; never blocks startup.
    def _startup_gateway_supervisor():
        try:
            from hermes_constants import get_default_hermes_root
            from api.gateway_autostart import (
                gateway_running, maybe_start_gateway,
            )
            status = maybe_start_gateway(
                get_default_hermes_root(),
                running_check=gateway_running,
                spawn=lambda argv: subprocess.Popen(
                    argv,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                ),
                log=logger.info,
            )
            logger.info("[startup] gateway supervisor: %s", status)
        except Exception as exc:
            logger.warning("[startup] gateway supervisor failed (non-fatal): %s", exc)

    _threading.Thread(
        target=_startup_gateway_supervisor, daemon=True, name="startup-gateway-supervisor",
    ).start()
```

- [ ] **Step 2: Ensure `subprocess` is imported in server.py**

Run: `grep -nE "^import subprocess|^import subprocess as" webui/server.py ; echo done`
- If it prints a match → already imported, do nothing.
- If it prints only `done` → add `import subprocess` near the other top-of-file stdlib imports in `webui/server.py`.

- [ ] **Step 3: Syntax check**

Run: `python3 -m py_compile webui/server.py && echo "OK"`
Expected: `OK`.

- [ ] **Step 4: Full gateway-autostart test still green**

Run: `cd webui && python3 -m pytest tests/test_gateway_autostart.py -v 2>&1 | tail -5`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add webui/server.py
git commit -m "feat(gateway): start supervised gateway loop from server.py boot"
```

---

## Task 4: Verification

**Files:** none (verification only)

- [ ] **Step 1: Run the webui test suite (no regressions)**

Run: `cd webui && python3 -m pytest tests/test_gateway_autostart.py tests/test_gateway_sync.py -v 2>&1 | tail -20`
Expected: all pass. (If `test_gateway_sync.py` needs the agent and errors on collection in this environment, that's environmental — confirm `test_gateway_autostart.py` passes standalone.)

- [ ] **Step 2: Confirm no syntax/import errors across touched files**

Run: `python3 -m py_compile webui/server.py webui/api/gateway_autostart.py && echo "OK"`
Expected: `OK`.

- [ ] **Step 3: Manual smoke (after image rebuild + deploy — operator step)**

On a test ECS with a configured gateway:
```bash
cd /opt/hermes-docker && docker compose up -d --force-recreate
sleep 30
docker exec hermes-webui sh -c 'pgrep -af "hermes gateway run"'        # → process present
docker exec hermes-webui sh -c 'cat /opt/hermes/.hermes/gateway_state.json'  # → "gateway_state":"running"
docker logs hermes-webui 2>&1 | grep -i "gateway supervisor"           # → "started"
```
Then confirm the WeCom Bot responds. On an instance with NO gateway configured (no gateway_state.json), confirm the log shows `gateway supervisor: skipped:not-configured` and no gateway process spins.

---

## Self-Review

**Spec coverage:**
- Daemon thread next to `_startup_skill_sync` → Task 3. ✓
- Persistent signal `gateway_state.json == running` (running→start, stopped/missing→skip) → Task 1 (`should_autostart`). ✓
- Dedup against live gateway process → Task 2 (`gateway_running` + `maybe_start_gateway` already-running skip). ✓
- Supervised relaunch loop identical to the manual one → Task 2 (`build_supervisor_argv`). ✓
- root agent home (not profile-scoped) → Task 3 uses `get_default_hermes_root()`; agent dir = `root/hermes-agent`. ✓
- Non-blocking, never breaks startup → Task 2 (`maybe_start_gateway` catches all) + Task 3 (thread try/except). ✓
- Tests without real subprocess → Tasks 1–2 inject `running_check`/`spawn`. ✓

**Placeholder scan:** None. Task 3 Step 2 is a conditional ("if already imported, skip") with the exact action either way — not a placeholder.

**Type consistency:** `should_autostart(root_home: Path)`, `gateway_running()`, `build_supervisor_argv(root_home: Path)`, `maybe_start_gateway(root_home, *, running_check, spawn, log)` — names + signatures identical across module (T1/T2), tests (T1/T2), and server.py call site (T3). `GATEWAY_RUNTIME_STATUS_FILE` constant consistent with agent_health. Agent dir `root_home / "hermes-agent"` used consistently in `build_supervisor_argv` and the manual-loop reference.
