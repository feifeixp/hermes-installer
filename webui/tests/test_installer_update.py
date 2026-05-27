"""Unit tests for installer_update module."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from api import installer_update as iu


def test_platform_asset_darwin():
    """darwin maps to .dmg asset."""
    assert iu.PLATFORM_ASSETS.get("darwin") == "Hermes-Installer-macOS.dmg"


def test_platform_asset_win32():
    """win32 maps to .zip asset."""
    assert iu.PLATFORM_ASSETS.get("win32") == "Hermes-Installer-Windows.zip"


def test_platform_asset_for_returns_none_on_linux():
    """Linux returns None — no installer published."""
    assert iu._platform_asset_for("linux") is None


def test_platform_asset_for_returns_correct_filename():
    """darwin/win32 return the right asset filename."""
    assert iu._platform_asset_for("darwin") == "Hermes-Installer-macOS.dmg"
    assert iu._platform_asset_for("win32") == "Hermes-Installer-Windows.zip"


def test_is_clean_semver_accepts_v_prefixed():
    assert iu._is_clean_semver("v1.4.2") is True
    assert iu._is_clean_semver("v0.0.1") is True
    assert iu._is_clean_semver("v12.345.6789") is True


def test_is_clean_semver_rejects_dev_format():
    """git-describe output like 'v1.4.2-3-g6d5a4b' is dev mode, not semver."""
    assert iu._is_clean_semver("v1.4.2-3-g6d5a4b") is False
    assert iu._is_clean_semver("1.4.2") is False  # missing v prefix
    assert iu._is_clean_semver("v1.4") is False  # only 2 components
    assert iu._is_clean_semver("v1.4.2-rc1") is False
    assert iu._is_clean_semver("unknown") is False
    assert iu._is_clean_semver("") is False


def test_compare_versions_newer():
    """latest > current → True."""
    assert iu._compare_versions("v1.4.2", "v1.4.3") is True
    assert iu._compare_versions("v1.4.2", "v1.5.0") is True
    assert iu._compare_versions("v1.4.2", "v2.0.0") is True


def test_compare_versions_same_or_older():
    """latest <= current → False."""
    assert iu._compare_versions("v1.4.2", "v1.4.2") is False
    assert iu._compare_versions("v1.4.3", "v1.4.2") is False
    assert iu._compare_versions("v2.0.0", "v1.9.9") is False


def test_compare_versions_handles_two_digit_components():
    """v1.10.0 must be NEWER than v1.9.0 — string compare would say opposite."""
    assert iu._compare_versions("v1.9.0", "v1.10.0") is True
    assert iu._compare_versions("v1.10.0", "v1.9.0") is False


def test_compare_versions_rejects_invalid_inputs():
    """Either side not clean semver → False (don't false-positive)."""
    assert iu._compare_versions("unknown", "v1.5.0") is False
    assert iu._compare_versions("v1.4.2-3-g6d5a", "v1.5.0") is False
    assert iu._compare_versions("v1.4.2", "v1.5.0-rc1") is False


def _make_github_response(tag: str = "v1.5.0", prerelease: bool = False, body: str = "## What's Changed\n- fix") -> MagicMock:
    """Build a mock urlopen response shaped like GitHub Releases API."""
    payload = json.dumps({
        "tag_name": tag,
        "prerelease": prerelease,
        "body": body,
        "html_url": f"https://github.com/feifeixp/hermes-installer/releases/tag/{tag}",
        "assets": [
            {"name": "Hermes-Installer-macOS.dmg"},
            {"name": "Hermes-Installer-Windows.zip"},
        ],
    }).encode("utf-8")
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    resp.read.return_value = payload
    return resp


def test_fetch_github_returns_parsed_release():
    """Successful 200 → returns dict with tag_name/prerelease/body/html_url."""
    with patch.object(iu.urllib.request, "urlopen", MagicMock(return_value=_make_github_response())):
        release = iu._fetch_github_latest_release()
    assert release is not None
    assert release["tag_name"] == "v1.5.0"
    assert release["prerelease"] is False
    assert "What's Changed" in release["body"]


def test_fetch_github_returns_none_on_403_rate_limit():
    """GitHub secondary rate-limit (403) → None, no exception."""
    err = iu.urllib.error.HTTPError("url", 403, "rate limited", {}, None)
    with patch.object(iu.urllib.request, "urlopen", MagicMock(side_effect=err)):
        release = iu._fetch_github_latest_release()
    assert release is None


def test_fetch_github_returns_none_on_network_error():
    """URLError → None, no exception."""
    err = iu.urllib.error.URLError("connection refused")
    with patch.object(iu.urllib.request, "urlopen", MagicMock(side_effect=err)):
        release = iu._fetch_github_latest_release()
    assert release is None


def test_fetch_github_returns_none_on_malformed_json():
    """Body not valid JSON → None."""
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    resp.read.return_value = b"not-json"
    with patch.object(iu.urllib.request, "urlopen", MagicMock(return_value=resp)):
        release = iu._fetch_github_latest_release()
    assert release is None


def _make_oss_head_response(status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    resp.status = status
    return resp


def test_oss_head_returns_true_on_200():
    """OSS asset exists → True."""
    with patch.object(iu.urllib.request, "urlopen", MagicMock(return_value=_make_oss_head_response(200))):
        assert iu._check_oss_asset("v1.5.0", "Hermes-Installer-macOS.dmg") is True


def test_oss_head_returns_false_on_404():
    """OSS asset not yet mirrored → False."""
    err = iu.urllib.error.HTTPError("url", 404, "not found", {}, None)
    with patch.object(iu.urllib.request, "urlopen", MagicMock(side_effect=err)):
        assert iu._check_oss_asset("v1.5.0", "Hermes-Installer-macOS.dmg") is False


def test_oss_head_returns_false_on_network_error():
    """Network failure → False, no exception."""
    err = iu.urllib.error.URLError("dns failure")
    with patch.object(iu.urllib.request, "urlopen", MagicMock(side_effect=err)):
        assert iu._check_oss_asset("v1.5.0", "Hermes-Installer-macOS.dmg") is False


def test_oss_head_uses_correct_url():
    """URL constructed as OSS_BASE/<tag>/<asset>."""
    captured = {}
    def capture_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return _make_oss_head_response(200)
    with patch.object(iu.urllib.request, "urlopen", capture_urlopen):
        iu._check_oss_asset("v1.5.0", "Hermes-Installer-macOS.dmg")
    assert captured["url"] == "https://neowow.oss-cn-hangzhou.aliyuncs.com/hermes/v1.5.0/Hermes-Installer-macOS.dmg"
    assert captured["method"] == "HEAD"
