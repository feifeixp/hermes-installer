"""
Hermes Installer — cross-platform entry point.
- macOS  : pywebview cocoa (WKWebView native window)
- Windows: pywebview edgechromium (Edge WebView2 native window)
- Always launches the Hermes WebUI (bootstrap.py handles first-time setup)
"""
# ── CHILD PROCESS GUARD ────────────────────────────────────────────────────
# Prevents subprocesses on Windows from re-launching the frozen exe.
# macOS subprocesses use fork() and are not affected.
import os
import sys

if sys.platform == "win32" and os.environ.get("_HERMES_MAIN") == "1":
    sys.exit(0)

if sys.platform == "win32":
    os.environ["_HERMES_MAIN"] = "1"

# ── Now safe to import everything ─────────────────────────────────────────
import multiprocessing
multiprocessing.freeze_support()

import shutil
import subprocess
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
    print(f"[ERROR] {title}: {msg}", file=sys.stderr)
    if sys.platform == "darwin":
        try:
            subprocess.Popen(
                ["osascript", "-e",
                 f'display dialog "{msg}" with title "{title}" buttons {{"OK"}} default button "OK" with icon stop'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


# ── Bundle path ────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["HERMES_INSTALLER_BASE_DIR"] = str(BASE_DIR)
log.info("BASE_DIR=%s", BASE_DIR)

WEBUI_DIR = BASE_DIR / "webui"
BOOTSTRAP_PY = WEBUI_DIR / "bootstrap.py"


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_server(port: int, timeout: float = 90.0) -> bool:
    """Wait until a TCP connection to 127.0.0.1:<port> succeeds.
    Large timeout because bootstrap.py may install hermes-agent first."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.3):
                return True
        except OSError:
            time.sleep(0.3)
    return False


# ══════════════════════════════════════════════════════════════════════════
# Native window — pywebview (macOS cocoa + Windows edgechromium)
# ══════════════════════════════════════════════════════════════════════════
def _open_native_window(title: str, url: str):
    """Open URL in a native window using pywebview.
    macOS  → WKWebView via cocoa backend
    Windows → Edge WebView2 via edgechromium backend
    Falls back to system browser if pywebview is unavailable."""
    try:
        import webview
    except ImportError:
        log.warning("pywebview not installed — falling back to browser")
        import webbrowser
        webbrowser.open(url)
        _alert("Hermes Installer",
               "pywebview 未安装，已在浏览器中打开。\n"
               "如需独立窗口，请运行: pip install pywebview")
        return

    gui = "cocoa" if sys.platform == "darwin" else "edgechromium"
    log.info("pywebview %s gui=%s", getattr(webview, "__version__", "?"), gui)

    try:
        webview.create_window(
            title, url,
            width=1080, height=760,
            resizable=True, min_size=(860, 620),
            background_color="#0f0f1a",
        )
        webview.start(gui=gui, debug=False)
        log.info("native window closed")
    except Exception as exc:
        log.exception("pywebview %s failed: %s", gui, exc)
        import webbrowser
        webbrowser.open(url)
        _alert("Hermes Installer",
               f"原生窗口启动失败（{exc}），\n已在浏览器中打开：{url}")


# ══════════════════════════════════════════════════════════════════════════
# Python discovery — find a usable interpreter for bootstrap.py
# ══════════════════════════════════════════════════════════════════════════

def _find_bootstrap_python() -> str:
    """Find a Python interpreter that can run bootstrap.py.
    Priority: hermes-agent venv → system python3 → sys.executable"""
    if sys.platform == "win32":
        venv_candidates = [
            Path.home() / ".hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe",
            Path.home() / ".hermes" / "hermes-agent" / ".venv" / "Scripts" / "python.exe",
        ]
    else:
        venv_candidates = [
            Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python",
            Path.home() / ".hermes" / "hermes-agent" / ".venv" / "bin" / "python",
        ]

    # Prefer hermes-agent venv Python (has all dependencies)
    for p in venv_candidates:
        if p.exists():
            log.info("Using hermes-agent venv Python: %s", p)
            return str(p)

    # When frozen, sys.executable is the app binary — useless as Python
    if not getattr(sys, "frozen", False):
        log.info("Using sys.executable: %s", sys.executable)
        return sys.executable

    # Fallback: find system Python (prefer 3.13+ for pywebview compatibility)
    for name in ("python3.13", "python3.12", "python3.11", "python3.10", "python3", "python"):
        found = shutil.which(name)
        if found:
            log.info("Using system Python: %s", found)
            return found

    # Last resort
    log.warning("No Python found; bootstrap.py will handle its own discovery")
    return "python3.13" if shutil.which("python3.13") else "python3"


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════

WEBUI_DEFAULT_PORT = 8787
WEBUI_STARTUP_TIMEOUT = 300  # 5 minutes (bootstrap may install hermes-agent)


def main():
    port = WEBUI_DEFAULT_PORT
    host = "127.0.0.1"

    # If another instance already owns the port, just open it in browser
    if _port_in_use(port):
        log.info("Port %d already in use — another WebUI instance running", port)
        try:
            import webbrowser
            webbrowser.open(f"http://{host}:{port}")
        except Exception:
            pass
        return

    # ── Launch bootstrap.py ──────────────────────────────────────────────
    # bootstrap.py handles everything:
    #   1. Detect hermes-agent installation
    #   2. Install hermes-agent if missing (git clone + venv + pip install)
    #   3. Create WebUI venv + install deps if needed
    #   4. Start server.py on the target port
    #   5. Health-check, then exit (server.py keeps running detached)
    #
    # We run bootstrap.py in a daemon thread so the main thread can show a
    # loading state in the window while bootstrap does its work.

    python_exe = _find_bootstrap_python()

    if not BOOTSTRAP_PY.exists():
        _alert("Hermes Installer",
               f"找不到 WebUI 启动脚本。\n路径：{BOOTSTRAP_PY}\n"
               f"请确认 webui/ 目录与 main.py 在同一文件夹下。")
        sys.exit(1)

    env = os.environ.copy()
    env["HERMES_WEBUI_PORT"] = str(port)
    env["HERMES_WEBUI_HOST"] = host
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"

    log.info("Launching bootstrap.py: %s %s", python_exe, BOOTSTRAP_PY)

    # Launch as detached child — bootstrap.py spawns server.py and exits,
    # server.py continues running
    try:
        proc = subprocess.Popen(
            [python_exe, str(BOOTSTRAP_PY), str(port), "--host", host],
            cwd=str(WEBUI_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(sys.platform != "win32"),
        )
    except FileNotFoundError:
        _alert("Hermes Installer",
               f"找不到 Python 解释器。\n尝试的路径：{python_exe}\n"
               f"请安装 Python 3.10+ 后重试。")
        sys.exit(1)
    except Exception as exc:
        _alert("Hermes Installer", f"无法启动 WebUI：{exc}")
        sys.exit(1)

    log.info("bootstrap.py PID=%s — waiting for WebUI on port %d (timeout=%ds)",
             proc.pid, port, WEBUI_STARTUP_TIMEOUT)

    # Wait for the WebUI server to be ready
    # bootstrap.py installs hermes-agent + deps first, so this can take a while
    ready = _wait_for_server(port, timeout=WEBUI_STARTUP_TIMEOUT)
    if not ready:
        # Server might still be starting — give it another 30s and try anyway
        log.warning("Port %d not ready after %ds, trying anyway in 30s",
                    port, WEBUI_STARTUP_TIMEOUT)
        time.sleep(30)
        ready = _wait_for_server(port, timeout=10)

    url = f"http://{host}:{port}/"
    title = "Hermes"

    log.info("Opening WebUI: %s (server ready=%s)", url, ready)

    _open_native_window(title, url)


if __name__ == "__main__":
    main()
