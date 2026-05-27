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
