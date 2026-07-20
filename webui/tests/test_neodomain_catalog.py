"""The static neodomain / neowow-coding-plan catalog must stay aligned with the
dashboard's auto-synced /api/me/plan list, so the desktop picker's FALLBACK
(offline / no JWT / cold cache) doesn't resurrect stale models.

History:
- 2026-05-31: ga added the Claude series (catalog missed them → no-Claude bug).
- 2026-06-11: ga REMOVED the whole gpt-* family (+ gemini-3.5-flash, and
  gemini-3-pro-preview — shut down by Google 2026-03-09, #669) and added
  qwen3.7 / MiniMax-M2.7 etc. A stale fallback kept showing GPT in pickers.
- 2026-07-20: live probes confirmed four additions and two removals. Three
  newly advertised GPT entries still return upstream_error and stay hidden.
"""

from api.config import _PROVIDER_MODELS


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


def test_catalog_excludes_models_ga_removed():
    # A fallback that still lists removed or advertised-but-broken entries
    # produces picker options that fail on every call.
    ids = _ids()
    assert not any(i.startswith("gpt-") for i in ids), f"gpt-* must be gone: {sorted(ids)}"
    for gone in ("gemini-2.5-flash", "gemini-3-pro-preview", "global.anthropic.claude-fable-5"):
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
