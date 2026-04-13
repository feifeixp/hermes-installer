"""
Hermes Agent Installer — cross-platform entry point.
- macOS  : pywebview cocoa (WKWebView native window)
- Windows: system browser; uvicorn runs on the MAIN thread (no daemon threads,
           no multiprocessing, no spawn loops)
"""
# Must be called before anything else on Windows (PyInstaller + multiprocessing)
import multiprocessing
multiprocessing.freeze_support()

import sys
import os
import threading
import time
import socket
import logging
from pathlib import Path

# ── Logging ────────────────────────────────────────────────────────────────
_LOG_PATH = Path(os.environ.get("TEMP", os.environ.get("TMPDIR", "/tmp"))) / "hermes-installer.log"
logging.basicConfig(
    filename=str(_LOG_PATH),
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("hermes")
log.info("=== starting === py=%s platform=%s frozen=%s pid=%s",
         sys.version.split()[0], sys.platform,
         getattr(sys, "frozen", False), os.getpid())


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


# ── Single-instance lock (prevents multiple exe copies running at once) ────
def _acquire_lock(port: int) -> bool:
    """Bind a UDP socket as a mutex. Returns False if another instance owns it."""
    try:
        _lock_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _lock_sock.bind(("127.0.0.1", port + 1))   # e.g. 7892
        # Store on module level so it isn't GC'd
        _acquire_lock._sock = _lock_sock
        return True
    except OSError:
        return False


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

# ── Import app ─────────────────────────────────────────────────────────────
try:
    import uvicorn
    from app import app as fastapi_app
    log.info("app imported OK")
except Exception as _e:
    _alert("Hermes Installer — 启动失败",
           f"无法加载应用：{_e}\nBASE_DIR={BASE_DIR}\n日志：{_LOG_PATH}")
    sys.exit(1)

PORT = 7891


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ══════════════════════════════════════════════════════════════════════════
# macOS — pywebview native window (cocoa, no multiprocessing issue)
# ══════════════════════════════════════════════════════════════════════════
def _run_macos(url: str):
    try:
        import webview
        log.info("pywebview %s  gui=cocoa", getattr(webview, "__version__", "?"))
        window = webview.create_window(
            title="Hermes Agent 安装向导",
            url=url, width=1080, height=760,
            resizable=True, min_size=(860, 620),
            background_color="#0f0f1a",
        )
        webview.start(gui="cocoa", debug=False)
    except Exception as exc:
        log.exception("pywebview failed: %s", exc)
        import webbrowser
        webbrowser.open(url)
        _alert("Hermes Installer",
               f"原生窗口不可用（{exc}），已在浏览器中打开。\n{url}")


# ══════════════════════════════════════════════════════════════════════════
# Windows — uvicorn on MAIN thread; browser opened via timer
# ══════════════════════════════════════════════════════════════════════════
def _run_windows(port: int):
    url = f"http://127.0.0.1:{port}"

    # Open browser 2 s after uvicorn is ready (timer fires on a side thread)
    def _open_browser():
        time.sleep(2)
        try:
            import webbrowser
            webbrowser.open(url)
            log.info("browser opened: %s", url)
        except Exception as exc:
            log.exception("webbrowser.open failed: %s", exc)

    threading.Thread(target=_open_browser, daemon=True).start()

    print()
    print("=" * 54)
    print("  ⚡ Hermes Agent 安装向导")
    print("=" * 54)
    print(f"  服务器地址：{url}")
    print()
    print("  浏览器正在打开，请稍候...")
    print("  如未自动打开，请手动访问上方地址。")
    print()
    print("  关闭此窗口即可退出程序。")
    print("=" * 54)
    print()

    log.info("uvicorn starting on main thread, port=%d", port)
    # Run uvicorn on the MAIN thread (blocking call — no daemon threads,
    # no subprocesses, no multiprocessing spawn loop possible)
    uvicorn.run(
        fastapi_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        reload=False,
    )


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════
def main():
    global PORT

    # ── Single-instance check ──────────────────────────────────────────────
    if not _acquire_lock(PORT):
        log.warning("Another instance is already running on port %d", PORT)
        # Just open the browser to the existing instance and exit
        try:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{PORT}")
        except Exception:
            pass
        print(f"Hermes Installer 已在运行，请访问 http://127.0.0.1:{PORT}")
        return

    # ── Check if port is free; if not, find another ────────────────────────
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", PORT)) == 0:
            PORT = _find_free_port()
            log.info("port 7891 busy → %d", PORT)

    log.info("Using port %d", PORT)

    if sys.platform == "darwin":
        # macOS: start uvicorn in background thread, webview on main thread
        server_thread = threading.Thread(
            target=lambda: uvicorn.run(
                fastapi_app, host="127.0.0.1", port=PORT,
                log_level="warning", reload=False),
            daemon=True,
        )
        server_thread.start()
        # Wait for server to be ready
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", PORT), 0.3):
                    break
            except OSError:
                time.sleep(0.15)
        _run_macos(f"http://127.0.0.1:{PORT}")
    else:
        # Windows/Linux: uvicorn on MAIN thread (browser opened via timer)
        _run_windows(PORT)


if __name__ == "__main__":
    main()
