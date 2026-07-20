import io
import importlib.util
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "audit_models.py"
SPEC = importlib.util.spec_from_file_location("audit_models", MODULE_PATH)
audit_models = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(audit_models)


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class AuditModelsTests(unittest.TestCase):
    def test_model_ids_support_both_shapes(self):
        self.assertEqual(audit_models._model_ids_from_payload({"models": ["b", "a", "a"]}), ["a", "b"])
        self.assertEqual(audit_models._model_ids_from_payload({"data": [{"id": "z"}]}), ["z"])

    def test_static_catalog_is_parsed_without_import(self):
        source = '_PROVIDER_MODELS = {"neodomain": [{"id": "m1", "label": "M1"}]}\n'
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.py"
            path.write_text(source, encoding="utf-8")
            self.assertEqual(audit_models.load_static_catalog(path), ["m1"])

    @patch("urllib.request.urlopen")
    def test_successful_probe_never_returns_completion_content(self, urlopen):
        urlopen.return_value = _Response(json.dumps({"choices": [{"message": {"content": "secret output"}}]}).encode())
        result = audit_models.probe_model("m1", chat_url="https://example.test/chat", token="secret", timeout=1)
        self.assertEqual(result["state"], "available")
        self.assertNotIn("content", result)
        self.assertNotIn("secret", json.dumps(result))

    @patch("urllib.request.urlopen")
    def test_http_400_is_unavailable_with_safe_error_metadata(self, urlopen):
        body = io.BytesIO(json.dumps({"error": {"code": "model_not_found", "type": "invalid_request"}}).encode())
        urlopen.side_effect = urllib.error.HTTPError("https://example.test", 400, "bad", {}, body)
        result = audit_models.probe_model("gone", chat_url="https://example.test/chat", token="secret", timeout=1)
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["error_code"], "model_not_found")

    def test_string_error_is_kept_as_bounded_code(self):
        self.assertEqual(audit_models._error_metadata({"error": "upstream_error"}), ("upstream_error", None))


if __name__ == "__main__":
    unittest.main()
