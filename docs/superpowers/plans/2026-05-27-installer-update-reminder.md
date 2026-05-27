# Installer Update Reminder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A WebUI banner that notifies users when a newer hermes-installer release is on GitHub + mirrored to OSS, with download / view-notes / skip-this-version / dismiss controls.

**Architecture:** New stdlib-only module `webui/api/installer_update.py` polls GitHub Releases API (15-min TTL cache), HEAD-probes the platform asset on OSS to confirm mirror readiness, returns a JSON status. WebUI boot fetches this once at startup and renders a top banner if all gating conditions are met. "Skip" persists to `settings.json`; "Later" stays in-memory.

**Tech Stack:** Python 3.11 stdlib only (urllib.request, json, threading, re). pytest for tests. WebUI frontend: vanilla JS (existing `boot.js` / `panels.js` style), `streaming-markdown` library already bundled, existing `.reconnect-banner` CSS pattern.

**Spec:** `docs/superpowers/specs/2026-05-27-installer-update-reminder-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `webui/api/installer_update.py` | Create | Core check logic: GitHub fetch + OSS HEAD + TTL cache + version compare. ~180 LOC. |
| `webui/tests/test_installer_update.py` | Create | Unit tests for all helpers + `check_installer_update()` branches |
| `webui/tests/test_installer_update_integration.py` | Create | Integration test with mock HTTP server |
| `webui/api/routes.py` | Modify | Wire `GET /api/installer-update/check` + `POST /api/installer-update/skip` |
| `webui/api/config.py` | Modify | Add `"installer_skipped_version": ""` to `_SETTINGS_DEFAULTS` |
| `webui/static/i18n.js` | Modify | New translation keys for banner copy (en + zh first; other locales fall back) |
| `webui/static/index.html` | Modify | Add `<div id="installerUpdateBanner">` DOM node |
| `webui/static/style.css` | Modify | Banner styling using existing `.reconnect-banner` color variables |
| `webui/static/boot.js` | Modify | Fetch `/check` at startup + render banner + wire 4 button handlers |

---

## PHASE 1 — Server-side core

## Task 1: Scaffold `installer_update.py` module + test file

**Files:**
- Create: `/Users/ff/hermes-installer/webui/api/installer_update.py`
- Create: `/Users/ff/hermes-installer/webui/tests/test_installer_update.py`

- [ ] **Step 1: Create the module skeleton.**

Create `webui/api/installer_update.py` with EXACTLY:

```python
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
```

Create `webui/tests/test_installer_update.py` with EXACTLY:

```python
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
```

- [ ] **Step 2: Verify imports clean.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -c \
  "from api import installer_update as iu; print(iu.GITHUB_RELEASES_API)"
```

Expected: `https://api.github.com/repos/feifeixp/hermes-installer/releases/latest`

- [ ] **Step 3: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/installer_update.py webui/tests/test_installer_update.py
git commit -m "feat(installer-update): scaffold module + test file"
```

---

## Task 2: Platform asset mapping + Linux skip

**Files:**
- Modify: `webui/api/installer_update.py`
- Modify: `webui/tests/test_installer_update.py`

- [ ] **Step 1: Append failing tests.**

Append to `webui/tests/test_installer_update.py`:

```python
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
```

- [ ] **Step 2: Run — first 2 PASS (data already in module), last 2 FAIL (helper missing).**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_installer_update.py -v
```

Expected: 2 PASS, 2 FAIL with `AttributeError: module 'api.installer_update' has no attribute '_platform_asset_for'`

- [ ] **Step 3: Add helper to installer_update.py.**

In `webui/api/installer_update.py`, ADD this helper RIGHT AFTER `PLATFORM_ASSETS`:

```python
def _platform_asset_for(platform: str) -> str | None:
    """Return the asset filename for the given sys.platform, or None if unsupported."""
    return PLATFORM_ASSETS.get(platform)
```

- [ ] **Step 4: Run tests — all 4 PASS.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_installer_update.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/installer_update.py webui/tests/test_installer_update.py
git commit -m "feat(installer-update): platform asset mapping + Linux skip"
```

---

## Task 3: Version comparison helpers

**Files:**
- Modify: `webui/api/installer_update.py`
- Modify: `webui/tests/test_installer_update.py`

- [ ] **Step 1: Append failing tests.**

```python
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
```

- [ ] **Step 2: Run tests — all FAIL with AttributeError.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_installer_update.py -v -k "semver or compare"
```

- [ ] **Step 3: Add helpers to installer_update.py.**

In `webui/api/installer_update.py`, ADD these helpers RIGHT BEFORE `check_installer_update`:

```python
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
```

- [ ] **Step 4: Run tests — all PASS.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_installer_update.py -v
```

Expected: 10 passed (4 from Task 2 + 6 new).

- [ ] **Step 5: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/installer_update.py webui/tests/test_installer_update.py
git commit -m "feat(installer-update): _is_clean_semver + _compare_versions helpers"
```

---

## Task 4: `_fetch_github_latest_release` + tests

**Files:**
- Modify: `webui/api/installer_update.py`
- Modify: `webui/tests/test_installer_update.py`

- [ ] **Step 1: Append failing tests.**

```python
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
```

- [ ] **Step 2: Run — 4 new FAIL with AttributeError.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_installer_update.py -v -k fetch_github
```

- [ ] **Step 3: Add helper to installer_update.py.**

In `webui/api/installer_update.py`, ADD this helper right after `_compare_versions`:

```python
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
```

- [ ] **Step 4: Run tests — all 14 PASS.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_installer_update.py -v
```

- [ ] **Step 5: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/installer_update.py webui/tests/test_installer_update.py
git commit -m "feat(installer-update): _fetch_github_latest_release with graceful failure"
```

---

## Task 5: `_check_oss_asset` HEAD probe + tests

**Files:**
- Modify: `webui/api/installer_update.py`
- Modify: `webui/tests/test_installer_update.py`

- [ ] **Step 1: Append failing tests.**

```python
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
```

- [ ] **Step 2: Run — 4 new FAIL.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_installer_update.py -v -k oss_head
```

- [ ] **Step 3: Add helper.**

In `webui/api/installer_update.py`, ADD this helper right after `_fetch_github_latest_release`:

```python
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
```

- [ ] **Step 4: Run all tests — 18 PASS.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_installer_update.py -v
```

- [ ] **Step 5: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/installer_update.py webui/tests/test_installer_update.py
git commit -m "feat(installer-update): _check_oss_asset HEAD probe"
```

---

## Task 6: `check_installer_update()` orchestrator with TTL cache

**Files:**
- Modify: `webui/api/installer_update.py`
- Modify: `webui/tests/test_installer_update.py`

- [ ] **Step 1: Append failing tests.**

```python
@pytest.fixture(autouse=True)
def reset_cache():
    """Clear the module-level cache before each test."""
    iu._check_cache.clear()
    yield
    iu._check_cache.clear()


def test_check_update_available(monkeypatch):
    """Newer release on GitHub + asset on OSS → update_available=True."""
    monkeypatch.setattr(iu.sys, "platform", "darwin")
    with patch.object(iu, "_fetch_github_latest_release", return_value={
            "tag_name": "v1.5.0", "prerelease": False, "body": "notes",
            "html_url": "https://github.com/x/y/releases/tag/v1.5.0",
            "assets": [{"name": "Hermes-Installer-macOS.dmg"}],
        }), \
         patch.object(iu, "_check_oss_asset", return_value=True):
        result = iu.check_installer_update(current_version="v1.4.2")
    assert result["ok"] is True
    assert result["update_available"] is True
    assert result["oss_ready"] is True
    assert result["is_prerelease"] is False
    assert result["latest_version"] == "v1.5.0"
    assert result["current_version"] == "v1.4.2"
    assert result["release_notes"] == "notes"
    assert "v1.5.0" in result["download_url"]
    assert result["download_url"].endswith("Hermes-Installer-macOS.dmg")
    assert result["fallback_url"] == "https://github.com/x/y/releases/tag/v1.5.0"


def test_check_no_update_when_same_version(monkeypatch):
    """current == latest → update_available=False."""
    monkeypatch.setattr(iu.sys, "platform", "darwin")
    with patch.object(iu, "_fetch_github_latest_release", return_value={
            "tag_name": "v1.4.2", "prerelease": False, "body": "",
            "html_url": "", "assets": [{"name": "Hermes-Installer-macOS.dmg"}]}), \
         patch.object(iu, "_check_oss_asset", return_value=True):
        result = iu.check_installer_update(current_version="v1.4.2")
    assert result["ok"] is True
    assert result["update_available"] is False


def test_check_oss_not_ready(monkeypatch):
    """GitHub has newer release but OSS HEAD returns 404 → oss_ready=False."""
    monkeypatch.setattr(iu.sys, "platform", "darwin")
    with patch.object(iu, "_fetch_github_latest_release", return_value={
            "tag_name": "v1.5.0", "prerelease": False, "body": "",
            "html_url": "", "assets": [{"name": "Hermes-Installer-macOS.dmg"}]}), \
         patch.object(iu, "_check_oss_asset", return_value=False):
        result = iu.check_installer_update(current_version="v1.4.2")
    assert result["update_available"] is True  # newer is on GitHub
    assert result["oss_ready"] is False        # but mirror not synced


def test_check_prerelease_flagged(monkeypatch):
    """is_prerelease flag is surfaced (client filters)."""
    monkeypatch.setattr(iu.sys, "platform", "darwin")
    with patch.object(iu, "_fetch_github_latest_release", return_value={
            "tag_name": "v1.5.0-rc1", "prerelease": True, "body": "",
            "html_url": "", "assets": [{"name": "Hermes-Installer-macOS.dmg"}]}), \
         patch.object(iu, "_check_oss_asset", return_value=True):
        result = iu.check_installer_update(current_version="v1.4.2")
    assert result["is_prerelease"] is True


def test_check_linux_returns_false(monkeypatch):
    """sys.platform=linux → update_available=False (no installer)."""
    monkeypatch.setattr(iu.sys, "platform", "linux")
    with patch.object(iu, "_fetch_github_latest_release", return_value={
            "tag_name": "v1.5.0", "prerelease": False, "body": "",
            "html_url": "", "assets": []}), \
         patch.object(iu, "_check_oss_asset", return_value=True):
        result = iu.check_installer_update(current_version="v1.4.2")
    assert result["update_available"] is False


def test_check_dev_mode_returns_false(monkeypatch):
    """current_version not clean semver → update_available=False."""
    monkeypatch.setattr(iu.sys, "platform", "darwin")
    with patch.object(iu, "_fetch_github_latest_release", return_value={
            "tag_name": "v1.5.0", "prerelease": False, "body": "",
            "html_url": "", "assets": [{"name": "Hermes-Installer-macOS.dmg"}]}), \
         patch.object(iu, "_check_oss_asset", return_value=True):
        result = iu.check_installer_update(current_version="v1.4.2-3-g6d5a4b")
    assert result["update_available"] is False


def test_check_github_unreachable(monkeypatch):
    """GitHub fetch returns None → ok=False, no cached entry written."""
    monkeypatch.setattr(iu.sys, "platform", "darwin")
    with patch.object(iu, "_fetch_github_latest_release", return_value=None):
        result = iu.check_installer_update(current_version="v1.4.2")
    assert result["ok"] is False
    assert "reason" in result
    # cache should NOT hold failure results
    assert "darwin" not in iu._check_cache


def test_check_uses_ttl_cache(monkeypatch):
    """Second call within TTL hits cache (mocked fetch called once)."""
    monkeypatch.setattr(iu.sys, "platform", "darwin")
    fetch_mock = MagicMock(return_value={
        "tag_name": "v1.5.0", "prerelease": False, "body": "",
        "html_url": "", "assets": [{"name": "Hermes-Installer-macOS.dmg"}]})
    with patch.object(iu, "_fetch_github_latest_release", fetch_mock), \
         patch.object(iu, "_check_oss_asset", return_value=True):
        iu.check_installer_update(current_version="v1.4.2")
        iu.check_installer_update(current_version="v1.4.2")
    assert fetch_mock.call_count == 1, "second call should hit cache"


def test_check_cache_expires_after_ttl(monkeypatch):
    """After TTL elapses, cache misses and fetch is called again."""
    monkeypatch.setattr(iu.sys, "platform", "darwin")
    fetch_mock = MagicMock(return_value={
        "tag_name": "v1.5.0", "prerelease": False, "body": "",
        "html_url": "", "assets": [{"name": "Hermes-Installer-macOS.dmg"}]})
    with patch.object(iu, "_fetch_github_latest_release", fetch_mock), \
         patch.object(iu, "_check_oss_asset", return_value=True):
        iu.check_installer_update(current_version="v1.4.2")
        # Backdate the cache entry past TTL
        for key in iu._check_cache:
            iu._check_cache[key]["fetched_at"] = time.time() - iu.CACHE_TTL_SECONDS - 1
        iu.check_installer_update(current_version="v1.4.2")
    assert fetch_mock.call_count == 2, "expired entry should trigger refetch"
```

- [ ] **Step 2: Run — 9 new FAIL with NotImplementedError.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_installer_update.py -v -k "test_check_"
```

- [ ] **Step 3: Implement the orchestrator.**

REPLACE the `check_installer_update` stub in `webui/api/installer_update.py` with this full body (placing the module-level cache dict ABOVE the function):

```python
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
```

- [ ] **Step 4: Run all tests — 27 PASS.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_installer_update.py -v
```

Expected: 27 passed (18 from prior tasks + 9 new).

- [ ] **Step 5: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/installer_update.py webui/tests/test_installer_update.py
git commit -m "feat(installer-update): check_installer_update orchestrator with TTL cache"
```

---

## Task 7: Integration test with real HTTP + mock server

**Files:**
- Create: `webui/tests/test_installer_update_integration.py`

- [ ] **Step 1: Create the integration test file.**

```python
"""End-to-end: real HTTP, mock GitHub + mock OSS, full check_installer_update flow."""
from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from api import installer_update as iu


@pytest.fixture(autouse=True)
def reset_cache():
    iu._check_cache.clear()
    yield
    iu._check_cache.clear()


@pytest.fixture
def mock_endpoints(monkeypatch):
    """Spin up a single mock server serving both /releases/latest and /<tag>/<asset>."""
    state = {
        "tag": "v1.5.0",
        "asset_present": True,
        "github_calls": 0,
        "oss_calls": 0,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Mock GitHub Releases API
            if self.path == "/repos/feifeixp/hermes-installer/releases/latest":
                state["github_calls"] += 1
                body = json.dumps({
                    "tag_name": state["tag"],
                    "prerelease": False,
                    "body": "## What's Changed\n- integration test fixture",
                    "html_url": f"https://github.com/feifeixp/hermes-installer/releases/tag/{state['tag']}",
                    "assets": [{"name": "Hermes-Installer-macOS.dmg"},
                               {"name": "Hermes-Installer-Windows.zip"}],
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def do_HEAD(self):
            state["oss_calls"] += 1
            if self.path.startswith(f"/hermes/{state['tag']}/") and state["asset_present"]:
                self.send_response(200)
            else:
                self.send_response(404)
            self.end_headers()

        def log_message(self, fmt, *args): pass

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    base = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(iu, "GITHUB_RELEASES_API",
                        f"{base}/repos/feifeixp/hermes-installer/releases/latest")
    monkeypatch.setattr(iu, "OSS_BASE", f"{base}/hermes")
    yield state
    server.shutdown()


def test_end_to_end_update_available(mock_endpoints, monkeypatch):
    """Mock GitHub + OSS reachable → update_available + oss_ready True."""
    monkeypatch.setattr(iu.sys, "platform", "darwin")
    result = iu.check_installer_update(current_version="v1.4.2")
    assert result["ok"] is True
    assert result["update_available"] is True
    assert result["oss_ready"] is True
    assert result["latest_version"] == "v1.5.0"
    assert result["download_url"].endswith("Hermes-Installer-macOS.dmg")
    assert mock_endpoints["github_calls"] == 1
    assert mock_endpoints["oss_calls"] == 1


def test_end_to_end_oss_not_ready_yet(mock_endpoints, monkeypatch):
    """GitHub returns new release but OSS not synced → oss_ready=False."""
    monkeypatch.setattr(iu.sys, "platform", "darwin")
    mock_endpoints["asset_present"] = False  # simulate OSS lag
    result = iu.check_installer_update(current_version="v1.4.2")
    assert result["update_available"] is True
    assert result["oss_ready"] is False
```

- [ ] **Step 2: Run the integration tests — 2 PASS.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_installer_update_integration.py -v
```

Expected: 2 passed.

- [ ] **Step 3: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/tests/test_installer_update_integration.py
git commit -m "test(installer-update): end-to-end with mock GitHub + OSS"
```

---

## Task 8: Wire `GET /api/installer-update/check` route

**Files:**
- Modify: `webui/api/routes.py`

- [ ] **Step 1: Find the route dispatch pattern.**

```bash
grep -n "if parsed.path == \"/api/updates" webui/api/routes.py | head -3
```

You should see GET-style entries like `if parsed.path == "/api/updates":` near the GET dispatcher.

- [ ] **Step 2: Add the route handler.**

In `webui/api/routes.py`, find `def handle_get(handler, parsed):`. Inside that function, FIND the existing `/api/updates` route handler block (or any similar `if parsed.path == "/api/..."` block). Right BEFORE that block, ADD:

```python
        if parsed.path == "/api/installer-update/check":
            from api.installer_update import check_installer_update
            try:
                # Current version comes from main.py if importable (frozen
                # installer); fall back to the env var that the bootstrap
                # sets, then to "unknown" for dev mode.
                try:
                    from main import _get_app_version
                    current = _get_app_version()
                except Exception:
                    current = os.environ.get("HERMES_INSTALLER_VERSION", "unknown")
                result = check_installer_update(current_version=current)
            except Exception as exc:
                logger.exception("installer-update check failed: %s", exc)
                result = {"ok": False, "reason": "internal_error"}
            j(handler, result)
            return True
```

- [ ] **Step 3: Verify syntax + smoke test the endpoint.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -c "import ast; ast.parse(open('api/routes.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/routes.py
git commit -m "feat(routes): wire GET /api/installer-update/check"
```

---

## Task 9: Add `installer_skipped_version` to `_SETTINGS_DEFAULTS`

**Files:**
- Modify: `webui/api/config.py`

- [ ] **Step 1: Find _SETTINGS_DEFAULTS block.**

```bash
grep -n "^_SETTINGS_DEFAULTS = {" webui/api/config.py
```

Expected: one line number, e.g. `4630:_SETTINGS_DEFAULTS = {`

- [ ] **Step 2: Add the new key.**

In `webui/api/config.py`, find the line `"check_for_updates": True,  # check if webui/agent repos are behind upstream`. RIGHT AFTER that line (and before `"ignore_agent_updates"`), ADD:

```python
    # Empty string = no version has been explicitly skipped. When the user
    # clicks "跳过这个版本" in the installer-update banner, we write the
    # release tag (e.g. "v1.5.0") here; the banner stays hidden until a
    # newer tag is released. See docs/superpowers/specs/2026-05-27-installer-
    # update-reminder-design.md
    "installer_skipped_version": "",
```

- [ ] **Step 3: Verify config still loads.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -c \
  "from api.config import _SETTINGS_DEFAULTS; print('installer_skipped_version' in _SETTINGS_DEFAULTS)"
```

Expected: `True`

- [ ] **Step 4: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/config.py
git commit -m "feat(config): add installer_skipped_version default setting"
```

---

## Task 10: Wire `POST /api/installer-update/skip` route

**Files:**
- Modify: `webui/api/routes.py`

- [ ] **Step 1: Find handle_post dispatch.**

```bash
grep -n "^def handle_post" webui/api/routes.py
```

- [ ] **Step 2: Add the route handler.**

In `webui/api/routes.py`, find `def handle_post(handler, parsed):`. Inside, find any existing settings-writing route (e.g. `/api/settings`) and add this BEFORE it:

```python
    if parsed.path == "/api/installer-update/skip":
        import re as _re
        try:
            body_raw = handler.rfile.read(int(handler.headers.get("Content-Length", "0")))
            payload = json.loads(body_raw or "{}")
        except Exception:
            bad(handler, "invalid JSON body", status=400)
            return True
        version = str(payload.get("version", "")).strip()
        # Validate format: must be exactly v<x>.<y>.<z>
        if not _re.fullmatch(r"v\d+\.\d+\.\d+", version):
            bad(handler, "version must be in v<MAJOR>.<MINOR>.<PATCH> format", status=400)
            return True
        from api.config import load_settings, save_settings
        try:
            settings = load_settings()
            settings["installer_skipped_version"] = version
            save_settings(settings)
        except Exception as exc:
            logger.exception("failed to persist installer_skipped_version: %s", exc)
            bad(handler, "could not save setting", status=500)
            return True
        j(handler, {"ok": True, "skipped_version": version})
        return True
```

Note: `bad()` and `j()` are existing helpers in routes.py — verify they're available in the same scope.

- [ ] **Step 3: Verify syntax.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -c "import ast; ast.parse(open('api/routes.py').read()); print('OK')"
```

- [ ] **Step 4: Smoke-test both routes via Python.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -c "
import os, tempfile, json, sys
sys.path.insert(0, '.')
os.environ['HERMES_HOME'] = tempfile.mkdtemp()
os.makedirs(os.path.join(os.environ['HERMES_HOME'], 'webui'), exist_ok=True)
# Just verify imports and that the routes are wired (full HTTP smoke test
# is covered by manual verification at Task 14).
from api.routes import handle_get, handle_post
from api.installer_update import check_installer_update
print('routes wired OK')
"
```

Expected: `routes wired OK`

- [ ] **Step 5: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/routes.py
git commit -m "feat(routes): wire POST /api/installer-update/skip with format validation"
```

---

## PHASE 2 — Client-side UI

## Task 11: Add i18n keys for the banner

**Files:**
- Modify: `webui/static/i18n.js`

- [ ] **Step 1: Find the LOCALES block.**

```bash
grep -n "^const LOCALES = {" webui/static/i18n.js
grep -n "^  en: {" webui/static/i18n.js
grep -n "^  zh: {" webui/static/i18n.js
```

- [ ] **Step 2: Add keys to the en locale.**

In `webui/static/i18n.js`, inside `LOCALES.en` (find by `en: {`), add these keys near other update-related keys (or at the end of the en block before `},`):

```javascript
    installer_update_banner_title:   'Hermes Installer {version} is available',
    installer_update_banner_current: '(currently {current})',
    installer_update_download:       'Download',
    installer_update_view_notes:     'View release notes',
    installer_update_skip_version:   'Skip this version',
    installer_update_dismiss:        'Later',
    installer_update_skipping:       'Skipping...',
    installer_update_skip_failed:    'Failed to skip: {error}',
```

- [ ] **Step 3: Add keys to the zh locale.**

Inside `LOCALES.zh`, add:

```javascript
    installer_update_banner_title:   'Hermes Installer {version} 已发布',
    installer_update_banner_current: '（当前 {current}）',
    installer_update_download:       '下载新版',
    installer_update_view_notes:     '查看更新内容',
    installer_update_skip_version:   '跳过这个版本',
    installer_update_dismiss:        '稍后',
    installer_update_skipping:       '保存中…',
    installer_update_skip_failed:    '保存失败：{error}',
```

Other locales fall back to en automatically per the existing i18n behavior — no need to add to all.

- [ ] **Step 4: Verify JS syntax.**

```bash
node -e "
const fs = require('fs');
const content = fs.readFileSync('/Users/ff/hermes-installer/webui/static/i18n.js', 'utf-8');
try { new Function(content); console.log('i18n.js parses OK'); }
catch (e) { console.log('SYNTAX ERROR:', e.message); process.exit(1); }
"
```

Expected: `i18n.js parses OK`

- [ ] **Step 5: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/static/i18n.js
git commit -m "feat(i18n): installer-update banner keys (en + zh)"
```

---

## Task 12: Banner DOM + CSS

**Files:**
- Modify: `webui/static/index.html`
- Modify: `webui/static/style.css`

- [ ] **Step 1: Find the existing banner DOM location.**

```bash
grep -n 'id="reconnectBanner"\|id="offlineBanner"' webui/static/index.html
```

Note the line numbers — the new banner goes adjacent to these.

- [ ] **Step 2: Add the banner DOM node.**

In `webui/static/index.html`, find the line containing `id="reconnectBanner"`. RIGHT AFTER that `<div>` element (i.e., after its closing `</div>` on the same or following line), add:

```html
    <!-- Installer-update banner (filled in by boot.js#renderInstallerUpdateBanner) -->
    <div class="installer-update-banner" id="installerUpdateBanner" hidden>
      <div class="installer-update-banner-row">
        <span class="installer-update-banner-icon" aria-hidden="true">🚀</span>
        <div class="installer-update-banner-text">
          <div class="installer-update-banner-title" id="installerUpdateBannerTitle"></div>
          <div class="installer-update-banner-subtitle" id="installerUpdateBannerSubtitle"></div>
        </div>
        <div class="installer-update-banner-actions">
          <button type="button" class="installer-update-btn primary" id="installerUpdateDownloadBtn"></button>
          <button type="button" class="installer-update-btn" id="installerUpdateNotesBtn"></button>
          <button type="button" class="installer-update-btn ghost" id="installerUpdateSkipBtn"></button>
          <button type="button" class="installer-update-banner-dismiss" id="installerUpdateDismissBtn" aria-label="dismiss">✕</button>
        </div>
      </div>
      <div class="installer-update-banner-notes" id="installerUpdateBannerNotes" hidden></div>
    </div>
```

- [ ] **Step 3: Add the CSS.**

In `webui/static/style.css`, find the `.reconnect-banner{` rule. RIGHT AFTER its block (and any associated `.reconnect-banner.visible` / `.reconnect-btn` rules), add:

```css
  /* ── Installer update banner ─────────────────────────────────────────── */
  .installer-update-banner{display:none;background:var(--surface);border:1px solid var(--accent-bg-strong);border-radius:10px;padding:12px 16px;margin:10px auto;max-width:780px;font-size:13px;color:var(--text);box-shadow:0 4px 12px rgba(0,0,0,.06);}
  .installer-update-banner:not([hidden]){display:block;}
  .installer-update-banner-row{display:flex;align-items:center;gap:14px;flex-wrap:wrap;}
  .installer-update-banner-icon{font-size:20px;flex-shrink:0;}
  .installer-update-banner-text{flex:1;min-width:200px;}
  .installer-update-banner-title{font-weight:600;font-size:14px;color:var(--text);}
  .installer-update-banner-subtitle{font-size:12px;color:var(--muted);margin-top:2px;}
  .installer-update-banner-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
  .installer-update-btn{padding:6px 12px;border:1px solid var(--border);background:transparent;color:var(--text);border-radius:8px;font-size:12px;font-weight:500;cursor:pointer;transition:all .15s;}
  .installer-update-btn:hover{background:var(--hover-bg);}
  .installer-update-btn.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600;}
  .installer-update-btn.primary:hover{background:var(--accent-hover);}
  .installer-update-btn.ghost{color:var(--muted);border-color:transparent;}
  .installer-update-btn.ghost:hover{color:var(--text);}
  .installer-update-banner-dismiss{background:transparent;border:none;color:var(--muted);font-size:16px;cursor:pointer;padding:4px 8px;border-radius:6px;}
  .installer-update-banner-dismiss:hover{background:var(--hover-bg);color:var(--text);}
  .installer-update-banner-notes{margin-top:12px;padding-top:12px;border-top:1px solid var(--border);max-height:280px;overflow-y:auto;font-size:13px;line-height:1.6;color:var(--text);}
  .installer-update-banner-notes h1,.installer-update-banner-notes h2,.installer-update-banner-notes h3{font-size:14px;font-weight:600;margin:8px 0 4px;}
  .installer-update-banner-notes ul,.installer-update-banner-notes ol{margin:4px 0 8px 20px;}
  .installer-update-banner-notes code{background:var(--code-bg);padding:1px 4px;border-radius:3px;font-size:12px;}
```

- [ ] **Step 4: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/static/index.html webui/static/style.css
git commit -m "feat(webui): installer-update banner DOM + CSS"
```

---

## Task 13: `boot.js` fetch + render + button handlers

**Files:**
- Modify: `webui/static/boot.js`

- [ ] **Step 1: Find where boot.js does its main async init.**

```bash
grep -n "^async function boot\|_bootSettings\|settings_fetched\|/api/settings" webui/static/boot.js | head -10
```

You should see existing settings fetch logic — we'll piggyback on it.

- [ ] **Step 2: Add the banner module at the END of boot.js.**

APPEND to `webui/static/boot.js`:

```javascript

// ── Installer update banner ───────────────────────────────────────────────
// Wired up by boot.js after settings are fetched. Polls
// /api/installer-update/check once per boot; if a new installer release is
// on GitHub AND the OSS mirror has it AND the user hasn't explicitly
// skipped this version, render a top banner with download / view-notes /
// skip / dismiss actions. See docs/superpowers/specs/2026-05-27-installer-
// update-reminder-design.md
(function setupInstallerUpdateBanner(){
  function $(id){ return document.getElementById(id); }

  // i18n helper — falls back to the key if no t() is available yet.
  function tx(key, vars){
    if (typeof t === 'function') {
      let s = t(key) || key;
      if (vars) for (const k in vars) s = s.replaceAll('{' + k + '}', vars[k]);
      return s;
    }
    return key;
  }

  function hide(){
    const b = $('installerUpdateBanner');
    if (b) b.setAttribute('hidden', '');
  }

  function show(){
    const b = $('installerUpdateBanner');
    if (b) b.removeAttribute('hidden');
  }

  function renderNotes(markdown){
    const box = $('installerUpdateBannerNotes');
    if (!box) return;
    // Use streaming-markdown for one-shot render. The library is loaded by
    // index.html ahead of boot.js and exposes a global `smd` namespace.
    if (typeof smd === 'undefined' || !smd) {
      // Fallback: plain text in a <pre>.
      box.innerHTML = '';
      const pre = document.createElement('pre');
      pre.style.whiteSpace = 'pre-wrap';
      pre.textContent = markdown;
      box.appendChild(pre);
      return;
    }
    box.innerHTML = '';
    const renderer = smd.default_renderer(box);
    const parser = smd.parser(renderer);
    smd.parser_write(parser, markdown);
    smd.parser_end(parser);
  }

  async function postSkip(version){
    try {
      const r = await fetch('/api/installer-update/skip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version }),
      });
      return r.ok;
    } catch (e) {
      return false;
    }
  }

  function wireActions(payload, skippedVersion){
    const dl    = $('installerUpdateDownloadBtn');
    const notes = $('installerUpdateNotesBtn');
    const skip  = $('installerUpdateSkipBtn');
    const dism  = $('installerUpdateDismissBtn');
    const notesBox = $('installerUpdateBannerNotes');

    if (dl) {
      dl.textContent = '📥 ' + tx('installer_update_download');
      dl.onclick = () => {
        const url = payload.download_url || payload.fallback_url;
        if (url) window.open(url, '_blank', 'noopener');
      };
    }
    if (notes) {
      notes.textContent = '📋 ' + tx('installer_update_view_notes');
      let expanded = false;
      notes.onclick = () => {
        expanded = !expanded;
        if (notesBox) {
          if (expanded) {
            renderNotes(payload.release_notes || tx('installer_update_view_notes'));
            notesBox.removeAttribute('hidden');
          } else {
            notesBox.setAttribute('hidden', '');
          }
        }
      };
    }
    if (skip) {
      skip.textContent = tx('installer_update_skip_version');
      skip.onclick = async () => {
        const original = skip.textContent;
        skip.disabled = true;
        skip.textContent = tx('installer_update_skipping');
        const ok = await postSkip(payload.latest_version);
        skip.disabled = false;
        if (ok) {
          hide();
        } else {
          skip.textContent = tx('installer_update_skip_failed', { error: 'network' });
          setTimeout(() => { skip.textContent = original; }, 3000);
        }
      };
    }
    if (dism) {
      dism.onclick = hide;  // in-memory only
    }
  }

  async function check(){
    // Settings fetched separately by boot.js — read from the global Hermes
    // state if available, otherwise hit the endpoint directly.
    let skippedVersion = '';
    try {
      const s = (typeof S === 'object' && S && S.settings) ? S.settings
              : await fetch('/api/settings').then(r => r.json());
      skippedVersion = (s && s.installer_skipped_version) || '';
    } catch (e) {
      // Best-effort; default to empty skip list.
    }

    let payload;
    try {
      const r = await fetch('/api/installer-update/check');
      if (!r.ok) return;
      payload = await r.json();
    } catch (e) {
      return;
    }

    if (!payload || !payload.ok) return;
    if (!payload.update_available) return;
    if (!payload.oss_ready) return;
    if (payload.is_prerelease) return;
    if (payload.latest_version === skippedVersion) return;

    // Populate banner copy
    const titleEl = $('installerUpdateBannerTitle');
    const subEl   = $('installerUpdateBannerSubtitle');
    if (titleEl) titleEl.textContent = tx('installer_update_banner_title',
                                          { version: payload.latest_version });
    if (subEl)   subEl.textContent   = tx('installer_update_banner_current',
                                          { current: payload.current_version });
    wireActions(payload, skippedVersion);
    show();
  }

  // Run after DOMContentLoaded + a short delay so other boot.js logic has
  // installed t() and fetched settings. Total cost: 1 fetch to /check.
  function start(){ setTimeout(check, 2000); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
```

- [ ] **Step 3: Verify JS syntax.**

```bash
node -e "
const fs = require('fs');
const content = fs.readFileSync('/Users/ff/hermes-installer/webui/static/boot.js', 'utf-8');
try { new Function(content); console.log('boot.js parses OK'); }
catch (e) { console.log('SYNTAX ERROR:', e.message); process.exit(1); }
"
```

Expected: `boot.js parses OK`

- [ ] **Step 4: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/static/boot.js
git commit -m "feat(boot): wire installer-update banner fetch + render + handlers"
```

---

## Task 14: End-to-end smoke + push branch + open PR

**Files:** (none modified)

- [ ] **Step 1: Run the full unit + integration test suite.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest \
  tests/test_installer_update.py tests/test_installer_update_integration.py -v 2>&1 | tail -10
```

Expected: 29 passed (27 unit + 2 integration).

- [ ] **Step 2: Run existing webui tests to confirm no regression.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest \
  tests/test_1003_appearance_autosave.py tests/test_1059_settings_picker_active_state.py \
  --timeout=15 2>&1 | tail -5
```

Expected: all PASS (or known-pre-existing failures, not from this change).

- [ ] **Step 3: Smoke-test the live `/check` endpoint against the real backend.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -c "
import sys
sys.path.insert(0, '.')
from api.installer_update import check_installer_update
# Use real GitHub + real OSS
result = check_installer_update(current_version='v1.0.0')  # force update_available=True
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

Expected: a JSON dict with `ok: true`, `latest_version: "vX.Y.Z"`, `oss_ready: true` (for currently-released versions). If `oss_ready: false`, OSS mirror hasn't caught up — that's not a bug, just timing.

- [ ] **Step 4: Smoke-test the banner end-to-end** (only if you have a local webui dev server):

Start the webui via `.claude/run-webui.sh` (or however you launch dev webui). Open `http://127.0.0.1:8787/` in browser. Open DevTools → Network. Look for `GET /api/installer-update/check` returning 200 and the JSON payload. If `update_available && oss_ready && !is_prerelease`, you should see the banner render at the top.

If no banner appears even though the JSON says update_available + oss_ready, check the browser console for JS errors.

- [ ] **Step 5: Push branch + open PR.**

```bash
cd /Users/ff/hermes-installer
git push -u origin feat/installer-update-reminder
gh pr create --base main --head feat/installer-update-reminder \
  --title "✨ Installer update reminder banner" \
  --body "Implements docs/superpowers/specs/2026-05-27-installer-update-reminder-design.md.

## Summary
- New webui endpoint \`GET /api/installer-update/check\` (TTL-cached, 15 min) polls GitHub Releases + HEAD-probes OSS mirror
- New endpoint \`POST /api/installer-update/skip\` persists user's choice to webui settings.json
- WebUI boot.js fetches \`/check\` ~2s after boot, renders a top banner if all gating conditions are met:
    update_available && oss_ready && !is_prerelease && latest != skipped_version
- 4 action buttons: 📥 Download (opens OSS URL) / 📋 View notes (inline markdown via streaming-markdown) / Skip this version (persists) / ✕ Later (in-memory)
- Linux + dev-mode (current_version not clean semver) explicitly skip → update_available=false

## Tests
- 27 unit tests + 2 integration tests against mock HTTP server
- All existing webui tests still pass

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

Expected: PR URL printed.

- [ ] **Step 6: Mark task complete in TaskList.**

The branch + PR is the integration point. CI will run the test suite automatically.

---

## Self-Review Notes

This plan covers the spec's requirements:

- ✅ **Architecture (server-side)**: Tasks 1-7 build `installer_update.py` with TTL cache, GitHub fetch, OSS HEAD probe, version compare. 27 unit + 2 integration tests.
- ✅ **Routes**: Task 8 (`/check`), Task 10 (`/skip` with format validation).
- ✅ **Settings**: Task 9 adds `installer_skipped_version` default.
- ✅ **UI banner**: Task 12 (DOM + CSS), Task 13 (JS handlers).
- ✅ **i18n**: Task 11 (en + zh, others fall back).
- ✅ **End-to-end smoke + PR**: Task 14.
- ✅ **Linux / dev mode skip**: Covered by tests in Task 6 (test_check_linux_returns_false, test_check_dev_mode_returns_false).
- ✅ **Prerelease detection**: Surfaced in payload but client filters (Task 13 logic + Task 6 test).
- ✅ **Streaming-markdown for notes**: Task 13 has explicit `smd.parser_write` usage with a plain-text fallback.

All test code is concrete (full bodies, not "similar to..."). All commit messages are concrete. Method/property names consistent: `check_installer_update`, `_fetch_github_latest_release`, `_check_oss_asset`, `_compare_versions`, `_is_clean_semver`, `_platform_asset_for`, `installer_skipped_version`, `latest_version`, `update_available`, `oss_ready`, `is_prerelease`, `download_url`, `fallback_url`, `release_notes`, `release_notes_url`.

Notes for the implementing engineer:
- Use the **venv pytest** at `/Users/ff/hermes-installer/.build_venv/bin/python -m pytest`, not system pytest.
- The webui test directory uses `sys.path.insert(0, Path(__file__).parent.parent)` (same as `tests/test_windows_install.py`). Tests live at `webui/tests/`.
- When inserting routes in `routes.py`, that file is **huge** (13K+ lines). The decorator pattern was already established in `crash_reporter`'s feat branch — but for THIS plan we just add `if parsed.path == "..."` blocks inside the existing dispatcher functions; that's the established style for this file.
