"""The static neodomain / neowow-coding-plan catalog must stay current with the
dashboard's /api/me/plan, so the desktop picker shows the latest models (incl.
Claude) even when the live overlay can't run (offline / no JWT / stale cache).

Regression: the catalog was a 2026-05-13 snapshot missing the Claude series
(added to the plan 2026-05-31) → desktop users saw an old list with no Claude.
"""

from api.config import _PROVIDER_MODELS


def test_neodomain_catalog_includes_claude_series():
    ids = {m["id"] for m in _PROVIDER_MODELS["neodomain"]}
    for cid in (
        "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
        "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
    ):
        assert cid in ids, f"static coding-plan catalog missing {cid}"


def test_neodomain_catalog_includes_newer_openai_gemini():
    ids = {m["id"] for m in _PROVIDER_MODELS["neodomain"]}
    for cid in ("gpt-5.4-pro", "gpt-5.5-pro", "gpt-5.4-nano",
                "gemini-3-pro-preview", "gemini-3.5-flash"):
        assert cid in ids, f"static coding-plan catalog missing {cid}"


def test_coding_plan_aliases_neodomain():
    # neowow-coding-plan must reference the SAME list object (single update site).
    assert _PROVIDER_MODELS["neowow-coding-plan"] is _PROVIDER_MODELS["neodomain"]


def test_every_catalog_entry_has_id_and_label():
    for m in _PROVIDER_MODELS["neodomain"]:
        assert m.get("id") and m.get("label"), f"bad entry: {m!r}"
