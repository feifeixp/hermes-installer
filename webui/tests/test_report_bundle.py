"""report_bundle — gather + redact + upload the diagnostic bundle.

Run: python3.13 -m pytest webui/tests/test_report_bundle.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import api.report_bundle as rb


def test_sanitize_redacts_secrets_everywhere(monkeypatch):
    monkeypatch.setattr(rb, "_collect_logs", lambda: {
        "agent": {"tail": ["hello sk-ABCDEFGHIJKLMNOPQRSTUV world"], "bytes": 1, "truncated": False},
    })
    monkeypatch.setattr(rb, "_collect_config", lambda: {"base_url": "app.neowow.studio"})
    bundle = rb.build_report_bundle("token sk-ABCDEFGHIJKLMNOPQRSTUV in desc", health={"active_runs": 1})
    dumped = json.dumps(bundle)
    assert "sk-ABCDEFGHIJKLMNOPQRSTUV" not in dumped
    assert "sk-***REDACTED***" in dumped
    assert bundle["kind"] == "user_report"
    assert bundle["health"] == {"active_runs": 1}


def test_collect_config_cred_kind(monkeypatch):
    monkeypatch.setenv("NEOWOW_CODING_PLAN_API_KEY", "nws_dt_deadbeef")
    assert rb._collect_config()["codingPlanCredKind"] == "deploy_token"
    monkeypatch.setenv("NEOWOW_CODING_PLAN_API_KEY", "eyJh.eyJb.sig")
    assert rb._collect_config()["codingPlanCredKind"] == "jwt"
    monkeypatch.delenv("NEOWOW_CODING_PLAN_API_KEY", raising=False)
    assert rb._collect_config()["codingPlanCredKind"] == "none"


def test_upload_success(monkeypatch):
    class _Resp:
        status = 200
        def read(self): return b'{"reportId":"BR-7Q2K9F"}'
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(rb.urllib.request, "urlopen", lambda *a, **k: _Resp())
    out = rb.upload_report({"kind": "user_report"})
    assert out == {"ok": True, "reportId": "BR-7Q2K9F"}


def test_upload_failure_saves_pending(tmp_path, monkeypatch):
    def _boom(*a, **k): raise OSError("network down")
    monkeypatch.setattr(rb.urllib.request, "urlopen", _boom)
    monkeypatch.setattr(rb, "_pending_dir", lambda: tmp_path / "pending-reports")
    out = rb.upload_report({"kind": "user_report", "x": 1})
    assert out["ok"] is False
    assert out["saved"]
    saved = Path(out["saved"])
    assert saved.is_file()
    assert json.loads(saved.read_text(encoding="utf-8"))["x"] == 1
