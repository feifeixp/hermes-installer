"""
Hermes Web UI -- Optional authentication.

Three auth modes (set via HERMES_WEBUI_AUTH_MODE):
  • "none"      — no auth (default for local single-user installs)
  • "password"  — single shared password (HERMES_WEBUI_PASSWORD env var
                  or Settings UI). Current default for self-hosted.
  • "neodomain" — JWT cookie issued by app.neowow.studio. Used by the
                  cloud-hosted chat.neowow.studio deployment so users
                  log in via Neodomain OAuth on the dashboard, then the
                  shared `.neowow.studio` cookie is visible to the chat
                  subdomain. New in Phase 1 of remote-WebUI rollout.

Auto-detection: when HERMES_WEBUI_AUTH_MODE isn't explicitly set, mode
is "password" iff a password is configured (env var or settings.json),
else "none". Setting it to "neodomain" is always explicit — wouldn't
make sense to auto-enable on a local install.
"""
import base64
import hashlib
import hmac
import http.cookies
import json
import logging
import os
import secrets
import tempfile
import time

from api.config import STATE_DIR, load_settings

logger = logging.getLogger(__name__)

# ── Public paths (no auth required) ─────────────────────────────────────────
PUBLIC_PATHS = frozenset({
    '/login', '/health', '/favicon.ico',
    '/api/auth/login', '/api/auth/status',
    '/manifest.json', '/manifest.webmanifest',
})

COOKIE_NAME = 'hermes_session'
SESSION_TTL = 86400 * 30  # 30 days

_SESSIONS_FILE = STATE_DIR / '.sessions.json'


def _load_sessions() -> dict[str, float]:
    """Load persisted sessions from STATE_DIR, pruning expired entries.

    Returns an empty dict on any read or parse error so startup is never
    blocked by a corrupt or missing sessions file.
    """
    try:
        if _SESSIONS_FILE.exists():
            data = json.loads(_SESSIONS_FILE.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('malformed sessions file — expected dict')
            now = time.time()
            return {t: exp for t, exp in data.items()
                    if isinstance(t, str) and isinstance(exp, (int, float)) and exp > now}
    except Exception as e:
        logger.debug("Failed to load sessions file, starting fresh: %s", e)
    return {}


def _save_sessions(sessions: dict[str, float]) -> None:
    """Atomically persist sessions to STATE_DIR/.sessions.json (0600).

    Uses a temp file + os.replace() so a crash mid-write never leaves a
    truncated file.  Mirrors the same pattern as .signing_key persistence.
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=STATE_DIR, suffix='.sessions.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(sessions, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, _SESSIONS_FILE)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.debug("Failed to persist sessions: %s", e)


# Active sessions: token -> expiry timestamp (persisted across restarts via STATE_DIR)
_sessions = _load_sessions()

# ── Login rate limiter ──────────────────────────────────────────────────────
_login_attempts = {}  # ip -> [timestamp, ...]
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW = 60  # seconds

def _check_login_rate(ip: str) -> bool:
    """Return True if the IP is allowed to attempt login."""
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Prune old attempts
    attempts = [t for t in attempts if now - t < _LOGIN_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) < _LOGIN_MAX_ATTEMPTS

def _record_login_attempt(ip: str) -> None:
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    attempts.append(now)
    _login_attempts[ip] = attempts


def _signing_key():
    """Return a random signing key, generating and persisting one on first call."""
    key_file = STATE_DIR / '.signing_key'
    try:
        if key_file.exists():
            raw = key_file.read_bytes()
            if len(raw) >= 32:
                return raw[:32]
    except Exception:
        logger.debug("Failed to read or access signing key file, using in-memory key")
    # Generate a new random key
    key = secrets.token_bytes(32)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(key)
        key_file.chmod(0o600)
    except Exception:
        logger.debug("Failed to persist signing key, using in-memory key only")
    return key


def _hash_password(password):
    """PBKDF2-SHA256 with 600k iterations (OWASP recommendation).
    Salt is the persisted random signing key, which is secret and unique per
    installation. This keeps the stored hash format a plain hex string
    (no format change to settings.json) while replacing the predictable
    STATE_DIR-derived salt from the original implementation."""
    salt = _signing_key()
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600_000)
    return dk.hex()


def get_password_hash() -> str | None:
    """Return the active password hash, or None if auth is disabled.
    Priority: env var > settings.json."""
    env_pw = os.getenv('HERMES_WEBUI_PASSWORD', '').strip()
    if env_pw:
        return _hash_password(env_pw)
    settings = load_settings()
    return settings.get('password_hash') or None


def is_auth_enabled() -> bool:
    """True if any non-`none` auth mode is active. Both password and
    neodomain modes count — both require the user to authenticate
    before reaching API or page routes."""
    return get_auth_mode() != "none"


# ── Mode dispatch ──────────────────────────────────────────────────────

# In-memory cache of validated Neodomain JWTs to avoid hitting the
# dashboard's whoami endpoint on every request. Keyed by raw JWT, value
# is (expires_at_unix, was_valid). Entries are dropped when expires_at
# elapses; cache is bounded by JWT count (one per logged-in user) so
# unbounded growth is a non-issue in practice.
_NEODOMAIN_VALID_CACHE: dict[str, tuple[float, bool]] = {}
_NEODOMAIN_CACHE_TTL = 300  # 5 minutes — JWT itself is ~30 days


def get_auth_mode() -> str:
    """Resolve the active auth mode. Returns one of:
        'none' / 'password' / 'neodomain'

    Priority:
      1. HERMES_WEBUI_AUTH_MODE env var (explicit override)
      2. Implicit: if HERMES_WEBUI_PASSWORD or settings password set → password
      3. Default: 'none'
    """
    env_mode = os.getenv('HERMES_WEBUI_AUTH_MODE', '').strip().lower()
    if env_mode in ('none', 'password', 'neodomain'):
        return env_mode
    if env_mode:
        # Unrecognized — log so a typo doesn't silently disable auth.
        logger.warning(
            "HERMES_WEBUI_AUTH_MODE=%r is not recognized; falling back to auto-detect",
            env_mode,
        )
    if get_password_hash() is not None:
        return 'password'
    return 'none'


def _decode_jwt_payload(jwt: str) -> dict | None:
    """Decode the payload section of a JWT without verifying the signature.

    We rely on app.neowow.studio's cookie having been set after a real
    OAuth flow — the dashboard verified the JWT then. Within our
    webui process we just need to extract `userId`/`exp` to know
    *which* user this is and whether the token is still valid.

    Returns None on any malformed input. Don't trust the result for
    security decisions beyond "the JWT looks structurally valid"; pair
    it with a call to /api/me/whoami if you need a stronger check.
    """
    if not isinstance(jwt, str) or jwt.count('.') != 2:
        return None
    try:
        _hdr, payload_b64, _sig = jwt.split('.')
        # base64url → base64: + and / replaced, padding restored.
        pad = payload_b64 + '=' * (-len(payload_b64) % 4)
        b64 = pad.replace('-', '+').replace('_', '/')
        raw = base64.b64decode(b64)
        return json.loads(raw)
    except Exception:
        return None


def _neodomain_jwt_looks_valid(jwt: str) -> bool:
    """True iff the JWT decodes cleanly, has a userId/sub claim, and
    isn't expired by `exp`. Doesn't verify the signature (see
    `_decode_jwt_payload` for why).

    Cached for `_NEODOMAIN_CACHE_TTL` seconds to keep request hot-path
    cheap. The cache is busted automatically on `exp` rollover.
    """
    cached = _NEODOMAIN_VALID_CACHE.get(jwt)
    now = time.time()
    if cached and cached[0] > now:
        return cached[1]

    payload = _decode_jwt_payload(jwt)
    if payload is None:
        _NEODOMAIN_VALID_CACHE[jwt] = (now + 30, False)  # short cache for invalid
        return False

    # Need a non-empty userId / sub / id — Neodomain JWTs carry
    # `userId`. Older issuers might use `sub`. Anything else means we
    # can't identify the caller, treat as invalid.
    has_id = any(
        isinstance(payload.get(k), (str, int)) and str(payload.get(k)).strip()
        for k in ('userId', 'sub', 'id', 'uid')
    )
    if not has_id:
        _NEODOMAIN_VALID_CACHE[jwt] = (now + 30, False)
        return False

    # Check expiration. JWT `exp` is unix seconds. Tolerate a 60-s skew
    # so a clock drift on either side doesn't reject a barely-fresh token.
    exp = payload.get('exp')
    if isinstance(exp, (int, float)) and now > exp + 60:
        _NEODOMAIN_VALID_CACHE[jwt] = (now + 30, False)
        return False

    _NEODOMAIN_VALID_CACHE[jwt] = (now + _NEODOMAIN_CACHE_TTL, True)
    return True


def parse_neo_cookie(handler) -> str | None:
    """Read the cross-subdomain `neoToken` cookie set by
    app.neowow.studio's OAuth callback. None if absent or malformed."""
    cookie_header = handler.headers.get('Cookie', '')
    if not cookie_header:
        return None
    cookie = http.cookies.SimpleCookie()
    try:
        cookie.load(cookie_header)
    except http.cookies.CookieError:
        return None
    morsel = cookie.get('neoToken')
    if not morsel:
        return None
    # Dashboard URL-encodes the value before stuffing in the cookie.
    import urllib.parse
    try:
        return urllib.parse.unquote(morsel.value)
    except Exception:
        return None


def verify_password(plain) -> bool:
    """Verify a plaintext password against the stored hash."""
    expected = get_password_hash()
    if not expected:
        return False
    return hmac.compare_digest(_hash_password(plain), expected)


def create_session() -> str:
    """Create a new auth session. Returns signed cookie value."""
    token = secrets.token_hex(32)
    _sessions[token] = time.time() + SESSION_TTL
    _save_sessions(_sessions)
    sig = hmac.new(_signing_key(), token.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{token}.{sig}"


def _prune_expired_sessions():
    """Remove all expired session entries to prevent unbounded memory growth."""
    now = time.time()
    expired = [t for t, exp in _sessions.items() if now > exp]
    if expired:
        for token in expired:
            _sessions.pop(token, None)
        _save_sessions(_sessions)


def verify_session(cookie_value) -> bool:
    """Verify a signed session cookie. Returns True if valid and not expired."""
    if not cookie_value or '.' not in cookie_value:
        return False
    _prune_expired_sessions()  # lazy cleanup on every verification attempt
    token, sig = cookie_value.rsplit('.', 1)
    expected_sig = hmac.new(_signing_key(), token.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected_sig):
        return False
    expiry = _sessions.get(token)
    if not expiry or time.time() > expiry:
        _sessions.pop(token, None)
        return False
    return True


def invalidate_session(cookie_value) -> None:
    """Remove a session token."""
    if cookie_value and '.' in cookie_value:
        token = cookie_value.rsplit('.', 1)[0]
        if token in _sessions:
            _sessions.pop(token, None)
            _save_sessions(_sessions)


def parse_cookie(handler) -> str | None:
    """Extract the auth cookie from the request headers."""
    cookie_header = handler.headers.get('Cookie', '')
    if not cookie_header:
        return None
    cookie = http.cookies.SimpleCookie()
    try:
        cookie.load(cookie_header)
    except http.cookies.CookieError:
        return None
    morsel = cookie.get(COOKIE_NAME)
    return morsel.value if morsel else None


def check_auth(handler, parsed) -> bool:
    """Check if request is authorized. Returns True if OK.
    If not authorized, sends 401 (API) or 302 redirect (page) and returns False.

    Three branches based on get_auth_mode():
      • none      → always allow (open WebUI)
      • password  → existing path: hermes_session cookie + login page
      • neodomain → cross-subdomain `neoToken` cookie set by
                    app.neowow.studio after Neodomain OAuth. Missing or
                    expired cookie → redirect to dashboard's login.
    """
    mode = get_auth_mode()
    if mode == 'none':
        return True
    # Public paths don't require auth in any mode
    if parsed.path in PUBLIC_PATHS or parsed.path.startswith('/static/') or parsed.path.startswith('/session/static/'):
        return True

    # ── Neodomain mode (chat.neowow.studio cloud deployment) ──────────
    if mode == 'neodomain':
        neo_jwt = parse_neo_cookie(handler)
        if neo_jwt and _neodomain_jwt_looks_valid(neo_jwt):
            return True
        # Missing or invalid → kick to dashboard login. The dashboard's
        # OAuth callback writes the cookie at `Domain=.neowow.studio` so
        # the next request to chat.neowow.studio will see it.
        return _redirect_to_neodomain_login(handler, parsed)

    # ── Password mode (default for self-hosted) ───────────────────────
    cookie_val = parse_cookie(handler)
    if cookie_val and verify_session(cookie_val):
        return True
    # Not authorized
    if parsed.path.startswith('/api/'):
        handler.send_response(401)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(b'{"error":"Authentication required"}')
    else:
        handler.send_response(302)
        # Pass the original path as ?next= so login.js redirects back after auth.
        # SECURITY/CORRECTNESS: the inner `?` and `&` MUST be percent-encoded
        # when stuffed into the outer `?next=` parameter, otherwise:
        #   (a) multi-param query strings get truncated at the first inner `&`
        #       (e.g. `/api/sessions?limit=50&offset=0` would round-trip as
        #       just `/api/sessions?limit=50` after the browser parses the
        #       outer URL — `offset=0` becomes a separate top-level query
        #       parameter that the login page ignores).
        #   (b) attacker-controlled paths could inject a second `next=`
        #       parameter; per RFC 3986 the duplicate behaviour is undefined
        #       and parsers diverge (Python's parse_qs returns last-match,
        #       URLSearchParams returns first-match), opening a query-pollution
        #       footgun even though _safeNextPath() rejects most malicious
        #       shapes downstream.
        # Encoding the entire `path?query` blob with quote(safe='/') turns
        # `?` → `%3F` and `&` → `%26`, so the outer parameter holds exactly
        # one path-with-query string and `searchParams.get('next')` returns
        # the full original URL (the browser auto-decodes once).
        # (Opus pre-release advisor finding for v0.50.258.)
        import urllib.parse as _urlparse
        _path_with_query = parsed.path or '/'
        if parsed.query:
            _path_with_query += '?' + parsed.query
        # safe='/' keeps path separators readable; everything else (including
        # `?`, `&`, `=`) gets percent-encoded.
        _next = _urlparse.quote(_path_with_query, safe='/')
        handler.send_header('Location', '/login?next=' + _next)
        handler.end_headers()
    return False


def set_auth_cookie(handler, cookie_value) -> None:
    """Set the auth cookie on the response."""
    cookie = http.cookies.SimpleCookie()
    cookie[COOKIE_NAME] = cookie_value
    cookie[COOKIE_NAME]['httponly'] = True
    cookie[COOKIE_NAME]['samesite'] = 'Lax'
    cookie[COOKIE_NAME]['path'] = '/'
    cookie[COOKIE_NAME]['max-age'] = str(SESSION_TTL)
    # Set Secure flag when connection is HTTPS
    if getattr(handler.request, 'getpeercert', None) is not None or handler.headers.get('X-Forwarded-Proto', '') == 'https':
        cookie[COOKIE_NAME]['secure'] = True
    handler.send_header('Set-Cookie', cookie[COOKIE_NAME].OutputString())


def clear_auth_cookie(handler) -> None:
    """Clear the auth cookie on the response."""
    cookie = http.cookies.SimpleCookie()
    cookie[COOKIE_NAME] = ''
    cookie[COOKIE_NAME]['httponly'] = True
    cookie[COOKIE_NAME]['path'] = '/'
    cookie[COOKIE_NAME]['max-age'] = '0'
    handler.send_header('Set-Cookie', cookie[COOKIE_NAME].OutputString())


# ── Neodomain redirect helper ───────────────────────────────────────────────

def _build_return_url(handler, parsed) -> str:
    """Reconstruct the absolute URL the user was trying to reach so the
    OAuth callback can land them back here. Honors X-Forwarded-Proto/
    Host so it works behind Caddy / Cloudflare."""
    proto = handler.headers.get('X-Forwarded-Proto', '').strip() or 'https'
    host  = (handler.headers.get('X-Forwarded-Host', '').strip()
             or handler.headers.get('Host', '').strip()
             or 'chat.neowow.studio')
    path  = parsed.path or '/'
    if parsed.query:
        path += '?' + parsed.query
    return f"{proto}://{host}{path}"


def _redirect_to_neodomain_login(handler, parsed) -> bool:
    """Send the user to app.neowow.studio's OAuth start endpoint with
    a return URL pointing back to chat.<...>. The dashboard's existing
    sanitizeReturnUrl() whitelists `*.neowow.studio` so chat.neowow.studio
    is accepted out of the box.

    For API requests we send 401 with a `loginUrl` body so the JS client
    can decide whether to redirect (kicks them out of mid-stream chat) or
    show a "session expired" toast. Page requests get a 302.
    """
    import urllib.parse
    return_url  = _build_return_url(handler, parsed)
    oauth_base  = os.getenv(
        'HERMES_NEODOMAIN_OAUTH_START',
        'https://app.neowow.studio/api/oauth/start',
    ).strip()
    login_url   = f"{oauth_base}?return={urllib.parse.quote(return_url, safe='')}"

    if parsed.path.startswith('/api/'):
        body = json.dumps({
            "error":    "Authentication required",
            "mode":     "neodomain",
            "loginUrl": login_url,
        }).encode('utf-8')
        handler.send_response(401)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Content-Length', str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    else:
        handler.send_response(302)
        handler.send_header('Location', login_url)
        handler.end_headers()
    return False
