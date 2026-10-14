"""Per-turn context survives thread hops and shared terminal snapshot exports."""
import concurrent.futures
import contextvars
import os
import shlex
import subprocess
import sys
import threading
from types import ModuleType


def test_context_bridge_overrides_shared_snapshot(monkeypatch, tmp_path):
    from api import config, neowow_cli as cli
    home_context = contextvars.ContextVar("test-home", default=None)
    session_context = contextvars.ContextVar("test-session", default="")
    constants = ModuleType("hermes_constants")
    constants.set_hermes_home_override = home_context.set
    constants.reset_hermes_home_override = home_context.reset
    constants.get_hermes_home_override = home_context.get
    gateway = ModuleType("gateway.session_context")
    gateway.set_session_vars = lambda **kw: session_context.set(kw.get("session_key", ""))
    gateway.clear_session_vars = lambda token: session_context.reset(token)
    monkeypatch.setitem(sys.modules, "hermes_constants", constants)
    monkeypatch.setitem(sys.modules, "gateway.session_context", gateway)
    local = ModuleType("tools.environments.local")

    class Terminal:
        def execute(self, command):
            # Emulate the real terminal's snapshot sourcing before command body.
            return subprocess.check_output(["/bin/sh", "-c", "export HERMES_HOME=wrong-snapshot; " + command], text=True).strip()

    local.LocalEnvironment = Terminal
    local._inject_context_hermes_home = lambda env: env.update(HERMES_HOME=home_context.get())
    monkeypatch.setitem(sys.modules, "tools.environments.local", local)
    cli._install_terminal_profile_bridge()
    cli._install_terminal_profile_bridge()  # idempotent registration
    shared = Terminal()
    barrier = threading.Barrier(2)

    def turn(name):
        home = str(tmp_path / name)
        config._set_thread_env(HERMES_HOME=home, HERMES_SESSION_KEY=name)
        try:
            context = contextvars.copy_context()
            def child():
                barrier.wait(timeout=5)
                assert session_context.get() == name
                command = shlex.quote(sys.executable) + " -c " + shlex.quote("import os; print(os.environ['HERMES_HOME'])")
                assert shared.execute(command) == home
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as workers:
                workers.submit(context.run, child).result(timeout=10)
        finally:
            config._clear_thread_env()
        assert home_context.get() is None
        assert session_context.get() == ""

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(turn, ["a", "b"]))
