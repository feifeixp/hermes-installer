"""
Unit tests for Windows-specific install helpers in main.py.

These run on all platforms (macOS/Linux in CI). subprocess calls are mocked
so no actual installation happens during tests.
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Add repo root to path so we can import from main
sys.path.insert(0, str(Path(__file__).parent.parent))


# ── _is_agent_installed ───────────────────────────────────────────────────


def test_is_agent_installed_missing_venv(tmp_path):
    """Returns False when venv/Scripts/python.exe doesn't exist."""
    with patch("pathlib.Path.home", return_value=tmp_path):
        from main import _is_agent_installed
        assert _is_agent_installed() is False


def test_is_agent_installed_import_fails(tmp_path):
    """Returns False when venv exists but run_agent import fails."""
    venv_py = tmp_path / ".hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()

    with patch("pathlib.Path.home", return_value=tmp_path), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr=b"ModuleNotFoundError: run_agent")
        from main import _is_agent_installed
        assert _is_agent_installed() is False
        mock_run.assert_called_once()


def test_is_agent_installed_ok(tmp_path):
    """Returns True when venv exists and run_agent import succeeds."""
    venv_py = tmp_path / ".hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()

    with patch("pathlib.Path.home", return_value=tmp_path), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        from main import _is_agent_installed
        assert _is_agent_installed() is True


def test_is_agent_installed_timeout(tmp_path):
    """Returns False (not raises) when subprocess times out."""
    venv_py = tmp_path / ".hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()

    with patch("pathlib.Path.home", return_value=tmp_path), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired("python", 10)):
        from main import _is_agent_installed
        assert _is_agent_installed() is False


# ── _find_system_python ───────────────────────────────────────────────────


def test_find_system_python_found(monkeypatch):
    """Returns path when a Python ≥3.11 is on PATH."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/python3.13" if name == "python3.13" else None)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        from main import _find_system_python
        result = _find_system_python()
    assert result == "/usr/bin/python3.13"


def test_find_system_python_old_version(monkeypatch):
    """Returns None when only Python <3.11 is available."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/python3" if name == "python3" else None)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)  # version check fails
        from main import _find_system_python
        result = _find_system_python()
    assert result is None


def test_find_system_python_none_found(monkeypatch):
    """Returns None when no Python is on PATH."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    from main import _find_system_python
    assert _find_system_python() is None


# ── _run_uv ───────────────────────────────────────────────────────────────


def test_run_uv_success(tmp_path, capsys):
    """Streams output and returns on success."""
    uv_exe = tmp_path / "uv.exe"
    uv_exe.touch()

    mock_proc = MagicMock()
    mock_proc.stdout = iter([b"Resolved 15 packages\n", b"Installed 15 packages\n"])
    mock_proc.wait.return_value = None
    mock_proc.returncode = 0

    with patch("subprocess.Popen", return_value=mock_proc):
        from main import _run_uv
        _run_uv(uv_exe, ["pip", "install", "-e", "."], error_prefix="install failed")

    captured = capsys.readouterr()
    assert "Resolved 15 packages" in captured.out
    assert "Installed 15 packages" in captured.out


def test_run_uv_failure_raises(tmp_path):
    """Raises RuntimeError with last output lines when uv exits non-zero."""
    uv_exe = tmp_path / "uv.exe"
    uv_exe.touch()

    mock_proc = MagicMock()
    mock_proc.stdout = iter([b"error: network unreachable\n"])
    mock_proc.wait.return_value = None
    mock_proc.returncode = 1

    with patch("subprocess.Popen", return_value=mock_proc):
        from main import _run_uv
        with pytest.raises(RuntimeError, match="network unreachable"):
            _run_uv(uv_exe, ["pip", "install", "."], error_prefix="install failed")
