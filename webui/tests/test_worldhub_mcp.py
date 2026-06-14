"""Tests for worldhub_mcp.py — WorldHub MCP server."""
import base64, json, sys
from pathlib import Path

import pytest
pytest.importorskip("mcp", reason="mcp package not installed (optional)")

_REPO = Path(__file__).parent.parent.resolve()
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import worldhub_mcp as wh


def _make_jwt(sub: str) -> str:
    def seg(o): return base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
    return f"{seg({'alg':'HS256','typ':'JWT'})}.{seg({'sub':sub})}.x"


def test_jwt_sub_extracts_sub():
    assert wh._jwt_sub(_make_jwt("tk_123")) == "tk_123"


def test_jwt_sub_bad_token_returns_empty():
    assert wh._jwt_sub("not-a-jwt") == ""


def test_rest_get_sets_schema_and_auth(monkeypatch):
    captured = {}
    def fake_http(method, url, *, headers, body=None, timeout=15):
        captured.update(method=method, url=url, headers=headers, body=body)
        return 200, [{"id": "w1"}]
    monkeypatch.setattr(wh, "_http", fake_http)
    monkeypatch.setattr(wh, "_supabase_jwt", lambda: "JWT123")
    out = wh._rest("GET", "/worlds", params={"select": "id"})
    assert out == [{"id": "w1"}]
    assert captured["headers"]["apikey"] == wh.SUPABASE_ANON
    assert captured["headers"]["Authorization"] == "Bearer JWT123"
    assert captured["headers"]["Accept-Profile"] == "worldhub"
    assert "select=id" in captured["url"]


def test_rest_write_uses_content_profile(monkeypatch):
    captured = {}
    monkeypatch.setattr(wh, "_supabase_jwt", lambda: "JWT123")
    monkeypatch.setattr(wh, "_http",
        lambda m,u,*,headers,body=None,timeout=15: (captured.update(headers=headers,body=body) or (201, None)))
    wh._rest("POST", "/proposals", body={"x": 1})
    assert captured["headers"]["Content-Profile"] == "worldhub"
    assert captured["body"] == {"x": 1}


def _world(entities, edges):
    return {"entities": entities, "edges": edges}


def test_conflict_duplicate_entity_id():
    existing = _world([{"ent_id": "char_a", "name": "A", "aliases": []}], [])
    draft = {"entities": [{"id": "char_a", "name": "A2"}], "relations": [], "edits": [], "deletes": []}
    out = wh._detect_conflicts(existing, draft)
    assert any(c["kind"] == "duplicate_id" for c in out)


def test_conflict_name_alias_collision():
    existing = _world([{"ent_id": "char_a", "name": "Arin", "aliases": ["阿临"]}], [])
    draft = {"entities": [{"id": "char_b", "name": "阿临"}], "relations": [], "edits": [], "deletes": []}
    out = wh._detect_conflicts(existing, draft)
    assert any(c["kind"] == "name_collision" for c in out)


def test_conflict_bad_type():
    existing = _world([], [])
    draft = {"entities": [{"id": "x1", "name": "X", "type": "spaceship"}], "relations": [], "edits": [], "deletes": []}
    out = wh._detect_conflicts(existing, draft)
    assert any(c["kind"] == "bad_entity_type" for c in out)


def test_conflict_dangling_edge():
    existing = _world([{"ent_id": "char_a", "name": "A", "aliases": []}], [])
    draft = {"entities": [], "relations": [{"source": "char_a", "target": "ghost", "type": "ally_of"}], "edits": [], "deletes": []}
    out = wh._detect_conflicts(existing, draft)
    assert any(c["kind"] == "dangling_edge" for c in out)


def test_conflict_mutex_edge():
    existing = _world(
        [{"ent_id": "a", "name": "A", "aliases": []}, {"ent_id": "b", "name": "B", "aliases": []}],
        [{"source": "a", "target": "b", "type": "ally_of"}])
    draft = {"entities": [], "relations": [{"source": "a", "target": "b", "type": "enemy_of"}], "edits": [], "deletes": []}
    out = wh._detect_conflicts(existing, draft)
    assert any(c["kind"] == "mutually_exclusive_edge" for c in out)


def test_no_conflict_clean_draft():
    existing = _world([{"ent_id": "a", "name": "A", "aliases": []}], [])
    draft = {"entities": [{"id": "b", "name": "B", "type": "characters"}],
             "relations": [{"source": "a", "target": "b", "type": "ally_of"}],
             "edits": [], "deletes": []}
    assert wh._detect_conflicts(existing, draft) == []


def test_get_world_by_slug(monkeypatch):
    calls = []
    def fake_rest(method, path, *, params=None, body=None, prefer=None):
        calls.append((method, path, params))
        if path == "/worlds":
            return [{"id": "w1", "slug": "xinghai", "name": "星骸", "visibility": "public",
                     "genre": [], "tone": "", "premise": "", "themes": [], "narrative_form": ""}]
        if path == "/entities":
            return [{"ent_id": "char_a", "type": "characters", "name": "A", "aliases": [], "tags": [],
                     "status": "draft", "summary": "", "fields": {}, "body": ""}]
        if path == "/edges":
            return []
        return []
    monkeypatch.setattr(wh, "_rest", fake_rest)
    out = wh._get_world_data("xinghai")
    assert out["overview"]["slug"] == "xinghai"
    assert out["entities"][0]["ent_id"] == "char_a"
    assert out["edges"] == []


def test_search_entities_filters_by_query(monkeypatch):
    monkeypatch.setattr(wh, "_get_world_data", lambda w: {
        "overview": {"id": "w1"},
        "entities": [{"ent_id": "a", "name": "阿临", "aliases": [], "tags": ["主角"], "type": "characters", "summary": ""},
                     {"ent_id": "b", "name": "城邦", "aliases": [], "tags": [], "type": "locations", "summary": ""}],
        "edges": []})
    res = wh._search_entities_data("w1", "主角")
    assert [e["ent_id"] for e in res] == ["a"]


def test_submit_as_contributor_creates_pending(monkeypatch):
    posted = []
    def fake_rest(method, path, *, params=None, body=None, prefer=None):
        if method == "POST" and path == "/proposals":
            posted.append(body); return [{"id": "p1"}]
        if path == "/rpc/approve_proposal":
            raise AssertionError("contributor must not approve")
        return []
    monkeypatch.setattr(wh, "_rest", fake_rest)
    monkeypatch.setattr(wh, "_resolve_world", lambda w: {"id": "w1"})
    monkeypatch.setattr(wh, "_my_role", lambda wid: "contributor")
    monkeypatch.setattr(wh, "_supabase_jwt", lambda: _make_jwt("tk_u"))
    out = wh._submit_world_changes_data("w1", {"entities": [{"id": "x", "name": "X"}],
                                               "relations": [], "edits": [], "deletes": []}, "add X")
    assert out["applied"] is False and out["proposals_created"] == 1
    assert posted[0]["kind"] == "ingest" and posted[0]["status"] == "pending"
    assert posted[0]["by_user"] == "tk_u"


def test_submit_as_founder_applies(monkeypatch):
    approved = []
    def fake_rest(method, path, *, params=None, body=None, prefer=None):
        if method == "POST" and path == "/proposals":
            return [{"id": "p9"}]
        if path == "/rpc/approve_proposal":
            approved.append(body); return None
        return []
    monkeypatch.setattr(wh, "_rest", fake_rest)
    monkeypatch.setattr(wh, "_resolve_world", lambda w: {"id": "w1"})
    monkeypatch.setattr(wh, "_my_role", lambda wid: "founder")
    monkeypatch.setattr(wh, "_supabase_jwt", lambda: _make_jwt("tk_f"))
    out = wh._submit_world_changes_data("w1", {"entities": [], "relations": [],
                                               "edits": [{"id": "a", "patch": {"name": "A2"}}], "deletes": []}, "edit a")
    assert out["applied"] is True
    assert approved and approved[0]["p_id"] == "p9"


import asyncio

def test_tools_registered():
    names = {t.name for t in wh.TOOLS}
    assert names == {"list_worlds","get_world","search_entities","check_consistency","submit_world_changes"}


def test_call_tool_check_consistency(monkeypatch):
    monkeypatch.setattr(wh, "_get_world_data", lambda w: {
        "overview": {"id": "w1"},
        "entities": [{"ent_id": "a", "name": "A", "aliases": []}], "edges": []})
    res = asyncio.run(
        wh.call_tool("check_consistency", {"world": "w1",
            "draft": {"entities": [{"id": "a", "name": "A2"}], "relations": [], "edits": [], "deletes": []}}))
    payload = json.loads(res[0].text)
    assert any(c["kind"] == "duplicate_id" for c in payload["conflicts"])
