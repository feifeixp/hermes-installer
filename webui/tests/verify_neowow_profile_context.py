"""Container integration probe: actual Agent terminal, dummy accounts, no API calls."""
import concurrent.futures
import contextvars
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import threading


def main():
    with tempfile.TemporaryDirectory(prefix="canvas-context-") as directory:
        root = Path(directory)
        os.environ.update(HERMES_HOME=str(root / "default"), HERMES_BASE_HOME=str(root),
                          HERMES_WEBUI_STATE_DIR=str(root / "state"))
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from api.config import _set_thread_env, _clear_thread_env
        from api import neowow_cli as cli
        from tools.environments.local import LocalEnvironment
        from gateway.session_context import get_session_env
        cli._install_terminal_profile_bridge()
        assert cli._supports_profile_context(), "Agent lacks safe canvas context"
        cli.STATE_DIR = root / "state"
        fake_bin = root / "bin"
        fake_bin.mkdir()
        # Stand-in for Node reports only the dummy userId that the real CLI
        # launcher selected. No requests, keys, or real profile files are read.
        node = fake_bin / "node"
        code = "import json,os;print(json.load(open(os.environ['NEOWOW_CONFIG_DIR']+'/config.json'))['userId'])"
        node.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -c " + shlex.quote(code) + "\n")
        node.chmod(0o755)
        homes = [root / "account-a", root / "account-b"]
        for home in homes:
            cli.ensure_installed(home)
            (home / "neowow-cli").mkdir()
            (home / "neowow-cli/config.json").write_text(json.dumps({"userId": home.name, "apiKey": "dummy-test-only"}))
        barrier = threading.Barrier(2)

        def turn(home):
            _set_thread_env(HERMES_HOME=str(home), HERMES_SESSION_KEY=home.name,
                            HERMES_SESSION_PLATFORM="webui")
            try:
                context = contextvars.copy_context()
                # Match the Agent executor's copy_context into a tool worker.
                def tool():
                    assert get_session_env("HERMES_SESSION_KEY") == home.name
                    backend = LocalEnvironment(cwd=str(root), env={})
                    try:
                        barrier.wait(timeout=30)
                        # Deliberately make the process-global home wrong for A.
                        os.environ["HERMES_HOME"] = str(homes[1])
                        command = "PATH=" + shlex.quote(str(fake_bin)) + ":$PATH " + shlex.quote(str(cli.STATE_DIR / "bin/neowow")) + " whoami"
                        for _ in range(2):
                            result = backend.execute(command, timeout=20)
                            assert result["returncode"] == 0, result
                            assert result["output"].strip() == home.name, result
                    finally:
                        backend.cleanup()
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(context.run, tool).result(timeout=90)
            finally:
                _clear_thread_env()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(turn, homes))
        # Actual shared snapshot: initialize under A, then execute under B.
        _set_thread_env(HERMES_HOME=str(homes[0]), HERMES_SESSION_KEY="shared")
        shared = LocalEnvironment(cwd=str(root), env={})
        try:
            for home in homes:
                _set_thread_env(HERMES_HOME=str(home), HERMES_SESSION_KEY="shared")
                command = "PATH=" + shlex.quote(str(fake_bin)) + ":$PATH " + shlex.quote(str(cli.STATE_DIR / "bin/neowow")) + " whoami"
                result = shared.execute(command, timeout=20)
                assert result["returncode"] == 0 and result["output"].strip() == home.name, result
        finally:
            shared.cleanup()
            _clear_thread_env()
        print("PASS: concurrent Agent terminals select their own dummy canvas accounts")


if __name__ == "__main__":
    main()
