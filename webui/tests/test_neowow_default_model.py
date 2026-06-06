"""The Neowow Coding Plan default model should prefer deepseek-v4-flash.

Regression for: claude-sonnet-4.5 became the configured default just because the
dashboard's /api/me/plan listed it first. _pick_neowow_default must pick
deepseek-v4-flash whenever the plan includes it, and only fall back to the first
listed model otherwise.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.onboarding import _pick_neowow_default  # noqa: E402


def test_prefers_deepseek_even_when_not_first():
    ids = ["claude-sonnet-4.5", "deepseek-v4-flash", "gpt-4o-mini"]
    assert _pick_neowow_default(ids) == "deepseek-v4-flash"


def test_matches_provider_prefixed_deepseek():
    ids = ["claude-sonnet-4.5", "deepseek/deepseek-v4-flash"]
    assert _pick_neowow_default(ids) == "deepseek/deepseek-v4-flash"


def test_falls_back_to_first_when_no_deepseek():
    ids = ["claude-sonnet-4.5", "gpt-4o-mini"]
    assert _pick_neowow_default(ids) == "claude-sonnet-4.5"


def test_empty_list_returns_none():
    assert _pick_neowow_default([]) is None
