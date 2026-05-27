"""End-to-end: real HTTP, mock GitHub + mock OSS, full check_installer_update flow."""
from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from api import installer_update as iu


@pytest.fixture(autouse=True)
def reset_cache():
    iu._check_cache.clear()
    yield
    iu._check_cache.clear()


@pytest.fixture
def mock_endpoints(monkeypatch):
    """Spin up a single mock server serving both /releases/latest and /<tag>/<asset>."""
    state = {
        "tag": "v1.5.0",
        "asset_present": True,
        "github_calls": 0,
        "oss_calls": 0,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Mock GitHub Releases API
            if self.path == "/repos/feifeixp/hermes-installer/releases/latest":
                state["github_calls"] += 1
                body = json.dumps({
                    "tag_name": state["tag"],
                    "prerelease": False,
                    "body": "## What's Changed\n- integration test fixture",
                    "html_url": f"https://github.com/feifeixp/hermes-installer/releases/tag/{state['tag']}",
                    "assets": [{"name": "Hermes-Installer-macOS.dmg"},
                               {"name": "Hermes-Installer-Windows.zip"}],
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def do_HEAD(self):
            state["oss_calls"] += 1
            if self.path.startswith(f"/hermes/{state['tag']}/") and state["asset_present"]:
                self.send_response(200)
            else:
                self.send_response(404)
            self.end_headers()

        def log_message(self, fmt, *args): pass

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    base = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(iu, "GITHUB_RELEASES_API",
                        f"{base}/repos/feifeixp/hermes-installer/releases/latest")
    monkeypatch.setattr(iu, "OSS_BASE", f"{base}/hermes")
    yield state
    server.shutdown()


def test_end_to_end_update_available(mock_endpoints, monkeypatch):
    """Mock GitHub + OSS reachable → update_available + oss_ready True."""
    monkeypatch.setattr(iu.sys, "platform", "darwin")
    result = iu.check_installer_update(current_version="v1.4.2")
    assert result["ok"] is True
    assert result["update_available"] is True
    assert result["oss_ready"] is True
    assert result["latest_version"] == "v1.5.0"
    assert result["download_url"].endswith("Hermes-Installer-macOS.dmg")
    assert mock_endpoints["github_calls"] == 1
    assert mock_endpoints["oss_calls"] == 1


def test_end_to_end_oss_not_ready_yet(mock_endpoints, monkeypatch):
    """GitHub returns new release but OSS not synced → oss_ready=False."""
    monkeypatch.setattr(iu.sys, "platform", "darwin")
    mock_endpoints["asset_present"] = False  # simulate OSS lag
    result = iu.check_installer_update(current_version="v1.4.2")
    assert result["update_available"] is True
    assert result["oss_ready"] is False
