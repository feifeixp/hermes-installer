"""Regression checks for direct entry and explicit Coding Plan login."""

import unittest
from pathlib import Path


STATIC = Path(__file__).parent.parent / "static"


class TestOnboardingOverlayJs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.neowow = (STATIC / "neowow.js").read_text(encoding="utf-8")
        cls.onboarding = (STATIC / "onboarding.js").read_text(encoding="utf-8")
        cls.boot = (STATIC / "boot.js").read_text(encoding="utf-8")
        cls.index = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_no_blocking_onboarding_overlay_exists(self):
        self.assertNotIn('id="onboardingOverlay"', self.index)
        self.assertNotIn('id="neoLoginOverlay"', self.index)

    def test_boot_does_not_load_onboarding_before_rendering_sessions(self):
        self.assertNotIn("loadOnboardingWizard()", self.boot)
        self.assertIn("await renderSessionList();", self.boot)

    def test_explicit_login_prepares_coding_plan_without_blocking_workspace(self):
        self.assertIn("async function activateCodingPlanAfterLogin(neowowOnly)", self.neowow)
        self.assertIn("if (!neowowOnly) return true;", self.neowow)
        self.assertIn("/api/neowow/activate-provider", self.neowow)
        self.assertIn("data.chat_ready !== true", self.neowow)

    def test_neowow_login_has_no_online_deployment_gate(self):
        self.assertNotIn("auth_unavailable_local", self.onboarding)
        self.assertNotIn("查看线上部署", self.onboarding)

    def test_expired_login_does_not_reopen_a_wizard(self):
        self.assertNotIn("window.loadOnboardingWizard({ force: true })", self.neowow)


if __name__ == "__main__":
    unittest.main()
