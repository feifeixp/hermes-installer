"""Regression: get_instance_status() must passthrough the 4 stop-extension
fields the dashboard adds in Phase 1 (stoppedAt, stoppedMs,
estimatedNewExpiresAt, extendedByMs). If neowow.py is ever refactored to
whitelist response keys, this test breaks loudly."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from api import neowow as nw


def _make_dashboard_response(payload: dict) -> MagicMock:
    """Build a urlopen-style mock returning the given JSON payload."""
    body = json.dumps(payload).encode("utf-8")
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    resp.read.return_value = body
    return resp


def test_get_instance_status_passes_through_extension_fields():
    """All 4 stop-extension fields survive the proxy roundtrip."""
    payload = {
        "ok": True,
        "state": "stopped",
        "publicIp": "1.2.3.4",
        "subdomain": "chat-u1.neowow.studio",
        # Phase 1 stop-extension fields
        "stoppedAt":             "2026-06-01T11:00:00Z",
        "stoppedMs":             3_600_000,
        "estimatedNewExpiresAt": "2026-06-30T01:00:00.000Z",
        "extendedByMs":          7_200_000,
    }
    mock_resp = _make_dashboard_response(payload)
    with patch.object(nw, "get_jwt", return_value="fake-jwt"), \
         patch.object(nw.urllib.request, "urlopen", MagicMock(return_value=mock_resp)):
        result = nw.get_instance_status()

    # Stop-extension fields
    assert result["stoppedAt"]             == "2026-06-01T11:00:00Z"
    assert result["stoppedMs"]             == 3_600_000
    assert result["estimatedNewExpiresAt"] == "2026-06-30T01:00:00.000Z"
    assert result["extendedByMs"]          == 7_200_000
    # Sanity: existing fields still come through
    assert result["state"]    == "stopped"
    assert result["publicIp"] == "1.2.3.4"


def test_get_instance_status_handles_null_stopped_fields():
    """Running instance → null/0 stop fields still passthrough cleanly."""
    payload = {
        "ok": True,
        "state": "running",
        "publicIp": "1.2.3.4",
        "stoppedAt":             None,
        "stoppedMs":             0,
        "estimatedNewExpiresAt": None,
        "extendedByMs":          0,
    }
    mock_resp = _make_dashboard_response(payload)
    with patch.object(nw, "get_jwt", return_value="fake-jwt"), \
         patch.object(nw.urllib.request, "urlopen", MagicMock(return_value=mock_resp)):
        result = nw.get_instance_status()

    assert result["stoppedAt"]             is None
    assert result["stoppedMs"]             == 0
    assert result["estimatedNewExpiresAt"] is None
    assert result["extendedByMs"]          == 0
