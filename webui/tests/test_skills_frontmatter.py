"""Synced SKILL.md must carry YAML frontmatter so the agent can register it.

The dashboard doesn't enforce frontmatter on publish, and skill names are
inconsistent (some slugs, some Chinese). Without a `name:`/`description:`
frontmatter block the agent's skill loader can't register the skill — it
shows up in 我的技能 (that list comes from dashboard metadata) but is never
invocable in conversation. `_write_skill` self-heals by synthesizing a
minimal frontmatter from the authoritative metadata when the content lacks
its own. Author-provided frontmatter is always preserved.
"""


def test_ensure_frontmatter_adds_block_when_absent():
    from api.skills import _ensure_frontmatter

    out = _ensure_frontmatter("Just a body, no frontmatter.", "skill-abc123",
                              "周报助手", "每周生成周报")
    assert out.startswith("---\n")
    assert "name: skill-abc123\n" in out
    assert "周报助手" in out          # human name carried into description for matching
    assert "每周生成周报" in out
    assert out.rstrip().endswith("Just a body, no frontmatter.")


def test_ensure_frontmatter_preserves_existing():
    from api.skills import _ensure_frontmatter

    original = "---\nname: my-own-name\ndescription: mine\n---\n\n# Body\n"
    out = _ensure_frontmatter(original, "skill-abc123", "周报助手", "每周生成周报")
    assert out == original          # author frontmatter wins, untouched
    assert "skill-abc123" not in out


def test_ensure_frontmatter_falls_back_to_name_when_no_description():
    from api.skills import _ensure_frontmatter

    out = _ensure_frontmatter("body", "skill-xy12", "视频生成", "")
    assert "name: skill-xy12\n" in out
    assert "视频生成" in out          # description falls back to the human name


def test_write_skill_adds_frontmatter_on_disk(tmp_path, monkeypatch):
    """A cloud skill whose content has no frontmatter lands on disk WITH one."""
    monkeypatch.setenv("HERMES_SKILLS_PATH", str(tmp_path))
    from api.skills import _write_skill, _neowow_dir

    _write_skill({
        "id": "skill-nofm01",
        "name": "neowow-视频生成",
        "description": "生成短视频",
        "version": 3,
        "content": "调用视频生成工具，按用户描述产出。",
    })
    md = (_neowow_dir() / "skill-nofm01" / "SKILL.md").read_text("utf-8")
    assert md.lstrip().startswith("---")
    assert "name: skill-nofm01\n" in md
    assert "生成短视频" in md
    assert "调用视频生成工具" in md


def test_write_skill_keeps_author_frontmatter(tmp_path, monkeypatch):
    """Content that already declares frontmatter is written through verbatim."""
    monkeypatch.setenv("HERMES_SKILLS_PATH", str(tmp_path))
    from api.skills import _write_skill, _neowow_dir

    body = "---\nname: keep-me\ndescription: authored\n---\n\n# Real instructions\n"
    _write_skill({
        "id": "skill-hasfm1",
        "name": "ignored-name",
        "description": "ignored-desc",
        "version": 1,
        "content": body,
    })
    md = (_neowow_dir() / "skill-hasfm1" / "SKILL.md").read_text("utf-8")
    assert "name: keep-me\n" in md
    assert "name: skill-hasfm1\n" not in md   # we did NOT inject a second block
