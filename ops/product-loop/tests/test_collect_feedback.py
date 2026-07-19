import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "collect_feedback.py"
SPEC = importlib.util.spec_from_file_location("collect_feedback", MODULE_PATH)
collect_feedback = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(collect_feedback)


class NormalizeReportsTests(unittest.TestCase):
    def test_keeps_only_allowlisted_metadata(self):
        payload = {
            "reports": [{
                "reportId": "BR-ABC123",
                "createdAt": "2026-07-19T08:00:00Z",
                "ownerId": "private-user",
                "displayName": "Private Name",
                "source": "desktop",
                "appVersion": "v1.5.25",
                "platform": "darwin",
                "description": "  cannot   send message  ",
                "status": "new",
                "blobKey": "reports/private/bundle.json.gz",
            }]
        }
        rows = collect_feedback.normalize_reports(
            payload, datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(rows[0]["description"], "cannot send message")
        self.assertNotIn("ownerId", rows[0])
        self.assertNotIn("displayName", rows[0])
        self.assertNotIn("blobKey", rows[0])

    def test_filters_items_before_since(self):
        payload = {"reports": [
            {"reportId": "BR-OLD001", "createdAt": "2026-07-18T08:00:00Z"},
            {"reportId": "BR-NEW001", "createdAt": "2026-07-19T08:00:00Z"},
        ]}
        rows = collect_feedback.normalize_reports(
            payload, datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)
        )
        self.assertEqual([row["reportId"] for row in rows], ["BR-NEW001"])

    def test_rejects_invalid_payload(self):
        with self.assertRaises(ValueError):
            collect_feedback.normalize_reports({"items": []})


if __name__ == "__main__":
    unittest.main()
