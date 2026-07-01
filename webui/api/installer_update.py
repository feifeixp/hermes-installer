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
    # Windows update → the installer (re-runs setup, refreshes shortcuts).
    "win32":  "Neowow-Studio-Setup.exe",
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


def _check_oss_asset(tag: str, asset: str) -> bool:
    """HEAD-probe the OSS mirror to confirm `<tag>/<asset>` is downloadable.

    Returns True iff HTTP 2xx; False on 4xx/5xx/network error.
    """
    url = f"{OSS_BASE}/{tag}/{asset}"
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=OSS_HEAD_TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        logger.debug("installer_update: OSS HEAD %s → %s", url, exc.code)
        return False
    except urllib.error.URLError as exc:
        logger.debug("installer_update: OSS HEAD network error: %s", exc)
        return False
    except Exception as exc:
        logger.warning("installer_update: OSS HEAD unexpected: %s", exc)
        return False


# ── Public API ───────────────────────────────────────────────────────────────
# Module-level TTL cache. Keyed by platform so multiple OS smoke tests don't
# collide. Value: {"result": dict, "fetched_at": float}. Only successful
# fetches are cached — failures retry on next call so transient network
# issues don't lock out updates for 15 minutes.
_check_cache: dict[str, dict] = {}


def check_installer_update(current_version: str | None = None) -> dict:
    """Return current installer-update status. TTL-cached for 15 minutes."""
    platform = sys.platform
    cached = _check_cache.get(platform)
    if cached and (time.time() - cached["fetched_at"]) < CACHE_TTL_SECONDS:
        # Refresh current_version on cached payload — the caller may pass a
        # different value than what was cached (e.g. user upgraded the
        # installer between checks).
        out = dict(cached["result"])
        if current_version:
            out["current_version"] = current_version
            out["update_available"] = _compare_versions(
                current_version, out.get("latest_version", "")
            )
        return out

    release = _fetch_github_latest_release()
    if release is None:
        return {"ok": False, "reason": "github_unreachable"}

    tag = str(release.get("tag_name", "")).strip()
    prerelease = bool(release.get("prerelease", False))
    body = str(release.get("body", "") or "")[:50_000]
    html_url = str(release.get("html_url", "") or "")

    asset_name = _platform_asset_for(platform)
    if asset_name is None:
        # Unsupported platform (e.g. linux) — no installer to offer.
        return {
            "ok": True,
            "update_available": False,
            "oss_ready": False,
            "is_prerelease": prerelease,
            "current_version": current_version or "",
            "latest_version": tag,
            "release_notes": "",
            "release_notes_url": html_url,
            "download_url": "",
            "fallback_url": html_url,
        }

    oss_ready = _check_oss_asset(tag, asset_name)
    download_url = f"{OSS_BASE}/{tag}/{asset_name}"

    update_available = _compare_versions(current_version or "", tag)

    result = {
        "ok": True,
        "update_available": update_available,
        "oss_ready": oss_ready,
        "is_prerelease": prerelease,
        "current_version": current_version or "",
        "latest_version": tag,
        "release_notes": body,
        "release_notes_url": html_url,
        "download_url": download_url,
        "fallback_url": html_url,
    }

    _check_cache[platform] = {"result": result, "fetched_at": time.time()}
    return result
