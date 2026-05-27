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


def _platform_asset_for(platform: str) -> str | None:
    """Return the asset filename for the given sys.platform, or None if unsupported."""
    return PLATFORM_ASSETS.get(platform)


_CLEAN_SEMVER_RE = re.compile(r'^v\d+\.\d+\.\d+$')


def _is_clean_semver(v: str | None) -> bool:
    """True iff `v` matches v<MAJOR>.<MINOR>.<PATCH> exactly."""
    if not v:
        return False
    return bool(_CLEAN_SEMVER_RE.match(v.strip()))


def _compare_versions(current: str, latest: str) -> bool:
    """Return True iff `latest` is strictly newer than `current`.

    Both inputs must be clean v<x>.<y>.<z> semver — otherwise returns False
    so dev-mode / unknown versions don't false-positive an update.
    """
    if not _is_clean_semver(current) or not _is_clean_semver(latest):
        return False
    c = tuple(int(x) for x in current.lstrip('v').split('.'))
    l = tuple(int(x) for x in latest.lstrip('v').split('.'))
    return l > c


def _fetch_github_latest_release() -> dict | None:
    """Fetch the latest release metadata from GitHub. Returns None on any failure."""
    headers = {
        "Accept":     "application/vnd.github+json",
        "User-Agent": "hermes-installer-update-check",  # GitHub requires UA
    }
    req = urllib.request.Request(GITHUB_RELEASES_API, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=GITHUB_TIMEOUT) as resp:
            raw = resp.read()
        return json.loads(raw)
    except urllib.error.HTTPError as exc:
        logger.debug("installer_update: GitHub HTTP %s: %s", exc.code, exc.reason)
        return None
    except urllib.error.URLError as exc:
        logger.debug("installer_update: GitHub URL error: %s", exc)
        return None
    except (json.JSONDecodeError, ValueError) as exc:
        logger.debug("installer_update: GitHub returned non-JSON: %s", exc)
        return None
    except Exception as exc:
        logger.warning("installer_update: GitHub fetch unexpected error: %s", exc)
        return None


# ── Public API ───────────────────────────────────────────────────────────────
def check_installer_update(current_version: str | None = None) -> dict:
    """Return current installer-update status. TTL-cached for 15 minutes.

    Returns dict with: ok, update_available, oss_ready, is_prerelease,
    current_version, latest_version, release_notes, release_notes_url,
    download_url, fallback_url.

    On any network / parse failure returns {ok: False, reason: "..."}.
    """
    raise NotImplementedError  # filled in by Task 6
