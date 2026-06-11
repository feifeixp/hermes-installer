"""The static neodomain / neowow-coding-plan catalog must stay aligned with the
dashboard's auto-synced /api/me/plan list, so the desktop picker's FALLBACK
(offline / no JWT / cold cache) doesn't resurrect stale models.

History:
- 2026-05-31: ga added the Claude series (catalog missed them → no-Claude bug).
- 2026-06-11: ga REMOVED the whole gpt-* family (+ gemini-3.5-flash, and
  gemini-3-pro-preview — shut down by Google 2026-03-09, #669) and added
  qwen3.7 / MiniMax-M2.7 etc. A stale fallback kept showing GPT in pickers.
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


def test_catalog_excludes_models_ga_removed():
    # ga dropped these 2026-06-11; a fallback that still lists them produces
    # picker entries that 502 on every call.
    ids = _ids()
    assert not any(i.startswith("gpt-") for i in ids), f"gpt-* must be gone: {sorted(ids)}"
    for gone in ("gemini-3.5-flash", "gemini-3-pro-preview"):
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
