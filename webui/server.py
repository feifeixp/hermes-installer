"""
Hermes Web UI -- Main server entry point.
Thin routing shell: imports Handler, delegates to api/routes.py, runs server.
All business logic lives in api/*.
"""
# ─── asyncio preload (Windows + uv-managed Python 3.11.x workaround) ────────
# Reported in the wild: when webui's import chain reaches `import asyncio`
# transitively via api.profiles → cron.scheduler (during init_profile_state),
# asyncio's __init__.py crashes with
#   NameError: name 'base_events' is not defined
# at line 25 (__all__ assignment). The same Python install loads asyncio
# fine standalone (`python -c "import asyncio"` works). Importtime traces
# show the asyncio submodule cascade *partially* completes — base_events
# itself loads — but the parent package's __init__.py never finishes
# binding base_events as a name in the asyncio namespace.
#
# Loading asyncio HERE as the very first import sidesteps the bug: once
# asyncio is fully in sys.modules, every later `import asyncio` (from
# cron.scheduler, run_agent, api.turn_journal, etc.) is a no-op that
# returns the already-loaded module without re-executing __init__.py.
# Verified fix on the affected Windows machine.
import asyncio as _asyncio_preload  # noqa: F401 — load order matters

# ── Crash reporter sys.excepthook (Windows reliability hardening) ────────
# Catches any unhandled exception during import or main() execution and
# reports it to https://app.neowow.studio/api/client-log. Without this,
# webui crashes leave only a local log file behind that nobody on the
# backend can see without the user pasting it. Sourced from
# docs/superpowers/specs/2026-05-27-crash-reporter-design.md
import sys as _sys, os as _os

# Load crash_reporter from HERMES_INSTALLER_BASE_DIR (the frozen exe's
# PyInstaller extraction dir, set by main.py before subprocess spawn).
# Use importlib.util.spec_from_file_location to load by EXPLICIT path
# WITHOUT touching sys.path.
#
# History: we previously did `sys.path.insert(0, _installer_dir)` here.
# In the frozen-exe spawn scenario _installer_dir is `_MEI<rand>/` —
# the PyInstaller extraction directory which contains the frozen
# Python 3.13's stdlib .pyd files (unicodedata.pyd, _socket.pyd, …,
# _cffi_backend.cp313-win_amd64.pyd). Putting that on sys.path[0]
# made the venv Python 3.11 import unicodedata from there ahead of
# its own cpython-3.11/DLLs, triggering
#   "Module use of python313.dll conflicts with this version of Python"
# and then a cascading "LookupError: unknown encoding: idna" inside
# socket.getfqdn → gethostbyaddr → encodings.idna lookup. server.py
# died at QuietHTTPServer.server_bind and the WebUI never came up.
#
# Same anti-pattern + same fix as the v1.4.6 webui/api/__init__.py
# repair. The two fixes are independent — this one was introduced
# later (Phase η.5 crash-reporter integration) and went unnoticed
# because the runtime health check happens to pass in the
# `_clean_subprocess_env` env even though the live spawn fails.
_cr = None
_installer_dir = _os.environ.get("HERMES_INSTALLER_BASE_DIR")
if _installer_dir:
    try:
        import importlib.util as _ilu
        _cr_path = _os.path.join(_installer_dir, "crash_reporter.py")
        if _os.path.isfile(_cr_path):
            _spec = _ilu.spec_from_file_location("crash_reporter", _cr_path)
            if _spec and _spec.loader:
                _cr = _ilu.module_from_spec(_spec)
                _spec.loader.exec_module(_cr)
    except Exception:
        _cr = None  # Best-effort — crash reporter is itself non-critical.

_main_started = False  # Flipped to True at the top of main()


def _default_webui_log_path() -> "str | None":
    """Compute the webui-server.log path. Mirrors the formula in main.py:_LOG_DIR
    so we don't need any cross-process env-var coordination."""
    if _sys.platform == "win32":
        base = _os.environ.get("APPDATA") or _os.environ.get("TEMP")
        return _os.path.join(base, "Hermes", "webui-server.log") if base else None
    if _sys.platform == "darwin":
        return _os.path.expanduser("~/Library/Logs/Hermes/webui-server.log")
    base = _os.environ.get("TMPDIR", "/tmp")
    return _os.path.join(base, "hermes", "webui-server.log")


def _excepthook(exc_type, exc_value, tb):
    """Catch unhandled webui exceptions and report them before letting Python exit."""
    if _cr is None:
        return _sys.__excepthook__(exc_type, exc_value, tb)
    import traceback as _tb
    phase = "webui_startup_crash" if _main_started else "webui_pre_main_import_error"
    try:
        _cr.report(
            phase=phase,
            error=f"{exc_type.__name__}: {exc_value}",
            traceback="".join(_tb.format_exception(exc_type, exc_value, tb)),
            log_path=_default_webui_log_path(),
        )
    except Exception:
        pass  # IRON RULE: reporting must never re-raise into the hook
    return _sys.__excepthook__(exc_type, exc_value, tb)


_sys.excepthook = _excepthook

import logging
import os
import re
import socket
import subprocess
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ── Test-mode network isolation ─────────────────────────────────────────────
# When `HERMES_WEBUI_TEST_NETWORK_BLOCK=1` is set in the environment, refuse
# outbound socket connections to anything that is not loopback / RFC1918 /
# link-local / reserved-TLD. This catches accidental real outbound (forgotten
# mocks, leaked credentials triggering SDK init, new code paths bypassing an
# existing mock) so the test suite stays hermetic and fast.
#
# tests/conftest.py sets this env var on every test_server subprocess so the
# server.py-side network isolation matches the pytest-process-side isolation
# already installed there.
#
# A test that legitimately needs real outbound spawns the server with the env
# var unset (no current callers — every test_server-using test should be
# mockable).
if os.environ.get("HERMES_WEBUI_TEST_NETWORK_BLOCK", "").strip() in ("1", "true", "yes"):
    _REAL_CREATE_CONN = socket.create_connection
    _REAL_SOCK_CONNECT = socket.socket.connect

    import re as _re

    def _re_match_unique_local_ipv6(h):
        """Match IPv6 fc00::/7 (canonical syntax). Tighter than startswith('fc')
        so we don't mistakenly classify hostnames like 'food.example.com' as local."""
        return bool(_re.match(r"^f[cd][0-9a-f]{0,2}:", h))

    def _addr_is_local(host):
        if not isinstance(host, str):
            return False
        h = host.strip().lower()
        if not h:
            return False
        # IPv6 unique-local fc00::/7: require hex pair + colon to avoid
        # matching hostnames like "food.example.com" or "fdsa.test".
        if h in ("::1", "0:0:0:0:0:0:0:1") or h.startswith("fe80:") or _re_match_unique_local_ipv6(h):
            return True
        if h == "localhost" or h.endswith(".localhost"):
            return True
        if h.endswith(".local") or h.endswith(".test") or h.endswith(".invalid"):
            return True
        if h == "example.com" or h.endswith(".example.com"):
            return True
        if h == "example.net" or h.endswith(".example.net"):
            return True
        if h == "example.org" or h.endswith(".example.org"):
            return True
        if h.endswith(".example"):
            return True
        if h and h[0].isdigit() and h.count(".") == 3:
            try:
                o1, o2, o3, o4 = [int(p) for p in h.split(".")]
            except ValueError:
                return False
            if o1 == 127:
                return True
            if o1 == 10:
                return True
            if o1 == 192 and o2 == 168:
                return True
            if o1 == 172 and 16 <= o2 <= 31:
                return True
            if o1 == 169 and o2 == 254:
                return True
            if o1 == 203 and o2 == 0 and o3 == 113:
                return True
        return False

    def _blocked_create_connection(address, *a, **kw):
        try:
            host = address[0]
        except (TypeError, IndexError):
            host = ""
        if _addr_is_local(host):
            return _REAL_CREATE_CONN(address, *a, **kw)
        raise OSError(
            f"hermes test network isolation (server.py): outbound to {address!r} blocked"
        )

    def _blocked_socket_connect(self, address):
        try:
            host = address[0]
        except (TypeError, IndexError):
            host = ""
        if _addr_is_local(host):
            return _REAL_SOCK_CONNECT(self, address)
        raise OSError(
            f"hermes test network isolation (server.py): socket.connect to {address!r} blocked"
        )

    socket.create_connection = _blocked_create_connection
    socket.socket.connect = _blocked_socket_connect


try:
    import resource
except ImportError:  # pragma: no cover - resource is Unix-only
    resource = None
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_CSP_CONNECT_BASE = (
    "'self' http://127.0.0.1:* http://localhost:* "
    "ws://127.0.0.1:* ws://localhost:*"
)
_CSP_EXTRA_CONNECT_RE = re.compile(
    r"^(?:https?|wss?)://(?:\*\.)?[A-Za-z0-9._~-]+(?::(?P<port>\d{1,5}|\*))?$"
)


def _valid_csp_extra_connect_source(source: str) -> bool:
    match = _CSP_EXTRA_CONNECT_RE.fullmatch(source)
    if not match:
        return False
    port = match.group("port")
    if not port or port == "*":
        return True
    try:
        return 1 <= int(port) <= 65535
    except ValueError:
        return False


def _csp_extra_connect_src() -> str:
    raw = os.getenv("HERMES_WEBUI_CSP_CONNECT_EXTRA", "").strip()
    if not raw:
        return ""
    sources = raw.split()
    if not sources or any(not _valid_csp_extra_connect_source(src) for src in sources):
        logger.warning("Ignoring invalid HERMES_WEBUI_CSP_CONNECT_EXTRA value")
        return ""
    return " " + " ".join(sources)


def _build_csp_report_only_policy() -> str:
    connect_src = _CSP_CONNECT_BASE + _csp_extra_connect_src()
    return (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "media-src 'self' data: blob:; "
        f"connect-src {connect_src}; "
        "report-uri /api/csp-report; report-to csp-endpoint"
    )

from api.auth import check_auth
from api.config import HOST, PORT, STATE_DIR, SESSION_DIR, DEFAULT_WORKSPACE
from api.helpers import j, get_profile_cookie
from api.profiles import set_request_profile, clear_request_profile
# Phase β.16 cookie-as-JWT request scoping: in cloud auth mode
# (HERMES_WEBUI_AUTH_MODE=neodomain) the JWT lives in the per-request
# neoToken cookie, not on disk. We stash it into a threadlocal at
# request start so api.neowow.get_jwt() picks it up transparently.
# No-op when not in cloud mode.
from api.neowow import set_request_jwt_from_cookie, clear_request_jwt
from api.routes import handle_delete, handle_get, handle_patch, handle_post, handle_put
from api.startup import auto_install_agent_deps, fix_credential_permissions
from api.updates import WEBUI_VERSION


class QuietHTTPServer(ThreadingHTTPServer):
    """Custom HTTP server that silently handles common network errors."""
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, *args, **kwargs):
        server_address = args[0] if args else kwargs.get('server_address', None)
        if server_address and ':' in server_address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(*args, **kwargs)
        self.accept_loop_requests_total = 0
        self.accept_loop_last_request_at = 0.0

    def _handle_request_noblock(self):
        """Record accept-loop progress before dispatching a request handler.

        A process can be alive and still stop accepting/dispatching requests.
        Exposing this heartbeat on /health gives supervisors and watchdogs a
        cheap signal that the accept loop is still moving.

        Note: this method is called only from the single ``serve_forever()``
        thread in CPython socketserver, so the un-locked ``+=`` increment is
        safe — there is no other thread mutating these counters. The /health
        readers may see a stale value momentarily but never an inconsistent
        one (Python int reads are atomic). Per Opus advisor on stage-297.
        """
        self.accept_loop_requests_total += 1
        self.accept_loop_last_request_at = time.time()
        return super()._handle_request_noblock()
    
    def handle_error(self, request, client_address):
        """Override to suppress logging for common client disconnect errors."""
        exc_type, exc_value, _ = sys.exc_info()
        
        # Silently ignore common connection errors caused by client disconnects
        if exc_type in (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError):
            return
        
        # Also handle socket errors that indicate client disconnect
        if issubclass(exc_type, OSError):
            # errno 54 is Connection reset by peer on macOS/BSD
            # errno 104 is Connection reset by peer on Linux
            if getattr(exc_value, 'errno', None) in (32, 54, 104, 110):  # EPIPE, ECONNRESET, ETIMEDOUT
                return
        
        # For other errors, use default logging
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 enables keep-alive connection reuse — major latency win on
    # high-RTT links where every saved TCP handshake is 2×RTT. Each response
    # MUST declare framing (Content-Length, Transfer-Encoding: chunked, or
    # Connection: close) so the client knows where the message ends. Helpers
    # j()/t() emit Content-Length; SSE/streaming endpoints emit
    # Connection: close because the body has no terminator. See PR notes.
    protocol_version = "HTTP/1.1"
    timeout = 30  # seconds — kills idle/incomplete connections to prevent thread exhaustion
    
    def setup(self):
        """Set socket options for each accepted connection."""
        super().setup()
        # TCP_NODELAY — universal, disables Nagle for HTTP latency
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        # SO_KEEPALIVE — universal master switch (must be set before timing params)
        try:
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        # Per-platform timing parameters
        if hasattr(socket, 'TCP_KEEPIDLE'):  # Linux
            try:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            except OSError:
                pass
        elif hasattr(socket, 'TCP_KEEPALIVE'):  # macOS
            try:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, 10)
            except OSError:
                pass
    _ver_suffix = WEBUI_VERSION.removeprefix('v')
    server_version = ('HermesWebUI/' + _ver_suffix) if _ver_suffix != 'unknown' else 'HermesWebUI'
    _CSP_REPORT_TO = '{"group":"csp-endpoint","max_age":10886400,"endpoints":[{"url":"/api/csp-report"}]}'

    @classmethod
    def csp_report_only_policy(cls) -> str:
        return _build_csp_report_only_policy()

    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy-Report-Only", self.csp_report_only_policy())
        self.send_header("Report-To", self._CSP_REPORT_TO)
        super().end_headers()

    def log_message(self, fmt, *args): pass  # suppress default Apache-style log

    def log_request(self, code: str='-', size: str='-') -> None:
        """Structured JSON logs for each request."""
        import json as _json
        duration_ms = round((time.time() - getattr(self, '_req_t0', time.time())) * 1000, 1)
        record = _json.dumps({
            'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'method': getattr(self, 'command', None) or '-',
            'path': getattr(self, 'path', None) or '-',
            'status': int(code) if str(code).isdigit() else code,
            'ms': duration_ms,
        })
        print(f'[webui] {record}', flush=True)

    def do_GET(self) -> None:
        self._req_t0 = time.time()
        # Per-request profile context from cookie (issue #798)
        cookie_profile = get_profile_cookie(self)
        if cookie_profile:
            set_request_profile(cookie_profile)
        # Per-request JWT context from neoToken cookie (cloud auth mode).
        # No-op when not running with HERMES_WEBUI_AUTH_MODE=neodomain.
        set_request_jwt_from_cookie(self)
        try:
            parsed = urlparse(self.path)
            if not check_auth(self, parsed): return
            result = handle_get(self, parsed)
            if result is False:
                return j(self, {'error': 'not found'}, status=404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The browser/client closed the socket while we were writing the
            # response. This is expected for probes, tab closes, and SSE
            # reconnect races; do not convert it into a misleading server 500.
            return
        except Exception as e:
            print(f'[webui] ERROR {self.command} {self.path}\n' + traceback.format_exc(), flush=True)
            return j(self, {'error': 'Internal server error'}, status=500)
        finally:
            clear_request_profile()
            clear_request_jwt()

    def _handle_write(self, route_func) -> None:
        self._req_t0 = time.time()
        # Per-request profile context from cookie (issue #798)
        cookie_profile = get_profile_cookie(self)
        if cookie_profile:
            set_request_profile(cookie_profile)
        # Per-request JWT context from neoToken cookie (cloud auth mode).
        set_request_jwt_from_cookie(self)
        try:
            parsed = urlparse(self.path)
            # Stage-346 Opus SHOULD-FIX defense-in-depth: scope the CSP-report
            # auth carve-out to POST only. The endpoint is intentionally
            # unauthenticated (browsers omit cookies on CSP reports), but the
            # carve-out should not extend to PATCH/DELETE on that path even
            # though they currently fail through CSRF/routing fallthrough.
            _is_csp_report_post = (
                parsed.path == "/api/csp-report" and self.command == "POST"
            )
            if not _is_csp_report_post and not check_auth(self, parsed): return
            result = route_func(self, parsed)
            if result is False:
                return j(self, {'error': 'not found'}, status=404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The browser/client closed the socket while we were writing the
            # response. This is expected for probes, tab closes, and SSE
            # reconnect races; do not convert it into a misleading server 500.
            return
        except Exception as e:
            print(f'[webui] ERROR {self.command} {self.path}\n' + traceback.format_exc(), flush=True)
            return j(self, {'error': 'Internal server error'}, status=500)
        finally:
            clear_request_profile()
            clear_request_jwt()

    def do_POST(self) -> None:
        self._handle_write(handle_post)

    def do_PUT(self) -> None:
        self._handle_write(handle_put)

    def do_PATCH(self) -> None:
        self._handle_write(handle_patch)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self._req_t0 = time.time()
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_DELETE(self) -> None:
        self._handle_write(handle_delete)


def _raise_fd_soft_limit(target: int = 4096) -> dict:
    """Best-effort raise of RLIMIT_NOFILE for persistent WebUI hosts.

    macOS launchd jobs often start with a 256 soft limit. If a future FD leak
    regresses, that low ceiling turns a leak into a hard HTTP wedge quickly.
    Raising the soft limit does not hide leaks; it buys enough headroom for
    diagnostics and watchdog recovery.
    """
    if resource is None:
        return {"status": "unsupported"}
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    # On Unix, RLIM_INFINITY is commonly a large int; keep the logic explicit
    # so tests can use ordinary integers without depending on platform values.
    desired = int(target)
    if hard not in (-1, getattr(resource, "RLIM_INFINITY", object())):
        desired = min(desired, int(hard))
    if soft >= desired:
        return {"status": "unchanged", "soft": soft, "hard": hard}
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (desired, hard))
    except Exception as exc:
        return {"status": "error", "soft": soft, "hard": hard, "error": str(exc)}
    return {"status": "raised", "soft": desired, "hard": hard, "previous_soft": soft}


def main() -> None:
    global _main_started
    _main_started = True
    from api.config import print_startup_config, verify_hermes_imports, _HERMES_FOUND

    print_startup_config()

    fd_limit = _raise_fd_soft_limit()
    if fd_limit.get("status") == "raised":
        print(
            f"[ok] Raised file descriptor soft limit "
            f"{fd_limit.get('previous_soft')} -> {fd_limit.get('soft')}",
            flush=True,
        )
    elif fd_limit.get("status") == "error":
        print(f"[!!] WARNING: Could not raise file descriptor limit: {fd_limit.get('error')}", flush=True)

    # Fix sensitive file permissions before doing anything else
    fix_credential_permissions()

    # ── #1558 startup self-heal ─────────────────────────────────────────
    # If a previous process wrote a session JSON with fewer messages than
    # its .bak (the data-loss shape #1558 produced), restore from the .bak.
    # Safe to run unconditionally — a clean install is a no-op.
    try:
        from api.models import _active_state_db_path
        from api.session_recovery import recover_all_sessions_on_startup
        result = recover_all_sessions_on_startup(
            SESSION_DIR,
            rebuild_index=True,
            state_db_path=_active_state_db_path(),
        )
        if result.get("restored"):
            print(f"[recovery] Restored {result['restored']}/{result['scanned']} sessions from .bak (see #1558).", flush=True)
    except Exception as exc:
        # Recovery is best-effort; never block server startup.
        print(f"[recovery] startup recovery failed: {exc}", flush=True)

    within_container = False
    # Check for the "/.within_container" file to determine if we're running inside a container; this file is created in the Dockerfile
    try:
        with open('/.within_container', 'r') as f:
            within_container = True
    except FileNotFoundError:
        pass

    if within_container:
        print('[ok] Running within container.', flush=True)

    # Security: warn if binding non-loopback without authentication
    from api.auth import is_auth_enabled
    if HOST not in ('127.0.0.1', '::1', 'localhost') and not is_auth_enabled():
        print(f'[!!] WARNING: Binding to {HOST} with NO PASSWORD SET.', flush=True)
        print(f'     Anyone on the network can access your filesystem and agent.', flush=True)
        print(f'     Set a password via Settings or HERMES_WEBUI_PASSWORD env var.', flush=True)
        print(f'     To suppress: bind to 127.0.0.1 or set a password.', flush=True)
        if within_container:
            print(f'     Note: You are running within a container, must bind to 0.0.0.0 (IPv4) or :: (IPv6) to publish the port.', flush=True)
    elif not is_auth_enabled():
        print(f'  [tip] No password set. Any process on this machine can read sessions', flush=True)
        print(f'        and memory via the local API. Set HERMES_WEBUI_PASSWORD to', flush=True)
        print(f'        enable authentication.', flush=True)

    ok, missing, errors = verify_hermes_imports()
    if not ok and _HERMES_FOUND:
        print(f'[!!] Warning: Hermes agent found but missing modules: {missing}', flush=True)
        for mod, err in errors.items():
            print(f'     {mod}: {err}', flush=True)
        print('     Attempting to install missing dependencies from agent requirements.txt...', flush=True)
        auto_install_agent_deps()
        ok, missing, errors = verify_hermes_imports()
        if not ok:
            print(f'[!!] Still missing after install attempt: {missing}', flush=True)
            for mod, err in errors.items():
                print(f'     {mod}: {err}', flush=True)
            print('     Agent features may not work correctly.', flush=True)
        else:
            print('[ok] Agent dependencies installed successfully.', flush=True)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)

    # Start the gateway session watcher for real-time SSE updates
    try:
        from api.gateway_watcher import start_watcher
        start_watcher()
    except Exception as e:
        print(f'[!!] WARNING: Gateway watcher failed to start: {e}', flush=True)

    # Phase ζ.5 — surface config mismatches in container logs at boot
    # instead of waiting for users to hit cryptic chat-time errors.
    try:
        from api.startup_check import run_startup_checks
        run_startup_checks()
    except Exception as e:
        print(f'[!!] WARNING: startup self-checks raised: {e}', flush=True)

    httpd = QuietHTTPServer((HOST, PORT), Handler)

    # ── TLS/HTTPS setup (optional) ─────────────────────────────────────────
    from api.config import TLS_ENABLED, TLS_CERT, TLS_KEY
    scheme = 'https' if TLS_ENABLED else 'http'
    if TLS_ENABLED:
        try:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(TLS_CERT, TLS_KEY)
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            print(f'  TLS enabled: cert={TLS_CERT}, key={TLS_KEY}', flush=True)
        except Exception as e:
            print(f'[!!] WARNING: TLS setup failed ({e}), falling back to HTTP', flush=True)
            scheme = 'http'

    print(f'  Hermes Web UI listening on {scheme}://{HOST}:{PORT}', flush=True)
    if HOST in ('127.0.0.1', '::1') or within_container:
        print(f'  Remote access: ssh -N -L {PORT}:127.0.0.1:{PORT} <user>@<your-server>', flush=True)
    print(f'  Then open:     {scheme}://localhost:{PORT}', flush=True)
    print('', flush=True)

    # ── Startup skill sync (background, non-blocking) ──────────────────────
    # After the server is listening, kick off a one-time sync of the user's
    # subscriptions + platform-default skills. Runs in a daemon thread so it
    # never delays the WebUI from accepting requests. Requires a saved token
    # (neodomain cloud instances always have one; bare local installs may not).
    import threading as _threading
    def _startup_skill_sync():
        try:
            from api.skills import sync_skills_if_token
            result = sync_skills_if_token()
            if result is None:
                return  # no token → skip silently
            added   = len(result.get("added", []))
            skipped = len(result.get("skipped_dismissed", []))
            logger.info(
                "[startup] skills sync complete: +%d updated=%d removed=%d skipped=%d",
                added,
                len(result.get("updated", [])),
                len(result.get("removed", [])),
                skipped,
            )
            if added:
                print(
                    f'  [skills] Installed {added} skill(s) '
                    f'({skipped} skipped by user preference)',
                    flush=True,
                )
        except Exception as exc:
            logger.warning("[startup] skills sync failed (non-fatal): %s", exc)

    _threading.Thread(target=_startup_skill_sync, daemon=True, name="startup-skill-sync").start()

    # ── Startup gateway supervisor (background, non-blocking) ──────────────
    # Cloud single-container gateways run as a manual while-true `hermes
    # gateway run` loop INSIDE the container — it survives crashes but not
    # container recreation (the hourly auto-update SIGKILLs the container).
    # On every container boot, if this instance is configured to run a
    # gateway (gateway_state.json == running) and one isn't already alive,
    # relaunch the supervised loop. Daemon thread; never blocks startup.
    def _startup_gateway_supervisor():
        try:
            from hermes_constants import get_default_hermes_root
            from api.gateway_autostart import (
                gateway_running, maybe_start_gateway,
            )
            status = maybe_start_gateway(
                get_default_hermes_root(),
                running_check=gateway_running,
                spawn=lambda argv: subprocess.Popen(
                    argv,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                ),
                log=logger.info,
            )
            logger.info("[startup] gateway supervisor: %s", status)
        except Exception as exc:
            logger.warning("[startup] gateway supervisor failed (non-fatal): %s", exc)

    _threading.Thread(
        target=_startup_gateway_supervisor, daemon=True, name="startup-gateway-supervisor",
    ).start()

    # ── Server-side idle-keep-alive heartbeat ──────────────────────────────
    # When the user's browser is closed while Hermes executes a background
    # task (/background or /btw), the browser-side heartbeat stops. Without
    # intervention, the idle-sweep cron (every 5 min) would shut down the
    # ECS after IDLE_TIMEOUT_MIN of browser silence.
    #
    # This daemon thread checks every 5 minutes whether any background task
    # is still running. If yes, it POSTs to the broker's server-heartbeat
    # endpoint which updates lastSeenAt in TableStore — same effect as the
    # browser heartbeat, just from the server side.
    #
    # Only fires on cloud instances that have both env vars set:
    #   HERMES_INSTANCE_OWNER_USERID  (set by cloud-init via %USER_ID%)
    #   NEOWOW_HEARTBEAT_TOKEN        (set by cloud-init via %HEARTBEAT_TOKEN%)
    # Desktop/native Hermes installs are unaffected (env vars absent → no-op).
    def _background_heartbeat_loop():
        import time as _time
        try:
            from api.background import has_any_running_task
            from api.neowow import heartbeat_broker
        except ImportError:
            return  # modules not available — skip
        # Interval deliberately matches the broker's idle-sweep cron period
        # (5 min) so we always stamp lastSeenAt before the next sweep check.
        INTERVAL = 5 * 60
        logger.debug("[heartbeat] background heartbeat loop started")
        while True:
            _time.sleep(INTERVAL)
            try:
                if has_any_running_task():
                    sent = heartbeat_broker()
                    if sent:
                        logger.info("[heartbeat] keep-alive sent (background task running)")
            except Exception as exc:
                logger.debug("[heartbeat] loop iteration error: %s", exc)

    _threading.Thread(
        target=_background_heartbeat_loop,
        daemon=True,
        name="background-heartbeat",
    ).start()

    # ── Periodic skill sync ────────────────────────────────────────────────
    # The startup sync above runs once. But a user can subscribe to a skill
    # in the browser market (app.neowow.studio) AFTER the container booted —
    # there's no push from the dashboard. Without a periodic pull, that
    # subscription wouldn't reach this instance until the next container
    # rebuild (hourly auto-update) or a manual sync from the skills panel.
    # This daemon re-pulls every few minutes so browser subscriptions land
    # automatically; the WebUI chat path rebuilds the agent per turn, so the
    # next message can use the freshly-synced skill. No-op (silent) on
    # tokenless desktop installs — the token guard is inside sync_skills_if_token.
    def _periodic_skill_sync_loop():
        import time as _time
        INTERVAL = 5 * 60
        while True:
            _time.sleep(INTERVAL)
            try:
                from api.skills import sync_skills_if_token
                result = sync_skills_if_token()
                if result and (result.get("added") or result.get("updated") or result.get("removed")):
                    logger.info(
                        "[skills] periodic sync: +%d updated=%d removed=%d",
                        len(result.get("added", [])),
                        len(result.get("updated", [])),
                        len(result.get("removed", [])),
                    )
            except Exception as exc:
                logger.debug("[skills] periodic sync iteration error: %s", exc)

    _threading.Thread(
        target=_periodic_skill_sync_loop,
        daemon=True,
        name="periodic-skill-sync",
    ).start()

    try:
        httpd.serve_forever()
    finally:
        # Stop the gateway watcher on shutdown
        try:
            from api.gateway_watcher import stop_watcher
            stop_watcher()
        except Exception:
            logger.debug("Failed to stop gateway watcher during shutdown")
        # Drain pending memory-provider lifecycle commits before exit
        try:
            from api.session_lifecycle import drain_all_on_shutdown
            drain_all_on_shutdown()
        except Exception:
            logger.debug("Failed to drain lifecycle on shutdown", exc_info=True)

if __name__ == '__main__':
    main()
