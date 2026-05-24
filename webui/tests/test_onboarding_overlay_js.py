"""Smoke-test that the neowow login onboarding JS functions exist in neowow.js."""
import re
import unittest
from pathlib import Path


NEOWOW_JS = Path(__file__).parent.parent / "static" / "neowow.js"


class TestOnboardingOverlayJs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.src = NEOWOW_JS.read_text(encoding="utf-8")

    def test_show_onboarding_function_defined(self):
        self.assertIn("function _neowowShowOnboarding(", self.src)

    def test_complete_onboarding_function_defined(self):
        self.assertIn("function _neowowCompleteOnboarding(", self.src)

    def test_show_onboarding_called_in_boot_resolve(self):
        """_neowowShowOnboarding() must be called inside neowowResolveBootOverlay."""
        match = re.search(
            r"async function neowowResolveBootOverlay\(\)(.*?)^\s{2}\}",
            self.src,
            re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(match, "neowowResolveBootOverlay not found")
        self.assertIn("_neowowShowOnboarding()", match.group(1))

    def test_complete_onboarding_called_in_session_updated(self):
        """_neowowCompleteOnboarding must be referenced in the neoSessionUpdated listener."""
        idx = self.src.find("neoSessionUpdated")
        self.assertGreater(idx, 0)
        snippet = self.src[idx: idx + 400]
        self.assertIn("_neowowCompleteOnboarding", snippet)

    def test_activate_provider_fetch_in_complete_onboarding(self):
        self.assertIn("/api/neowow/activate-provider", self.src)

    def test_neo_login_overlay_id_referenced(self):
        self.assertIn("neoLoginOverlay", self.src)


if __name__ == "__main__":
    unittest.main()
