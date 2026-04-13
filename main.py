"""
Hermes Agent Installer — cross-platform entry point (macOS + Windows).
Starts FastAPI server in background thread, opens pywebview native window.
"""
import sys
import os
import threading
import time
import socket
import logging
import traceback
from pathlib import Path

# ── Log file (critical for diagnosing silent crashes on Windows) ───────────
_LOG_PATH = Path(os.environ.get("TEMP", os.environ.get("TMPDIR", "/tmp"))) / "hermes-installer.log"
logging.basicConfig(
    filename=str(_LOG_PATH),
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("hermes")
log.info("=== Hermes Installer starting ===")
log.info("Python %s", sys.version)
log.info("Platform: %s", sys.platform)
log.info("Frozen: %s", getattr(sys, "frozen", False))


def _alert(title: str, msg: str):
    """Show an error dialog — works even without a webview."""
    log.error("%s: %s", title, msg)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, msg)
        root.destroy()
    except Exception:
        print(f"[ERROR] {title}: {msg}", file=sys.stderr)


# ── PyInstaller bundle path fix ────────────────────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["HERMES_INSTALLER_BASE_DIR"] = str(BASE_DIR)
log.info("BASE_DIR: %s", BASE_DIR)
log.info("sys.path[:3]: %s", sys.path[:3])

# ── Windows: event loop policy ────────────────────────────────────────────
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    log.info("WindowsSelectorEventLoopPolicy set")

# ── Import app (with full error reporting) ─────────────────────────────────
try:
    import uvicorn
    from app import app as fastapi_app
    log.info("app imported OK")
except Exception as _e:
    _alert(
        "Hermes Installer — 启动失败",
        f"无法加载应用模块：\n{_e}\n\n"
        f"BASE_DIR: {BASE_DIR}\n"
        f"日志文件：{_LOG_PATH}"
    )
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
        log.info("Starting uvicorn on port %d", port)
        uvicorn.run(
            fastapi_app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            reload=False,
        )
    except Exception as exc:
        log.exception("uvicorn crashed: %s", exc)


def main():
    global PORT

    # Pick port
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", PORT))
    except OSError:
        PORT = _find_free_port()
        log.info("Port 7891 busy, using %d", PORT)

    # Start FastAPI in background
    server_thread = threading.Thread(
        target=_start_server, args=(PORT,), daemon=True
    )
    server_thread.start()

    # Wait up to 20s for server
    log.info("Waiting for server on port %d ...", PORT)
    if not _wait_for_server(PORT, timeout=20.0):
        _alert(
            "Hermes Installer — 服务器启动失败",
            f"内置服务器在端口 {PORT} 启动超时。\n\n"
            f"详细日志：{_LOG_PATH}"
        )
        sys.exit(1)

    log.info("Server ready on port %d", PORT)
    url = f"http://127.0.0.1:{PORT}"

    # ── Try pywebview ──────────────────────────────────────────────────────
    try:
        import webview
        log.info("pywebview version: %s", getattr(webview, "__version__", "?"))

        gui = "cocoa" if sys.platform == "darwin" else (
              "edgechromium" if sys.platform == "win32" else None)
        log.info("GUI backend: %s", gui)

        window = webview.create_window(
            title="Hermes Agent 安装向导",
            url=url,
            width=1080,
            height=760,
            resizable=True,
            min_size=(860, 620),
            background_color="#0f0f1a",
        )
        log.info("Window created, calling webview.start()")
        webview.start(gui=gui, debug=False)
        log.info("webview.start() returned (window closed)")

    except Exception as exc:
        log.exception("pywebview failed: %s", exc)
        # ── Fallback: system browser ───────────────────────────────────────
        try:
            import webbrowser
            log.info("Fallback: opening browser at %s", url)
            webbrowser.open(url)
            # Keep process alive (server is a daemon thread)
            _alert(
                "Hermes Installer",
                f"界面已在默认浏览器中打开：\n{url}\n\n"
                f"（WebView 不可用：{exc}）\n\n"
                "关闭此窗口后程序将退出。"
            )
        except Exception as exc2:
            log.exception("Browser fallback also failed: %s", exc2)
            _alert(
                "Hermes Installer — 严重错误",
                f"WebView 和浏览器回退均失败。\n\n"
                f"WebView 错误：{exc}\n"
                f"Browser 错误：{exc2}\n\n"
                f"日志文件：{_LOG_PATH}\n"
                f"请手动访问：{url}"
            )


if __name__ == "__main__":
    main()
