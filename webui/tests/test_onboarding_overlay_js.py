"""Regression checks for the single-container onboarding readiness flow."""

import unittest
from pathlib import Path


STATIC = Path(__file__).parent.parent / "static"


class TestOnboardingOverlayJs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.neowow = (STATIC / "neowow.js").read_text(encoding="utf-8")
        cls.onboarding = (STATIC / "onboarding.js").read_text(encoding="utf-8")
        cls.index = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_only_canonical_onboarding_overlay_exists(self):
        self.assertIn('id="onboardingOverlay"', self.index)
        self.assertNotIn('id="neoLoginOverlay"', self.index)

    def test_neowow_does_not_auto_dismiss_on_login(self):
        self.assertNotIn("_neowowCompleteOnboarding", self.neowow)
        self.assertNotIn("_neowowShowOnboarding", self.neowow)

    def test_provider_activation_waits_for_chat_ready(self):
        self.assertIn("async function _activateOnboardingProvider()", self.onboarding)
        self.assertIn("activated.chat_ready!==true", self.onboarding)
        self.assertIn("status.chat_ready!==true", self.onboarding)

    def test_local_auth_unavailable_copy_and_report_action_exist(self):
        self.assertIn("auth_unavailable_local", self.onboarding)
        self.assertIn("Neowow 账号授权仅支持实际线上部署", self.onboarding)
        self.assertIn("reportOnboardingIssue", self.onboarding)

    def test_activate_provider_fetch_is_in_canonical_onboarding(self):
        self.assertIn("/api/neowow/activate-provider", self.onboarding)

    def test_expired_login_reopens_canonical_wizard(self):
        self.assertIn("window.loadOnboardingWizard({ force: true })", self.neowow)

    def test_local_oauth_error_does_not_show_impossible_fallback(self):
        self.assertIn("e.code === 'auth_unavailable_local'", self.neowow)
        self.assertIn("void window.loadOnboardingWizard({ force: true })", self.neowow)

    def test_required_onboarding_cannot_be_skipped_with_escape(self):
        boot = (STATIC / "boot.js").read_text(encoding="utf-8")
        self.assertIn("const canSkip=skipBtn&&!skipBtn.disabled", boot)
        self.assertIn("if(canSkip&&typeof skipOnboarding==='function')", boot)


if __name__ == "__main__":
    unittest.main()
