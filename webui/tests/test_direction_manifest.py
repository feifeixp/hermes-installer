"""Tests for the onboarding direction manifest (编剧/导演/AIGC动画师)."""

from api.direction_manifest import get_direction, list_directions


def test_list_has_three_with_fields():
    ds = list_directions()
    assert {d["id"] for d in ds} == {"screenwriter", "director", "animator"}
    for d in ds:
        assert d["name"] and d["emoji"] and d["summary"]


def test_get_direction_case_insensitive_and_miss():
    assert get_direction("Director")["name"] == "导演"
    assert get_direction("nope") is None
    assert get_direction("") is None
    assert get_direction(None) is None


def test_each_direction_has_full_soul_and_defaults():
    for k in ("screenwriter", "director", "animator"):
        d = get_direction(k)
        assert len(d["soul"]) > 200, f"{k} soul too short"
        # Second-person persona, not a bio.
        assert "你是" in d["soul"]
        assert "需要自我警惕" in d["soul"]
        # v1 defaults (skills source A): empty bundle + no model override.
        assert d["skill_ids"] == []
        assert d["model"] == ""
        assert d["workspace"]
