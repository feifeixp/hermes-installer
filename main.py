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
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

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
def _run_macos(url: str):
    try:
        import webview
        log.info("pywebview %s gui=cocoa", getattr(webview, "__version__", "?"))
        webview.create_window(
            "Hermes Agent 安装向导", url,
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
# Windows — browser + uvicorn on MAIN thread
# ══════════════════════════════════════════════════════════════════════════
def _run_windows(port: int):
    url = f"http://127.0.0.1:{port}"

    # Open browser from a side thread after server is ready
    def _open():
        if _wait_for_server(port, timeout=30.0):
            import webbrowser
            webbrowser.open(url)
            log.info("browser opened: %s", url)
        else:
            _alert("Hermes Installer", f"服务器未能在 30 秒内启动。\n日志：{_LOG_PATH}")

    threading.Thread(target=_open, daemon=True).start()

    print()
    print("=" * 54)
    print("  ⚡ Hermes Agent 安装向导  (Windows)")
    print("=" * 54)
    print(f"  地址: {url}")
    print("  浏览器正在打开，请稍候...")
    print("  关闭此窗口即可退出。")
    print("=" * 54)

    log.info("uvicorn starting on main thread port=%d", port)
    # Run on main thread — blocks until server is stopped.
    # Forcing h11 + asyncio avoids any C-extension or subprocess spawning.
    uvicorn.run(
        fastapi_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        reload=False,
        loop="asyncio",   # pure Python, no uvloop subprocess
        http="h11",       # pure Python, no httptools subprocess
    )


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════
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

    if sys.platform == "darwin":
        # macOS: server in daemon thread, webview on main thread
        t = threading.Thread(
            target=lambda: uvicorn.run(
                fastapi_app, host="127.0.0.1", port=PORT,
                log_level="warning", reload=False,
                loop="asyncio", http="h11"),
            daemon=True)
        t.start()
        if not _wait_for_server(PORT, timeout=20.0):
            _alert("Hermes Installer", f"服务器启动超时。\n日志：{_LOG_PATH}")
            sys.exit(1)
        _run_macos(f"http://127.0.0.1:{PORT}")
    else:
        _run_windows(PORT)


if __name__ == "__main__":
    main()
