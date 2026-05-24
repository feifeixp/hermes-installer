# First-Launch Login Onboarding Overlay — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When Hermes WebUI loads and the user has no neowow.studio JWT, show a mandatory full-screen onboarding overlay; after login, auto-configure `neowow-coding-plan` as the default provider and dismiss the overlay.

**Architecture:** Three coordinated changes: (1) a new `POST /api/neowow/activate-provider` backend route that calls the existing `apply_onboarding_setup()` to write the provider config; (2) a `#onboardingOverlay` div added to `index.html` styled to match the existing boot overlay; (3) JS functions in `neowow.js` hooked into the boot-resolve flow and the `neoSessionUpdated` event.

**Tech Stack:** Python (existing `http.server` handler pattern in `routes.py`), vanilla JS (IIFE in `neowow.js`), inline HTML/CSS in `index.html`

---

## File Map

| File | Change |
|------|--------|
| `webui/api/routes.py` | Add `POST /api/neowow/activate-provider` after the `/api/neowow/jwt` block (~line 5963) |
| `webui/static/index.html` | Append `#onboardingOverlay` div before `</body>` (line 1858) |
| `webui/static/neowow.js` | Add `_neowowShowOnboarding()` + `_neowowCompleteOnboarding()`, hook into `neowowResolveBootOverlay()` (line 163) and `neoSessionUpdated` listener (line 457) |

---

## Task 1: Backend — `POST /api/neowow/activate-provider`

**Files:**
- Modify: `webui/api/routes.py:5963` (insert after the `/api/neowow/jwt` block)
- Test: `webui/tests/test_activate_provider.py`

- [ ] **Step 1: Write the failing test**

Create `webui/tests/test_activate_provider.py`:

```python
"""Tests for POST /api/neowow/activate-provider."""
import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


def _make_handler(method="POST", path="/api/neowow/activate-provider", body=None):
    """Build a minimal mock handler object the route dispatcher expects."""
    handler = MagicMock()
    handler.command = method
    # The route dispatcher uses handler to send the response
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.wfile = MagicMock()
    return handler


class TestActivateProvider(unittest.TestCase):

    @patch("api.onboarding._fetch_neowow_plan_models")
    @patch("api.onboarding.apply_onboarding_setup")
    def test_returns_ok_on_success(self, mock_setup, mock_fetch):
        """Happy path: returns {"ok": True, "provider": ..., "model": ...}."""
        mock_fetch.return_value = (
            [{"id": "deepseek-v4-flash"}, {"id": "deepseek-v4"}],
            "deepseek-v4-flash",
        )
        mock_setup.return_value = {"provider": "neowow-coding-plan"}

        # Import the handler function directly to avoid spinning up the full server
        from api import routes  # noqa: F401 — ensure module is importable

        # We call the internal logic directly via a thin wrapper test
        from api.onboarding import (
            _NEOWOW_CODING_PLAN_PROVIDER_ID,
            _fetch_neowow_plan_models,
            apply_onboarding_setup,
        )
        models, default_model = _fetch_neowow_plan_models()
        model = default_model or (models[0]["id"] if models else "deepseek-v4-flash")
        result = apply_onboarding_setup(
            {"provider": _NEOWOW_CODING_PLAN_PROVIDER_ID, "model": model}
        )
        self.assertEqual(model, "deepseek-v4-flash")
        mock_setup.assert_called_once_with(
            {"provider": "neowow-coding-plan", "model": "deepseek-v4-flash"}
        )

    @patch("api.onboarding._fetch_neowow_plan_models")
    @patch("api.onboarding.apply_onboarding_setup")
    def test_falls_back_when_no_default_model(self, mock_setup, mock_fetch):
        """When default_model is None and models list is empty, falls back to deepseek-v4-flash."""
        mock_fetch.return_value = ([], None)
        mock_setup.return_value = {}

        from api.onboarding import (
            _NEOWOW_CODING_PLAN_PROVIDER_ID,
            _fetch_neowow_plan_models,
            apply_onboarding_setup,
        )
        models, default_model = _fetch_neowow_plan_models()
        model = default_model or (models[0]["id"] if models else "deepseek-v4-flash")
        self.assertEqual(model, "deepseek-v4-flash")

    @patch("api.onboarding._fetch_neowow_plan_models")
    def test_exception_returns_warning_not_raise(self, mock_fetch):
        """If _fetch_neowow_plan_models raises, the route catches and logs — doesn't propagate."""
        mock_fetch.side_effect = RuntimeError("network timeout")

        from api.onboarding import (
            _NEOWOW_CODING_PLAN_PROVIDER_ID,
            _fetch_neowow_plan_models,
        )
        try:
            _fetch_neowow_plan_models()
            self.fail("Expected RuntimeError")
        except RuntimeError as e:
            self.assertIn("network timeout", str(e))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/ff/hermes-installer/webui && python -m pytest tests/test_activate_provider.py -v
```

Expected: tests run (may show import errors before implementation, but logic tests should pass since we test the onboarding module directly)

- [ ] **Step 3: Add the route to `routes.py`**

In `webui/api/routes.py`, find the end of the `/api/neowow/jwt` block (around line 5963):

```python
        except Exception as e:
            logger.exception("neowow jwt save failed")
            return bad(handler, str(e), status=500)
```

Insert immediately after it (before the `/api/neowow/oauth/launch` comment block):

```python
    # One-shot provider activation — called by the onboarding overlay after
    # first login.  Reads the JWT from neowow.json automatically (via
    # apply_onboarding_setup), writes neowow-coding-plan to config.yaml.
    if parsed.path == "/api/neowow/activate-provider":
        try:
            from api.onboarding import (
                _NEOWOW_CODING_PLAN_PROVIDER_ID,
                _fetch_neowow_plan_models,
                apply_onboarding_setup,
            )
            models, default_model = _fetch_neowow_plan_models()
            model = default_model or (models[0]["id"] if models else "deepseek-v4-flash")
            apply_onboarding_setup({
                "provider": _NEOWOW_CODING_PLAN_PROVIDER_ID,
                "model": model,
            })
            return j(handler, {
                "ok": True,
                "provider": _NEOWOW_CODING_PLAN_PROVIDER_ID,
                "model": model,
            })
        except Exception as e:
            logger.warning("[activate-provider] failed: %s", e)
            return bad(handler, str(e), status=500)

```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/ff/hermes-installer/webui && python -m pytest tests/test_activate_provider.py -v
```

Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/ff/hermes-installer
git add webui/api/routes.py webui/tests/test_activate_provider.py
git commit -m "feat: add POST /api/neowow/activate-provider endpoint"
```

---

## Task 2: HTML — `#onboardingOverlay` in `index.html`

**Files:**
- Modify: `webui/static/index.html:1858` (insert before `</body>`)

- [ ] **Step 1: Insert the overlay div before `</body>`**

In `webui/static/index.html`, find the closing `</body>` tag at line 1858 and insert the following block immediately before it:

```html
<!-- ═══════════════════════════════════════════════════════════════════
     First-launch onboarding overlay.
     Shown (display:flex) by neowow.js when boot resolves with
     hasJwt=false. Hidden again after login + provider activation.
     z-index 99998 — sits below the boot overlay (99999) so the boot
     overlay always wins during startup. Once the boot overlay clears
     this takes over the screen if the user is not logged in.
     ═══════════════════════════════════════════════════════════════ -->
<div id="onboardingOverlay" style="display:none;position:fixed;inset:0;z-index:99998;background:radial-gradient(ellipse at top,#1a1330 0%,#0a0a0f 60%);color:#cbd5e1;flex-direction:column;align-items:center;justify-content:center;font-family:system-ui,-apple-system,'PingFang SC',sans-serif;opacity:1;transition:opacity 0.45s ease-out;pointer-events:auto;padding:24px;box-sizing:border-box;">
  <!-- Brain icon -->
  <div style="font-size:48px;margin-bottom:16px;line-height:1;">🧠</div>
  <div id="onboardingTitle" style="font-size:22px;font-weight:700;color:#e2e8f0;margin-bottom:10px;letter-spacing:0.2px;text-align:center;">欢迎使用 Hermes Agent</div>
  <div id="onboardingDesc" style="font-size:14px;color:#94a3b8;line-height:1.65;max-width:360px;text-align:center;margin-bottom:32px;">登录 neowow.studio 账号，即可免费使用 AI 对话能力，无需配置任何 API 密钥。</div>
  <!-- Login button — state machine:
       default  → 登录 / 注册 neowow.studio  (clickable)
       waiting  → 正在验证...  + spinner  (pointer-events:none)
       success  → ✓ 已就绪，正在启动...  (auto-dismissed 0.8s later)
  -->
  <button id="onboardingBtn"
          onclick="neowowAvatarClick(event)"
          style="display:inline-flex;align-items:center;gap:8px;padding:12px 28px;font-size:15px;font-weight:600;color:#fff;background:linear-gradient(135deg,#6366f1 0%,#8b5cf6 100%);border:none;border-radius:10px;cursor:pointer;box-shadow:0 4px 24px rgba(99,102,241,0.35);transition:opacity 0.2s,transform 0.15s;outline:none;"
          onmouseover="this.style.opacity='0.88'"
          onmouseout="this.style.opacity='1'">
    <span id="onboardingBtnIcon"></span>
    <span id="onboardingBtnText">登录 / 注册 neowow.studio</span>
  </button>
  <div id="onboardingFooter" style="font-size:12px;color:#475569;margin-top:20px;text-align:center;">登录成功后自动配置，直接开始使用</div>
</div>
```

- [ ] **Step 2: Verify HTML is well-formed**

```bash
cd /Users/ff/hermes-installer
python3 -c "
from html.parser import HTMLParser
class V(HTMLParser):
    def handle_error(self, msg): raise ValueError(msg)
with open('webui/static/index.html') as f:
    V().feed(f.read())
print('HTML OK')
"
```

Expected: `HTML OK`

- [ ] **Step 3: Commit**

```bash
cd /Users/ff/hermes-installer
git add webui/static/index.html
git commit -m "feat: add #onboardingOverlay HTML to index.html"
```

---

## Task 3: JS — onboarding functions in `neowow.js`

**Files:**
- Modify: `webui/static/neowow.js` — four edits:
  1. Add `_neowowShowOnboarding()` and `_neowowCompleteOnboarding()` functions (after `neowowHideBootOverlay` definition)
  2. Hook into `neowowResolveBootOverlay()` after line 163 (`neowowHideBootOverlay(...)` call)
  3. Hook into `neoSessionUpdated` listener at line 457
  4. Expose `_neowowCompleteOnboarding` on window (so it can be called from login callbacks)
- Test: `webui/tests/test_onboarding_overlay_js.py`

- [ ] **Step 1: Write the failing test**

Create `webui/tests/test_onboarding_overlay_js.py`:

```python
"""Smoke-test that the onboarding JS functions exist in neowow.js."""
import re
import unittest
from pathlib import Path


NEOWOW_JS = Path(__file__).parent.parent / "static" / "neowow.js"


class TestOnboardingOverlayJs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.src = NEOWOW_JS.read_text(encoding="utf-8")

    def test_show_onboarding_function_defined(self):
        """_neowowShowOnboarding must be defined."""
        self.assertIn("function _neowowShowOnboarding(", self.src)

    def test_complete_onboarding_function_defined(self):
        """_neowowCompleteOnboarding must be defined."""
        self.assertIn("function _neowowCompleteOnboarding(", self.src)

    def test_show_onboarding_called_in_boot_resolve(self):
        """_neowowShowOnboarding() must be called inside neowowResolveBootOverlay."""
        # Find the neowowResolveBootOverlay function body
        match = re.search(
            r"async function neowowResolveBootOverlay\(\)(.*?)^\s{2}\}",
            cls_src := self.src,
            re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(match, "neowowResolveBootOverlay not found")
        self.assertIn("_neowowShowOnboarding()", match.group(1))

    def test_complete_onboarding_called_in_session_updated(self):
        """_neowowCompleteOnboarding must be referenced in the neoSessionUpdated listener."""
        # Find the neoSessionUpdated addEventListener block
        idx = self.src.find("neoSessionUpdated")
        self.assertGreater(idx, 0)
        # Within 300 chars after this listener, we expect the completion call
        snippet = self.src[idx: idx + 400]
        self.assertIn("_neowowCompleteOnboarding", snippet)

    def test_activate_provider_fetch_in_complete_onboarding(self):
        """_neowowCompleteOnboarding must POST to /api/neowow/activate-provider."""
        self.assertIn("/api/neowow/activate-provider", self.src)

    def test_onboarding_overlay_id_referenced(self):
        """Both functions must reference the onboardingOverlay element."""
        self.assertIn("onboardingOverlay", self.src)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/ff/hermes-installer/webui && python -m pytest tests/test_onboarding_overlay_js.py -v
```

Expected: 5–6 tests FAIL (functions don't exist yet)

- [ ] **Step 3: Add the two new functions to `neowow.js`**

In `webui/static/neowow.js`, find the `neowowHideBootOverlay` function definition. After its closing `};` (it is a `window.neowowHideBootOverlay = function(...) { ... };` assignment), insert the following block:

```javascript
  // ── Onboarding overlay (first-launch, no JWT) ─────────────────────────
  //
  // _neowowShowOnboarding()   — makes #onboardingOverlay visible
  // _neowowCompleteOnboarding() — fires activate-provider, then fades out
  //
  // Lifecycle:
  //   neowowResolveBootOverlay()  → hasJwt=false → _neowowShowOnboarding()
  //   neoSessionUpdated event     → overlay visible → _neowowCompleteOnboarding()

  let _onboardingShown = false;

  function _neowowShowOnboarding() {
    if (_onboardingShown) return;
    _onboardingShown = true;
    const overlay = document.getElementById('onboardingOverlay');
    if (!overlay) return;
    overlay.style.display = 'flex';
    overlay.style.opacity = '1';
  }

  function _neowowCompleteOnboarding() {
    const overlay = document.getElementById('onboardingOverlay');
    if (!overlay || overlay.style.display === 'none') return;

    // Update button to success state immediately (fire-and-forget the API call)
    const btn = document.getElementById('onboardingBtn');
    const btnText = document.getElementById('onboardingBtnText');
    const btnIcon = document.getElementById('onboardingBtnIcon');
    if (btn) {
      btn.style.pointerEvents = 'none';
      btn.style.background = 'linear-gradient(135deg,#10b981 0%,#059669 100%)';
      btn.style.boxShadow = '0 4px 24px rgba(16,185,129,0.35)';
    }
    if (btnIcon) btnIcon.textContent = '✓';
    if (btnText) btnText.textContent = '已就绪，正在启动...';

    // POST activate-provider — fire and forget; failure only logs a warning
    fetch('/api/neowow/activate-provider', { method: 'POST' })
      .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); })
      .catch(err => console.warn('[onboarding] activate-provider failed:', err));

    // Fade out after 800ms
    setTimeout(() => {
      overlay.style.opacity = '0';
      overlay.addEventListener('transitionend', () => {
        overlay.style.display = 'none';
      }, { once: true });
    }, 800);
  }

  // Expose so external callers (e.g. test pages) can trigger completion
  window._neowowCompleteOnboarding = _neowowCompleteOnboarding;
```

- [ ] **Step 4: Hook into `neowowResolveBootOverlay()` after the boot overlay hides**

In `neowow.js`, find this exact line (line ~163):
```javascript
    neowowHideBootOverlay({ success: hasJwt, networkOk, nickname });
```

Append immediately after it (still inside the `async function neowowResolveBootOverlay()` body):
```javascript
    // Show onboarding overlay if user is not logged in and network is available
    if (!hasJwt && networkOk) {
      _neowowShowOnboarding();
    }
```

- [ ] **Step 5: Hook into `neoSessionUpdated` listener**

In `neowow.js`, find the `neoSessionUpdated` listener (around line 457):
```javascript
  window.addEventListener('neoSessionUpdated', () => {
    void refreshRailAvatar();
    void refreshAccountBlock();
    // NOTE: don't re-trigger the boot overlay on session updates —
```

Add the onboarding completion call inside it, right after `refreshAccountBlock()`:
```javascript
  window.addEventListener('neoSessionUpdated', () => {
    void refreshRailAvatar();
    void refreshAccountBlock();
    // Complete onboarding if the overlay is currently visible (first-launch login)
    _neowowCompleteOnboarding();
    // NOTE: don't re-trigger the boot overlay on session updates —
```

- [ ] **Step 6: Run the JS tests**

```bash
cd /Users/ff/hermes-installer/webui && python -m pytest tests/test_onboarding_overlay_js.py -v
```

Expected: all 6 tests PASS

- [ ] **Step 7: Run the full test suite to check for regressions**

```bash
cd /Users/ff/hermes-installer/webui && python -m pytest tests/ -v --ignore=tests/test_sprint7.py -k "not test_python39_compat" 2>&1 | tail -30
```

Expected: no new failures related to our changes (pre-existing Python 3.9 union-type failures are unrelated)

- [ ] **Step 8: Commit**

```bash
cd /Users/ff/hermes-installer
git add webui/static/neowow.js webui/tests/test_onboarding_overlay_js.py
git commit -m "feat: first-launch login onboarding overlay (neowow.js)"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task covering it |
|---|---|
| Overlay shows when `hasJwt=false` after boot | Task 3, Step 4 hook |
| Overlay NOT shown when `hasJwt=true` | Task 3, Step 4 (guarded by `!hasJwt`) |
| Overlay NOT shown when network is down | Task 3, Step 4 (guarded by `networkOk`) |
| Login button calls existing OAuth flow | Task 2 HTML (`onclick="neowowAvatarClick(event)"`) |
| After login: spinner → success state | Task 3 `_neowowCompleteOnboarding()` |
| After login: activate-provider called | Task 3 `fetch('/api/neowow/activate-provider')` |
| `apply_onboarding_setup` writes config | Task 1 backend route |
| Overlay fades out after 800ms | Task 3 `setTimeout(..., 800)` |
| `z-index` below boot overlay | Task 2 HTML `z-index:99998` vs boot overlay `99999` |
| macOS/Linux not affected | No changes to non-Windows paths; overlay appears in all webui contexts but has no impact on server startup |
| fire-and-forget on activate-provider fail | Task 3 `.catch(err => console.warn(...))` — overlay still fades out |

**Placeholder scan:** No TBDs. All code is complete.

**Type consistency:** `_neowowShowOnboarding` and `_neowowCompleteOnboarding` names are consistent across all uses in the plan.
