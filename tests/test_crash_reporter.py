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
