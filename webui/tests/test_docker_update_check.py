"""Tests for the Docker image update check in api/neowow.py.

Uses source-code inspection to avoid importing api.neowow, which transitively
imports api.config (Python 3.10+ syntax) and fails on Python 3.9.

For _version_newer we extract the function body via ast and exec it in an
isolated namespace so we can run real behavioural assertions without the
import chain.
"""
import ast
import textwrap
import unittest
from pathlib import Path

NEOWOW_PY = Path(__file__).parent.parent / "api" / "neowow.py"
NEOWOW_JS = Path(__file__).parent.parent / "static" / "neowow.js"

_SRC_PY: str = NEOWOW_PY.read_text(encoding="utf-8")
_SRC_JS: str = NEOWOW_JS.read_text(encoding="utf-8")


def _extract_and_compile_function(src, func_name):
    """Parse *src*, find the top-level def *func_name*, compile and return it."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            func_src = ast.get_source_segment(src, node)
            assert func_src, "could not extract source for {}".format(func_name)
            ns = {}
            exec(compile(textwrap.dedent(func_src), "<{}>".format(func_name), "exec"), ns)
            return ns[func_name]
    raise AssertionError("function {!r} not found in source".format(func_name))


_version_newer = _extract_and_compile_function(_SRC_PY, "_version_newer")


class TestVersionNewer(unittest.TestCase):
    """Behavioural tests for the _version_newer() pure helper."""

    def test_newer_patch(self):
        self.assertTrue(_version_newer("1.3.6", "1.3.5"))

    def test_same_version(self):
        self.assertFalse(_version_newer("1.3.5", "1.3.5"))

    def test_older(self):
        self.assertFalse(_version_newer("1.3.4", "1.3.5"))

    def test_strips_v_prefix(self):
        self.assertTrue(_version_newer("v1.4.0", "v1.3.5"))

    def test_strips_dirty_suffix(self):
        self.assertTrue(_version_newer("1.3.6", "1.3.5-dirty"))

    def test_minor_bump(self):
        self.assertTrue(_version_newer("1.4.0", "1.3.9"))

    def test_major_bump(self):
        self.assertTrue(_version_newer("2.0.0", "1.99.99"))

    def test_bad_input_does_not_raise(self):
        # Malformed version strings must not raise -- return False gracefully.
        self.assertFalse(_version_newer("not-a-version", "1.0.0"))


class TestDockerUpdateCheckSource(unittest.TestCase):
    """Source-level checks: the Docker update check logic exists in neowow.py."""

    def test_is_docker_detection(self):
        self.assertIn("/.within_container", _SRC_PY)
        self.assertIn("_IS_DOCKER", _SRC_PY)

    def test_github_releases_api_url(self):
        self.assertIn("feifeixp/hermes-installer", _SRC_PY)
        self.assertIn("api.github.com/repos", _SRC_PY)

    def test_docker_image_constant(self):
        self.assertIn("ghcr.io/feifeixp/hermes-installer", _SRC_PY)
        self.assertIn("_DOCKER_IMAGE", _SRC_PY)

    def test_check_docker_github_release_defined(self):
        self.assertIn("def _check_docker_github_release(", _SRC_PY)

    def test_version_newer_defined(self):
        self.assertIn("def _version_newer(", _SRC_PY)

    def test_get_update_notice_calls_docker_check(self):
        self.assertIn("_check_docker_github_release()", _SRC_PY)

    def test_is_docker_field_in_result(self):
        self.assertIn('"isDocker"', _SRC_PY)

    def test_docker_cache_defined(self):
        self.assertIn("_docker_update_cache", _SRC_PY)


class TestDockerBannerJS(unittest.TestCase):
    """Smoke-check that the frontend JS includes Docker banner code."""

    def test_isdocker_check_in_banner(self):
        self.assertIn("isDocker", _SRC_JS)

    def test_docker_pull_cmd_in_banner(self):
        self.assertIn("docker pull", _SRC_JS)

    def test_docker_compose_cmd_in_banner(self):
        self.assertIn("docker compose up -d", _SRC_JS)

    def test_copy_button_in_banner(self):
        self.assertIn("navigator.clipboard", _SRC_JS)

    def test_docker_image_referenced_in_banner(self):
        self.assertIn("dockerImage", _SRC_JS)

    def test_banner_function_exists(self):
        self.assertIn("function _showNeowowUpdateBanner(", _SRC_JS)


if __name__ == "__main__":
    unittest.main()
