"""Pure regression tests for deployment-aware onboarding readiness."""

import os
import unittest
from unittest.mock import patch

from api import onboarding as onboarding_module
from api.onboarding import _experience_state, get_onboarding_capabilities


class TestOnboardingCapabilities(unittest.TestCase):
    def test_managed_neodomain_deployment_supports_oauth(self):
        with patch.dict(os.environ, {"HERMES_WEBUI_AUTH_MODE": "neodomain"}, clear=True):
            caps = get_onboarding_capabilities()
        self.assertEqual(caps["deployment_mode"], "online")
        self.assertTrue(caps["neowow_oauth_supported"])

    def test_local_server_does_not_advertise_oauth(self):
        with patch.dict(os.environ, {}, clear=True):
            caps = get_onboarding_capabilities()
        self.assertEqual(caps["deployment_mode"], "local_server")
        self.assertFalse(caps["neowow_oauth_supported"])

    def test_online_label_alone_cannot_enable_oauth_locally(self):
        with patch.dict(os.environ, {"HERMES_DEPLOYMENT_MODE": "online"}, clear=True):
            caps = get_onboarding_capabilities()
        self.assertEqual(caps["deployment_mode"], "local_server")
        self.assertFalse(caps["neowow_oauth_supported"])

    def test_local_managed_build_reports_auth_unavailable(self):
        env = {"HERMES_NEOWOW_ONLY": "1", "HERMES_DEPLOYMENT_MODE": "local_desktop"}
        with patch.dict(os.environ, env, clear=True):
            state = _experience_state(
                {"chat_ready": False, "provider_configured": False},
                {"hasJwt": False},
                False,
            )
        self.assertEqual(state["stage"], "auth_unavailable_local")
        self.assertIn("deployment_help", state["available_actions"])

    def test_ready_requires_runtime_chat_ready(self):
        env = {"HERMES_NEOWOW_ONLY": "1", "HERMES_DEPLOYMENT_MODE": "online"}
        with patch.dict(os.environ, env, clear=True):
            syncing = _experience_state(
                {"chat_ready": False, "provider_configured": True},
                {"hasJwt": True},
                True,
            )
            ready = _experience_state(
                {"chat_ready": True, "provider_configured": True},
                {"hasJwt": True},
                True,
            )
        self.assertEqual(syncing["stage"], "provider_syncing")
        self.assertFalse(syncing["chat_ready"])
        self.assertEqual(ready["stage"], "ready")
        self.assertTrue(ready["chat_ready"])

    def test_cached_only_catalog_never_waits_for_network(self):
        cached = [{"id": "kimi-k3", "label": "Kimi K3"}]
        with (
            patch("api.neowow.get_jwt", return_value="header.payload.signature"),
            patch.object(onboarding_module, "_LAST_GOOD_PLAN_MODELS", None),
            patch.object(onboarding_module, "_load_last_good_plan_models_from_disk", return_value=cached),
            patch("urllib.request.urlopen", side_effect=AssertionError("network must not run")),
        ):
            models, default = onboarding_module._fetch_neowow_plan_models(cached_only=True)
        self.assertEqual(models, cached)
        self.assertEqual(default, "kimi-k3")


if __name__ == "__main__":
    unittest.main()
