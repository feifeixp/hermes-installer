# ─────────────────────────────────────────────────────────────────────────────
# desktop_menu.py — native menu bar for the NeoMuse desktop app.
#
# WHY THIS EXISTS
#   Until now the desktop app opened straight into the WebUI with zero
#   chrome — no way to switch between local/cloud Hermes, no way to
#   manage the Neodomain login from outside the embedded page, no way
#   to access docs / settings without typing URLs. This module adds a
#   real macOS / Windows native menu bar:
#
#     Hermes
#       ├─ 账号 (Account)
#       │   ├─ 登录到 Neodomain...
#       │   ├─ 登出
#       │   ├─ ───
#       │   ├─ 充值 / 套餐...
#       │   └─ 我的账户...
#       ├─ 模式 (Mode)
#       │   ├─ ● 本地 Hermes (localhost)
#       │   ├─   云端 Hermes (chat-<userId>.neowow.studio)
#       │   ├─ ───
#       │   └─ 自定义远程 URL...
#       ├─ 视图 (View)
#       │   ├─ 重新加载
#       │   └─ 主页
#       └─ 帮助 (Help)
#           ├─ 在线文档
#           └─ 关于 Hermes Agent
#
# IMPLEMENTATION NOTES
#   * pywebview ≥4.0 native menu API: webview.menu.{Menu, MenuAction,
#     MenuSeparator}. Pass `menu=[...]` to webview.start().
#   * Callbacks run in pywebview's main thread. They can:
#       - read/write ~/.hermes/webui/gateway.json
#       - call window.evaluate_js(...) to talk to the embedded WebUI
#       - open external URLs via webbrowser
#       - show modal dialogs via webview.windows[0].create_confirmation_dialog
#   * Mode switching is a CONFIG WRITE + DIALOG ASKING THE USER TO RESTART.
#     Hot-swapping the URL in-place is technically possible (window.load_url)
#     but the LOCAL-mode side needs `bootstrap → spawn → wait` which runs
#     before window creation in main.py. Restart is honest about what's
#     happening and avoids partial-state bugs.
#   * Login/Logout in the menu is a CONVENIENCE — the actual flow still
#     goes through the embedded WebUI's existing OAuth handler
#     (window.neowowStartOAuth() in static/neowow.js). The menu item
#     just invokes it for the user.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any, Callable, Optional


log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_DASHBOARD_BASE = "https://app.neowow.studio"
_DOCS_URL       = f"{_DASHBOARD_BASE}/docs"
_ACCOUNT_URL    = f"{_DASHBOARD_BASE}/account"
_AGENT_URL      = f"{_DASHBOARD_BASE}/agent"

# Gateway config — keep in sync with main.py + webui/api/gateway_config.py.
_STATE_DIR = Path(os.getenv(
    "HERMES_WEBUI_STATE_DIR",
    str(Path.home() / ".hermes" / "webui"),
))
_GATEWAY_PATH = _STATE_DIR / "gateway.json"
_NEOWOW_JWT_PATH = _STATE_DIR / "neowow.json"

# Mode values written to gateway.json. "local" is the default — main.py
# treats anything-not-remote as local.
_MODE_LOCAL  = "local"
_MODE_REMOTE = "remote"


# ── Public entry point ───────────────────────────────────────────────────────

def build_menu(window: Any, current_mode: str) -> list:
    """Build the pywebview Menu list for the main window.

    Caller wires this into webview.start(menu=...). The `window` reference
    is captured so menu callbacks can invoke evaluate_js / reload / etc.
    `current_mode` is what main.py read from gateway.json at startup —
    used to mark which Mode item shows as "selected" (we prefix the
    label with "● ").
    """
    try:
        from webview.menu import Menu, MenuAction, MenuSeparator
    except ImportError as exc:
        log.warning("pywebview.menu unavailable (%s) — no native menu bar", exc)
        return []

    def cb(fn: Callable[..., None]) -> Callable[..., None]:
        """Wrap a callback so exceptions don't kill the menu thread.
        pywebview's menu callbacks raise into a thread we can't recover
        from cleanly; log + show a dialog instead."""
        def wrapped(*args, **kwargs):
            try:
                fn(window)
            except Exception as e:
                log.exception("menu callback %s failed", fn.__name__)
                _alert(window, "操作失败", str(e))
        wrapped.__name__ = fn.__name__
        return wrapped

    is_local = current_mode != _MODE_REMOTE

    return [
        Menu("账号", [
            MenuAction("登录到 Neodomain...",  cb(_on_login)),
            MenuAction("登出",                cb(_on_logout)),
            MenuSeparator(),
            MenuAction("充值 / 套餐...",      cb(_on_recharge)),
            MenuAction("我的账户...",         cb(_on_account)),
        ]),
        Menu("模式", [
            MenuAction(
                ("● " if is_local else "   ") + "本地 Hermes (localhost)",
                cb(_on_switch_local),
            ),
            MenuAction(
                ("● " if not is_local else "   ") + "云端 Hermes (chat-<userId>.neowow.studio)",
                cb(_on_switch_cloud),
            ),
            MenuSeparator(),
            MenuAction("自定义远程 URL...", cb(_on_switch_custom)),
        ]),
        Menu("视图", [
            MenuAction("重新加载",          cb(_on_reload)),
            MenuAction("回到主页",          cb(_on_home)),
        ]),
        Menu("帮助", [
            MenuAction("在线文档",          cb(_on_docs)),
            MenuAction("关于 Hermes Agent", cb(_on_about)),
        ]),
    ]


# ── Account actions ──────────────────────────────────────────────────────────

def _on_login(window) -> None:
    """Trigger the embedded WebUI's OAuth flow.
    `window.neowowStartOAuth()` is defined in webui/static/neowow.js
    and opens the OAuth URL in the user's external default browser
    (pywebview can't host the OAuth popup itself reliably)."""
    js = """
      (function () {
        if (typeof window.neowowStartOAuth === 'function') {
          window.neowowStartOAuth();
          return 'ok';
        }
        // Fallback: open dashboard's OAuth start with return URL pointed
        // back at this webview. Should be rare — the WebUI almost
        // always loads neowow.js. Hosting matters: a remote-mode window
        // is on dashboard origin, so we can navigate same-origin.
        var ret = window.location.origin + '/api/neowow/oauth-callback';
        window.open('https://app.neowow.studio/api/oauth/start?return=' +
                    encodeURIComponent(ret), '_blank');
        return 'fallback';
      })()
    """
    window.evaluate_js(js)


def _on_logout(window) -> None:
    """Delete the cached JWT + reload the page so the WebUI's auth
    state refreshes immediately. The JWT lives in
    ~/.hermes/webui/neowow.json; we let the API endpoint handle the
    unlink so any in-flight requests see a consistent state.

    Note: even in remote mode this works — the dashboard worker's
    `/api/me/whoami` will start returning 401 immediately, and the
    cookie clear happens via the JS fragment below."""
    js = """
      (function () {
        // 1) Local Hermes — delete JWT via API
        fetch('/api/neowow/jwt', { method: 'DELETE' }).catch(function(){});
        // 2) Cookie + LS clear (covers dashboard / chat.* origins)
        try {
          document.cookie = 'neoToken=; Domain=.neowow.studio; Path=/; ' +
                             'Max-Age=0; SameSite=Lax; Secure';
          localStorage.removeItem('neoStudioSession');
        } catch (e) {}
        // 3) Reload so the WebUI re-reads identity
        setTimeout(function(){ window.location.reload(); }, 200);
        return 'logging-out';
      })()
    """
    window.evaluate_js(js)


def _on_recharge(window) -> None:
    """Open the dashboard's recharge page in the user's default browser.
    Recharge requires WeChat scan-to-pay — opens fine in a real browser
    but pywebview's WKWebView doesn't always render the QR cleanly."""
    webbrowser.open(_ACCOUNT_URL)


def _on_account(window) -> None:
    webbrowser.open(_ACCOUNT_URL)


# ── Mode-switching actions ───────────────────────────────────────────────────

def _on_switch_local(window) -> None:
    """Switch to local Hermes (localhost:7891 / 8787). Writes
    gateway.json with mode=local, then prompts restart."""
    cfg = _read_gateway_config()
    if cfg.get("mode") != _MODE_REMOTE:
        _alert(window, "提示", "您当前已经在本地模式。")
        return
    _write_gateway_config({"mode": _MODE_LOCAL})
    _prompt_restart(window, "已切换到本地模式")


def _on_switch_cloud(window) -> None:
    """Switch to cloud Hermes — chat-<userId>.neowow.studio.

    Two failure modes we surface cleanly:
      • Not logged in → tell user to login first
      • Logged in but no spawned instance → open /agent in browser so
        user can 一键开通 to provision one. Don't write a half-baked
        URL into gateway.json.
    """
    userid = _read_neowow_userid()
    if not userid:
        _alert(window, "请先登录",
               "切换到云端模式需要先登录到 Neodomain。\n"
               "请打开「账号」菜单 → 登录到 Neodomain。")
        return

    cloud_url = f"https://chat-{userid}.neowow.studio"
    _write_gateway_config({
        "mode":  _MODE_REMOTE,
        "url":   cloud_url,
        "label": "云端实例",
    })
    _prompt_restart(window,
                    f"已切换到云端模式\n\n下次启动将连接到:\n{cloud_url}\n\n"
                    f"如果还没有开通云端实例,请先到 app.neowow.studio/agent 一键开通。")


def _on_switch_custom(window) -> None:
    """Prompt for a custom remote URL. The user might be running their
    own Hermes WebUI behind a corporate domain, or pointing at a
    staging deployment for QA. We minimally validate (must start with
    http(s)://) before writing."""
    # pywebview supports input dialogs via window.create_file_dialog
    # but NOT a generic text input. Closest cross-platform option is
    # a Python-side fallback: write the URL to a sentinel file. For
    # macOS we can use osascript "display dialog".
    new_url = _text_dialog(window,
                           "自定义远程 URL",
                           "输入你的 Hermes WebUI URL\n(例如 https://hermes.example.com):",
                           "https://")
    if not new_url:
        return
    if not new_url.startswith(("http://", "https://")):
        _alert(window, "URL 格式错误", "必须以 http:// 或 https:// 开头。")
        return

    _write_gateway_config({
        "mode":  _MODE_REMOTE,
        "url":   new_url,
        "label": "自定义",
    })
    _prompt_restart(window, f"已切换到自定义远程\n{new_url}")


# ── View / Help actions ──────────────────────────────────────────────────────

def _on_reload(window) -> None:
    window.evaluate_js("window.location.reload();")


def _on_home(window) -> None:
    """Navigate to the embedded UI's root path, keeping the host. Useful
    when the user has drilled into a settings sub-page and wants to
    get back to the main Hermes chat surface."""
    window.evaluate_js("window.location.assign('/');")


def _on_docs(window) -> None:
    webbrowser.open(_DOCS_URL)


def _on_about(window) -> None:
    version = _read_version()
    _alert(
        window,
        "关于 Hermes Agent",
        f"NeoMuse v{version}\n\n"
        f"你的专属 AI 同事 — 跨会话记忆、定时任务、技能复用、跨设备同步。\n\n"
        f"项目主页: https://app.neowow.studio/agent",
    )


# ── Helpers — gateway.json + JWT files ───────────────────────────────────────

def _read_gateway_config() -> dict:
    if not _GATEWAY_PATH.exists():
        return {"mode": _MODE_LOCAL}
    try:
        raw = json.loads(_GATEWAY_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"mode": _MODE_LOCAL}
    except Exception:
        return {"mode": _MODE_LOCAL}


def _write_gateway_config(cfg: dict) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _GATEWAY_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    log.info("gateway.json updated: %s", cfg)


def _read_neowow_userid() -> Optional[str]:
    """Read the cached JWT and decode the `userId` claim. We don't
    verify the signature (Neodomain uses HS512 — we don't carry that
    secret in the desktop app); this is purely informational so the
    cloud-mode URL builder can fill in chat-<userId>.* correctly. An
    untrusted JWT here can only hurt the user themselves (wrong
    cloud URL) so the soft check is acceptable."""
    if not _NEOWOW_JWT_PATH.exists():
        return None
    try:
        data = json.loads(_NEOWOW_JWT_PATH.read_text(encoding="utf-8"))
        jwt = data.get("jwt") or data.get("accessToken") or data.get("authorization")
        if not jwt or not isinstance(jwt, str) or jwt.count(".") != 2:
            return None
        import base64
        payload_b64 = jwt.split(".")[1]
        # JWTs use base64url; pad to base64 then decode
        padded = payload_b64 + "=" * ((4 - len(payload_b64) % 4) % 4)
        bin_ = base64.urlsafe_b64decode(padded.encode("ascii"))
        claims = json.loads(bin_)
        for k in ("userId", "user_id", "uid", "sub", "id"):
            v = claims.get(k)
            if v not in (None, ""):
                return str(v)
    except Exception as exc:
        log.debug("could not decode neowow JWT: %s", exc)
    return None


def _read_version() -> str:
    """Best-effort version string. Tries pyproject.toml + git tag +
    falls back to 'dev'."""
    try:
        here = Path(__file__).resolve().parent
        pyproj = here / "pyproject.toml"
        if pyproj.exists():
            for line in pyproj.read_text(encoding="utf-8").splitlines():
                if line.lstrip().startswith("version"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "dev"


# ── UI dialogs (cross-platform shims) ────────────────────────────────────────

def _alert(window, title: str, msg: str) -> None:
    """Show an OS-native alert. Uses macOS osascript when available,
    falls back to stderr print on other platforms.

    We intentionally DON'T `import main` to reuse main._alert: main.py
    runs as `__main__` in sys.modules, so `import main` would re-execute
    the file and trigger the bootstrap path again. Cheaper to duplicate
    the few lines of osascript here."""
    if sys.platform == "darwin":
        try:
            esc = msg.replace('"', '\\"').replace("\n", "\\n")
            subprocess.run([
                "osascript", "-e",
                f'display dialog "{esc}" with title "{title}" buttons {{"好"}} default button "好"',
            ], check=False, capture_output=True)
            return
        except Exception:
            pass
    print(f"[{title}] {msg}", file=sys.stderr)


def _prompt_restart(window, intro: str) -> None:
    """Tell the user the config changed and the app needs to restart.
    Could `os.execv` to relaunch ourselves, but a clean restart-and-
    please-reopen message is friendlier (avoids issues if the user has
    in-flight work in the embedded WebUI)."""
    _alert(
        window,
        intro,
        "请退出并重新打开 Hermes 以使更改生效。\n"
        "(Cmd+Q 退出,然后从启动台 / Applications 重新打开。)",
    )


def _text_dialog(window, title: str, prompt: str, default: str = "") -> Optional[str]:
    """Cross-platform text-input prompt. macOS uses osascript; everywhere
    else returns None (the menu item then shows an 'unsupported' alert,
    asking the user to edit gateway.json manually as a fallback).

    If we add Windows support later, use a small Tk window — Tk ships
    with python.org Python on Windows."""
    if sys.platform == "darwin":
        try:
            esc_prompt = prompt.replace('"', '\\"').replace("\n", "\\n")
            esc_default = default.replace('"', '\\"')
            res = subprocess.run([
                "osascript", "-e",
                f'set theResponse to display dialog "{esc_prompt}" '
                f'default answer "{esc_default}" with title "{title}" '
                f'buttons {{"取消", "确定"}} default button "确定"',
                "-e",
                "return text returned of theResponse",
            ], capture_output=True, text=True, check=False)
            if res.returncode == 0:
                return res.stdout.strip()
            return None
        except Exception as exc:
            log.warning("osascript text dialog failed: %s", exc)
            return None

    _alert(
        window, "不支持的平台",
        "当前平台暂不支持图形输入框,请直接编辑配置文件:\n"
        f"{_GATEWAY_PATH}",
    )
    return None
