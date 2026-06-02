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

# ── Force UTF-8 stdout/stderr ─────────────────────────────────────────────
# On Chinese Windows the console codepage is GBK (cp936) by default and the
# frozen exe inherits stdout/stderr bound to that codec. Any print() that
# emits '✓' (U+2713), '→', '←', etc. raises UnicodeEncodeError. The install
# wizard prints those chars during every step, so first-run install on a
# Chinese Windows machine died at "Step 1 ✓ 解压完成" before we touched a
# single byte of agent code.
#
# Same mitigation bundle_source.py uses on Windows CI; harmless no-op on
# POSIX (already utf-8). PYTHONIOENCODING is belt-and-suspenders for any
# child Python process that inherits our env.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover — defensive for older Python
    pass
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# ── Now safe to import everything ─────────────────────────────────────────
import multiprocessing
multiprocessing.freeze_support()

import atexit
import shutil
import signal
import subprocess
import threading
import time
import socket
import logging
from pathlib import Path

# ── Log file ────────────────────────────────────────────────────────────────
# On Windows: %APPDATA%\Hermes\hermes-startup.log  (user-visible location)
# On macOS:   ~/Library/Logs/Hermes/hermes-startup.log
# Elsewhere:  /tmp/hermes/hermes-startup.log
try:
    if sys.platform == "win32":
        _LOG_DIR = Path(os.environ.get("APPDATA", os.environ.get("TEMP", "C:\\Temp"))) / "Hermes"
    elif sys.platform == "darwin":
        _LOG_DIR = Path.home() / "Library" / "Logs" / "Hermes"
    else:
        _LOG_DIR = Path(os.environ.get("TMPDIR", "/tmp")) / "hermes"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_PATH = _LOG_DIR / "hermes-startup.log"
except Exception:
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


# ── Console window visibility (Windows only) ────────────────────────────────
# On Windows with console=True the black CMD window appears immediately.
# We hide it as soon as possible so normal runs show only the Edge WebView
# window. During first-run installation we reveal it so the user can see
# the progress output, then hide it again when install finishes.
#
# SW_HIDE=0, SW_SHOW=5, SW_MINIMIZE=6
def _console_hwnd() -> int:
    """Return the Win32 console HWND, or 0 if unavailable."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        return ctypes.windll.kernel32.GetConsoleWindow()  # type: ignore[attr-defined]
    except Exception:
        return 0


def _show_console() -> None:
    """Make the console window visible (for install progress)."""
    hwnd = _console_hwnd()
    if hwnd:
        try:
            import ctypes
            ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
        except Exception:
            pass


def _hide_console() -> None:
    """Hide the console window (normal / post-install runs)."""
    hwnd = _console_hwnd()
    if hwnd:
        try:
            import ctypes
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        except Exception:
            pass


def _alert(title: str, msg: str):
    log.error("ALERT %s | %s", title, msg)
    print(f"[ERROR] {title}: {msg}", file=sys.stderr)
    if sys.platform == "darwin":
        try:
            esc_msg   = msg.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
            esc_title = title.replace('\\', '\\\\').replace('"', '\\"')
            subprocess.Popen(
                ["osascript", "-e",
                 f'display dialog "{esc_msg}" with title "{esc_title}" buttons {{"OK"}} default button "OK" with icon stop'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
    elif sys.platform == "win32":
        # PyInstaller hides the console window, so stderr is invisible.
        # Use a native MessageBox so the user ALWAYS sees the error.
        try:
            import ctypes
            MB_OK        = 0x0
            MB_ICONERROR = 0x10
            ctypes.windll.user32.MessageBoxW(0, msg, title, MB_OK | MB_ICONERROR)
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

# ── Neowow distribution defaults ──────────────────────────────────────────
# This installer IS the Neowow-flavored Hermes build. Default the WebUI
# into Coding-Plan-only mode (hides openai / anthropic / openrouter /
# ollama / custom / neodomain from the onboarding wizard) so every chat
# routes through app.neowow.studio/api/me/chat/completions and bills
# the user's Coding Plan credits.
#
# Upstream community Hermes uses its own entry-point and never sets this.
# Power users who want BYO provider on the Neowow build can explicitly
# set HERMES_NEOWOW_ONLY=0 in their environment to opt out — `setdefault`
# preserves any value the operator already chose.
os.environ.setdefault("HERMES_NEOWOW_ONLY", "1")

# ── Unify HERMES_HOME across all subprocesses ─────────────────────────────
# webui/api/config.py defaults to %LOCALAPPDATA%/hermes on native Windows
# (and ~/.hermes on POSIX). The hermes-agent CLI defaults to ~/.hermes
# everywhere. When these disagree on Windows:
#   - WebUI reads/writes config.yaml, gateway.pid, sessions under LOCALAPPDATA
#   - The `hermes gateway` daemon writes its pid + state under ~/.hermes
# Result: the WebUI's /api/gateway/status sees no gateway and surfaces
# "GATEWAY NOT CONFIGURED" even when the daemon is happily running.
#
# Set HERMES_HOME at the installer level so every child (server.py,
# `hermes gateway run`, `hermes` CLI invocations from Step 3.5) inherits
# the SAME root. We pick the WebUI's native-Windows default
# (%LOCALAPPDATA%/hermes) so the WebUI's existing settings.json / config.yaml
# / sessions keep their location and we just align the gateway to them.
# `setdefault` keeps any operator override intact.
if sys.platform == "win32":
    _local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if _local_app_data:
        os.environ.setdefault("HERMES_HOME", str(Path(_local_app_data) / "hermes"))

log.info("BASE_DIR=%s  HERMES_NEOWOW_ONLY=%s  HERMES_HOME=%s",
         BASE_DIR, os.environ.get("HERMES_NEOWOW_ONLY"),
         os.environ.get("HERMES_HOME", "(unset)"))

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
# Crash reporting helpers
# ══════════════════════════════════════════════════════════════════════════

def _get_app_version() -> str:
    """Read version from pyproject.toml (dev) or version.txt (frozen)."""
    try:
        if getattr(sys, "frozen", False):
            ver_file = BASE_DIR / "version.txt"
            if ver_file.exists():
                return ver_file.read_text(encoding="utf-8").strip()
        else:
            pyproj = Path(__file__).parent / "pyproject.toml"
            if pyproj.exists():
                for line in pyproj.read_text(encoding="utf-8").splitlines():
                    if line.lstrip().startswith("version"):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "unknown"


# ── Crash reporter (shared with webui/server.py) ────────────────────────────
# The actual implementation lives in crash_reporter.py at repo root. Imported
# here via the BASE_DIR-on-sys.path machinery already set up above. webui side
# uses the HERMES_INSTALLER_BASE_DIR env var (also set above) to find it.
try:
    import crash_reporter as _crash_reporter
except ImportError as _cr_exc:
    log.warning("crash_reporter import failed (%s) — reports disabled this run", _cr_exc)
    _crash_reporter = None


def _send_crash_report(phase: str, error: str, extra: "dict | None" = None) -> None:
    """Backward-compat shim — forwards to crash_reporter.report().

    Kept as a named function so existing call sites don't need touching.
    New triggers in main.py call crash_reporter.report() directly.
    """
    if _crash_reporter is None:
        return
    try:
        _crash_reporter.report(phase, error, extra=extra)
    except Exception as exc:
        log.debug("crash report dispatch failed: %s", exc)


# Flush any pending crash reports from a previous (likely-crashed) run.
# Best-effort: don't let queue-flush exceptions block the installer.
try:
    if _crash_reporter is not None:
        _flushed = _crash_reporter.flush_queue()
        if _flushed:
            log.info("flushed %d pending crash reports from previous run", _flushed)
except Exception as _exc:
    log.debug("flush_queue at startup failed: %s", _exc)


def _check_webview2_windows() -> "str | None":
    """Return None if WebView2 Runtime is installed, or an error string if missing.

    WebView2 is required by pywebview's edgechromium backend. When absent,
    webview.start() crashes with a cryptic DLL-load error. We check the
    registry first and show a helpful download URL instead.

    Returns None on non-Windows or if the registry check itself fails
    (we don't want to block launch if winreg behaves unexpectedly).
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg
        # WebView2 Runtime is registered under this GUID regardless of version
        wv2_guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        key_paths = [
            (winreg.HKEY_LOCAL_MACHINE,
             rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{wv2_guid}"),
            (winreg.HKEY_LOCAL_MACHINE,
             rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{wv2_guid}"),
            (winreg.HKEY_CURRENT_USER,
             rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{wv2_guid}"),
        ]
        for hive, path in key_paths:
            try:
                with winreg.OpenKey(hive, path) as k:
                    version = winreg.QueryValueEx(k, "pv")[0]
                    if version and version != "0.0.0.0":
                        log.info("WebView2 Runtime found: v%s", version)
                        return None
            except OSError:
                continue
        # Checked all paths — not found
        return "WebView2 Runtime 未找到 (未安装)"
    except Exception as exc:
        log.debug("WebView2 registry check failed: %s", exc)
        return None  # Don't block if the check itself errors


# ══════════════════════════════════════════════════════════════════════════
# Windows install helpers
# ══════════════════════════════════════════════════════════════════════════

def _clean_subprocess_env(*, extra: dict | None = None) -> dict:
    """Return an env dict safe to pass to a venv-Python subprocess.

    When the installer is a PyInstaller-frozen exe (always, in production),
    sys.executable is a Python 3.13 interpreter that lives inside
    ``_MEI<random>/`` along with its own ``python313.dll``. PyInstaller
    leaks a handful of env vars into os.environ that, if inherited by a
    child venv Python 3.11, make Windows resolve ``python3.dll`` /
    ``python313.dll`` from the parent's _MEI dir before the venv's
    cpython-3.11 prefix — every native .pyd then fails with
    ``Module use of python313.dll conflicts with this version of Python``
    AND the stdlib's ``encodings.idna`` codec can't be located via the
    expected path (``LookupError: unknown encoding: idna`` inside
    socket.getfqdn → gethostbyaddr).

    Strip the leaks:
      - ``PYTHONHOME`` / ``PYTHONPATH``: anchor stdlib to _MEI's 3.13 layout
      - ``_PYI_*`` / ``_MEIPASS2``: PyInstaller internals that bootloader sets
      - Any ``_MEI<n>``-prefixed entry in PATH

    Then layer caller-supplied vars on top (``extra``).
    """
    env = os.environ.copy()
    for var in ("PYTHONHOME", "PYTHONPATH", "_PYI_APPLICATION_HOME_DIR",
                "_PYI_LINK_TARGET", "_PYI_ARCHIVE_FILE", "_MEIPASS2"):
        env.pop(var, None)
    # Drop _MEI* dirs from PATH (Win) / colon-PATH (POSIX, defensive).
    sep = ";" if sys.platform == "win32" else ":"
    parts = env.get("PATH", "").split(sep)
    parts = [p for p in parts if "_MEI" not in p]
    env["PATH"] = sep.join(parts)
    if extra:
        env.update(extra)
    return env


def _is_agent_installed() -> bool:
    """Return True if the hermes-agent venv exists, is healthy, and run_agent is importable.

    Windows-only check. Fast (<1 s) — runs on every startup to decide
    whether to show the install wizard.

    Health check (added v1.4.2): also verifies the venv's Python isn't
    broken by previous-system-Python pollution. Specifically checks:
      - encodings.idna can be loaded (socket.getfqdn needs it; Microsoft
        Store Python + some custom Python installs miss this)
      - run_agent imports without 'Module use of pythonXXX.dll' conflicts
        (caused by mixing wheels from a parallel-installed Python version)
    If the health check fails, returns False so the caller wipes and
    re-creates the venv (with v1.4.2's only-managed Python preference).
    """
    venv_python = (
        Path.home() / ".hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe"
    )
    if not venv_python.exists():
        log.info("agent not installed: venv python not found at %s", venv_python)
        return False
    agent_dir = Path.home() / ".hermes" / "hermes-agent"

    # ── Structural pre-check: pyvenv.cfg.home must point at uv-managed Python ──
    # The runtime health check below catches venvs that crash *in the same
    # environment as the health probe*. It MISSES venvs that crash only
    # under the frozen-exe spawn path (different DLL search order, different
    # sys.path[0], etc.) — a real user (DN, Phase η.8 bug report) had a
    # Python 3.12-based venv that passed the in-isolation health check but
    # then died with "python313.dll conflicts" + "unknown encoding: idna"
    # the moment _start_webui_server_windows tried to boot server.py.
    #
    # Wider net: refuse to trust any venv whose `home` in pyvenv.cfg isn't
    # rooted under the uv-managed Python directory (`%APPDATA%\uv\python\`).
    # That's a STRUCTURAL property — independent of env, sys.path, and
    # DLL-search order — so it can't false-positive the way a runtime
    # probe can. Any pre-v1.4.5 venv (which were built against whatever
    # system Python uv could find) gets force-rebuilt on the next .exe run.
    # uv-managed venvs (v1.4.5+) keep being treated as healthy.
    try:
        pyvenv_cfg = (Path.home() / ".hermes" / "hermes-agent" / "venv" / "pyvenv.cfg")
        if pyvenv_cfg.exists():
            cfg_text = pyvenv_cfg.read_text(encoding="utf-8", errors="replace")
            # Parse `home = <path>` line (case-insensitive on Windows
            # but we accept exact-match here — pyvenv.cfg is generated
            # by venv/uv with lowercase `home`).
            home_value = ""
            for line in cfg_text.splitlines():
                if line.lower().lstrip().startswith("home"):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        home_value = parts[1].strip()
                        break
            # uv-managed Pythons live under %APPDATA%\uv\python\ — that's
            # where `uv venv --python 3.11 --python-preference only-managed`
            # places them. Any other location (e.g. C:\Users\<x>\AppData\
            # Local\Programs\Python\Python312\) means the venv was built
            # by an older code path against a system Python, before v1.4.5
            # enforced only-managed.
            uv_root_indicators = (
                "uv\\python\\",        # Windows: %APPDATA%\uv\python\cpython-3.11-...
                "uv/python/",          # POSIX equivalent (defensive — main path is Windows)
            )
            looks_uv_managed = any(ind in home_value.replace("/", "\\") or ind in home_value
                                   for ind in uv_root_indicators)
            if home_value and not looks_uv_managed:
                log.warning(
                    "agent venv is not uv-managed (home=%s) — will rebuild "
                    "so we get a known-good cpython-3.11. This typically "
                    "happens to users who installed Hermes v1.4.4 or older "
                    "and are now upgrading.", home_value,
                )
                return False
            if not home_value:
                log.warning(
                    "agent venv pyvenv.cfg has no `home` line — treating as "
                    "corrupt and rebuilding. cfg=%s", cfg_text[:200],
                )
                return False
    except Exception as exc:
        # File-read errors shouldn't block install detection — fall through
        # to the runtime health check, which catches most failure modes.
        log.debug("pyvenv.cfg structural check failed: %s", exc)

    # ── Health check: idna codec + run_agent import ───────────────────────
    # Both are quick. If either fails with a recognisable wheel-conflict /
    # missing-codec signature, return False so the venv gets rebuilt with
    # uv-managed Python.
    # Also do a real socket bind — gethostbyaddr is the C-level codec path
    # that server.py's QuietHTTPServer crashes on, and it can fail when the
    # Python-level `import encodings.idna` succeeds. Without this the health
    # check returns False positive on frozen-exe-leaked python313.dll envs.
    health_script = (
        "import encodings.idna; "  # missing → user Python install is broken
        "import codecs; codecs.lookup('idna'); "
        "import run_agent; "  # python313.dll conflicts surface here
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer; "
        "srv = ThreadingHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler); "
        "srv.server_close(); "
        "print('ok')"
    )
    try:
        result = subprocess.run(
            [str(venv_python), "-c", health_script],
            capture_output=True,
            timeout=10,
            env=_clean_subprocess_env(extra={"PYTHONPATH": str(agent_dir)}),
        )
        if result.returncode == 0:
            log.info("agent installed and healthy at %s", venv_python)
            return True
        stderr = result.stderr.decode("utf-8", errors="replace")[:400]
        log.info("agent health check failed (rc=%s): %s", result.returncode, stderr)
        # Pollution markers worth surfacing explicitly so future logs are
        # easier to grep for the wipe-and-rebuild path.
        if "python313.dll" in stderr or "python312.dll" in stderr or "python310.dll" in stderr:
            log.warning("agent venv contaminated by cross-version DLL — will rebuild")
            if _crash_reporter is not None:
                try:
                    _crash_reporter.report(
                        phase="venv_health_check_failed",
                        error=stderr[:200] or f"venv health check failed rc={result.returncode}",
                        extra={
                            "returncode": result.returncode,
                            "stderr_tail": stderr[:1000],
                            "venv_python": str(venv_python),
                        },
                    )
                except Exception:
                    pass
        elif "unknown encoding: idna" in stderr or "encodings.idna" in stderr:
            log.warning("agent venv missing idna codec — will rebuild")
            if _crash_reporter is not None:
                try:
                    _crash_reporter.report(
                        phase="venv_health_check_failed",
                        error=stderr[:200] or f"venv health check failed rc={result.returncode}",
                        extra={
                            "returncode": result.returncode,
                            "stderr_tail": stderr[:1000],
                            "venv_python": str(venv_python),
                        },
                    )
                except Exception:
                    pass
        elif "websockets" in stderr.lower() or "No module named" in stderr:
            # Missing pip dep — not a venv corruption, real install issue.
            # Don't trigger wipe (rebuild won't help if requirements changed).
            log.info("agent missing a dep (not a venv corruption) — leaving as-is")
            return True
        return False
    except Exception as exc:
        log.info("agent check exception: %s", exc)
        return False


def _wipe_contaminated_agent_venv() -> bool:
    """Delete a contaminated hermes-agent venv so the install wizard rebuilds it.

    Called when _is_agent_installed() returns False on a venv that DOES exist
    (i.e. the venv is corrupted by previous-system-Python pollution rather
    than absent). Returns True if a wipe happened, False if the path didn't
    exist. Never raises — wipe failure logs and returns False.
    """
    agent_dir = Path.home() / ".hermes" / "hermes-agent"
    venv_dir = agent_dir / "venv"
    if not venv_dir.exists():
        return False
    log.info("Wiping contaminated agent venv at %s", venv_dir)
    try:
        # Whole agent_dir, not just venv: the extracted bundle source can
        # also be partially corrupted, and recreating it from
        # hermes_agent_bundle.zip is cheap (<5 s).
        shutil.rmtree(agent_dir, ignore_errors=False)
        log.info("Agent dir wiped successfully")
        if _crash_reporter is not None:
            try:
                _crash_reporter.report(
                    phase="windows_install_dir_wiped",
                    error="auto-rebuild triggered by health check failure",
                    extra={"agent_dir": str(agent_dir)},
                )
            except Exception:
                pass
        return True
    except Exception as exc:
        log.error("Failed to wipe agent dir: %s — install will likely fail again", exc)
        # Best-effort: try removing just venv subdir
        try:
            shutil.rmtree(venv_dir, ignore_errors=True)
        except Exception:
            pass
        return False


def _find_system_python() -> "str | None":
    """Find a system Python ≥3.11 on Windows PATH.

    Returns the executable path, or None if not found. Used as a hint
    to uv so it doesn't need to download Python from the internet.

    Microsoft Store Python (WindowsApps shim) is EXCLUDED — sandboxed,
    can't load idna codec reliably, breaks C-extension wheels with
    python313.dll conflicts. Two real-world users hit this:
      - venv created on top of WindowsApps Python loses `encodings.idna`
      - tools.* and run_agent fail with "Module use of python313.dll
        conflicts with this version of Python"
    Falling back to None makes uv download its own python-build-
    standalone distribution, which doesn't have these issues.
    """
    # Get the WindowsApps shim path so we can recognize / skip its hits.
    # Path varies by user but always under LOCALAPPDATA\Microsoft\WindowsApps.
    _windowsapps_dir = (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps"
    ).resolve(strict=False)

    for name in ("python3.13", "python3.12", "python3.11", "python3", "python"):
        found = shutil.which(name)
        if not found:
            continue
        # Skip Microsoft Store stubs — they look like real python.exe but
        # resolve to a sandboxed AppExecutionAlias under WindowsApps.
        try:
            found_resolved = Path(found).resolve(strict=False)
            if str(_windowsapps_dir).lower() in str(found_resolved).lower():
                log.info("skipping Microsoft Store Python at %s (sandboxed)", found)
                continue
            # Belt-and-braces: also skip anything in any \WindowsApps\ path
            if "windowsapps" in str(found_resolved).lower():
                log.info("skipping WindowsApps Python at %s (sandboxed)", found)
                continue
        except Exception:
            pass
        try:
            result = subprocess.run(
                [found, "-c",
                 "import sys; v=sys.version_info; exit(0 if v>=(3,11) else 1)"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                log.info("found system Python ≥3.11: %s", found)
                return found
        except Exception:
            continue
    log.info("no system Python ≥3.11 found — uv will manage its own Python")
    return None


def _agent_venv_python(agent_dir: "Path", *, is_windows: bool) -> "Path":
    """Path to the hermes-agent venv's Python for the given OS layout."""
    if is_windows:
        return agent_dir / "venv" / "Scripts" / "python.exe"
    return agent_dir / "venv" / "bin" / "python"


def _uv_pip_install_args(agent_dir: str, venv_python: str) -> "list[str]":
    """uv args to install the agent editable, CN-mirror-first.

    Identical mirror policy to the Windows path (_windows_install_agent):
    Aliyun primary (reliable wheel downloads), USTC/Huawei/PyPI fallbacks,
    first-index strategy to avoid cross-mirror 403 on .whl downloads.
    """
    return [
        "pip", "install",
        "-e", agent_dir,
        "--python", venv_python,
        "--index-strategy", "first-index",
        "--index-url", "https://mirrors.aliyun.com/pypi/simple/",
        "--extra-index-url", "https://mirrors.ustc.edu.cn/pypi/simple/",
        "--extra-index-url", "https://repo.huaweicloud.com/repository/pypi/simple/",
        "--extra-index-url", "https://pypi.org/simple/",
    ]


def _run_uv(uv_exe: Path, args: "list[str]", error_prefix: str = "uv 命令失败") -> None:
    """Run a uv command, streaming output to console + log.

    Raises RuntimeError (with last 10 output lines) on non-zero exit.
    """
    cmd = [str(uv_exe)] + args
    log.info("Running uv: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=_clean_subprocess_env(extra={"UV_NO_PROGRESS": "1", "PYTHONUTF8": "1"}),
    )
    output_lines: list[str] = []
    for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip()
        print(f"    {line}", flush=True)
        log.info("[uv] %s", line)
        output_lines.append(line)
    proc.wait()
    if proc.returncode != 0:
        tail = "\n".join(output_lines[-10:])
        raise RuntimeError(f"{error_prefix} (exit {proc.returncode}):\n{tail}")


def _windows_install_agent() -> None:
    """First-run Windows setup: extract bundle → create venv → pip install.

    Prints step-by-step progress to the console window (console=True in spec).
    All output is also written to hermes-startup.log via _run_uv.
    Raises RuntimeError with a user-readable message on any failure.
    """
    agent_dir = Path.home() / ".hermes" / "hermes-agent"
    venv_dir = agent_dir / "venv"

    # ── Locate hermes_agent_bundle.zip ────────────────────────────────────
    bundle_zip = BASE_DIR / "hermes_agent_bundle.zip"
    if not bundle_zip.exists():
        raise RuntimeError(
            f"找不到安装包：{bundle_zip}\n"
            "请重新下载最新版 Hermes Installer。"
        )

    # ── Locate uv.exe ─────────────────────────────────────────────────────
    uv_exe = BASE_DIR / "tools" / "uv.exe"
    if not uv_exe.exists():
        uv_sys = shutil.which("uv")
        if uv_sys:
            uv_exe = Path(uv_sys)
            log.info("Using system uv: %s", uv_exe)
        else:
            raise RuntimeError(
                "找不到 uv 安装工具。\n"
                "请下载最新版 Hermes Installer（已内置 uv）。\n"
                "或访问 https://github.com/astral-sh/uv/releases 手动安装 uv。"
            )

    # ── Step 1: Extract bundle ─────────────────────────────────────────────
    print("\n[1/3] 正在解压 hermes-agent 源码...", flush=True)
    log.info("Extracting %s → %s", bundle_zip, agent_dir)
    if agent_dir.exists():
        log.info("Removing previous (possibly incomplete) agent dir: %s", agent_dir)
        shutil.rmtree(agent_dir, ignore_errors=True)
    agent_dir.mkdir(parents=True, exist_ok=True)
    import zipfile as _zipfile
    with _zipfile.ZipFile(bundle_zip) as zf:
        zf.extractall(agent_dir)
    log.info("Extraction complete (%d files)", sum(1 for _ in agent_dir.rglob("*")))
    print("      ✓ 解压完成", flush=True)

    # ── Step 2: Create venv ────────────────────────────────────────────────
    print("[2/3] 正在创建 Python 虚拟环境...", flush=True)
    # ALWAYS use uv-managed python-build-standalone, NEVER system Python.
    #
    # The v1.4.1 fix ('--python-preference managed' + WindowsApps filter)
    # wasn't enough — when we passed a system Python EXECUTABLE PATH to
    # `uv venv --python <path>`, uv used that path verbatim regardless of
    # the preference setting (the preference only applies when uv has
    # to RESOLVE a version spec like "3.11", not when given a concrete
    # path). Users with corrupted system Python (idna codec missing,
    # python313.dll wheel conflicts from a prior parallel Python 3.13
    # install) hit hard fails inside the venv even after v1.4.1.
    #
    # Going `only-managed` + version spec "3.11" forces uv to download
    # and use its own python-build-standalone distribution. ~30 MB
    # one-time download, then cached. This eliminates an entire class
    # of "user's Python install is broken" issues — installer ships
    # with a known-good Python every time.
    log.info("Creating venv at %s (uv-managed python-build-standalone 3.11)", venv_dir)
    _run_uv(uv_exe, ["venv", str(venv_dir),
                     "--python", "3.11",
                     "--python-preference", "only-managed"],
            error_prefix="创建虚拟环境失败")
    print("      ✓ 虚拟环境创建完成", flush=True)

    # ── Step 3: Install dependencies ──────────────────────────────────────
    print("[3/3] 正在安装依赖（多镜像并行，约 1-3 分钟请耐心等待）...", flush=True)
    venv_python_path = venv_dir / "Scripts" / "python.exe"
    _run_uv(uv_exe, [
        "pip", "install",
        "-e", str(agent_dir),
        "--python", str(venv_python_path),
        # Use "first-index" strategy: uv stops at the first mirror that has
        # the package and downloads the wheel from THAT same mirror only.
        # Without this, uv resolves metadata from all indexes simultaneously
        # and may try to download a wheel from a mirror (e.g. Tsinghua) that
        # blocks direct .whl downloads with 403 Forbidden, even though the
        # package was available on the primary mirror. "first-index" prevents
        # that cross-mirror 403 scenario.
        "--index-strategy", "first-index",
        # Primary: Aliyun syncs frequently, wide package coverage, reliable wheel downloads
        "--index-url", "https://mirrors.aliyun.com/pypi/simple/",
        # Fallbacks tried in order when a package isn't on the primary mirror.
        # Note: Tsinghua (pypi.tuna.tsinghua.edu.cn) is intentionally excluded
        # because it returns 403 Forbidden on direct wheel downloads.
        "--extra-index-url", "https://mirrors.ustc.edu.cn/pypi/simple/",
        "--extra-index-url", "https://repo.huaweicloud.com/repository/pypi/simple/",
        "--extra-index-url", "https://pypi.org/simple/",
    ], error_prefix="依赖安装失败")
    # ── Step 3.5: Patch hermes-agent with neowow-coding-plan ─────────────
    # Upstream hermes-agent's PROVIDER_REGISTRY doesn't know about
    # `neowow-coding-plan`. The Docker image runs this same script after
    # pip install; native Windows installs were skipping it, so the WebUI
    # would write `model.provider = neowow-coding-plan` into config.yaml
    # but hermes_cli would reject every chat with "Unknown provider".
    # Auto-onboarding silently bailed out via _agent_recognizes_provider,
    # leaving /api/models empty and the user wondering where the catalog
    # went. Run the patch here so the agent CLI knows the provider before
    # the WebUI server boots.
    print("[3.5/3] 正在为 hermes-agent 注入 neowow-coding-plan provider...", flush=True)
    patch_script = BASE_DIR / "docker" / "patch_hermes_agent.py"
    if patch_script.exists():
        try:
            # --skip-import-verify: the script's verifier uses venv/bin/python
            # which doesn't exist on Windows (venv/Scripts/python.exe). We do
            # our own import check below.
            # Force UTF-8 decoding on the captured streams. Without
            # `encoding=`, subprocess.run(text=True) uses locale.
            # getpreferredencoding() which is GBK (cp936) on Chinese
            # Windows. The patch script + our verify probe both emit
            # UTF-8 (because we set PYTHONIOENCODING=utf-8 in the
            # subprocess env), so the GBK decoder choked on the first
            # multibyte sequence and the reader THREAD crashed with
            #   UnicodeDecodeError: 'gbk' codec can't decode byte 0x93
            #   ...
            # The exception fired from the daemon thread (which doesn't
            # propagate to subprocess.run), printed itself to stderr,
            # and main.py continued — the patch had already landed at
            # that point, so the install still finished, but the visible
            # traceback in the install console scared users. errors=
            # 'replace' is belt-and-suspenders in case the subprocess
            # ever emits a byte that's malformed UTF-8 (mojibake from a
            # downstream library); we want a "▒" character, not a crash.
            patch_proc = subprocess.run(
                [str(venv_python_path), str(patch_script),
                 "--agent-dir", str(agent_dir), "--skip-import-verify"],
                capture_output=True, encoding='utf-8', errors='replace', timeout=30,
                env=_clean_subprocess_env(extra={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}),
            )
            log.info("patch_hermes_agent stdout:\n%s", patch_proc.stdout)
            if patch_proc.stderr:
                log.info("patch_hermes_agent stderr:\n%s", patch_proc.stderr)
            # The script exits non-zero on a final cosmetic UnicodeEncodeError
            # (print "✓") under Windows GBK terminals — the patches themselves
            # have already landed. Verify by importing.
            verify = subprocess.run(
                [str(venv_python_path), "-c",
                 "from hermes_cli.auth import PROVIDER_REGISTRY; "
                 "import sys; "
                 "sys.exit(0 if 'neowow-coding-plan' in PROVIDER_REGISTRY else 1)"],
                capture_output=True, encoding='utf-8', errors='replace', timeout=15,
                env=_clean_subprocess_env(),
            )
            if verify.returncode != 0:
                raise RuntimeError(
                    f"patch_hermes_agent appeared to run but PROVIDER_REGISTRY "
                    f"still lacks 'neowow-coding-plan'. stderr: {verify.stderr[:300]}"
                )
            print("      ✓ provider 注入完成", flush=True)
        except Exception as exc:
            log.exception("patch_hermes_agent failed: %s", exc)
            raise RuntimeError(
                f"为 hermes-agent 注入 neowow-coding-plan 失败：\n{exc}\n\n"
                "WebUI 仍可启动,但默认模型会不可用。"
            ) from exc
    else:
        log.warning("patch script not found at %s — skipping", patch_script)

    print("\n      ✓ 安装完成！Hermes 即将启动...\n", flush=True)
    log.info("Windows agent install complete — venv at %s", venv_dir)


def _start_webui_server_windows(port: int, host: str) -> subprocess.Popen:
    """Start server.py directly using the hermes-agent venv Python.

    Bypasses bootstrap.py (which blocks native Windows).
    Logs server stdout/stderr to %APPDATA%/Hermes/webui-server.log.
    Returns the Popen object; caller uses proc.pid for cleanup tracking.
    Raises RuntimeError if venv python or server.py is missing.
    """
    agent_dir = Path.home() / ".hermes" / "hermes-agent"
    venv_python = agent_dir / "venv" / "Scripts" / "python.exe"
    server_py = WEBUI_DIR / "server.py"

    if not venv_python.exists():
        raise RuntimeError(
            f"venv Python 未找到：{venv_python}\n"
            "请删除 ~/.hermes/hermes-agent/ 后重启 Hermes 重新安装。"
        )
    if not server_py.exists():
        raise RuntimeError(
            f"server.py 未找到：{server_py}\n"
            "请重新下载 Hermes Installer。"
        )

    env = _clean_subprocess_env(extra={
        "HERMES_WEBUI_PORT":      str(port),
        "HERMES_WEBUI_HOST":      host,
        "HERMES_WEBUI_AGENT_DIR": str(agent_dir),
        "PYTHONUNBUFFERED":       "1",
        "PYTHONUTF8":             "1",
    })

    server_log_path = _LOG_DIR / "webui-server.log"
    log.info(
        "Starting server.py: python=%s server=%s cwd=%s log=%s",
        venv_python, server_py, agent_dir, server_log_path,
    )
    # Open in append mode so logs survive across restarts
    server_log_fh = open(server_log_path, "ab")  # noqa: SIM115 (kept open for subprocess lifetime)
    # Pass -X frozen_modules=off to work around a python-build-standalone
    # bug: in some Python 3.11.x builds the frozen asyncio importer fails to
    # bind `base_events` as a name on the asyncio package when imported via
    # webui's lazy/multi-threaded import chain, raising
    #   NameError: name 'base_events' is not defined
    # at asyncio/__init__.py:25. The same Python works fine for hermes CLI
    # (which imports asyncio first thing) — webui only hits it because
    # api.routes / api.streaming / api.profiles import run_agent + cron
    # deep in the chain. Disabling frozen modules forces the regular
    # filesystem importer for stdlib, which doesn't trigger the bug.
    # Startup cost (~50ms) is irrelevant next to webui's other init work.
    proc = subprocess.Popen(
        [str(venv_python), "-X", "frozen_modules=off", str(server_py)],
        cwd=str(agent_dir),
        env=env,
        stdout=server_log_fh,
        stderr=subprocess.STDOUT,
    )
    log.info("server.py PID=%s — log at %s", proc.pid, server_log_path)
    return proc


def _start_gateway_windows() -> "subprocess.Popen | None":
    """Start ``hermes gateway run`` as a background subprocess on Windows.

    The gateway is the long-lived daemon hermes-agent uses for messaging
    platforms (Telegram/Discord/etc.) and scheduled-job ticks. Without it
    the WebUI's 计划任务 (scheduled jobs) panel shows
    "GATEWAY NOT CONFIGURED" — jobs can still be created and run manually,
    but periodic ticks never fire.

    The frozen exe spawns this alongside server.py so the user gets a
    fully-functional install out of the box. Set
    ``HERMES_AUTO_START_GATEWAY=0`` to opt out (e.g. for users who want
    `hermes gateway install` as a Scheduled Task instead, or who don't
    want the ~50 MB gateway resident in memory).

    Returns the Popen handle (caller appends pid to server_pids for
    atexit cleanup) or None when skipped / missing.
    """
    if os.environ.get("HERMES_AUTO_START_GATEWAY", "1").strip().lower() in {"0", "false", "no", "off"}:
        log.info("HERMES_AUTO_START_GATEWAY=0 — skipping gateway auto-start")
        return None

    agent_dir = Path.home() / ".hermes" / "hermes-agent"
    hermes_exe = agent_dir / "venv" / "Scripts" / "hermes.exe"
    if not hermes_exe.exists():
        log.warning("hermes.exe not found at %s — skipping gateway auto-start", hermes_exe)
        return None

    gateway_log = _LOG_DIR / "gateway.log"
    log.info("Starting hermes gateway: exe=%s log=%s", hermes_exe, gateway_log)
    try:
        gw_log_fh = open(gateway_log, "ab")  # noqa: SIM115 — kept open for subprocess lifetime
        # --replace: take over if a stale instance is around (e.g. user
        #            crashed the previous Hermes window without atexit
        #            cleanup firing). --quiet: gateway is chatty by default
        #            and we don't want to flood gateway.log with INFO when
        #            the user has no messaging platforms configured.
        # --accept-hooks: avoid blocking on hook prompts inside a headless
        #                 background process (no TTY available).
        proc = subprocess.Popen(
            [str(hermes_exe), "gateway", "run", "--quiet", "--replace", "--accept-hooks"],
            cwd=str(agent_dir),
            env=_clean_subprocess_env(extra={
                "PYTHONUNBUFFERED": "1",
                "PYTHONUTF8":       "1",
                # Auto-accept any hooks the gateway encounters at runtime
                # — same default we use for `hermes` CLI invocations
                # elsewhere in the installer.
                "HERMES_ACCEPT_HOOKS": "1",
            }),
            stdout=gw_log_fh,
            stderr=subprocess.STDOUT,
            # CREATE_NO_WINDOW: hide the new console window the gateway
            # would otherwise spawn on Windows (subprocess.Popen creates
            # a fresh console by default when the parent has one).
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log.info("hermes gateway PID=%s — log at %s", proc.pid, gateway_log)
        return proc
    except Exception as exc:
        log.warning("gateway auto-start failed (non-fatal): %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════════════
# Native window — pywebview (macOS cocoa + Windows edgechromium)
# ══════════════════════════════════════════════════════════════════════════
def _open_native_window(title: str, url: str, on_close=None, current_mode: str = "local"):
    """Open URL in a native window using pywebview.
    macOS  → WKWebView via cocoa backend
    Windows → Edge WebView2 via edgechromium backend

    *on_close* is invoked synchronously when the window is closing or has
    closed.  This is the only reliable cleanup hook on macOS — the cocoa
    backend's ``NSApp.terminate`` exits the process without returning from
    ``webview.start()``, so ``finally`` / ``atexit`` may never run.

    *current_mode* is `"local"` or `"remote"`, used by the menu bar to
    mark which Mode item is currently selected. Caller passes whatever
    it read from gateway.json before deciding which run path to take.
    """
    # ── WebView2 check (Windows only) ────────────────────────────────
    # Must run BEFORE importing webview so the error is actionable
    # (if WebView2 is absent, webview.start() would crash with a
    # cryptic DLL error that gives the user no clue what to do).
    wv2_err = _check_webview2_windows()
    if wv2_err:
        log.error("WebView2 Runtime missing: %s", wv2_err)
        _send_crash_report("startup_webview2_missing", wv2_err)
        _alert(
            "缺少必要组件：Edge WebView2 Runtime",
            "Hermes 需要 Microsoft Edge WebView2 Runtime 才能显示界面。\n\n"
            "请访问以下地址免费下载安装（约 2MB）：\n"
            "https://go.microsoft.com/fwlink/p/?LinkId=2124703\n\n"
            "安装完成后重新启动 Hermes 即可。\n\n"
            f"错误日志保存在：\n{_LOG_PATH}",
        )
        sys.exit(1)

    try:
        import webview
    except ImportError:
        log.warning("pywebview not installed — falling back to browser")
        _send_crash_report("startup_pywebview_missing", "pywebview ImportError")
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

        # Build the top-level native menu bar (账号 / 模式 / 视图 / 帮助).
        # Menu items run callbacks that talk back to the embedded WebUI
        # via window.evaluate_js, write gateway.json, or open external
        # URLs in the default browser. See desktop_menu.py for the full
        # layout + each action's behavior.
        menu_items = []
        try:
            import desktop_menu
            menu_items = desktop_menu.build_menu(window, current_mode)
        except Exception as exc:
            log.warning("could not build native menu: %s", exc)

        webview.start(gui=gui, debug=False, menu=menu_items)
        log.info("native window closed")
    except Exception as exc:
        import traceback as _tb
        tb = _tb.format_exc()
        log.exception("pywebview %s failed: %s", gui, exc)
        _send_crash_report("startup_pywebview_failed", str(exc), {"traceback": tb[:2000]})
        if on_close is not None:
            try:
                on_close()
            except Exception:
                pass
        _alert(
            "Hermes 窗口启动失败",
            f"原生窗口启动失败：\n{exc}\n\n"
            f"错误日志保存在：\n{_LOG_PATH}",
        )
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


def _read_gateway_config() -> dict:
    """Read ~/.hermes/webui/gateway.json directly — main.py runs BEFORE
    the webui package is on the import path, so we can't `from api.
    gateway_config import ...` here. Keep the schema in sync with
    webui/api/gateway_config.py.

    Always returns a dict with at minimum a `mode` key. Errors degrade
    silently to {"mode":"local"} so a corrupt file never bricks the
    installer — user can fix via --reset-gateway.
    """
    import json
    state_dir = Path(os.getenv("HERMES_WEBUI_STATE_DIR",
                               str(Path.home() / ".hermes" / "webui")))
    path = state_dir / "gateway.json"
    if not path.exists():
        return {"mode": "local"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"mode": "local"}
        return raw
    except Exception as exc:
        log.warning("gateway.json unreadable, falling back to local: %s", exc)
        return {"mode": "local"}


def _reset_gateway_config():
    """Wipe the gateway config file and report. Used by `--reset-gateway`
    to recover when a saved remote URL is broken/unreachable and the
    user can't access the settings UI to fix it."""
    state_dir = Path(os.getenv("HERMES_WEBUI_STATE_DIR",
                               str(Path.home() / ".hermes" / "webui")))
    path = state_dir / "gateway.json"
    if path.exists():
        try:
            path.unlink()
            print(f"已重置 gateway 配置: {path}", file=sys.stderr)
        except OSError as exc:
            print(f"无法删除 {path}: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"配置文件不存在: {path}（已经是本地模式）", file=sys.stderr)


def _run_remote_mode(url: str, label: str = ""):
    """Remote mode: skip bootstrap entirely, open the configured URL in
    a native window. The cloud-side WebUI handles its own auth (login
    page, session cookies) — we don't need to thread tokens through.

    No port-conflict check, no install, no spawn, no cleanup. The window
    closing == process exit. Pure thin client.
    """
    title = f"Hermes · {label}" if label else "Hermes (远程)"
    log.info("Remote mode: opening %s as %s", url, title)
    try:
        _open_native_window(title, url, on_close=None, current_mode="remote")
    except Exception as exc:
        log.exception("remote-mode pywebview failed: %s", exc)
        _alert("Hermes Installer",
               f"无法打开远程 WebUI: {exc}\n\n"
               f"配置的 URL: {url}\n\n"
               f"如果 URL 有误，请运行：\n"
               f"  hermes-installer --reset-gateway\n"
               f"重置回本地模式。")
        sys.exit(1)


def main():
    # Recovery flag — wipes gateway.json and exits. Useful when a saved
    # remote URL is broken and the user can't reach the settings UI.
    if "--reset-gateway" in sys.argv:
        _reset_gateway_config()
        sys.exit(0)

    # ── Remote mode short-circuit ────────────────────────────────────────
    # If the user has configured a remote WebUI URL, bypass bootstrap +
    # install + local server entirely. The Hermes Installer becomes a
    # thin pywebview shell pointing at the cloud WebUI.
    cfg = _read_gateway_config()
    if cfg.get("mode") == "remote":
        url = (cfg.get("url") or "").strip()
        if url:
            _run_remote_mode(url, label=str(cfg.get("label") or ""))
            return
        # Configured as remote but URL empty: log + fall through to local.
        log.warning("gateway.json has mode=remote but empty url; falling back to local")

    # ── Local mode (default / current behavior) ──────────────────────────
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

    # ── Launch WebUI server ──────────────────────────────────────────────
    if sys.platform == "win32":
        # ── Hide the console window immediately ──────────────────────────
        # On Windows the process starts with console=True so PyInstaller
        # gives us a black CMD window.  We hide it right away so the user
        # only ever sees the Edge WebView window, not a raw terminal.
        # During first-run installation we reveal it so the user can watch
        # the progress output, then hide it again before opening the UI.
        _hide_console()

        # ── Windows: install if needed, start server.py directly ────────
        # bootstrap.py has ensure_supported_platform() that blocks Windows.
        # We handle install + launch inline instead.
        if not _is_agent_installed():
            # Distinguish "not installed yet" from "installed but contaminated"
            # by checking if the venv dir exists. Contaminated venv = wipe and
            # rebuild with v1.4.2's only-managed Python; the v1.4.1 path used
            # a system Python that was broken (Microsoft Store sandboxed,
            # python.org install with python313.dll wheel pollution, etc.).
            existing_venv = Path.home() / ".hermes" / "hermes-agent" / "venv"
            if existing_venv.exists():
                log.info(
                    "Existing venv failed health check — wiping for clean reinstall"
                )
                print("\n" + "=" * 56, flush=True)
                print("   检测到旧版本环境损坏，正在清理并重新安装...", flush=True)
                print("=" * 56 + "\n", flush=True)
                _wipe_contaminated_agent_venv()

            log.info("First run: hermes-agent not installed — starting Windows setup")
            # Show the console so the user can watch install progress.
            _show_console()
            print("\n" + "=" * 56, flush=True)
            print("   Hermes 首次启动 — 正在安装必要组件", flush=True)
            print("   日志保存在：" + str(_LOG_PATH), flush=True)
            print("=" * 56 + "\n", flush=True)
            try:
                _windows_install_agent()
            except Exception as exc:
                import traceback as _tb
                tb = _tb.format_exc()
                log.exception("Windows install failed: %s", exc)
                _send_crash_report("windows_install_failed", str(exc), {"traceback": tb[:2000]})
                _alert(
                    "Hermes 安装失败",
                    f"首次安装 hermes-agent 时出错：\n\n{exc}\n\n"
                    f"请检查网络连接后重试。\n"
                    f"详细日志：{_LOG_PATH}",
                )
                sys.exit(1)
            # Install complete — hide console again before opening UI.
            _hide_console()

        log.info("Windows: starting server.py directly (bypassing bootstrap.py)")
        try:
            _win_server_proc = _start_webui_server_windows(port, host)
        except Exception as exc:
            log.exception("Failed to start server.py on Windows: %s", exc)
            _alert(
                "Hermes 启动失败",
                f"无法启动 WebUI 服务：\n\n{exc}\n\n"
                f"日志：{_LOG_PATH}",
            )
            sys.exit(1)

        server_pids = [_win_server_proc.pid]
        log.info("Windows server PID=%s — waiting for WebUI on port %d (timeout=%ds)",
                 _win_server_proc.pid, port, WEBUI_STARTUP_TIMEOUT)
        ready = _wait_for_server(port, timeout=WEBUI_STARTUP_TIMEOUT)

        # ── Windows: explicit diagnostics on startup failure ─────────────
        # The old behavior was to silently open the WebView even when ready
        # was False, leading to user-visible ERR_CONNECTION_REFUSED with no
        # actionable error. Now we detect server-process crashes (poll
        # returns non-None returncode), surface the last lines of the server
        # log, and pop an alert with the failing details + log path so the
        # user can copy-paste it for support — instead of having to know
        # %APPDATA%\Hermes\webui-server.log exists.
        if not ready:
            returncode = _win_server_proc.poll()
            server_log_path = _LOG_DIR / "webui-server.log"
            tail = ""
            try:
                if server_log_path.exists():
                    with open(server_log_path, "rb") as fh:
                        # Read last ~6 KB; enough to capture a Python traceback
                        try:
                            fh.seek(0, 2)
                            size = fh.tell()
                            fh.seek(max(0, size - 6144))
                            tail = fh.read().decode("utf-8", errors="replace")
                        except OSError:
                            tail = fh.read().decode("utf-8", errors="replace")
            except Exception as exc:
                log.debug("could not read server log tail: %s", exc)
                tail = f"(无法读取日志: {exc})"

            tail_short = tail.strip()
            if len(tail_short) > 1500:
                tail_short = "..." + tail_short[-1500:]

            if returncode is not None:
                # Process exited before binding the port. The log tail should
                # carry the Python traceback from server.py.
                log.error("server.py died early (rc=%s) — log tail follows:\n%s",
                          returncode, tail)
                _alert(
                    "Hermes 启动失败",
                    f"WebUI 服务在启动过程中崩溃（退出码 {returncode}）。\n\n"
                    f"日志末尾：\n{tail_short or '(日志为空)'}\n\n"
                    f"完整日志：{server_log_path}",
                )
            else:
                # Process still alive but never bound the port within timeout.
                log.error("server.py alive but port %d not bound after %ds — log tail:\n%s",
                          port, WEBUI_STARTUP_TIMEOUT, tail)
                _alert(
                    "Hermes 启动超时",
                    f"WebUI 服务在 {WEBUI_STARTUP_TIMEOUT} 秒内未绑定端口 {port}。\n"
                    f"进程仍在运行 (PID {_win_server_proc.pid})。\n\n"
                    f"日志末尾：\n{tail_short or '(日志为空)'}\n\n"
                    f"完整日志：{server_log_path}",
                )
            # ── Report timeout to backend so we can see how often this happens ──
            if not ready and _crash_reporter is not None:
                try:
                    _crash_reporter.report(
                        phase="wait_for_server_timeout",
                        error=f"webui server did not bind port {port} within {WEBUI_STARTUP_TIMEOUT}s",
                        log_path=str(_LOG_DIR / "webui-server.log"),
                        extra={
                            "port": port,
                            "subprocess_returncode": (
                                _win_server_proc.poll() if _win_server_proc else None
                            ),
                        },
                    )
                except Exception as exc:
                    log.debug("wait_for_server_timeout report failed: %s", exc)
            sys.exit(1)

        # ── Auto-start the hermes gateway daemon ─────────────────────────
        # Required for the 计划任务 (scheduled jobs) panel's tick scheduler
        # — without it the panel surfaces "GATEWAY NOT CONFIGURED". Best-
        # effort: gateway is optional, so failures here only WARN (chat
        # still works). The PID joins server_pids so atexit/SIGTERM cleans
        # it up alongside server.py.
        _gw_proc = _start_gateway_windows()
        if _gw_proc is not None:
            server_pids.append(_gw_proc.pid)

    else:
        # ── macOS / Linux: existing bootstrap.py path (unchanged) ────────
        python_exe = _find_bootstrap_python()

        if not BOOTSTRAP_PY.exists():
            _alert("Hermes Installer",
                   f"找不到 WebUI 启动脚本。\n路径：{BOOTSTRAP_PY}\n"
                   f"请确认 webui/ 目录与 main.py 在同一文件夹下。")
            sys.exit(1)

        env = _clean_subprocess_env(extra={
            "HERMES_WEBUI_PORT": str(port),
            "HERMES_WEBUI_HOST": host,
            "PYTHONUNBUFFERED":  "1",
            "PYTHONUTF8":        "1",
        })

        log.info("Launching bootstrap.py: %s %s", python_exe, BOOTSTRAP_PY)

        try:
            proc = subprocess.Popen(
                [python_exe, str(BOOTSTRAP_PY), str(port), "--host", host],
                cwd=str(WEBUI_DIR),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
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

        ready = _wait_for_server(port, timeout=WEBUI_STARTUP_TIMEOUT)
        if not ready:
            log.warning("Port %d not ready after %ds, trying anyway in 30s",
                        port, WEBUI_STARTUP_TIMEOUT)
            time.sleep(30)
            ready = _wait_for_server(port, timeout=10)

        # ── Report timeout to backend so we can see how often this happens ──
        if not ready and _crash_reporter is not None:
            try:
                _crash_reporter.report(
                    phase="wait_for_server_timeout",
                    error=f"webui server did not bind port {port} within {WEBUI_STARTUP_TIMEOUT}s",
                    log_path=str(_LOG_DIR / "webui-server.log"),
                    extra={
                        "port": port,
                        "subprocess_returncode": (
                            proc.poll() if proc else None
                        ),
                    },
                )
            except Exception as exc:
                log.debug("wait_for_server_timeout report failed: %s", exc)

        # bootstrap.py spawns server.py detached then exits — find it by port
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

    # ── Monitor subprocess for unexpected death after webview opens ──
    if locals().get("_win_server_proc") is not None and _crash_reporter is not None:
        _win_proc_ref = _win_server_proc

        def _monitor_webui_subprocess():
            while True:
                rc = _win_proc_ref.poll()
                if rc is None:
                    time.sleep(2)
                    continue
                log.error("webui server.py exited rc=%s while installer alive", rc)
                try:
                    _crash_reporter.report(
                        phase="webui_subprocess_exit_unexpected",
                        error=f"server.py exited rc={rc} while installer was running",
                        log_path=str(_LOG_DIR / "webui-server.log"),
                        extra={"returncode": rc},
                    )
                except Exception as exc:
                    log.debug("subprocess-exit report failed: %s", exc)
                break

        threading.Thread(
            target=_monitor_webui_subprocess,
            name="hermes-webui-subprocess-monitor",
            daemon=True,
        ).start()

    try:
        _open_native_window(title, url, on_close=_cleanup_servers, current_mode="local")
    finally:
        # Defense-in-depth: also clean up if we somehow get here. On macOS
        # this rarely runs because cocoa terminates the process directly;
        # the on_close callback registered above is the primary path.
        _cleanup_servers()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise   # normal exit codes must propagate
    except Exception as exc:
        import traceback as _tb
        tb = _tb.format_exc()
        log.exception("Unhandled exception in main: %s", exc)
        try:
            _send_crash_report("main_unhandled", str(exc), {"traceback": tb[:2000]})
        except Exception:
            pass
        _alert(
            "Hermes 遇到意外错误",
            f"应用启动时遇到未处理的错误：\n\n{exc}\n\n"
            f"错误日志保存在：\n{_LOG_PATH}\n\n"
            "请截图此对话框后联系支持团队。",
        )
        sys.exit(1)
