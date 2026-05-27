"""End-to-end test: real HTTP, mock server, full report() flow."""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import crash_reporter as cr


@pytest.fixture
def mock_server(monkeypatch):
    """Start a real HTTP server on a random port. Yields received payloads."""
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                received.append({
                    "payload": json.loads(body),
                    "auth": self.headers.get("Authorization", ""),
                })
            except Exception:
                received.append({"payload": None, "auth": ""})
            self.send_response(204)
            self.end_headers()
        def log_message(self, fmt, *args): pass  # silence

    # Bind to a random free port
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    monkeypatch.setattr(cr, "ENDPOINT", f"http://127.0.0.1:{port}/api/client-log")
    yield received
    server.shutdown()


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path, monkeypatch):
    qdir = tmp_path / "queue"
    monkeypatch.setattr(cr, "QUEUE_DIR", qdir)
    monkeypatch.setattr(cr, "DLQ_DIR", qdir / "quarantine")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return qdir


def test_end_to_end_report_with_log_tail(mock_server, tmp_path):
    """Full report() invocation hits the wire and server sees expected payload."""
    # Set up a fake log file to attach (lines so readline() only drops a partial first line)
    log_file = tmp_path / "webui-server.log"
    line = ("a" * 99) + "\n"  # 100 bytes/line
    log_file.write_text(line * 2000 + "TAIL-MARKER\n", encoding="utf-8")

    result = cr.report(
        "webui_startup_crash",
        "NameError: name 'base_events' is not defined",
        traceback="Traceback (most recent call last):\n  File ...",
        log_path=str(log_file),
        extra={"venv_python": "/Users/alice/.hermes/hermes-agent/venv/python.exe"},
    )

    # Wait for the daemon thread to finish
    time.sleep(1.0)

    assert result is True, "should succeed against mock"
    assert len(mock_server) == 1, f"expected 1 POST, got {len(mock_server)}"

    record = mock_server[0]
    p = record["payload"]
    assert p["app"] == "hermes-installer"
    assert p["phase"] == "webui_startup_crash"
    assert "base_events" in p["error"]
    assert p["traceback"].startswith("Traceback")
    assert p["logTail"].endswith("TAIL-MARKER\n")
    assert len(p["logTail"].encode("utf-8")) <= cr.MAX_LOG_TAIL_BYTES
    # PII filter ran
    assert "alice" not in p["extra"]["venv_python"]
    assert "<USER>" in p["extra"]["venv_python"]


def test_end_to_end_failure_then_recovery(mock_server, tmp_path):
    """After a failed report, flush_queue() recovers when the server comes back."""
    # Phase 1: report when ENDPOINT is bad (use a port nobody's on)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()  # actually free it so connection refused fires

    saved_endpoint = cr.ENDPOINT
    cr.ENDPOINT = f"http://127.0.0.1:{dead_port}/api/client-log"
    cr.report("main_unhandled", "phase 1 fail")
    time.sleep(0.6)
    cr.ENDPOINT = saved_endpoint  # restore monkeypatch target

    queued = list((cr.QUEUE_DIR).glob("*.json"))
    assert len(queued) == 1, "first report should be queued"

    # Phase 2: server back up, flush
    n = cr.flush_queue()
    assert n == 1
    assert not list(cr.QUEUE_DIR.glob("*.json")), "queue should be empty after flush"
    assert len(mock_server) == 1, "mock should have received one recovered report"
