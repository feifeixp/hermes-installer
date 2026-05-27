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
