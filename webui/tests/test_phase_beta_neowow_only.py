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
        # Phase β.14+.16: env_var is OPENAI_API_KEY (not NEOWOW_TOKEN)
        # because the auto-onboard writes config.yaml's provider='custom'
        # (the agent CLI doesn't know "neowow-coding-plan" — it's a UI
        # label — and 'openai' exists in PROVIDER_REGISTRY but maps to {}
        # so it errors with "Unknown provider 'openai'". 'custom' is the
        # canonical OpenAI-compat fall-through that reads OPENAI_API_KEY).
        assert p["env_var"] == "OPENAI_API_KEY"
        assert p["quick"] is True
        # Static fallback list when /api/me/plan is unreachable.
        # Phase ε swapped the bake-in defaults to the actual chat models
        # on ga.neodomain.cn (deepseek-v4-flash + gpt-4o-mini).
        assert any(m["id"] == "deepseek-v4-flash" for m in p["models"])

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


# ─── Auto-onboard when flag-on + JWT present (Phase β.10) ───────────────────

class TestNeowowAutoOnboard:
    """The flag-on + JWT-present combination should skip the wizard
    entirely — get_onboarding_status returns completed=True so the SPA
    boots straight into chat. No human in the loop; both fields the
    wizard would ask for (provider + key) are uniquely determined by
    the build flag and the saved JWT."""

    def _stub_writes(self, monkeypatch, tmp_path):
        """Common setup — point all file writes at tmp_path and stub
        the dashboard fetch (otherwise we'd hang on real network).
        Also force load_settings/save_settings to a fresh in-memory dict
        so test ordering doesn't matter: STATE_DIR is resolved at
        api.config import time and can't be re-pointed via monkeypatch
        of HERMES_WEBUI_STATE_DIR, so without this stub a previous
        test's save_settings would leak into the next test."""
        from api import onboarding as ob_mod
        monkeypatch.setattr(ob_mod, "_get_config_path",        lambda: tmp_path / "config.yaml")
        monkeypatch.setattr(ob_mod, "_get_active_hermes_home", lambda: tmp_path)
        monkeypatch.setattr(ob_mod, "_load_yaml_config",       lambda p: {})
        monkeypatch.setattr(ob_mod, "_load_env_file",          lambda p: {})
        monkeypatch.setattr(ob_mod, "_save_yaml_config",       lambda p, c: None)
        monkeypatch.setattr(ob_mod, "_write_env_file",         lambda p, d: None)
        monkeypatch.setattr(ob_mod, "reload_config",           lambda: None)
        monkeypatch.setattr(
            ob_mod,
            "_fetch_neowow_plan_models",
            lambda: ([{"id": "deepseek-chat", "label": "DeepSeek Chat"}], "deepseek-chat"),
        )
        # Fresh in-memory settings store. save_settings writes back into
        # this dict so the test can still observe the side effect; the
        # next test gets a brand-new dict via tmp_path-scoped closure.
        _settings: dict = {}
        monkeypatch.setattr(ob_mod, "load_settings", lambda: dict(_settings))
        monkeypatch.setattr(
            ob_mod,
            "save_settings",
            lambda patch: _settings.update(patch) or dict(_settings),
        )
        return ob_mod

    def test_flag_on_with_jwt_auto_completes(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_NEOWOW_ONLY", "1")
        monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(tmp_path))
        ob_mod = self._stub_writes(monkeypatch, tmp_path)
        import api.neowow as nw
        monkeypatch.setattr(nw, "get_jwt", lambda: "eyJfake.payload.sig")
        status = ob_mod.get_onboarding_status()
        assert status["completed"] is True

    def test_auto_onboard_writes_openai_runtime_provider(self, monkeypatch, tmp_path):
        """Phase β.14: auto-onboard must write provider='custom' (not the
        UI label 'neowow-coding-plan') so the agent CLI's openai-compatible
        path handles dispatch. Likewise env var is OPENAI_API_KEY, the
        name agent CLI auto-derives from the provider field."""
        monkeypatch.setenv("HERMES_NEOWOW_ONLY", "1")
        monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(tmp_path))
        ob_mod = self._stub_writes(monkeypatch, tmp_path)
        import api.neowow as nw

        # Capture every write
        written_cfg: dict = {}
        written_env: dict = {}
        monkeypatch.setattr(ob_mod, "_save_yaml_config", lambda p, c: written_cfg.update(c))
        monkeypatch.setattr(ob_mod, "_write_env_file",   lambda p, d: written_env.update(d))
        monkeypatch.setattr(nw, "get_jwt", lambda: "eyJfake.payload.sig")

        status = ob_mod.get_onboarding_status()
        assert status["completed"] is True
        # config.yaml has the AGENT-RECOGNIZED provider name + our proxy URL
        assert written_cfg.get("model", {}).get("provider") == "custom"
        assert written_cfg.get("model", {}).get("base_url") == "https://app.neowow.studio/api/me"
        # .env has the JWT under the name the agent CLI looks up
        assert written_env.get("OPENAI_API_KEY") == "eyJfake.payload.sig"
        # And NOT under the old broken name
        assert "NEOWOW_TOKEN" not in written_env

    def test_auto_fix_bogus_neowow_provider_in_existing_config(self, monkeypatch, tmp_path):
        """When config.yaml has the pre-fix Phase-β.10 literal
        provider='neowow-coding-plan' (which the agent CLI can't dispatch),
        get_onboarding_status must trigger re-onboard to rewrite it as
        provider='custom'. Loop-breaker: once rewritten to canonical, the
        next status check sees the canonical shape + doesn't re-trigger."""
        monkeypatch.setenv("HERMES_NEOWOW_ONLY", "1")
        monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(tmp_path))
        ob_mod = self._stub_writes(monkeypatch, tmp_path)
        import api.neowow as nw

        # Simulate existing config.yaml with the bogus value. Note —
        # get_onboarding_status reads via get_config() (cached), NOT
        # _load_yaml_config — so we patch the cached-getter directly.
        # _load_yaml_config is used by the auto-onboard branch's OWN
        # read so we also patch it (returns the same bogus shape).
        _bogus_cfg = {
            "model": {
                "provider": "neowow-coding-plan",        # bogus
                "base_url": "https://app.neowow.studio/api/me",
                "default": "deepseek-v4-flash",
            }
        }
        monkeypatch.setattr(ob_mod, "get_config",        lambda: _bogus_cfg)
        monkeypatch.setattr(ob_mod, "_load_yaml_config", lambda p: _bogus_cfg)
        # Pretend onboarding was already marked complete by pre-fix code
        _settings: dict = {"onboarding_completed": True}
        monkeypatch.setattr(ob_mod, "load_settings", lambda: dict(_settings))
        monkeypatch.setattr(
            ob_mod, "save_settings",
            lambda patch: _settings.update(patch) or dict(_settings),
        )

        written_cfg: dict = {}
        monkeypatch.setattr(ob_mod, "_save_yaml_config", lambda p, c: written_cfg.update(c))
        monkeypatch.setattr(ob_mod, "_write_env_file",   lambda p, d: None)
        monkeypatch.setattr(nw, "get_jwt", lambda: "eyJfake.payload.sig")

        ob_mod.get_onboarding_status()
        # Was auto-rewritten — provider field now uses the agent name.
        assert written_cfg.get("model", {}).get("provider") == "custom"

    def test_flag_on_without_jwt_falls_through_to_wizard(self, monkeypatch, tmp_path):
        # Without a JWT the wizard is the only path to acquire one —
        # don't silently auto-complete with a bogus key.
        monkeypatch.setenv("HERMES_NEOWOW_ONLY", "1")
        monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(tmp_path))
        ob_mod = self._stub_writes(monkeypatch, tmp_path)
        import api.neowow as nw
        monkeypatch.setattr(nw, "get_jwt", lambda: "")
        status = ob_mod.get_onboarding_status()
        assert status["completed"] is False
        # Wizard still renders, single card.
        assert [p["id"] for p in status["setup"]["providers"]] == ["neowow-coding-plan"]

    def test_flag_off_with_jwt_does_not_auto_complete(self, monkeypatch, tmp_path):
        # Auto-onboard only triggers when the build is locked. Without
        # the flag, a stored JWT shouldn't force-pick neowow-coding-plan
        # over whatever the user might want to configure.
        monkeypatch.delenv("HERMES_NEOWOW_ONLY", raising=False)
        monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(tmp_path))
        ob_mod = self._stub_writes(monkeypatch, tmp_path)
        import api.neowow as nw
        monkeypatch.setattr(nw, "get_jwt", lambda: "eyJfake.payload.sig")
        status = ob_mod.get_onboarding_status()
        # Falls through to the normal "config_auto_completed" gate,
        # which itself requires config.yaml to exist — it doesn't, so
        # completed should be False here too.
        assert status["completed"] is False
