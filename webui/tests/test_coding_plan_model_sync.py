"""neowow-coding-plan model picker syncs with the dashboard's live /api/me/plan.

Bug: the desktop picker's Coding Plan model list was the static neodomain
alias (no Claude), so models added on the dashboard never appeared. The build
now overlays the live /api/me/plan catalogue onto the neowow-coding-plan group.
This tests the pure overlay helper + that the live fetch is wired in.
"""
from pathlib import Path

from api.config import _sync_coding_plan_group_models


def _groups():
    return [
        {"provider_id": "openai", "models": [{"id": "gpt-4o", "label": "GPT-4o"}]},
        {"provider_id": "neowow-coding-plan", "models": [
            {"id": "gpt-4o", "label": "GPT-4o"},
            {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
        ]},
    ]


def test_overlays_live_models_onto_coding_plan_group():
    groups = _groups()
    live = [
        {"id": "gpt-4o", "label": "gpt-4o"},                 # known → keep pretty label
        {"id": "claude-sonnet-4-6", "label": "claude-sonnet-4-6"},
        {"id": "claude-opus-4-8", "label": "claude-opus-4-8"},
    ]
    _sync_coding_plan_group_models(groups, live)
    nc = next(g for g in groups if g["provider_id"] == "neowow-coding-plan")
    ids = [m["id"] for m in nc["models"]]
    assert ids == ["gpt-4o", "claude-sonnet-4-6", "claude-opus-4-8"]
    labels = {m["id"]: m["label"] for m in nc["models"]}
    assert labels["gpt-4o"] == "GPT-4o"                      # pretty label preserved
    assert labels["claude-opus-4-8"] == "claude-opus-4-8"    # new id → raw id label
    # Other groups untouched.
    assert [m["id"] for m in groups[0]["models"]] == ["gpt-4o"]


def test_noop_when_no_live_models():
    groups = _groups()
    before = [m["id"] for m in groups[1]["models"]]
    _sync_coding_plan_group_models(groups, [])
    assert [m["id"] for m in groups[1]["models"]] == before


def test_noop_when_group_absent():
    groups = [{"provider_id": "openai", "models": [{"id": "gpt-4o", "label": "GPT-4o"}]}]
    _sync_coding_plan_group_models(groups, [{"id": "claude-opus-4-8", "label": "x"}])
    assert len(groups) == 1 and groups[0]["provider_id"] == "openai"


def test_live_sync_wired_into_builder():
    src = (Path(__file__).resolve().parent.parent / "api" / "config.py").read_text("utf-8")
    assert "_sync_coding_plan_group_models(groups" in src
    assert "_fetch_neowow_plan_models" in src
