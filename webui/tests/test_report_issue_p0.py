"""P0 privacy and consent regressions for user-initiated reports."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api import report_bundle as rb
from api import routes


class TestReportIssueP0(unittest.TestCase):
    def test_extended_sensitive_values_are_redacted(self):
        raw = (
            "email user@example.com ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456 "
            "AKIAABCDEFGHIJKLMNOP url?access_token=secret-value "
            "Cookie: session=top-secret"
        )
        safe = rb._sanitize_pii(raw)
        self.assertNotIn("user@example.com", safe)
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456", safe)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", safe)
        self.assertNotIn("secret-value", safe)
        self.assertNotIn("top-secret", safe)

    def test_preview_contains_metadata_not_log_text(self):
        bundle = {
            "description": "stuck",
            "logs": {"agent": {"tail": ["private log line"], "bytes": 123, "truncated": False}},
        }
        preview = rb.preview_report_bundle(bundle)
        self.assertTrue(preview["preview"])
        self.assertEqual(preview["files"][0]["id"], "agent")
        self.assertNotIn("private log line", str(preview))

    def test_empty_log_selection_uploads_no_logs(self):
        self.assertEqual(rb._collect_logs([]), {})

    def test_pending_directory_uses_hermes_home_fallback(self):
        with patch.dict(os.environ, {"HERMES_HOME": "/tmp/hermes-profile-a"}, clear=False):
            with patch("builtins.__import__", side_effect=ImportError("no profiles")):
                pending = rb._pending_dir()
        self.assertEqual(str(pending), "/tmp/hermes-profile-a/pending-reports")

    def test_string_confirm_value_does_not_authorize_upload(self):
        with (
            patch.object(routes, "_report_issue_rate_limited", return_value=False),
            patch.object(routes, "j", side_effect=lambda _handler, payload, status=200: payload),
            patch.object(rb, "build_report_bundle", return_value={"logs": {}}),
            patch.object(rb, "preview_report_bundle", return_value={"ok": True, "preview": True}),
            patch.object(rb, "upload_report") as upload,
        ):
            result = routes._handle_report_issue(
                SimpleNamespace(),
                {"confirm_upload": "false"},
            )
        self.assertTrue(result["preview"])
        upload.assert_not_called()

    def test_pending_reports_are_unique_and_private(self):
        with self.subTest("unique names"):
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as tmp:
                with (
                    patch.object(rb, "_pending_dir", return_value=rb.Path(tmp)),
                    patch.object(rb, "_now_iso", return_value="2026-07-19T00:00:00Z"),
                ):
                    first = rb._save_pending({"kind": "user_report"})
                    second = rb._save_pending({"kind": "user_report"})
                self.assertNotEqual(first, second)
                if os.name == "posix":
                    self.assertEqual(os.stat(first).st_mode & 0o777, 0o600)
                    self.assertEqual(os.stat(second).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
