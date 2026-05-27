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
