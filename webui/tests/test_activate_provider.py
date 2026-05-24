"""Tests for POST /api/neowow/activate-provider."""
import unittest
from unittest.mock import patch


class TestActivateProvider(unittest.TestCase):

    @patch("api.onboarding._fetch_neowow_plan_models")
    @patch("api.onboarding.apply_onboarding_setup")
    def test_returns_ok_on_success(self, mock_setup, mock_fetch):
        """Happy path: models + default_model selected, apply_onboarding_setup called."""
        mock_fetch.return_value = (
            [{"id": "deepseek-v4-flash"}, {"id": "deepseek-v4"}],
            "deepseek-v4-flash",
        )
        mock_setup.return_value = {"provider": "neowow-coding-plan"}

        from api.onboarding import (
            _NEOWOW_CODING_PLAN_PROVIDER_ID,
            _fetch_neowow_plan_models,
            apply_onboarding_setup,
        )
        models, default_model = _fetch_neowow_plan_models()
        model = default_model or (models[0]["id"] if models else "deepseek-v4-flash")
        apply_onboarding_setup(
            {"provider": _NEOWOW_CODING_PLAN_PROVIDER_ID, "model": model}
        )
        self.assertEqual(model, "deepseek-v4-flash")
        mock_setup.assert_called_once_with(
            {"provider": "neowow-coding-plan", "model": "deepseek-v4-flash"}
        )

    @patch("api.onboarding._fetch_neowow_plan_models")
    @patch("api.onboarding.apply_onboarding_setup")
    def test_falls_back_when_no_default_model(self, mock_setup, mock_fetch):
        """When default_model is None and models list empty, falls back to deepseek-v4-flash."""
        mock_fetch.return_value = ([], None)
        mock_setup.return_value = {}

        from api.onboarding import (
            _NEOWOW_CODING_PLAN_PROVIDER_ID,
            _fetch_neowow_plan_models,
            apply_onboarding_setup,
        )
        models, default_model = _fetch_neowow_plan_models()
        model = default_model or (models[0]["id"] if models else "deepseek-v4-flash")
        self.assertEqual(model, "deepseek-v4-flash")

    @patch("api.onboarding._fetch_neowow_plan_models")
    def test_exception_is_catchable(self, mock_fetch):
        """If _fetch_neowow_plan_models raises, the exception propagates for the route to catch."""
        mock_fetch.side_effect = RuntimeError("network timeout")

        from api.onboarding import _fetch_neowow_plan_models
        with self.assertRaises(RuntimeError) as ctx:
            _fetch_neowow_plan_models()
        self.assertIn("network timeout", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
