"""
Hermes Agent Installer — cross-platform entry point.
- macOS : pywebview (WKWebView / cocoa) — native window
- Windows: system browser — avoids Edge WebView2 multiprocessing loop in
           PyInstaller onefile; server stays alive via console window
"""
# freeze_support() MUST be the very first call — required for any
# PyInstaller + multiprocessing usage on Windows.
import multiprocessing
multiprocessing.freeze_support()

import sys
import os
import threading
import time
import socket
import logging
from pathlib import Path

# ── Log file ───────────────────────────────────────────────────────────────
_LOG_PATH = Path(os.environ.get("TEMP", os.environ.get("TMPDIR", "/tmp"))) / "hermes-installer.log"
logging.basicConfig(
    filename=str(_LOG_PATH),
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("hermes")
log.info("=== Hermes Installer starting === Python %s  platform=%s  frozen=%s",
         sys.version, sys.platform, getattr(sys, "frozen", False))


def _alert(title: str, msg: str):
    """Error dialog that works without a running webview."""
    log.error("%s | %s", title, msg)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, msg)
        root.destroy()
    except Exception:
        print(f"[ERROR] {title}: {msg}", file=sys.stderr)


# ── Bundle path fix ────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["HERMES_INSTALLER_BASE_DIR"] = str(BASE_DIR)
log.info("BASE_DIR: %s", BASE_DIR)

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
           f"无法加载应用模块：{_e}\n\nBASE_DIR: {BASE_DIR}\n日志：{_LOG_PATH}")
    sys.exit(1)

PORT = 7891


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _start_server(port: int):
    try:
        log.info("uvicorn starting on port %d", port)
        uvicorn.run(fastapi_app, host="127.0.0.1", port=port,
                    log_level="warning", reload=False)
    except Exception as exc:
        log.exception("uvicorn crashed: %s", exc)


# ══════════════════════════════════════════════════════════════════════════
# macOS — pywebview native window
# ══════════════════════════════════════════════════════════════════════════
def _run_macos(url: str):
    try:
        import webview
        log.info("pywebview %s  gui=cocoa", getattr(webview, "__version__", "?"))
        window = webview.create_window(
            title="Hermes Agent 安装向导",
            url=url,
            width=1080, height=760,
            resizable=True, min_size=(860, 620),
            background_color="#0f0f1a",
        )
        webview.start(gui="cocoa", debug=False)
        log.info("webview closed")
    except Exception as exc:
        log.exception("pywebview failed: %s", exc)
        import webbrowser
        webbrowser.open(url)
        _alert("Hermes Installer",
               f"原生窗口不可用（{exc}），已在浏览器中打开：\n{url}")


# ══════════════════════════════════════════════════════════════════════════
# Windows — system browser + console keep-alive
# (avoids Edge WebView2 / multiprocessing infinite-spawn bug in onefile)
# ══════════════════════════════════════════════════════════════════════════
def _run_windows(url: str):
    import webbrowser
    webbrowser.open(url)
    log.info("browser opened: %s", url)

    # Keep the console window open so the FastAPI server (daemon thread)
    # stays alive.  User closes the black window to quit.
    print()
    print("=" * 54)
    print("  ⚡ Hermes Agent 安装向导")
    print("=" * 54)
    print(f"  服务器地址：{url}")
    print()
    print("  浏览器已自动打开。")
    print("  如未打开，请手动访问上方地址。")
    print()
    print("  关闭此窗口即可退出程序。")
    print("=" * 54)
    try:
        # Block forever; closing the console window kills the process
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════
def main():
    global PORT

    # Reserve port
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", PORT))
    except OSError:
        PORT = _find_free_port()
        log.info("port 7891 busy → using %d", PORT)

    # Start FastAPI server thread
    server_thread = threading.Thread(
        target=_start_server, args=(PORT,), daemon=True)
    server_thread.start()

    log.info("waiting for server on port %d …", PORT)
    if not _wait_for_server(PORT, timeout=20.0):
        _alert("Hermes Installer — 启动失败",
               f"服务器在端口 {PORT} 启动超时。\n日志：{_LOG_PATH}")
        sys.exit(1)
    log.info("server ready")

    url = f"http://127.0.0.1:{PORT}"

    if sys.platform == "darwin":
        _run_macos(url)
    else:
        _run_windows(url)


if __name__ == "__main__":
    main()
