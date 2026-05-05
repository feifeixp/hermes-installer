"""
Hermes Agent Installer — cross-platform entry point.
- macOS  : pywebview cocoa (WKWebView native window)
- Windows: system browser + uvicorn on main thread
"""
# ── CHILD PROCESS GUARD ────────────────────────────────────────────────────
# This must be the VERY FIRST executable code.
# When any library (uvicorn, aiohttp, anyio …) spawns a subprocess on
# Windows, that subprocess re-runs this exe from scratch.
# Setting HERMES_MAIN before anything else, and exiting immediately when
# it's already set, guarantees child processes never run the GUI/server.
import os
import sys

if os.environ.get("_HERMES_MAIN") == "1":
    # We are a child process — do nothing and exit cleanly.
    sys.exit(0)

os.environ["_HERMES_MAIN"] = "1"   # mark: subprocesses must exit

# ── Now safe to import everything ─────────────────────────────────────────
import multiprocessing
multiprocessing.freeze_support()

import threading
import time
import socket
import logging
from pathlib import Path

# ── Log file ────────────────────────────────────────────────────────────────
_LOG_PATH = Path(os.environ.get("TEMP", os.environ.get("TMPDIR", "/tmp"))) / "hermes-installer.log"
logging.basicConfig(
    filename=str(_LOG_PATH),
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("hermes")
log.info("=== Hermes Installer starting === pid=%s py=%s platform=%s frozen=%s",
         os.getpid(), sys.version.split()[0], sys.platform,
         getattr(sys, "frozen", False))


def _alert(title: str, msg: str):
    log.error("ALERT %s | %s", title, msg)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk(); root.withdraw()
        messagebox.showerror(title, msg)
        root.destroy()
    except Exception:
        print(f"[ERROR] {title}: {msg}", file=sys.stderr)


# ── Bundle path ────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["HERMES_INSTALLER_BASE_DIR"] = str(BASE_DIR)
log.info("BASE_DIR=%s", BASE_DIR)

# ── Windows event-loop policy ──────────────────────────────────────────────
# Python 3.8+ defaults to ProactorEventLoop on Windows, which supports both
# uvicorn (h11/asyncio mode) AND asyncio.create_subprocess_exec().
# DO NOT switch to WindowsSelectorEventLoopPolicy — SelectorEventLoop does
# NOT support subprocess creation and will raise NotImplementedError.

# ── Import FastAPI app ─────────────────────────────────────────────────────
try:
    import uvicorn
    from app import app as fastapi_app
    log.info("app imported OK")
except Exception as _e:
    _alert("Hermes Installer — 启动失败",
           f"无法加载应用：{_e}\nBASE_DIR={BASE_DIR}\n日志：{_LOG_PATH}")
    sys.exit(1)

PORT = 7891


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.3):
                return True
        except OSError:
            time.sleep(0.15)
    return False


# ══════════════════════════════════════════════════════════════════════════
# macOS — pywebview cocoa
# ══════════════════════════════════════════════════════════════════════════
def _run_macos(title: str, url: str):
    try:
        import webview
        log.info("pywebview %s gui=cocoa", getattr(webview, "__version__", "?"))
        webview.create_window(
            title, url,
            width=1080, height=760,
            resizable=True, min_size=(860, 620),
            background_color="#0f0f1a",
        )
        webview.start(gui="cocoa", debug=False)
    except Exception as exc:
        log.exception("pywebview failed: %s", exc)
        import webbrowser
        webbrowser.open(url)
        _alert("Hermes Installer", f"原生窗口不可用，已在浏览器中打开。\n{url}\n\n错误：{exc}")


# ══════════════════════════════════════════════════════════════════════════
# Windows — pywebview edgechromium (native window)
# Edge WebView2 spawns native msedgewebview2.exe processes — those are
# NOT Python processes and are NOT affected by our _HERMES_MAIN guard.
# ══════════════════════════════════════════════════════════════════════════
def _run_windows(title: str, url: str):
    try:
        import webview
        log.info("pywebview %s gui=edgechromium", getattr(webview, "__version__", "?"))
        webview.create_window(
            title, url,
            width=1080, height=760,
            resizable=True, min_size=(860, 620),
            background_color="#0f0f1a",
        )
        webview.start(gui="edgechromium", debug=False)
        log.info("webview closed")
    except Exception as exc:
        log.exception("pywebview failed: %s", exc)
        # Fallback: open system browser
        try:
            import webbrowser
            webbrowser.open(url)
            _alert("Hermes Installer",
                   f"原生窗口不可用（{exc}），\n已在浏览器中打开：{url}\n\n"
                   f"关闭浏览器标签后请手动关闭控制台窗口退出。")
        except Exception as exc2:
            _alert("Hermes Installer — 错误",
                   f"WebView 和浏览器均无法打开。\n{exc}\n{exc2}\n"
                   f"请手动访问：{url}")


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════

def _find_agent_python() -> str | None:
    """Find a usable Python interpreter from the hermes-agent venv.
    Returns the path (str) if found, None otherwise."""
    if sys.platform == "win32":
        candidates = [
            Path.home() / ".hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe",
            Path.home() / ".hermes" / "hermes-agent" / ".venv" / "Scripts" / "python.exe",
        ]
    else:
        candidates = [
            Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python",
            Path.home() / ".hermes" / "hermes-agent" / ".venv" / "bin" / "python",
        ]
    for p in candidates:
        if p.exists():
            return str(p)

    # When frozen, sys.executable is the app binary — useless as a Python.
    # Try common system Python names as a last resort.
    if getattr(sys, "frozen", False):
        for name in ("python3.13", "python3.12", "python3.11", "python3.10", "python3", "python"):
            import shutil
            found = shutil.which(name)
            if found:
                log.info("_find_agent_python: fallback system Python → %s", found)
                return found
    return None


def main():
    global PORT

    # If another instance already owns the port, just open its browser
    if _port_in_use(PORT):
        log.info("Port %d already in use — another instance running", PORT)
        try:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{PORT}")
        except Exception:
            pass
        return

    # ── Step 1: start app.py (FastAPI installer API) on port 7891 ────────
    t = threading.Thread(
        target=lambda: uvicorn.run(
            fastapi_app, host="127.0.0.1", port=PORT,
            log_level="warning", reload=False,
            loop="asyncio", http="h11"),
        daemon=True)
    t.start()

    log.info("waiting for server on port %d …", PORT)
    if not _wait_for_server(PORT, timeout=20.0):
        _alert("Hermes Installer", f"服务器启动超时。\n日志：{_LOG_PATH}")
        sys.exit(1)
    log.info("server ready")

    # ── Step 2: is setup already done? ──────────────────────────────────
    setup_complete_file = Path.home() / ".hermes" / ".setup_complete"
    webui_started = False
    webui_port = 0
    url = f"http://127.0.0.1:{PORT}"
    title = "Hermes Agent 安装向导"

    if setup_complete_file.exists():
        agent_py = _find_agent_python()
        if agent_py:
            webui_port = _find_free_port()
            os.environ["HERMES_WEBUI_PORT"] = str(webui_port)
            os.environ["HERMES_WEBUI_HOST"] = "127.0.0.1"

            webui_dir = BASE_DIR / "webui"
            import subprocess as _sp
            env = os.environ.copy()
            env["HERMES_WEBUI_PORT"] = str(webui_port)
            env["HERMES_WEBUI_HOST"] = "127.0.0.1"

            log.info("Starting WebUI: %s %s", agent_py, webui_dir / "server.py")
            try:
                _sp.Popen(
                    [agent_py, str(webui_dir / "server.py")],
                    env=env, cwd=str(webui_dir),
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                )
                webui_started = True
            except Exception as e:
                log.exception("WebUI Popen failed: %s", e)
        else:
            log.warning("setup complete but hermes-agent venv not found — "
                        "showing installer, /chat will fall back")

        if webui_started:
            log.info("waiting for WebUI server on port %d …", webui_port)
            if _wait_for_server(webui_port, timeout=20.0):
                url = f"http://127.0.0.1:{webui_port}/"
                title = "Hermes"
            else:
                log.warning("WebUI server didn't start in time — falling back to installer")

    if sys.platform == "darwin":
        _run_macos(title, url)
    else:
        _run_windows(title, url)


if __name__ == "__main__":
    main()
