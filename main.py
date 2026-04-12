"""
Hermes Agent Installer — cross-platform entry point (macOS + Windows).
Starts FastAPI server in background thread, opens pywebview native window.
"""
import sys
import os
import threading
import time
import socket
from pathlib import Path

# ── PyInstaller bundle path fix ────────────────────────────────────────────
# When frozen by PyInstaller, resources live in sys._MEIPASS (one-dir mode).
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys._MEIPASS)
    sys.path.insert(0, str(BASE_DIR))
else:
    BASE_DIR = Path(__file__).parent

os.environ["HERMES_INSTALLER_BASE_DIR"] = str(BASE_DIR)

# ── Windows: set event loop policy before any asyncio usage ────────────────
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn
from app import app as fastapi_app

PORT = 7891


def _find_free_port() -> int:
    """Find a free port if 7891 is in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 10.0) -> bool:
    """Wait until the server is accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _start_server(port: int):
    uvicorn.run(
        fastapi_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        # Disable reload — not supported in frozen apps
        reload=False,
    )


def _pick_gui() -> str | None:
    """Return the best pywebview GUI backend for the current platform."""
    if sys.platform == "darwin":
        return "cocoa"       # WKWebView (modern, supports ES2020+)
    if sys.platform == "win32":
        return "edgechromium"  # Edge WebView2 (Win10/11 built-in)
    return None              # Linux: gtk / qt auto-detected


def main():
    global PORT

    # Try the default port, fall back to random free port
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", PORT))
    except OSError:
        PORT = _find_free_port()

    server_thread = threading.Thread(
        target=_start_server, args=(PORT,), daemon=True
    )
    server_thread.start()

    # Wait for server to be ready (up to 10s) instead of fixed sleep
    if not _wait_for_server(PORT):
        print(f"Server failed to start on port {PORT}", file=sys.stderr)
        sys.exit(1)

    url = f"http://127.0.0.1:{PORT}"

    try:
        import webview

        gui = _pick_gui()
        window = webview.create_window(
            title="Hermes Agent 安装向导",
            url=url,
            width=1080,
            height=760,
            resizable=True,
            min_size=(860, 620),
            background_color="#0f0f1a",
            # Allow JS ↔ Python bridge (not used yet, but enables future features)
            js_api=None,
        )
        webview.start(gui=gui, debug=False)
    except Exception as exc:
        # Fallback: open in system browser when pywebview is unavailable
        import webbrowser
        print(f"[warn] pywebview unavailable ({exc}), opening browser.")
        webbrowser.open(url)
        server_thread.join()   # Keep process alive until user closes browser


if __name__ == "__main__":
    main()
