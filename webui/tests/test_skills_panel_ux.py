"""Skills panel 订阅体验改进 — pure, testable units.

The panel changes themselves (render functions, routes) involve network /
agent imports, so the logic lives in these injectable/pure helpers which we
TDD here. Frontend wiring is covered by source-grep assertions below.
"""
from pathlib import Path


# ── _local_skill_version (skills.py) ──────────────────────────────────────────

def test_local_skill_version_reads_neowow_json(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SKILLS_PATH", str(tmp_path))
    from api.skills import _local_skill_version, _neowow_dir
    d = _neowow_dir() / "skill-abc123"
    d.mkdir(parents=True)
    (d / "_neowow.json").write_text('{"id":"skill-abc123","version":7}', encoding="utf-8")
    assert _local_skill_version("skill-abc123") == 7


def test_local_skill_version_missing_is_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SKILLS_PATH", str(tmp_path))
    from api.skills import _local_skill_version
    assert _local_skill_version("skill-none00") == 0


def test_local_skill_version_corrupt_is_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SKILLS_PATH", str(tmp_path))
    from api.skills import _local_skill_version, _neowow_dir
    d = _neowow_dir() / "skill-bad0001"
    d.mkdir(parents=True)
    (d / "_neowow.json").write_text("{not json", encoding="utf-8")
    assert _local_skill_version("skill-bad0001") == 0


# ── sync_one_skill (skills.py) ────────────────────────────────────────────────

def test_sync_one_skill_writes_when_found():
    from api.skills import sync_one_skill
    written = []
    cloud = [{"id": "skill-aaa111", "name": "A", "version": 3},
             {"id": "skill-bbb222", "name": "B", "version": 5}]
    res = sync_one_skill(
        "skill-bbb222",
        fetch=lambda: cloud,
        write=lambda sk: written.append(sk),
        refresh=lambda: None,
    )
    assert res["ok"] is True
    assert res["id"] == "skill-bbb222"
    assert res["version"] == 5
    assert len(written) == 1 and written[0]["id"] == "skill-bbb222"


def test_sync_one_skill_error_when_not_found():
    from api.skills import sync_one_skill
    written = []
    res = sync_one_skill(
        "skill-zzz999",
        fetch=lambda: [{"id": "skill-aaa111"}],
        write=lambda sk: written.append(sk),
        refresh=lambda: None,
    )
    assert res["ok"] is False
    assert written == []


def test_sync_one_skill_rejects_bad_id():
    from api.skills import sync_one_skill
    called = []
    res = sync_one_skill(
        "../etc/passwd",
        fetch=lambda: called.append(1) or [],
        write=lambda sk: None,
        refresh=lambda: None,
    )
    assert res["ok"] is False
    assert called == []   # never even hits the cloud for an invalid id


# ── _subscribed_meta (routes.py) ──────────────────────────────────────────────

def test_subscribed_meta_reads_title_and_author(tmp_path):
    from api.routes import _subscribed_meta
    (tmp_path / "_neowow.json").write_text(
        '{"name":"星垂导演助手","displayName":"星垂"}', encoding="utf-8")
    meta = _subscribed_meta(tmp_path)
    assert meta.get("title") == "星垂导演助手"
    assert meta.get("author") == "星垂"


def test_subscribed_meta_missing_is_empty(tmp_path):
    from api.routes import _subscribed_meta
    assert _subscribed_meta(tmp_path) == {}


# ── Frontend wiring (source-grep, repo convention) ───────────────────────────

_WEBUI = Path(__file__).resolve().parent.parent


def test_mine_tab_renamed_to_subscription():
    html = (_WEBUI / "static" / "index.html").read_text("utf-8")
    assert "我的订阅" in html


def test_panels_has_sync_one_and_grouping():
    js = (_WEBUI / "static" / "panels.js").read_text("utf-8")
    assert "skillsSyncOne(" in js          # per-row sync handler
    assert "我的订阅技能" in js              # 技能列表 subscribed group title


def test_sync_one_route_registered():
    routes = (_WEBUI / "api" / "routes.py").read_text("utf-8")
    assert "/api/skills/sync-one" in routes
