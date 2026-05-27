"""Hermes WebUI — installer update reminder.

Polls https://api.github.com/repos/feifeixp/hermes-installer/releases/latest
to detect new hermes-installer releases, then HEAD-probes the corresponding
asset on the neowow OSS mirror to confirm download readiness. Result is
TTL-cached so 15 minutes worth of /check calls hit at most once.

Spec: docs/superpowers/specs/2026-05-27-installer-update-reminder-design.md
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
GITHUB_RELEASES_API = "https://api.github.com/repos/feifeixp/hermes-installer/releases/latest"
OSS_BASE = "https://neowow.oss-cn-hangzhou.aliyuncs.com/hermes"
CACHE_TTL_SECONDS = 900  # 15 minutes
GITHUB_TIMEOUT = 5
OSS_HEAD_TIMEOUT = 3

# Map sys.platform → asset filename in the GitHub release / OSS mirror.
# Linux is intentionally absent — no installer published for it.
PLATFORM_ASSETS = {
    "darwin": "Hermes-Installer-macOS.dmg",
    "win32":  "Hermes-Installer-Windows.zip",
}


# ── Public API ───────────────────────────────────────────────────────────────
def check_installer_update(current_version: str | None = None) -> dict:
    """Return current installer-update status. TTL-cached for 15 minutes.

    Returns dict with: ok, update_available, oss_ready, is_prerelease,
    current_version, latest_version, release_notes, release_notes_url,
    download_url, fallback_url.

    On any network / parse failure returns {ok: False, reason: "..."}.
    """
    raise NotImplementedError  # filled in by Task 6
