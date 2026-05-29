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
