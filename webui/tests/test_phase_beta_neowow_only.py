"""Phase β — HERMES_NEOWOW_ONLY gate + neowow-coding-plan provider.

Verifies the two enforcement points:

  1. _build_setup_catalog drops every provider except neowow-coding-plan
     when the env flag is on, and pre-selects it as the 'current' even if
     config.yaml still mentions an old provider (e.g. openrouter from a
     pre-NEOWOW_ONLY run).

  2. apply_onboarding_setup raises ValueError when the body asks for a
     non-neowow provider — defense-in-depth in case the frontend filter
     is bypassed.

We do NOT exercise the live /api/me/plan fetch path here — that needs a
real dashboard. The catalog falls back to _neowow_coding_plan_default_models
when the JWT or network are missing, and that fallback is what these
tests assert against.
"""

from __future__ import annotations

import pytest


# ─── Catalog filtering ──────────────────────────────────────────────────────

class TestNeowowOnlyCatalog:
    def test_catalog_default_includes_neowow_and_others(self, monkeypatch):
        """Without the flag, neowow-coding-plan coexists with every other
        provider — community / self-hosted users keep their picks."""
        monkeypatch.delenv("HERMES_NEOWOW_ONLY", raising=False)
        from api.onboarding import _build_setup_catalog
        cat = _build_setup_catalog({})
        ids = {p["id"] for p in cat["providers"]}
        assert "neowow-coding-plan" in ids
        assert "openrouter" in ids
        assert "anthropic" in ids
        # Sanity: more than just the new card present.
        assert len(ids) >= 5

    def test_catalog_only_returns_neowow_when_flag_set(self, monkeypatch):
        monkeypatch.setenv("HERMES_NEOWOW_ONLY", "1")
        from api.onboarding import _build_setup_catalog
        cat = _build_setup_catalog({})
        ids = [p["id"] for p in cat["providers"]]
        assert ids == ["neowow-coding-plan"], ids

    def test_catalog_truthy_variants_enable_flag(self, monkeypatch):
        """The flag accepts the same truthy spellings other Hermes env
        gates do (1 / true / yes, case-insensitive)."""
        from api.onboarding import _build_setup_catalog
        for val in ("1", "true", "True", "YES"):
            monkeypatch.setenv("HERMES_NEOWOW_ONLY", val)
            cat = _build_setup_catalog({})
            ids = [p["id"] for p in cat["providers"]]
            assert ids == ["neowow-coding-plan"], (val, ids)

    def test_catalog_neowow_card_shape(self, monkeypatch):
        """The lone neowow-coding-plan entry carries everything the
        wizard needs to short-circuit the api-key + base-url inputs."""
        monkeypatch.setenv("HERMES_NEOWOW_ONLY", "1")
        from api.onboarding import _build_setup_catalog
        cat = _build_setup_catalog({})
        p = cat["providers"][0]
        assert p["id"] == "neowow-coding-plan"
        assert p["default_base_url"] == "https://app.neowow.studio/api/me"
        assert p["key_optional"] is True            # JWT comes from local store
        assert p["env_var"] == "NEOWOW_TOKEN"
        assert p["quick"] is True
        # Static fallback list when /api/me/plan is unreachable.
        assert any(m["id"] == "deepseek-chat" for m in p["models"])

    def test_catalog_current_provider_pinned_to_neowow_when_flag_set(self, monkeypatch):
        """Even if a previous wizard run wrote model.provider=openrouter
        into config.yaml, the catalog surfaces neowow-coding-plan as
        the current provider so the wizard re-renders cleanly."""
        monkeypatch.setenv("HERMES_NEOWOW_ONLY", "1")
        from api.onboarding import _build_setup_catalog
        cat = _build_setup_catalog({
            "model": {"provider": "openrouter", "default": "anthropic/claude-sonnet-4.6"}
        })
        assert cat["current"]["provider"] == "neowow-coding-plan"


# ─── apply_onboarding_setup gate ────────────────────────────────────────────

class TestNeowowOnlyApplyGate:
    def test_reject_non_neowow_provider(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_NEOWOW_ONLY", "1")
        monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(tmp_path))
        from api.onboarding import apply_onboarding_setup
        with pytest.raises(ValueError, match="Neowow Coding Plan"):
            apply_onboarding_setup({"provider": "openrouter", "model": "x"})

    def test_accept_neowow_provider_with_jwt_from_store(
        self, monkeypatch, tmp_path
    ):
        """When the user has run OAuth (JWT saved via api.neowow.save_jwt),
        apply_onboarding_setup pulls it automatically — body.api_key may
        be empty."""
        monkeypatch.setenv("HERMES_NEOWOW_ONLY", "1")
        monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(tmp_path))
        # Stub api.neowow.get_jwt → return a fake JWT.
        import api.neowow as neowow_mod
        monkeypatch.setattr(neowow_mod, "get_jwt", lambda: "eyJfake.payload.sig")
        # And stub the YAML writes so we don't touch real config.yaml.
        cfg_path = tmp_path / "config.yaml"
        from api import onboarding as ob_mod
        # _get_config_path returns a Path in production (callers do
        # `Path(_get_config_path()).exists()` AND pass to `_load_yaml_config`
        # which expects a Path) — stay consistent here.
        monkeypatch.setattr(ob_mod, "_get_config_path", lambda: cfg_path)
        monkeypatch.setattr(ob_mod, "_get_active_hermes_home", lambda: tmp_path)
        monkeypatch.setattr(ob_mod, "_load_yaml_config", lambda p: {})
        monkeypatch.setattr(ob_mod, "_load_env_file", lambda p: {})
        monkeypatch.setattr(ob_mod, "_save_yaml_config", lambda p, c: None)
        monkeypatch.setattr(ob_mod, "_write_env_file", lambda p, d: None)
        monkeypatch.setattr(ob_mod, "reload_config", lambda: None)
        # The status return-trip touches more helpers — stub the whole
        # status getter so the test focuses on the gate, not state read.
        monkeypatch.setattr(ob_mod, "get_onboarding_status", lambda: {"ok": True})

        out = apply_onboarding_setup_safely(
            ob_mod,
            {
                "provider": "neowow-coding-plan",
                "model":    "claude-sonnet-4.6",
                # api_key omitted intentionally — JWT should auto-fill.
            },
        )
        assert out == {"ok": True}


def apply_onboarding_setup_safely(ob_mod, body):
    """Call apply_onboarding_setup and pass-through its return value.
    Wraps the call so the test reads naturally (avoid inline import +
    monkeypatch shenanigans in the assertion line)."""
    return ob_mod.apply_onboarding_setup(body)
