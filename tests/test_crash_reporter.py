"""Unit tests for crash_reporter module.

These run on all platforms. No real network IO — urllib.request.urlopen is
mocked in every test that exercises the wire format.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add repo root to path so we can import crash_reporter
sys.path.insert(0, str(Path(__file__).parent.parent))

import crash_reporter as cr


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path, monkeypatch):
    """Redirect the queue directory to a temp path per test."""
    qdir = tmp_path / "queue"
    monkeypatch.setattr(cr, "QUEUE_DIR", qdir)
    monkeypatch.setattr(cr, "DLQ_DIR", qdir / "quarantine")
    return qdir


def _mock_urlopen_ok(status: int = 204):
    """Build a mock urlopen that returns an HTTP response with the given status."""
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.status = status
    return MagicMock(return_value=response)


def test_report_success_204(isolated_queue):
    """A successful POST returns True and writes nothing to the queue."""
    with patch.object(cr.urllib.request, "urlopen", _mock_urlopen_ok()):
        result = cr.report("main_unhandled", "test error")
    assert result is True
    assert not isolated_queue.exists() or not any(isolated_queue.iterdir())


def test_report_network_fail_enqueues(isolated_queue):
    """When POST fails, payload lands in the queue."""
    failing_urlopen = MagicMock(side_effect=cr.urllib.error.URLError("connection refused"))
    with patch.object(cr.urllib.request, "urlopen", failing_urlopen):
        result = cr.report("main_unhandled", "boom")
    assert result is False
    files = list(isolated_queue.glob("*.json"))
    assert len(files) == 1, f"expected 1 queued report, got {len(files)}"
    # Filename is epoch-ns + .attempt-N.json
    assert files[0].name.endswith(".attempt-1.json")
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["phase"] == "main_unhandled"
    assert payload["error"] == "boom"


def test_queue_file_has_0600_permissions(isolated_queue):
    """Queue files must be 0600 — they contain JWT."""
    if sys.platform == "win32":
        pytest.skip("POSIX permissions don't apply on Windows")
    failing_urlopen = MagicMock(side_effect=cr.urllib.error.URLError("nope"))
    with patch.object(cr.urllib.request, "urlopen", failing_urlopen):
        cr.report("main_unhandled", "boom")
    files = list(isolated_queue.glob("*.json"))
    assert files
    mode = files[0].stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_report_timeout_returns_within_budget(isolated_queue):
    """Slow POST → report() returns within ~0.6s even if network would take 10s."""
    def slow_urlopen(*args, **kwargs):
        time.sleep(2.0)
        m = MagicMock()
        m.__enter__.return_value = m
        m.__exit__.return_value = False
        m.status = 204
        return m

    with patch.object(cr.urllib.request, "urlopen", slow_urlopen):
        t0 = time.monotonic()
        result = cr.report("main_unhandled", "slow network")
        elapsed = time.monotonic() - t0

    assert result is False, "should return False since we didn't wait for completion"
    assert elapsed < 0.8, f"report() blocked {elapsed:.2f}s — should be < 0.8s"


def test_pii_windows_username_filtered(isolated_queue):
    """C:\\Users\\Alice\\foo → C:\\Users\\<USER>\\foo in traceback."""
    captured = {}
    def capture_urlopen(req, timeout=None):
        captured["body"] = req.data.decode("utf-8")
        m = MagicMock()
        m.__enter__.return_value = m
        m.__exit__.return_value = False
        m.status = 204
        return m
    with patch.object(cr.urllib.request, "urlopen", capture_urlopen):
        cr.report(
            "main_unhandled",
            "Error in C:\\Users\\Alice\\.hermes\\webui\\foo.py",
            traceback="File C:\\Users\\Alice\\AppData\\Local\\Temp\\x.py",
        )
    time.sleep(0.6)  # let worker thread finish
    payload = json.loads(captured["body"])
    assert "Alice" not in payload["error"], f"Alice leaked: {payload['error']!r}"
    assert "<USER>" in payload["error"]
    assert "Alice" not in payload["traceback"]
    assert "<USER>" in payload["traceback"]


def test_pii_unix_username_filtered(isolated_queue):
    """/Users/alice/foo → /Users/<USER>/foo on macOS-style paths."""
    captured = {}
    def capture_urlopen(req, timeout=None):
        captured["body"] = req.data.decode("utf-8")
        m = MagicMock()
        m.__enter__.return_value = m
        m.__exit__.return_value = False
        m.status = 204
        return m
    with patch.object(cr.urllib.request, "urlopen", capture_urlopen):
        cr.report("main_unhandled", "Crash in /Users/alice/.hermes/server.py")
    time.sleep(0.6)
    payload = json.loads(captured["body"])
    assert "alice" not in payload["error"]
    assert "/Users/<USER>" in payload["error"]


def test_pii_api_key_and_jwt_redacted(isolated_queue):
    """API keys + JWTs are not transmitted in plaintext."""
    secrets = [
        "sk-abcdefghijklmnopqrstuvwxyz1234567890",
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        "api_key=secretvalue123456789",
        "neoToken=secret-cookie-value",
    ]
    body = "stuff\n" + "\n".join(secrets) + "\nmore stuff"
    captured = {}
    def capture_urlopen(req, timeout=None):
        captured["body"] = req.data.decode("utf-8")
        m = MagicMock()
        m.__enter__.return_value = m
        m.__exit__.return_value = False
        m.status = 204
        return m
    with patch.object(cr.urllib.request, "urlopen", capture_urlopen):
        cr.report("main_unhandled", "ok", traceback=body)
    time.sleep(0.6)
    payload = json.loads(captured["body"])
    for secret in ["abcdefghijklmnopqrstuvwxyz1234567890",
                   "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
                   "secretvalue123456789",
                   "secret-cookie-value"]:
        assert secret not in payload["traceback"], f"secret {secret[:10]}... leaked"


def test_log_tail_reads_last_n_bytes(tmp_path):
    """Large log file → only the tail is read."""
    log_file = tmp_path / "big.log"
    # Write 1 MB of distinct lines
    lines = [f"line {i:06d}\n" for i in range(50_000)]
    log_file.write_text("".join(lines), encoding="utf-8")
    assert log_file.stat().st_size > 200_000

    tail = cr._read_log_tail(str(log_file))
    assert tail is not None
    assert len(tail.encode("utf-8")) <= cr.MAX_LOG_TAIL_BYTES
    # Should contain the LAST line, not the FIRST
    assert "line 049999" in tail
    assert "line 000000" not in tail


def test_log_tail_missing_file_ok(tmp_path):
    """Non-existent path → None, no exception."""
    missing = tmp_path / "nope.log"
    result = cr._read_log_tail(str(missing))
    assert result is None
