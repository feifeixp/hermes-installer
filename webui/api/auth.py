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
import threading
import time

from api.config import STATE_DIR, load_settings

logger = logging.getLogger(__name__)


# Default session TTL — 30 days. Kept as a module-level constant for backwards
# compatibility with downstream code and regression tests that import it.
# At runtime, prefer ``_resolve_session_ttl()`` which honours the env var and
# settings.json overrides; this constant is the floor / fallback.
SESSION_TTL = 86400 * 30  # 30 days


def _resolve_session_ttl() -> int:
    """Resolve session TTL from env > settings > default.

    Priority mirrors get_password_hash(): HERMES_WEBUI_SESSION_TTL env var
    first, then settings.json, falling back to ``SESSION_TTL`` (30 days).
    Clamped to [60s, 1 year] to prevent runaway cookies or self-lockout.
    """
    env_v = os.getenv('HERMES_WEBUI_SESSION_TTL', '').strip()
    if env_v.isdigit():
        val = int(env_v)
        if 60 <= val <= 86400 * 365:
            return val
    s = load_settings()
    v = s.get('session_ttl_seconds')
    if isinstance(v, int) and 60 <= v <= 86400 * 365:
        return v
    return SESSION_TTL


# ── Public paths (no auth required) ─────────────────────────────────────────
PUBLIC_PATHS = frozenset({
    '/login', '/health', '/favicon.ico', '/sw.js',
    '/api/auth/login', '/api/auth/status',
    '/manifest.json', '/manifest.webmanifest',
    '/session/manifest.json', '/session/manifest.webmanifest',
})

COOKIE_NAME = 'hermes_session'

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
_LOGIN_ATTEMPTS_FILE = STATE_DIR / '.login_attempts.json'
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW = 60  # seconds


def _load_login_attempts() -> dict[str, list[float]]:
    """Load persisted login attempts from STATE_DIR, pruning expired entries."""
    try:
        if _LOGIN_ATTEMPTS_FILE.exists():
            data = json.loads(_LOGIN_ATTEMPTS_FILE.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('malformed login-attempts file — expected dict')
            now = time.time()
            attempts: dict[str, list[float]] = {}
            for ip, raw_times in data.items():
                if not isinstance(ip, str) or not isinstance(raw_times, list):
                    continue
                fresh = [
                    float(t)
                    for t in raw_times
                    if isinstance(t, (int, float)) and now - float(t) < _LOGIN_WINDOW
                ]
                if fresh:
                    attempts[ip] = fresh
            return attempts
    except Exception as e:
        logger.debug("Failed to load login attempts file, starting fresh: %s", e)
    return {}


def _save_login_attempts(attempts: dict[str, list[float]]) -> None:
    """Atomically persist login attempts to STATE_DIR/.login_attempts.json (0600)."""
    try:
        _LOGIN_ATTEMPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_LOGIN_ATTEMPTS_FILE.parent, suffix='.login_attempts.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(attempts, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, _LOGIN_ATTEMPTS_FILE)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.debug("Failed to persist login attempts: %s", e)


_login_attempts = _load_login_attempts()  # ip -> [timestamp, ...]
_LOGIN_ATTEMPTS_LOCK = threading.Lock()


def _check_login_rate(ip: str) -> bool:
    """Return True if the IP is allowed to attempt login (thread-safe)."""
    with _LOGIN_ATTEMPTS_LOCK:
        now = time.time()
        attempts = _login_attempts.get(ip, [])
        # Prune old attempts
        attempts = [t for t in attempts if now - t < _LOGIN_WINDOW]
        if attempts:
            _login_attempts[ip] = attempts
        else:
            _login_attempts.pop(ip, None)
        _save_login_attempts(_login_attempts)
        return len(attempts) < _LOGIN_MAX_ATTEMPTS


def _record_login_attempt(ip: str) -> None:
    """Record a login attempt for rate limiting (thread-safe)."""
    with _LOGIN_ATTEMPTS_LOCK:
        now = time.time()
        attempts = _login_attempts.get(ip, [])
        attempts.append(now)
        _login_attempts[ip] = attempts
        _save_login_attempts(_login_attempts)


def _load_key(filename: str) -> bytes:
    """Load a 32-byte key from STATE_DIR, generating and persisting one if missing."""
    key_file = STATE_DIR / filename
    try:
        if key_file.exists():
            raw = key_file.read_bytes()
            if len(raw) >= 32:
                return raw[:32]
    except OSError:
        logger.debug("Failed to read key %s", filename)
    key = secrets.token_bytes(32)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(key)
        key_file.chmod(0o600)
    except OSError:
        logger.debug("Failed to persist key %s", filename)
    return key


_PBKDF2_KEY_CACHE: bytes | None = None
_SIGNING_KEY_CACHE: bytes | None = None


def _pbkdf2_key() -> bytes:
    global _PBKDF2_KEY_CACHE
    if _PBKDF2_KEY_CACHE is None:
        _PBKDF2_KEY_CACHE = _load_key('.pbkdf2_key')
    return _PBKDF2_KEY_CACHE


def _signing_key() -> bytes:
    global _SIGNING_KEY_CACHE
    if _SIGNING_KEY_CACHE is None:
        _SIGNING_KEY_CACHE = _load_key('.signing_key')
    return _SIGNING_KEY_CACHE


def _hash_password(password, *, salt: bytes | None = None) -> str:
    """PBKDF2-SHA256 with 600k iterations (OWASP recommendation).
    Salt is the persisted PBKDF2 key, which is secret and unique per
    installation. This keeps the stored hash format a plain hex string
    (no format change to settings.json) while replacing the predictable
    STATE_DIR-derived salt from the original implementation.

    The *salt* parameter exists solely to support transparent migration
    of password hashes that were computed with a different key (e.g. the
    old `.signing_key`). Normal callers should never pass it.
    """
    if salt is None:
        salt = _pbkdf2_key()
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600_000)
    return dk.hex()


_AUTH_HASH_LOCK = threading.Lock()
_AUTH_HASH_COMPUTED: bool = False
_AUTH_HASH_CACHE: str | None = None


def _invalidate_password_hash_cache() -> None:
    """Invalidate the in-process password hash cache so the next call to
    get_password_hash() re-reads from settings.json or the env var."""
    global _AUTH_HASH_COMPUTED, _AUTH_HASH_CACHE
    with _AUTH_HASH_LOCK:
        _AUTH_HASH_COMPUTED = False
        _AUTH_HASH_CACHE = None


def get_password_hash() -> str | None:
    """Return the active password hash, or None if auth is disabled.
    Priority: env var > settings.json.

    The hash is computed once and cached for the lifetime of the process.
    PBKDF2-600k takes ~1 s and is called on nearly every HTTP request via
    check_auth → is_auth_enabled, so caching avoids wasting a full second
    of CPU per request after the first one.

    Thread-safe: double-checked locking ensures that under a burst of
    concurrent requests only one thread computes PBKDF2, while the fast
    path (after initialisation) requires zero locks.
    """
    global _AUTH_HASH_COMPUTED, _AUTH_HASH_CACHE

    # Fast path — no lock needed once cache is populated.
    if _AUTH_HASH_COMPUTED:
        return _AUTH_HASH_CACHE

    with _AUTH_HASH_LOCK:
        # Re-check inside lock — another thread may have populated while
        # we were waiting to acquire.
        if _AUTH_HASH_COMPUTED:
            return _AUTH_HASH_CACHE

        env_pw = os.getenv('HERMES_WEBUI_PASSWORD', '').strip()
        if env_pw:
            result = _hash_password(env_pw)
        else:
            result = load_settings().get('password_hash') or None

        _AUTH_HASH_CACHE = result
        _AUTH_HASH_COMPUTED = True
        return result


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

    ALSO enforces HERMES_INSTANCE_OWNER_USERID when set — used by
    Phase 2 per-user ECS instances to reject other users' valid JWTs.
    A JWT can be valid (signed, fresh) but for a DIFFERENT user, and
    on a private instance we want only the owner in.
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
    jwt_user_id = None
    for k in ('userId', 'sub', 'id', 'uid'):
        v = payload.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            jwt_user_id = str(v).strip()
            break
    if not jwt_user_id:
        _NEODOMAIN_VALID_CACHE[jwt] = (now + 30, False)
        return False

    # Check expiration. JWT `exp` is unix seconds. Tolerate a 60-s skew
    # so a clock drift on either side doesn't reject a barely-fresh token.
    exp = payload.get('exp')
    if isinstance(exp, (int, float)) and now > exp + 60:
        _NEODOMAIN_VALID_CACHE[jwt] = (now + 30, False)
        return False

    # ── Per-instance owner gate (Phase 2) ─────────────────────────────
    # If HERMES_INSTANCE_OWNER_USERID is set, the JWT must belong to
    # that exact userId. Phase 1 chat.neowow.studio doesn't set this
    # and is multi-tenant. Phase 2 chat-<userId>.neowow.studio sets it
    # to the spawning user's id and rejects everyone else.
    owner = os.getenv('HERMES_INSTANCE_OWNER_USERID', '').strip()
    if owner and jwt_user_id != owner:
        logger.info(
            'neodomain auth: rejecting JWT (jwt_user_id=%s ≠ owner=%s)',
            jwt_user_id, owner,
        )
        # Short-cache the rejection. If the user mis-clicks into someone
        # else's instance, we want to reject every request fast.
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


def verify_password(plain: str) -> bool:
    """Verify a plaintext password against the stored hash.

    Supports transparent migration of password hashes that were computed
    with the old `.signing_key` salt.  When the two keys differ and the
    legacy-salted hash matches, the password is transparently re-hashed
    with the current `.pbkdf2_key` and persisted to settings.json.
    """
    expected = get_password_hash()
    if not expected:
        return False
    # Fast path: current PBKDF2 key
    if hmac.compare_digest(_hash_password(plain), expected):
        return True
    # Migration: some hashes were computed with `.signing_key` before the
    # PBKDF2 key was separated.  Try the legacy salt; if it matches,
    # transparently upgrade so the next login uses the fast path.
    legacy_salt = _signing_key()
    current_salt = _pbkdf2_key()
    if legacy_salt != current_salt:
        if hmac.compare_digest(_hash_password(plain, salt=legacy_salt), expected):
            from api.config import save_settings

            save_settings({'_set_password': plain})
            # Password re-hashed and persisted to disk using the current salt.
            # Cache invalidation is handled by fix 2/3 (#2192) which adds the
            # _invalidate_password_hash_cache() call inside save_settings().
            return True
    return False


def create_session() -> str:
    """Create a new auth session. Returns signed cookie value."""
    token = secrets.token_hex(32)
    _sessions[token] = time.time() + _resolve_session_ttl()
    _save_sessions(_sessions)
    sig = hmac.new(_signing_key(), token.encode(), hashlib.sha256).hexdigest()
    return f"{token}.{sig}"


def _prune_expired_sessions():
    """Remove all expired session entries to prevent unbounded memory growth."""
    now = time.time()
    expired = [t for t, exp in _sessions.items() if now > exp]
    if expired:
        for token in expired:
            _sessions.pop(token, None)
        _save_sessions(_sessions)


def verify_session(cookie_value: str) -> bool:
    """Verify a signed session cookie. Returns True if valid and not expired."""
    if not cookie_value or '.' not in cookie_value:
        return False
    _prune_expired_sessions()  # lazy cleanup on every verification attempt
    token, sig = cookie_value.rsplit('.', 1)
    full_sig = hmac.new(_signing_key(), token.encode(), hashlib.sha256).hexdigest()
    # Accept both new (64-char) and legacy (32-char truncated) signatures so
    # existing sessions survive the upgrade without a forced global logout.
    # The legacy branch can be removed once session TTLs have expired (~30 days).
    valid = hmac.compare_digest(sig, full_sig) or (
        len(sig) == 32 and hmac.compare_digest(sig, full_sig[:32])
    )
    if not valid:
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
        handler.send_header('Location', 'login?next=' + _next)
        handler.end_headers()
    return False


def _is_secure_context(handler=None) -> bool:
    """Return True if cookies should carry the Secure flag.

    Behaviour is overridable via HERMES_WEBUI_SECURE env var for
    reverse-proxy setups where TLS terminates at a frontend proxy
    (nginx, Cloudflare, etc.) and Python only sees plain HTTP.
    1/true/yes → force Secure on; 0/false/no → force Secure off.
    When unset, fall back to heuristics: direct TLS socket (getpeercert)
    or X-Forwarded-Proto header from the request.

    .. warning::
       The ``X-Forwarded-Proto`` header is only trustworthy when a
       reverse proxy (nginx, Cloudflare, etc.) is deployed in front
       of the application.  Without a proxy, any client can forge the
       header and cause the Secure flag to be set on plain HTTP.
    """
    env = os.getenv('HERMES_WEBUI_SECURE', '').strip().lower()
    if env in ('1', 'true', 'yes'):
        return True
    if env in ('0', 'false', 'no'):
        return False
    if handler is not None:
        if getattr(handler.request, 'getpeercert', None) is not None:
            return True
        if handler.headers.get('X-Forwarded-Proto', '') == 'https':
            return True
    return False


def set_auth_cookie(handler, cookie_value) -> None:
    """Set the auth cookie on the response."""
    cookie = http.cookies.SimpleCookie()
    cookie[COOKIE_NAME] = cookie_value
    cookie[COOKIE_NAME]['httponly'] = True
    cookie[COOKIE_NAME]['samesite'] = 'Lax'
    cookie[COOKIE_NAME]['path'] = '/'
    cookie[COOKIE_NAME]['max-age'] = str(_resolve_session_ttl())
    if _is_secure_context(handler):
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
