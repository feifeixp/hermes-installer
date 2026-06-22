"""The Coding-Plan model picker must be constrained to the user's PLAN — never
the full static catalogue. Otherwise a Basic user sees Claude in the dropdown,
selects it, and the server-side gate rejects it (400 model_not_in_plan) /
freezes. The plan whitelist (/api/me/plan) is the single source of truth.

Run: python3.13 -m pytest webui/tests/test_coding_plan_picker_constrained.py -q
"""

from __future__ import annotations

import json
import urllib.error


def _fake_resp(payload: dict):
    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode()

    return _R()


class TestPlanModelsLastGoodCache:
    def test_genuine_then_blip_reuses_last_good(self, monkeypatch):
        import api.onboarding as ob
        import api.neowow as neowow

        monkeypatch.setattr(neowow, "get_jwt", lambda: "eyJ.fake.jwt")
        monkeypatch.setattr(ob, "_LAST_GOOD_PLAN_MODELS", None, raising=False)

        # 1) Genuine fetch caches the real plan catalogue.
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=3: _fake_resp(
                {"models": ["deepseek-v4-flash", "glm-5", "kimi-k2.5"]}),
        )
        models, _ = ob._fetch_neowow_plan_models()
        assert {m["id"] for m in models} == {"deepseek-v4-flash", "glm-5", "kimi-k2.5"}

        # 2) Transient blip → reuse last-good, NOT the bare flash fallback.
        def _boom(req, timeout=3):
            raise urllib.error.URLError("blip")

        monkeypatch.setattr("urllib.request.urlopen", _boom)
        models2, _ = ob._fetch_neowow_plan_models()
        assert {m["id"] for m in models2} == {"deepseek-v4-flash", "glm-5", "kimi-k2.5"}, \
            "a blip must reuse the last-good plan list, not collapse to fallback"

    def test_logged_out_returns_safe_fallback_only(self, monkeypatch):
        import api.onboarding as ob
        import api.neowow as neowow

        monkeypatch.setattr(neowow, "get_jwt", lambda: "")
        monkeypatch.setattr(ob, "_LAST_GOOD_PLAN_MODELS", None, raising=False)
        models, _ = ob._fetch_neowow_plan_models()
        fb_ids = {m["id"] for m in ob._neowow_coding_plan_default_models()}
        assert {m["id"] for m in models} == fb_ids
        # The safe fallback must NOT contain any premium model.
        assert "claude-sonnet-4-6" not in {m["id"] for m in models}


class TestSyncConstrains:
    def test_sync_replaces_full_catalogue_with_plan_list(self):
        from api.config import _sync_coding_plan_group_models

        # The group starts with the FULL static catalogue (Claude leaks in).
        groups = [{
            "provider_id": "neowow-coding-plan",
            "models": [
                {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
                {"id": "claude-sonnet-4-6", "label": "Sonnet 4.6"},
            ],
        }]
        # The user's plan only serves the cheap models.
        _sync_coding_plan_group_models(groups, [{"id": "deepseek-v4-flash"}, {"id": "glm-5"}])
        ids = {m["id"] for m in groups[0]["models"]}
        assert ids == {"deepseek-v4-flash", "glm-5"}
        assert "claude-sonnet-4-6" not in ids, "picker must drop models outside the plan"
