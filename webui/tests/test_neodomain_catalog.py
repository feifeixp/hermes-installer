"""The static neodomain / neowow-coding-plan catalog must stay aligned with the
dashboard's auto-synced /api/me/plan list, so the desktop picker's FALLBACK
(offline / no JWT / cold cache) doesn't resurrect stale models.

History:
- 2026-05-31: ga added the Claude series (catalog missed them → no-Claude bug).
- 2026-06-11: ga REMOVED the whole gpt-* family (+ gemini-3.5-flash, and
  gemini-3-pro-preview — shut down by Google 2026-03-09, #669) and added
  qwen3.7 / MiniMax-M2.7 etc. A stale fallback kept showing GPT in pickers.
- 2026-07-20: live probes confirmed four additions and two removals.
- 2026-07-26: product approval added three advertised GPT entries and removed
  the retired Gemini 3.1 Flash Lite Preview entry.
- 2026-07-28: product approval added Claude Opus 5 and Claude Sonnet 5.
"""

from api.config import _MODELS_CACHE_SCHEMA_VERSION, _PROVIDER_MODELS


def _ids():
    return {m["id"] for m in _PROVIDER_MODELS["neodomain"]}


def test_catalog_includes_claude_series():
    ids = _ids()
    for cid in (
        "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
        "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
    ):
        assert cid in ids, f"static coding-plan catalog missing {cid}"


def test_catalog_includes_2026_06_additions():
    ids = _ids()
    for cid in ("qwen3.7-max", "qwen3.7-plus", "MiniMax-M2.7",
                "gemini-3.1-flash-lite", "doubao-seed-2-0-pro-260215"):
        assert cid in ids, f"static coding-plan catalog missing {cid}"


def test_catalog_includes_kimi_k3():
    assert "kimi-k3" in _ids(), "static coding-plan catalog missing kimi-k3"


def test_catalog_includes_2026_07_20_verified_additions():
    ids = _ids()
    for model_id in (
        "doubao-seed-character-260628",
        "gemini-3.1-pro-preview-customtools",
        "gemini-3.5-flash",
        "glm-5.2",
    ):
        assert model_id in ids, f"verified live model missing: {model_id}"


def test_catalog_applies_2026_07_26_model_decision_and_bumps_cache_schema():
    """The approved catalog change must force existing picker caches to rebuild."""
    ids = _ids()
    for model_id in (
        "gemini-2.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
    ):
        assert model_id in ids, f"confirmed live model missing: {model_id}"
    for model_id in ("gpt-5.5", "gpt-5.6-sol", "gpt-5.6-terra"):
        assert model_id in ids, f"approved model missing: {model_id}"
    assert "gemini-3.1-flash-lite-preview" not in ids
    assert _MODELS_CACHE_SCHEMA_VERSION == 11


def test_catalog_applies_2026_07_28_claude_5_decision():
    """The approved Claude 5 additions must be available after cache rebuild."""
    ids = _ids()
    assert {"claude-opus-5", "claude-sonnet-5"} <= ids


def test_catalog_excludes_models_ga_removed():
    # A fallback must not resurrect models that the gateway has removed.
    ids = _ids()
    assert {i for i in ids if i.startswith("gpt-")} == {
        "gpt-5.5", "gpt-5.6-sol", "gpt-5.6-terra"
    }
    for gone in ("gemini-3-pro-preview", "global.anthropic.claude-fable-5"):
        assert gone not in ids, f"{gone} was removed upstream"


def test_catalog_excludes_media_models():
    # Image/video/audio generation lives on story.neodomain.cn — never here.
    ids = _ids()
    for frag in ("seedance", "seedream", "image", "video", "music", "speech", "embed"):
        assert not any(frag in i.lower() for i in ids), f"media model leaked: {frag}"


def test_coding_plan_aliases_neodomain():
    # neowow-coding-plan must reference the SAME list object (single update site).
    assert _PROVIDER_MODELS["neowow-coding-plan"] is _PROVIDER_MODELS["neodomain"]


def test_every_catalog_entry_has_id_and_label():
    for m in _PROVIDER_MODELS["neodomain"]:
        assert m.get("id") and m.get("label"), f"bad entry: {m!r}"
