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

import atexit
import shutil
import signal
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


def _confirm(title: str, msg: str, ok_label: str = "打开新窗口", cancel_label: str = "取消") -> bool:
    """Show a Yes/No dialog. Returns True if user picks *ok_label*.

    Falls back to True on platforms without a GUI dialog (no good way to ask).
    """
    log.info("PROMPT %s | %s", title, msg)
    if sys.platform == "darwin":
        # AppleScript string literals don't interpret \n — replace with `& return &`.
        def _esc(s: str) -> str:
            return s.replace('\\', '\\\\').replace('"', '\\"')
        if "\n" in msg:
            parts = [f'"{_esc(p)}"' for p in msg.split("\n")]
            msg_expr = " & return & ".join(parts)
        else:
            msg_expr = f'"{_esc(msg)}"'
        script = (
            f'display dialog {msg_expr} with title "{_esc(title)}" '
            f'buttons {{"{cancel_label}", "{ok_label}"}} '
            f'default button "{ok_label}" cancel button "{cancel_label}" '
            f'with icon caution'
        )
        try:
            res = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=120,
            )
        except Exception as exc:
            log.debug("confirm dialog failed: %s", exc)
            return False
        # Default button → returncode 0, stdout has "button returned:<label>"
        # Cancel button  → returncode 1
        if res.returncode != 0:
            return False
        return ok_label in (res.stdout or "")
    if sys.platform == "win32":
        try:
            import ctypes
            MB_YESNO = 0x4
            MB_ICONQUESTION = 0x20
            IDYES = 6
            return ctypes.windll.user32.MessageBoxW(0, msg, title, MB_YESNO | MB_ICONQUESTION) == IDYES
        except Exception as exc:
            log.debug("confirm dialog failed: %s", exc)
            return False
    return True


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


def _pids_on_port(port: int) -> list[int]:
    """Return PIDs listening on 127.0.0.1:<port>. Cross-platform best-effort."""
    pids: list[int] = []
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, text=True, timeout=5,
            ).stdout or ""
            needle = f":{port}"
            for line in out.splitlines():
                parts = line.split()
                # Format: Proto  Local  Foreign  State  PID
                if len(parts) >= 5 and parts[0].upper() == "TCP" and needle in parts[1] \
                        and parts[3].upper() == "LISTENING":
                    try:
                        pids.append(int(parts[4]))
                    except ValueError:
                        pass
        else:
            out = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=5,
            ).stdout or ""
            for tok in out.split():
                try:
                    pids.append(int(tok))
                except ValueError:
                    pass
    except Exception as exc:
        log.debug("port lookup failed: %s", exc)
    return list(dict.fromkeys(pids))  # dedupe, preserve order


def _kill_pid(pid: int) -> None:
    """Send SIGTERM, then SIGKILL after 2s if still alive."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, timeout=5,
        )
        return
    try:
        os.kill(pid, 15)  # SIGTERM
    except ProcessLookupError:
        return
    except Exception as exc:
        log.debug("SIGTERM pid=%s failed: %s", pid, exc)
    # Wait up to 2s for graceful exit
    deadline = time.time() + 2.0
    while time.time() < deadline:
        try:
            os.kill(pid, 0)  # probe
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, 9)  # SIGKILL
    except ProcessLookupError:
        pass
    except Exception as exc:
        log.debug("SIGKILL pid=%s failed: %s", pid, exc)


def _free_port(port: int) -> bool:
    """Best-effort: kill anything holding *port*, return True if port freed."""
    pids = _pids_on_port(port)
    if not pids:
        # Either nothing's there, or lsof/netstat couldn't see it.
        return not _port_in_use(port)
    log.info("Port %d held by PIDs %s — terminating", port, pids)
    for pid in pids:
        _kill_pid(pid)
    # Wait up to 5s for the socket to actually release
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not _port_in_use(port):
            log.info("Port %d freed", port)
            return True
        time.sleep(0.2)
    log.warning("Port %d still in use after kill", port)
    return False


# ══════════════════════════════════════════════════════════════════════════
# Native window — pywebview (macOS cocoa + Windows edgechromium)
# ══════════════════════════════════════════════════════════════════════════
def _open_native_window(title: str, url: str, on_close=None):
    """Open URL in a native window using pywebview.
    macOS  → WKWebView via cocoa backend
    Windows → Edge WebView2 via edgechromium backend

    *on_close* is invoked synchronously when the window is closing or has
    closed.  This is the only reliable cleanup hook on macOS — the cocoa
    backend's ``NSApp.terminate`` exits the process without returning from
    ``webview.start()``, so ``finally`` / ``atexit`` may never run.
    """
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
        window = webview.create_window(
            title, url,
            width=1080, height=760,
            resizable=True, min_size=(860, 620),
            background_color="#0f0f1a",
        )
        if on_close is not None:
            def _on_closing():
                try:
                    on_close()
                except Exception as exc:
                    log.debug("on_close callback failed: %s", exc)
            try:
                window.events.closing += _on_closing
                window.events.closed += _on_closing
            except Exception as exc:
                log.debug("could not attach window close handler: %s", exc)
        webview.start(gui=gui, debug=False)
        log.info("native window closed")
    except Exception as exc:
        log.exception("pywebview %s failed: %s", gui, exc)
        if on_close is not None:
            try:
                on_close()
            except Exception:
                pass
        _alert("Hermes Installer", f"原生窗口启动失败：{exc}")
        sys.exit(1)


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

    # If another instance already owns the port, ask the user whether to
    # terminate it and launch a fresh native window. (No browser fallback —
    # the previous window is just hidden behind other apps; we always
    # restart in a native window when the user confirms.)
    if _port_in_use(port):
        log.info("Port %d already in use — prompting user", port)
        if not _confirm(
            "Hermes Installer",
            f"端口 {port} 已被另一个 Hermes 实例占用（窗口可能被遮挡）。\n"
            f"关闭旧实例并打开新的 Hermes 窗口？",
        ):
            log.info("User declined — leaving existing instance running")
            sys.exit(0)
        log.info("User confirmed — terminating previous WebUI")
        if not _free_port(port):
            _alert("Hermes Installer",
                   f"端口 {port} 无法释放。\n请手动停止占用进程后重试。")
            sys.exit(1)

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

    # ── Capture server.py PIDs so we can clean them up on exit ──────────────
    # bootstrap.py spawns server.py detached and then exits. Without explicit
    # cleanup, server.py would survive window close and accumulate as orphans
    # on every launch — eventually holding the port and forcing the user
    # through the conflict dialog every time.
    server_pids = _pids_on_port(port) if ready else []
    log.info("WebUI server PIDs to terminate on exit: %s", server_pids)

    _cleanup_done = {"flag": False}

    def _cleanup_servers(*_args):
        if _cleanup_done["flag"]:
            return
        _cleanup_done["flag"] = True
        if not server_pids:
            return
        log.info("Terminating WebUI server PIDs: %s", server_pids)
        for pid in server_pids:
            try:
                _kill_pid(pid)
            except Exception as exc:
                log.debug("cleanup kill %s failed: %s", pid, exc)

    atexit.register(_cleanup_servers)
    # Cmd+Q / SIGTERM (e.g. Force Quit's polite first phase) / Ctrl+C
    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, lambda *_: (_cleanup_servers(), sys.exit(0)))
        except (ValueError, OSError) as exc:
            log.debug("signal %s install failed: %s", _sig, exc)

    url = f"http://{host}:{port}/"
    title = "Hermes"

    log.info("Opening WebUI: %s (server ready=%s)", url, ready)

    try:
        _open_native_window(title, url, on_close=_cleanup_servers)
    finally:
        # Defense-in-depth: also clean up if we somehow get here. On macOS
        # this rarely runs because cocoa terminates the process directly;
        # the on_close callback registered above is the primary path.
        _cleanup_servers()


if __name__ == "__main__":
    main()
