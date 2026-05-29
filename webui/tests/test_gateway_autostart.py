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
