"""Authentication/CSRF boundaries for pre-login diagnostic reports."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api import auth, routes


class TestPreLoginReportAuth(unittest.TestCase):
    def test_neodomain_post_reaches_strict_route_gate_before_login(self):
        handler = SimpleNamespace(command="POST")
        parsed = SimpleNamespace(path="/api/report-issue")
        with patch("api.auth.get_auth_mode", return_value="neodomain"):
            self.assertTrue(auth.check_auth(handler, parsed))
        self.assertTrue(handler._hermes_unauthenticated_report)

    def test_anonymous_non_browser_report_is_rejected(self):
        handler = SimpleNamespace(
            headers={},
            _hermes_unauthenticated_report=True,
        )
        self.assertFalse(routes._check_csrf(handler))
        self.assertEqual(
            getattr(handler, routes._CSRF_FAILURE_ATTR),
            "origin_mismatch",
        )

    def test_same_origin_prelogin_report_reaches_token_only_bypass(self):
        handler = SimpleNamespace(
            headers={"Origin": "https://chat.example.test", "Host": "chat.example.test"},
            _hermes_unauthenticated_report=True,
        )
        with (
            patch("api.auth.is_auth_enabled", return_value=True),
            patch("api.auth.get_auth_mode", return_value="neodomain"),
            patch("api.auth.parse_neo_cookie", return_value=None),
            patch("api.auth.parse_cookie", return_value=None),
            patch("api.auth.verify_csrf_token", return_value=False),
        ):
            self.assertFalse(routes._check_csrf(handler))
        self.assertEqual(
            getattr(handler, routes._CSRF_FAILURE_ATTR),
            "token_mismatch",
        )


if __name__ == "__main__":
    unittest.main()
