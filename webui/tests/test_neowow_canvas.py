"""Canvas account scope, read-only extraction, and native creation brief tests."""
import json
import io
import subprocess
from types import SimpleNamespace

import pytest


@pytest.fixture
def canvas(monkeypatch, tmp_path):
    from api import neowow_canvas as module
    home = tmp_path / "home"
    directory = home / "neowow-cli"
    directory.mkdir(parents=True)
    (directory / "config.json").write_text('{"apiKey":"dummy-private-key","userId":"123"}')
    monkeypatch.setattr(module.cli, "_home", lambda: home)
    monkeypatch.setattr(module.cli, "_binding_available", lambda: True)
    return module


def test_selection_scoped_to_account_and_profile(canvas, monkeypatch, tmp_path):
    monkeypatch.setattr(canvas, "_run", lambda args, home: {"sessionId": "123", "title": "我的画布"})
    assert canvas.select_project("123")["active"]["sessionId"] == "123"
    assert canvas.active_project()["title"] == "我的画布"
    saved = json.loads((canvas.cli._home() / "neowow-cli/studio-project.json").read_text())
    assert "dummy-private-key" not in json.dumps(saved)
    (canvas.cli._home() / "neowow-cli/config.json").write_text('{"apiKey":"different-account-key"}')
    assert canvas.active_project() is None
    other = tmp_path / "other"
    (other / "neowow-cli").mkdir(parents=True)
    (other / "neowow-cli/config.json").write_text('{"apiKey":"dummy-private-key"}')
    assert canvas.active_project(other) is None


def test_project_list_uses_folder_pagination(canvas, monkeypatch):
    calls = []
    def run(args, home):
        calls.append(args)
        return {"projects": [{"sessionId": "123", "title": "<script>"}], "folders": [{"folderId": "f_1", "name": "项目"}]}
    monkeypatch.setattr(canvas, "_run", run)
    result = canvas.list_projects("2", "f_1")
    assert calls == [["project", "list", "--page-num", "2", "--page-size", "20", "--folder", "f_1"]]
    assert result["projects"][0]["title"] == "<script>"
    with pytest.raises(ValueError):
        canvas.list_projects("0")
    with pytest.raises(ValueError):
        canvas.list_projects("1", "--other-flag")


def test_create_requires_literal_ack_before_cli_write(canvas, monkeypatch):
    calls = []
    monkeypatch.setattr(canvas, "_run", lambda args, home: calls.append(args) or {"sessionId": "123", "title": "新画布"})
    with pytest.raises(ValueError):
        canvas.create_project("新画布", "true")
    assert not calls
    canvas.create_project("新画布", True)
    assert calls == [["project", "create", "新画布"]]


def test_creation_brief_requires_current_scope_and_cost_confirmation(canvas):
    canvas._save_active(canvas.cli._home(), {"sessionId": "123", "title": "作品"}, canvas._credentials(canvas.cli._home()))
    body = {"projectId": "123", "canvasClosed": True, "kind": "video", "prompt": "雨夜街头"}
    brief = canvas.creation_brief(body)
    assert "sessionId：123" in brief["message"]
    assert "确认后再提交付费生成" in brief["message"]
    assert "回填画布" in brief["message"]
    assert "dummy-private-key" not in json.dumps(brief)
    for change in ({"canvasClosed": "true"}, {"projectId": "456"}, {"kind": "command"}):
        with pytest.raises(ValueError):
            canvas.creation_brief({**body, **change})


def test_only_final_node_outputs_are_extracted(canvas):
    project = {"nodes": [
        {"id": "image1", "type": "image", "data": {"status": "SUCCESS", "inputImageUrls": ["https://media.example/final.png", "javascript:alert(1)"], "connectedImageUrls": ["https://media.example/reference.png"]}},
        {"id": "upload1", "type": "image", "data": {"status": "SUCCESS", "type": "upload", "inputImageUrls": ["https://media.example/upload.png"]}},
        {"id": "pending1", "type": "video", "data": {"status": "PENDING", "taskId": "task1", "inputVideoUrls": ["https://media.example/old.mp4"]}},
        {"id": "failed1", "type": "image", "data": {"status": "FAILED", "inputImageUrls": ["https://media.example/failed.png"]}},
        {"id": "audio1", "type": "audio", "data": {"status": "completed", "inputAudioUrls": ["https://media.example/final.mp3"]}},
        {"id": "text1", "type": "text", "data": {"result": "最终脚本 <script>"}},
    ]}
    result = canvas.extract_results(project)
    assert [r["nodeKey"] for r in result["results"]] == ["image1", "audio1", "text1"]
    assert result["counts"] == {"success": 3, "pending": 1, "failed": 1, "unknown": 0}
    assert not any("reference" in r.get("url", "") or "old" in r.get("url", "") for r in result["results"])
    tasks = {"tasks": [{"taskId": "task1", "status": "SUCCESS", "resultData": ["https://media.example/new.mp4"]}]}
    fresh = canvas.extract_results(project, tasks)
    assert next(r for r in fresh["results"] if r["nodeKey"] == "pending1")["url"].endswith("new.mp4")


def test_result_poll_is_read_only_and_failed_tasks_are_not_success(canvas, monkeypatch):
    home = canvas.cli._home()
    canvas._save_active(home, {"sessionId": "123", "title": "作品"}, canvas._credentials(home))
    calls = []
    def run(args, home):
        calls.append(args)
        if args[0] == "project":
            return {"sessionId": "123", "nodes": [{"id": "node1", "type": "image", "data": {"status": "PENDING", "taskId": "task1"}}]}
        return {"tasks": [{"taskId": "task1", "status": "FAILED", "resultData": ["https://media.example/wrong.png"]}]}
    monkeypatch.setattr(canvas, "_run", run)
    result = canvas.get_results("123")
    assert not result["results"]
    assert result["counts"]["failed"] == 1
    assert calls[-1] == ["poll", "task1", "--once", "-p", "123"]
    assert not any("--apply" in args for args in calls)
    with pytest.raises(ValueError):
        canvas.get_results("456")


def test_cli_credentials_never_in_arguments_or_error(canvas, monkeypatch, tmp_path):
    skill = tmp_path / "skill"
    monkeypatch.setattr(canvas.cli, "ensure_installed", lambda home: skill)
    monkeypatch.setattr(canvas.shutil, "which", lambda name: "/mock/node")
    def run(args, **kwargs):
        assert "dummy-private-key" not in " ".join(args)
        assert kwargs["env"]["NEOWOW_API_KEY"] == "dummy-private-key"
        return SimpleNamespace(returncode=3, stdout="", stderr="dummy-private-key password errCode=2001")
    monkeypatch.setattr(canvas.subprocess, "run", run)
    with pytest.raises(ValueError) as error:
        canvas._run(["project", "list"], canvas.cli._home())
    assert "dummy-private-key" not in str(error.value)
    assert "password" not in str(error.value)
    assert "授权已失效" in str(error.value)


def test_shared_instance_cannot_select_or_extract(canvas, monkeypatch):
    monkeypatch.setattr(canvas.cli, "_binding_available", lambda: False)
    with pytest.raises(ValueError, match="专属"):
        canvas.active_project()
    with pytest.raises(ValueError, match="专属"):
        canvas.select_project("123")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://example.com/a.png", "https://key:secret@example.com/a.png", "https://example.com/a\n.png"])
def test_unsafe_result_links_are_rejected(canvas, url):
    assert canvas.media_url(url) is None


def test_export_route_preserves_attachment_and_private_cache(monkeypatch):
    from api import routes, neowow_canvas
    result = {"active": {"sessionId": "123", "title": "作品"}, "results": [{"kind": "text", "text": "成品"}], "nodes": [], "counts": {}, "warning": ""}
    monkeypatch.setattr(neowow_canvas, "get_results", lambda project: result)
    handler = SimpleNamespace(wfile=io.BytesIO(), headers={}, sent_headers={})
    handler.send_response = lambda code: setattr(handler, "status", code)
    handler.send_header = lambda key, value: handler.sent_headers.update({key: value})
    handler.end_headers = lambda: None
    routes.handle_get(handler, SimpleNamespace(path="/api/neowow/canvas/export", query="project=123"))
    assert handler.status == 200
    assert handler.sent_headers["Content-Disposition"].startswith("attachment;")
    assert handler.sent_headers["Cache-Control"] == "no-store"
    assert json.loads(handler.wfile.getvalue())["results"][0]["text"] == "成品"


def test_canvas_media_allowed_without_opening_script_sources():
    from api.helpers import _security_headers
    from server import Handler
    headers = {}
    _security_headers(SimpleNamespace(send_header=lambda key, value: headers.update({key: value})))
    enforced = headers["Content-Security-Policy"]
    media = next(part for part in enforced.split(";") if part.strip().startswith("media-src"))
    assert "https:" in media and "'self'" in media
    connect = next(part for part in enforced.split(";") if part.strip().startswith("connect-src"))
    assert "https://cdn.jsdelivr.net" in connect and "'self'" in connect
    assert "'unsafe-eval'" not in enforced
    assert "media-src 'self' data: blob: https:" in Handler.csp_report_only_policy()
