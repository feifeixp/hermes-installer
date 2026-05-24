"""
Hermes ↔ Neowow Studio integration.

Lets the user push the current workspace to https://app.neowow.studio with
one click. The flow:

  1. User pastes their `nws_dt_*` deploy token (minted at
     https://app.neowow.studio/account/deploy-tokens) into Hermes settings.
     Token is persisted to STATE_DIR/neowow.json (file mode 0600).
  2. User clicks "Deploy to neowow" in the workspace toolbar.
  3. Hermes walks the workspace, builds a `[{name, content}]` payload for
     every text file under a sane size cap, and POSTs to
     https://app.neowow.studio/api/deploy with the bearer token.
  4. The published URL (https://<workerName>.neowow.studio) is returned to
     the UI.

Why a separate file instead of folding into config.py / settings.json:
- Settings has a strict allowed-keys validator and lots of legacy code
  paths. Adding a token field there means touching that whole machinery.
- A token is sensitive material — keep it isolated, write it with explicit
  0600 permissions, and don't round-trip it through the generic settings
  GET endpoint by accident.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from api.config import STATE_DIR, reload_config

try:
    import yaml  # type: ignore[import-not-found]
except ImportError:
    yaml = None  # type: ignore[assignment]


# Mirror of `api.config._hermes_config_path()` — that function is module-
# private; replicating the small bit of logic here keeps us off the
# private surface and means a refactor of profiles.py only breaks one
# place. Falls through cleanly when the profiles module isn't available
# (the on-disk default is always ~/.hermes/config.yaml).
def _hermes_config_path() -> Path:
    env_override = os.getenv("HERMES_CONFIG_PATH")
    if env_override:
        return Path(env_override).expanduser()
    try:
        from api.profiles import get_active_hermes_home  # type: ignore[import-not-found]
        return get_active_hermes_home() / "config.yaml"
    except ImportError:
        return Path.home() / ".hermes" / "config.yaml"

logger = logging.getLogger(__name__)

# Persisted token + last-deploy bookkeeping. Sibling to settings.json so
# the `~/.hermes/webui/` directory stays the single source of state.
_NEOWOW_FILE = STATE_DIR / "neowow.json"

# Fixed dashboard endpoint — Hermes always pushes to production neowow.
# (No staging story today; if we add one we'll plumb it through env.)
_NEOWOW_BASE = "https://app.neowow.studio"
_NEOWOW_DEPLOY_URL = f"{_NEOWOW_BASE}/api/deploy"

# Cloud-config endpoints. These are the read endpoints Hermes calls — the
# dashboard accepts deploy-tokens for GET (so Hermes startup doesn't need
# an SSO popup), but the PATCH /active write is JWT-only. To switch the
# active config the user goes to /account/hermes-configs in the dashboard
# UI, then clicks "🔄 Sync" here so Hermes picks up the new active.
_CLOUD_LIST_URL    = f"{_NEOWOW_BASE}/api/me/hermes-configs"
_CLOUD_ACTIVE_URL  = f"{_NEOWOW_BASE}/api/me/hermes-configs/active"
_WHOAMI_URL        = f"{_NEOWOW_BASE}/api/me/whoami"

# ── Managed-update notice ────────────────────────────────────────────────────
#
# When HERMES_NEOWOW_ONLY=1 all native git-based self-updates are disabled.
# Instead Hermes polls this public endpoint which the Neowow admin controls
# via POST /api/admin/update-notice on the dashboard. The response is cached
# locally (30 min TTL) to avoid hammering the dashboard on every page load.
_UPDATE_NOTICE_URL  = f"{_NEOWOW_BASE}/api/public/update-notice"
_UPDATE_NOTICE_TTL  = 1800          # seconds; same as native update cache
_update_notice_cache: dict = {}     # {data: dict, fetched_at: float}

# ── Docker image update check ────────────────────────────────────────────────
#
# When running inside a Docker container (/.within_container exists) the git
# checkout is absent so the native update checker is a no-op. Instead we poll
# the GitHub Releases API for the repo that publishes the Docker image and
# compare against the baked-in _version.py tag.
_IS_DOCKER = Path("/.within_container").exists()
_DOCKER_GITHUB_REPO  = "feifeixp/hermes-installer"
_DOCKER_IMAGE        = f"ghcr.io/{_DOCKER_GITHUB_REPO}"
_GITHUB_RELEASES_API = f"https://api.github.com/repos/{_DOCKER_GITHUB_REPO}/releases/latest"
_DOCKER_SOCKET       = Path("/var/run/docker.sock")
_docker_update_cache: dict = {}     # {data: dict, fetched_at: float}


def _neowow_only() -> bool:
    return os.getenv("HERMES_NEOWOW_ONLY", "").strip().lower() in {"1", "true", "yes"}


# ── Docker socket HTTP client ─────────────────────────────────────────────────

class _UnixHTTPConnection:
    """Minimal HTTP/1.1 client over a Unix-domain socket.

    Avoids taking a dependency on the ``docker`` Python SDK or requiring the
    ``docker`` CLI binary inside the container.  Only used when
    ``/var/run/docker.sock`` is mounted, i.e. the user explicitly opted in to
    Docker-managed updates by adding the socket volume to docker-compose.yml.
    """

    def __init__(self, socket_path: str = "/var/run/docker.sock"):
        import http.client as _http
        import socket as _sock

        class _Conn(_http.HTTPConnection):
            def connect(self_inner) -> None:  # noqa: N805
                self_inner.sock = _sock.socket(
                    _sock.AF_UNIX, _sock.SOCK_STREAM
                )
                self_inner.sock.settimeout(self.timeout)
                self_inner.sock.connect(socket_path)

        self._cls = _Conn
        self.timeout = 300  # long timeout for image pulls

    def request(self, method: str, path: str, body=None, headers=None):
        import json as _j
        conn = self._cls("localhost")
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        body_bytes = _j.dumps(body).encode() if (body is not None) else None
        conn.request(method, path, body=body_bytes, headers=h)
        resp = conn.getresponse()
        raw = resp.read()
        try:
            data = _j.loads(raw) if raw.strip() else {}
        except Exception:
            data = {"_raw": raw.decode(errors="replace")}
        return resp.status, data


def docker_socket_available() -> bool:
    """Return True when the Docker socket is mounted and accessible."""
    return _DOCKER_SOCKET.exists() and os.access(_DOCKER_SOCKET, os.W_OK)


def pull_docker_image(image: str = _DOCKER_IMAGE, tag: str = "latest") -> dict:
    """Pull *image*:*tag* via the Docker socket API.

    Requires the Docker socket to be mounted (docker_socket_available() == True).
    Blocking call — image pulls can take minutes on slow connections.

    Returns:
      ok          bool   — True on success
      status      int    — HTTP status from Docker API
      message     str    — human-readable result or error detail
    """
    if not docker_socket_available():
        return {
            "ok": False,
            "status": 0,
            "message": (
                "Docker socket 未挂载。请在 docker-compose.yml 的 volumes 中"
                " 添加 /var/run/docker.sock:/var/run/docker.sock 后重启容器。"
            ),
        }
    try:
        client = _UnixHTTPConnection()
        # Docker Engine API: POST /images/create?fromImage=IMAGE&tag=TAG
        path = f"/images/create?fromImage={urllib.parse.quote(image)}&tag={urllib.parse.quote(tag)}"
        status, data = client.request("POST", path)
        if status in (200, 201):
            # Invalidate cached update notice so next check shows "up-to-date"
            _docker_update_cache.clear()
            return {
                "ok": True,
                "status": status,
                "message": f"镜像 {image}:{tag} 拉取完成，运行 `docker compose up -d` 以应用。",
            }
        err = data.get("message") or str(data)
        return {
            "ok": False,
            "status": status,
            "message": f"拉取失败 (HTTP {status}): {err[:200]}",
        }
    except Exception as exc:
        logger.warning("pull_docker_image failed: %s", exc)
        return {
            "ok": False,
            "status": 0,
            "message": f"Docker socket 通信失败：{exc}",
        }


def _version_newer(latest: str, current: str) -> bool:
    """Return True when *latest* is strictly newer than *current* (semver-ish).

    Strips a leading 'v' and ignores pre-release suffixes after '-' so that
    comparing 'v1.3.6' against 'v1.3.5-dirty' works as expected.
    """
    def _parse(v: str) -> tuple[int, ...]:
        clean = v.lstrip("v").split("-")[0]   # drop dirty/pre-release suffix
        parts = clean.split(".")[:3]
        result = []
        for part in parts:
            try:
                result.append(int(part))
            except ValueError:
                result.append(0)
        return tuple(result)

    try:
        return _parse(latest) > _parse(current)
    except Exception:
        return False


def _check_docker_github_release() -> dict:
    """Check GitHub Releases API for a newer Docker image (30-min cached).

    Returns a dict compatible with get_update_notice():
      available     bool  — True when a newer release exists
      version       str   — latest tag, e.g. "1.3.6"  (empty when not available)
      message       str   — first 200 chars of release notes
      downloadUrl   str   — GitHub release page URL
      publishedAt   str   — ISO timestamp from GitHub
      isDocker      bool  — always True; tells the UI to show docker pull cmds
      dockerImage   str   — e.g. "ghcr.io/feifeixp/hermes-installer"
      currentVersion str  — running version (from _version.py / git describe)
    """
    import time
    now = time.time()
    if _docker_update_cache and now - _docker_update_cache.get("fetched_at", 0) < _UPDATE_NOTICE_TTL:
        return dict(_docker_update_cache["data"])

    try:
        from api.updates import WEBUI_VERSION as _current
    except Exception:
        _current = "unknown"

    try:
        req = urllib.request.Request(
            _GITHUB_RELEASES_API,
            headers={
                "User-Agent": f"hermes-webui/{_current}",
                "Accept":     "application/vnd.github.v3+json",
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = json.loads(resp.read().decode())
        if not isinstance(raw, dict):
            raise ValueError("unexpected GitHub API response")

        latest_tag   = str(raw.get("tag_name") or "")
        release_url  = str(raw.get("html_url")  or "")
        release_body = str(raw.get("body")       or "")[:200].strip()
        published_at = str(raw.get("published_at") or "")

        available = _version_newer(latest_tag, _current)
        result = {
            "available":      available,
            "version":        latest_tag.lstrip("v") if available else "",
            "message":        release_body if available else "",
            "downloadUrl":    release_url if available else "",
            "publishedAt":    published_at if available else "",
            "isDocker":       True,
            "dockerImage":    _DOCKER_IMAGE,
            "currentVersion": _current,
            "latestTag":      latest_tag,
        }
    except Exception as exc:
        logger.debug("docker github-release check failed: %s", exc)
        result = {
            "available":      False,
            "version":        "",
            "message":        "",
            "downloadUrl":    "",
            "publishedAt":    "",
            "isDocker":       True,
            "dockerImage":    _DOCKER_IMAGE,
            "currentVersion": _current,
        }

    _docker_update_cache["data"]       = result
    _docker_update_cache["fetched_at"] = now
    return dict(result)


def get_update_notice() -> dict:
    """Fetch the Neowow-managed update notice (cached 30 min).

    In Docker environments, falls back to checking the GitHub Releases API
    for the published Docker image tag instead of the admin-controlled notice,
    because the admin notice pipeline is designed for the native installer and
    Docker users need version-aware docker-pull instructions.

    Returns a dict with at least:
      available   bool   — True when a new version is ready
      version     str    — e.g. "1.3.6" (empty when not available)
      message     str    — release note (may be empty)
      downloadUrl str    — link to release / changelogs page
      publishedAt str    — ISO timestamp when the release was published
      isDocker    bool   — True when running inside Docker (drives UI copy)
    Returns {"available": False} on network error / not configured.
    """
    import time
    now = time.time()
    if _update_notice_cache and now - _update_notice_cache.get("fetched_at", 0) < _UPDATE_NOTICE_TTL:
        return _update_notice_cache["data"]

    # Docker: check the GitHub Releases API instead of the admin-pushed notice.
    # The docker_update_cache has its own TTL so this is still bounded.
    if _IS_DOCKER:
        return _check_docker_github_release()

    try:
        req = urllib.request.Request(
            _UPDATE_NOTICE_URL,
            headers={"User-Agent": "hermes-webui/1.0"},
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())
        if not isinstance(data, dict):
            data = {}
    except Exception as exc:
        logger.debug("update-notice fetch failed: %s", exc)
        data = {}

    result = {
        "available":    bool(data.get("available")),
        "version":      str(data.get("version") or ""),
        "message":      str(data.get("message") or ""),
        "downloadUrl":  str(data.get("downloadUrl") or ""),
        "publishedAt":  str(data.get("publishedAt") or ""),
        "isDocker":     False,
    }
    _update_notice_cache["data"]       = result
    _update_notice_cache["fetched_at"] = now
    return result


# ── OAuth-callback bridge HTML ───────────────────────────────────────────────
#
# Served at /api/neowow/oauth-callback. The dashboard's /api/oauth/callback
# appends `#neo_session=<base64-json>` to the return URL when redirecting
# the user back. This page reads that fragment, extracts the JWT, POSTs
# it to /api/neowow/jwt to persist on disk, and shows a success message
# the user can close.
#
# Why a constant string rather than a separate static file: keeps the
# page self-contained — no extra route to wire up for the asset, no
# build step that could miss it, and zero chance of the file going
# missing in a packaged install. ~2 KB.

_OAUTH_CALLBACK_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Neodomain 登录回调</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { background: #0a0a0f; color: #e2e8f0;
           font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
           min-height: 100vh; display: flex; align-items: center; justify-content: center;
           padding: 24px; }
    .card { width: 100%; max-width: 480px; background: rgba(22,22,30,0.9);
            border: 1px solid rgba(255,255,255,0.06); border-radius: 16px;
            padding: 32px; text-align: center; box-shadow: 0 20px 60px rgba(0,0,0,0.5); }
    .icon  { font-size: 48px; margin-bottom: 16px; }
    h1     { font-size: 20px; font-weight: 600; margin-bottom: 8px; }
    .desc  { font-size: 14px; color: rgba(255,255,255,0.6); line-height: 1.6; margin-bottom: 20px; }
    .ok    { color: #51cf66; }
    .err   { color: #ff6b6b; }
    .spinner { width: 28px; height: 28px; margin: 0 auto 16px;
               border: 3px solid rgba(255,255,255,0.15); border-top-color: #8b8df8;
               border-radius: 50%; animation: spin 0.8s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .close-hint { margin-top: 20px; font-size: 12px; color: rgba(255,255,255,0.35); }
  </style>
</head>
<body>
  <div class="card">
    <div id="state-pending">
      <div class="spinner"></div>
      <h1>正在保存授权信息…</h1>
      <p class="desc">从 app.neowow.studio 接收登录态中。</p>
    </div>
    <div id="state-ok" style="display:none">
      <div class="icon ok">✅</div>
      <h1 class="ok">Neodomain 授权成功</h1>
      <p class="desc">JWT 已保存到 ~/.hermes/webui/neowow.json，可以关闭此页面回到 Hermes 控制台查看积分余额。</p>
      <p class="close-hint">本页将在 3 秒后尝试自动关闭。</p>
    </div>
    <div id="state-err" style="display:none">
      <div class="icon err">❌</div>
      <h1 class="err">授权失败</h1>
      <p class="desc" id="err-msg"></p>
    </div>
  </div>
<script>
(function () {
  function $(id) { return document.getElementById(id); }
  function show(id) {
    $('state-pending').style.display = 'none';
    $('state-ok').style.display      = 'none';
    $('state-err').style.display     = 'none';
    $(id).style.display              = '';
  }
  function fail(msg) { $('err-msg').textContent = msg; show('state-err'); }

  // Parse `#neo_session=<base64>`. Accept `#x=y&neo_session=z` too.
  var hash = (window.location.hash || '').replace(/^#/, '');
  var match = hash.match(/(?:^|&)neo_session=([^&]+)/);
  if (!match) { fail('回调 URL 没有携带 neo_session 片段，可能登录流程被中断。'); return; }

  // base64url → JSON.  The dashboard encodes the session blob as
  // base64(JSON.stringify(...)) — invert that here.
  function b64urlDecode(s) {
    s = s.replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    return decodeURIComponent(atob(s).split('').map(function (c) {
      return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
    }).join(''));
  }

  var sess;
  try { sess = JSON.parse(b64urlDecode(match[1])); }
  catch (e) { fail('无法解析授权信息：' + e.message); return; }

  var jwt = sess && sess.authorization;
  if (!jwt) { fail('授权信息里没有找到 JWT (authorization 字段)。'); return; }

  // Persist via Hermes' local server.  Same-origin (both on localhost)
  // so no CORS concerns.
  fetch('/api/neowow/jwt', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ jwt: jwt }),
  }).then(function (r) {
    if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || ('HTTP ' + r.status)); });
    return r.json();
  }).then(function () {
    // Strip the fragment so a refresh / bookmark doesn't replay the JWT.
    try { history.replaceState({}, document.title, location.pathname); } catch (e) {}
    show('state-ok');
    // Best-effort auto-close. Browsers reject window.close() on tabs
    // they didn't open programmatically — that's fine, the success
    // message above tells the user to close manually.
    setTimeout(function () { try { window.close(); } catch (e) {} }, 3000);
  }).catch(function (e) {
    fail('保存 JWT 失败：' + (e.message || e));
  });
})();
</script>
</body>
</html>
"""

# Workspace walk caps. The dashboard's deploy pipeline tolerates ~5 MB
# total but we don't want to silently hammer it with node_modules — be
# conservative on the client side and require the user to scope the
# workspace if they want to publish a heavier app.
_MAX_TOTAL_BYTES = 5 * 1024 * 1024
_MAX_FILES = 200
_MAX_FILE_BYTES = 1 * 1024 * 1024

# Directories we never bundle. node_modules / .git / build artifacts have
# no reason to ship to a static-page deploy; the OS / IDE entries are
# noise. If someone has a legit use case we can revisit, but the safe
# default is to skip.
_SKIP_DIRS = {
    "node_modules", ".git", ".next", ".turbo", ".cache", ".venv", "venv",
    "__pycache__", "dist", "build", ".pytest_cache", ".idea", ".vscode",
    ".DS_Store",
}

# Treat anything matching these prefixes as binary and skip. The dashboard
# expects text files; binary assets should go to OSS via the dashboard's
# upload flow first (out of scope for v1 of this integration).
_BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".mp3", ".mp4", ".mov", ".webm", ".wav", ".ogg",
    ".pdf", ".zip", ".tar", ".gz", ".7z",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".bin", ".exe", ".dll", ".so", ".dylib",
}

# Worker names: must match dashboard's regex. Keep validation client-side
# so we surface a clean error instead of letting the server 400.
_WORKER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]?$")


# ── Token storage ────────────────────────────────────────────────────────────

def _read_state() -> dict:
    try:
        if _NEOWOW_FILE.exists():
            return json.loads(_NEOWOW_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("neowow state unreadable; treating as empty", exc_info=True)
    return {}


def _write_state(state: dict) -> None:
    _NEOWOW_FILE.parent.mkdir(parents=True, exist_ok=True)
    _NEOWOW_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    # Chmod last-write-wins, so Windows (which ignores 0600) doesn't crash;
    # POSIX systems get the lock-down.
    try:
        os.chmod(_NEOWOW_FILE, 0o600)
    except OSError:
        pass


def get_status(handler=None) -> dict:
    """Return whatever the UI needs to render the integration panel.

    Never returns the full token / JWT. Masking shapes:
      • deploy token: `nws_dt_…1234`
      • JWT:          `eyJ…1234` (just enough to confirm presence)
    so the user can verify "yes that's the credential I saved" without
    copy-pasting it back out of a screenshot.

    Phase β cloud mode: when HERMES_WEBUI_AUTH_MODE=neodomain (i.e.
    chat.neowow.studio behind cross-subdomain `neoToken` cookie), the
    JWT lives in the request cookie — NOT in the local state file. The
    avatar UI was originally desktop-only and read only the file, which
    made the avatar perpetually say "未登录" on the cloud deployment
    even though chat itself worked (because the request cookie WAS
    being honored by auth.py for everything else).

    Pass `handler` (BaseHTTPRequestHandler) when calling from a route
    so we can sniff the `neoToken` cookie and report the cloud session
    too. When `handler` is None (e.g. an admin script calling this
    function directly) we fall back to the file-only behavior.
    """
    state = _read_state()
    token = (state.get("token") or "").strip()
    file_jwt = (state.get("jwt") or "").strip()

    # Cloud-mode JWT discovery — sniff the request's neoToken cookie.
    cookie_jwt = ""
    if handler is not None and _is_neodomain_mode():
        try:
            from api.auth import parse_neo_cookie
            cookie_jwt = (parse_neo_cookie(handler) or "").strip()
        except Exception:
            # If cookie parsing throws (malformed header etc.) fall
            # back to file-only — avatar is best-effort.
            cookie_jwt = ""

    # Cookie wins over file when both are present. They normally won't
    # be — the file path is for desktop, the cookie path is for cloud.
    effective_jwt = cookie_jwt or file_jwt
    jwt_source = (
        "cookie" if cookie_jwt
        else ("file" if file_jwt else "")
    )

    return {
        "hasToken":     bool(token),
        "maskedToken":  _mask_token(token) if token else "",
        "hasJwt":       bool(effective_jwt),
        "maskedJwt":    _mask_jwt(effective_jwt) if effective_jwt else "",
        # Lets the UI know WHERE the JWT came from. Cookie-mode means
        # logout has to clear the cookie (server-side via dashboard),
        # not just the local file.
        "jwtSource":    jwt_source,
        "lastDeploy":   state.get("lastDeploy"),
        # Tells the frontend whether native git-based updates are disabled
        # and replaced by Neowow-managed notices. Used by neowow.js to
        # decide whether to show the Neowow update banner vs. the
        # upstream git update banner.
        "neowowOnly":   _neowow_only(),
    }


def _is_neodomain_mode() -> bool:
    """True iff the WebUI is running in cross-subdomain neoToken cookie
    auth mode (the cloud chat.neowow.studio deployment). We read the
    env var directly rather than importing get_auth_mode() to avoid a
    circular dep — api/auth.py imports things from api/onboarding.py
    which transitively imports things from here."""
    return os.getenv("HERMES_WEBUI_AUTH_MODE", "").strip().lower() == "neodomain"


def save_token(token: str) -> dict:
    token = (token or "").strip()
    if not token:
        raise ValueError("token is required")
    if not token.startswith("nws_dt_"):
        # Help users who pasted the wrong thing (e.g. their JWT login token).
        raise ValueError(
            "Token must start with 'nws_dt_'. Mint one at "
            "https://app.neowow.studio/account/deploy-tokens"
        )
    state = _read_state()
    state["token"] = token
    _write_state(state)
    return get_status()


def clear_token() -> dict:
    state = _read_state()
    state.pop("token", None)
    _write_state(state)
    return get_status()


# ── JWT (Neodomain user-auth token) — separate from the deploy token ─────────
#
# A nws_dt_ deploy token authenticates Hermes to /api/* endpoints on the
# dashboard (deploy / market / hermes-configs). It does NOT carry credit-
# spending authority — the dashboard intentionally does not let it call
# /agent/* endpoints on Neodomain.
#
# The JWT IS that authority. Acquired via the OAuth flow (Login Neodomain
# button → app.neowow.studio/api/oauth/start → callback writes it here),
# we keep it alongside the deploy token in the same neowow.json so a
# single `clear` action can wipe everything.

def save_jwt(jwt: str) -> dict:
    jwt = (jwt or "").strip()
    if not jwt:
        raise ValueError("jwt is required")
    # Cheap shape check — JWTs have three base64url segments separated
    # by dots. Reject obvious mistakes (someone pasting their deploy
    # token here, or a stray newline).
    if jwt.count(".") != 2 or jwt.startswith("nws_dt_"):
        raise ValueError(
            "That doesn't look like a Neodomain JWT (expected the "
            "three-segment 'eyJ…' form, not nws_dt_… deploy token)."
        )
    state = _read_state()
    state["jwt"] = jwt
    _write_state(state)
    return get_status()


def clear_jwt() -> dict:
    state = _read_state()
    state.pop("jwt", None)
    _write_state(state)
    return get_status()


# ── Per-request JWT context (cloud mode) ────────────────────────────────────
#
# Cloud mode (HERMES_WEBUI_AUTH_MODE=neodomain — chat.neowow.studio): the
# JWT lives in the per-request `neoToken` cookie, NOT in the local state
# file. server.py calls `set_request_jwt_from_cookie(handler)` at the
# start of every request (paired with clear in a finally), so any code
# path that subsequently calls `get_jwt()` sees the cookie's JWT.
#
# Desktop mode (HERMES_WEBUI_AUTH_MODE unset or = password — local
# pywebview / Tauri Hermes app): the JWT lives in neowow.json (file).
# The context var stays empty; get_jwt() falls through to the file.
#
# Why threadlocal + threading.local() instead of contextvars.ContextVar:
# the existing api/profiles.py pattern uses threadlocal (it predates
# this code), and ThreadingHTTPServer is one-thread-per-request, so a
# threadlocal is functionally equivalent. Reusing the pattern keeps the
# server.py wrapper symmetric (set_request_*/clear_request_* pairs).

import threading

_request_tls = threading.local()


def set_request_jwt_from_cookie(handler) -> None:
    """Sniff the neoToken cookie on the current request and stash it
    so get_jwt() picks it up. Called by server.py per-request. No-op
    when not in neodomain mode, when handler is None, or when the
    cookie is absent (cleanly degrades to file-based JWT)."""
    if handler is None:
        return
    if os.getenv("HERMES_WEBUI_AUTH_MODE", "").strip().lower() != "neodomain":
        return
    try:
        from api.auth import parse_neo_cookie
        jwt = (parse_neo_cookie(handler) or "").strip()
    except Exception:
        jwt = ""
    _request_tls.jwt = jwt


def clear_request_jwt() -> None:
    """Pair with set_request_jwt_from_cookie. Called in server.py's
    finally block so the threadlocal doesn't bleed across requests
    (matters with thread reuse in ThreadingHTTPServer)."""
    _request_tls.jwt = None


def get_jwt() -> str:
    """Return the active JWT for the current request, or '' when none.

    Resolution order:
      1. Per-request context (set by server.py from neoToken cookie in
         cloud mode). When present, ALWAYS wins.
      2. Local state file (desktop mode or admin scripts running
         outside a request).

    All /agent/* proxy paths and avatar status calls go through here, so
    chat.neowow.studio's per-request cookie automatically authenticates
    every downstream call without each caller having to know about the
    two modes.
    """
    req_jwt = getattr(_request_tls, "jwt", None)
    if req_jwt:
        return req_jwt
    state = _read_state()
    return (state.get("jwt") or "").strip()


# ── OAuth launch ─────────────────────────────────────────────────────────────
#
# Why we need a Python-side launcher: Hermes WebUI loads inside a
# pywebview window (see main.py).  pywebview silently blocks
# `window.open(url, '_blank')` — the user clicks the rail avatar and
# nothing visible happens.
#
# The fix is to call out to Python's `webbrowser` module, which always
# opens the OS's default browser regardless of what window the WebUI
# is running inside.  Same module the installer's first-run uses
# (`app.py:1533: webbrowser.open("http://localhost:7891")`), so we
# know it works on every supported platform.
#
# The return URL has to be on this Hermes server (localhost:<port>) so
# the OAuth callback can POST the JWT back to /api/neowow/jwt without
# CORS gymnastics.  We accept it from the caller (JS knows its own
# origin) and validate that it points at us before forwarding.

import re

_OAUTH_AUTHORIZE_URL = "https://app.neowow.studio/api/oauth/start"

# Allow callbacks ONLY back to localhost / 127.0.0.1 — defends against
# a buggy / malicious browser-tab calling this endpoint to redirect
# the user's OAuth landing somewhere we don't control.
_LOCAL_RETURN_RE = re.compile(
    r"^https?://(?:localhost|127\.0\.0\.1)(?::\d+)?/api/neowow/oauth-callback$"
)


def launch_oauth(return_url: str) -> dict:
    """Open the system default browser at the dashboard's OAuth start
    URL with the given local-callback as the return target.

    Validates that return_url points back at this Hermes server before
    forwarding.  Returns {ok, url} so the UI can fall back to "click
    here manually" if webbrowser.open() fails (rare, but possible on
    headless / restricted environments).
    """
    return_url = (return_url or "").strip()
    if not _LOCAL_RETURN_RE.match(return_url):
        raise ValueError(
            "Invalid return URL — must point back at this Hermes "
            "server's /api/neowow/oauth-callback"
        )

    # URL-encode the return URL so spaces / special chars in the port
    # don't trip up the dashboard's parser.
    from urllib.parse import quote
    auth_url = f"{_OAUTH_AUTHORIZE_URL}?return={quote(return_url, safe='')}"

    import webbrowser
    try:
        # `new=2` asks the browser for a new tab (rather than a new
        # window).  Most browsers honor this; the rest fall back to
        # whatever they prefer, which is fine.
        opened = webbrowser.open(auth_url, new=2)
    except Exception as e:
        raise RuntimeError(f"Failed to launch browser: {e}")

    return {
        "ok":      bool(opened),
        "url":     auth_url,
        # When opened is False (rare — e.g. on a headless box without
        # a registered browser), the UI should surface `url` so the
        # user can copy-paste it themselves.
    }


def _mask_token(t: str) -> str:
    # Show prefix + last 4. Sanity: tokens are 32+ chars, so this won't
    # accidentally reveal everything for a short string.
    if len(t) < 12:
        return "nws_dt_***"
    return f"nws_dt_…{t[-4:]}"


def _mask_jwt(t: str) -> str:
    # JWTs are long; show first 6 + last 4 so users can spot it's a
    # JWT without exposing the signature.
    if len(t) < 16:
        return "eyJ***"
    return f"{t[:6]}…{t[-4:]}"


# ── Workspace bundling ───────────────────────────────────────────────────────

def collect_files(root: Path) -> list[dict]:
    """Walk a workspace and return [{name, content}] for every shippable file.

    Raises ValueError when the workspace is empty or oversized — surface
    the message verbatim to the user, it's already actionable.
    """
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Workspace not found: {root}")

    files: list[dict] = []
    total = 0
    skipped: list[str] = []

    for path in sorted(root.rglob("*")):
        # Skip directories we never bundle — also prune the walk under them
        # by checking parent path components.
        rel_parts = path.relative_to(root).parts
        if any(part in _SKIP_DIRS or part.startswith(".") and part not in {".env"}
               for part in rel_parts[:-1]):
            continue
        if path.is_dir():
            continue
        if path.name in _SKIP_DIRS:
            continue
        if path.suffix.lower() in _BINARY_EXT:
            skipped.append(str(path.relative_to(root)))
            continue

        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _MAX_FILE_BYTES:
            skipped.append(f"{path.relative_to(root)} ({size} bytes — over per-file cap)")
            continue
        if total + size > _MAX_TOTAL_BYTES:
            raise ValueError(
                f"Workspace exceeds {_MAX_TOTAL_BYTES // (1024*1024)} MB cap. "
                f"Trim files (skip large assets, prune build output) before deploying."
            )
        if len(files) >= _MAX_FILES:
            raise ValueError(
                f"Too many files ({_MAX_FILES} cap). Did you mean to deploy a "
                f"sub-directory? Adjust the workspace path."
            )

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped.append(f"{path.relative_to(root)} (binary)")
            continue

        rel = str(path.relative_to(root)).replace(os.sep, "/")
        files.append({"name": rel, "content": content})
        total += size

    if not files:
        raise ValueError("No deployable text files found in workspace.")

    # The dashboard requires an index.html as the entry point. If the
    # workspace doesn't have one, fail early with a readable error rather
    # than letting the deploy succeed and the served URL 404.
    has_entry = any(re.fullmatch(r"index\.html?", f["name"], re.I) for f in files)
    if not has_entry:
        raise ValueError(
            "Workspace must have an `index.html` at its root for neowow to "
            "serve. (Put it where you want the deployed URL's `/` to land.)"
        )

    return files


def deploy(worker_name: str, workspace: str) -> dict:
    """Deploy a workspace to neowow.studio. Returns the dashboard's response.

    Raises ValueError on caller mistakes (invalid worker name, no token,
    oversized workspace) and RuntimeError on remote failures (so callers
    can surface clean messages without leaking internals).
    """
    state = _read_state()
    token = (state.get("token") or "").strip()
    if not token:
        raise ValueError("No deploy token saved. Paste one in Hermes settings first.")

    name = (worker_name or "").strip().lower()
    if not _WORKER_NAME_RE.match(name):
        raise ValueError(
            "Worker name must be 1-63 chars, lowercase letters/digits/dashes, "
            "starting and ending with alphanumeric. Example: my-app"
        )

    files = collect_files(Path(workspace).expanduser())

    payload = json.dumps({
        "workerName": name,
        "files":      files,
        # ownerId is intentionally omitted — the dashboard resolves it
        # from the bearer token and ignores any client-supplied value.
    }).encode("utf-8")

    req = urllib.request.Request(
        _NEOWOW_DEPLOY_URL,
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent":    "Hermes/neowow-deploy",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
    except urllib.error.HTTPError as e:
        # Try to surface the dashboard's JSON error body to the user — that's
        # where the actionable message lives ("Invalid deploy token", "Worker
        # name already taken", etc.).
        body = ""
        try:
            body = e.read().decode("utf-8")
            err = json.loads(body).get("error") or body
        except Exception:
            err = body or str(e)
        raise RuntimeError(f"Deploy failed ({e.code}): {err}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach neowow.studio: {e.reason}")

    # Persist last-deploy summary so the UI can show "deployed 2m ago".
    state["lastDeploy"] = {
        "workerName": name,
        "url":        data.get("url", ""),
        "fileCount":  len(files),
        "at":         data.get("deployedAt") or "",
    }
    _write_state(state)
    return data


# ── Cloud-config sync ────────────────────────────────────────────────────────
#
# The companion side of /account/hermes-configs. The user picks a model +
# system prompt + tool/skill set on the web; Hermes pulls it via the same
# deploy-token that powers the deploy flow above and applies the bits we
# can apply locally.
#
# v1 covers the high-impact fields:
#   • model.name        → Hermes config.yaml `model.default` (the model
#                          string Hermes-agent passes to the gateway)
#   • systemPrompt      → Hermes config.yaml `agent.system_prompt`
#                          (Hermes-agent reads this for chat sessions)
# The full blob is also stored under a top-level `neowow_cloud:` key so
# users can audit what the cloud sent without parsing the response.
#
# Skill content sync, MCP merging, and per-tool toggles are explicitly
# out of scope for v1 — those touch Hermes-agent core behavior and want
# a careful design pass before automation ships. The UI surfaces what's
# in the cloud config so users can see what's not auto-applied yet.

def _cloud_request(url: str, method: str = "GET") -> dict:
    """Make an authenticated request to the dashboard. Returns the parsed
    JSON body. Auth-source order:

      1. JWT — preferred. Comes from the user's neoToken cookie via the
         per-request threadlocal set by server.py
         (set_request_jwt_from_cookie) in cloud mode, or from the
         persisted neowow.json on desktop. Identifies the actual user;
         endpoints downstream can do full-account operations.

      2. Deploy token (nws_dt_*) — fallback. Lives in neowow.json's
         `token` field. Scoped — typically just `chat:invoke`. Useful
         when the user pasted a deploy token directly without OAuth-ing
         in (e.g. headless CLI integration). Identifies the OWNER of
         the token, not the live caller.

    Either path produces an `Authorization: Bearer <token>` header for
    the dashboard's resolveCaller (which now accepts both via the
    cookie fallback in dashboard/src/lib/caller.ts:resolveCaller).

    Raises:
      • ValueError when neither auth source is available — surface to
        UI so the user knows to log in or paste a deploy token.
      • RuntimeError on HTTP / transport errors — caller decides how
        to render.
    """
    # JWT first: get_jwt() prefers per-request threadlocal (cloud mode
    # cookie), falls through to file-based state (desktop). Either way,
    # if a JWT is present, use it.
    jwt = get_jwt()
    auth_token = jwt
    auth_source = "jwt" if jwt else ""

    # Deploy token as fallback. Some users have only a deploy token
    # configured (no OAuth login on this WebUI yet) — keep their flow
    # working.
    if not auth_token:
        state = _read_state()
        deploy_token = (state.get("token") or "").strip()
        if deploy_token:
            auth_token = deploy_token
            auth_source = "deploy_token"

    if not auth_token:
        raise ValueError(
            "Not signed in. Click the avatar to log in via Neodomain, "
            "or paste a deploy token in Settings → Neowow Studio."
        )

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {auth_token}",
            "User-Agent":    f"Hermes/neowow-cloud-config (auth={auth_source})",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
            err = json.loads(body).get("error") or body
        except Exception:
            err = body or str(e)
        raise RuntimeError(f"neowow API error ({e.code}): {err}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach neowow.studio: {e.reason}")


def list_cloud_configs() -> list[dict]:
    """Proxy GET /api/me/hermes-configs.

    Returns the dashboard's `configs` array verbatim — each entry has
    slug / name / description / modelName / skillCount / updatedAt and
    is a SUMMARY (no configJson). Use get_active_cloud_config to fetch
    the full blob.
    """
    data = _cloud_request(_CLOUD_LIST_URL)
    return data.get("configs", []) or []


def get_active_cloud_config() -> dict | None:
    """Proxy GET /api/me/hermes-configs/active.

    Returns either the full active config dict (with .configJson) or
    None when no slug is currently active in the dashboard. The caller
    should distinguish "no token / network error" (exception) from
    "token works but user hasn't picked an active config yet" (None).
    """
    data = _cloud_request(_CLOUD_ACTIVE_URL)
    if not data.get("slug") or not data.get("config"):
        return None
    return data["config"]


def apply_active_cloud_config() -> dict:
    """Pull the active cloud config and apply it to ~/.hermes/config.yaml.

    Idempotent — running twice writes the same bytes. Returns a small
    summary the UI uses to render the post-apply state:
      { applied: bool, slug, name, modelName, syncedAt, applied_fields, skipped_fields }

    Why we DON'T apply everything:
      • tools.shell/git/browser — Hermes-agent doesn't currently honor
        per-toggle config; gating happens via personality prompts. Wiring
        up enforcement is a Hermes-agent change, not an installer change.
      • tools.mcp — Hermes already has its own MCP-server config flow
        (see Settings → System → MCP Servers). Auto-merging risks
        duplicating user-managed entries.
      • skills — local ~/.hermes/skills/ has its own folder structure;
        cloud-skills sync wants its own pull-to-disk pass with conflict
        rules.
    All three live under `neowow_cloud:` for visibility but aren't
    auto-applied.
    """
    if yaml is None:
        raise RuntimeError(
            "PyYAML not installed — Hermes config.yaml writes are unavailable. "
            "Run `pip install pyyaml` and retry."
        )

    blob_wrap = get_active_cloud_config()
    if blob_wrap is None:
        return {
            "applied": False,
            "reason":  "no_active",
            "message": "云端没有激活的配置。先在 dashboard 选一个：",
            "url":     f"{_NEOWOW_BASE}/account/hermes-configs",
        }

    blob = blob_wrap.get("configJson", {}) or {}
    slug = blob_wrap.get("slug", "") or ""
    name = blob_wrap.get("name", "") or slug

    config_path = _hermes_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if config_path.exists():
        try:
            existing = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            # Corrupt yaml — keep the user's bytes around as a backup so
            # we don't silently destroy hand edits.
            backup = config_path.with_suffix(".yaml.before-neowow-sync")
            try:
                backup.write_text(config_path.read_text(encoding="utf-8"))
            except OSError:
                pass
            existing = {}

    applied_fields: list[str] = []
    skipped_fields: list[str] = []

    # ── model.name → model.default ───────────────────────────────────
    new_model = (blob.get("model") or {}).get("name", "").strip()
    if new_model:
        existing.setdefault("model", {})
        if existing["model"].get("default") != new_model:
            existing["model"]["default"] = new_model
        applied_fields.append("model.default")
    else:
        skipped_fields.append("model.default (cloud config has empty model name)")

    # ── systemPrompt → agent.system_prompt ───────────────────────────
    # We save the raw base prompt via skills.save_base_prompt() which
    # persists it as _base_prompt.txt, then calls rebuild_skills_system_prompt()
    # to merge it with the skills appendix (_skills_prompt.txt) before
    # writing to config.yaml. This prevents the skills layer from being
    # silently overwritten every time a cloud config sync runs.
    new_prompt = (blob.get("systemPrompt") or "").strip()
    try:
        from api.skills import save_base_prompt as _save_base_prompt
        _save_base_prompt(new_prompt)
        # rebuild_skills_system_prompt() is called inside save_base_prompt,
        # so config.yaml is updated automatically — skip the direct write below.
        applied_fields.append("agent.system_prompt (via skills layer)")
    except Exception as _sp_err:
        # skills module unavailable or write failed — fall back to direct write
        logger.warning("[neowow] save_base_prompt failed (%s), falling back", _sp_err)
        if new_prompt:
            existing.setdefault("agent", {})
            existing["agent"]["system_prompt"] = new_prompt
            applied_fields.append("agent.system_prompt")
        else:
            if "agent" in existing and "system_prompt" in (existing["agent"] or {}):
                existing["agent"].pop("system_prompt", None)
                applied_fields.append("agent.system_prompt (cleared)")

    # ── Full blob → neowow_cloud: (audit-trail; not auto-applied) ────
    existing["neowow_cloud"] = {
        "slug":        slug,
        "name":        name,
        "synced_at":   _utc_now_iso(),
        "config":      blob,
        "_note":
            "Synced from app.neowow.studio. model.default and "
            "agent.system_prompt are auto-applied above; tools / mcp / "
            "skills here are stored for visibility but not yet wired.",
    }

    config_path.write_text(
        yaml.dump(existing, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    # Tell webui's cached config to re-read on next get_config()
    try:
        reload_config()
    except Exception:
        logger.debug("reload_config after cloud apply failed", exc_info=True)

    # Persist last-synced bookkeeping for the status card.
    state = _read_state()
    state["lastCloudSync"] = {
        "slug":      slug,
        "name":      name,
        "modelName": new_model,
        "syncedAt":  _utc_now_iso(),
    }
    _write_state(state)

    return {
        "applied":        True,
        "slug":           slug,
        "name":           name,
        "modelName":      new_model,
        "syncedAt":       state["lastCloudSync"]["syncedAt"],
        "appliedFields":  applied_fields,
        "skippedFields":  skipped_fields,
    }


# ── Local → Cloud config push ────────────────────────────────────────────────
#
# Inverse of `apply_active_cloud_config`. Reads ~/.hermes/config.yaml,
# maps it to the dashboard's ConfigBlob shape, and creates / updates
# the matching config row via the dashboard API.
#
# What this DOES push:
#   • model.default → ConfigBlob.model.name
#   • agent.system_prompt → ConfigBlob.systemPrompt
#
# What this does NOT push (and why):
#   • API keys from ~/.hermes/.env — ConfigBlob has no slot for them, by
#     design. Each machine's API keys are local. Pulling this config on
#     a different machine still requires that machine to configure its
#     own .env (or use the Neodomain platform's bundled credentials).
#   • Tools enabled/disabled — ConfigBlob has slots but local config.yaml
#     doesn't have a 1:1 mapping yet. Sent as defaults.
#   • Skills — managed via the separate skills-sync flow.
#
# Auth: uses the saved nws_dt_ deploy token. Token must have the
# `configs:write` scope (or be a legacy `*`-scope token).
#
# Slug collision handling: GET the slug first to see if it exists. If
# yes → PUT (update). If no → POST (create). Avoids the dashboard's
# create endpoint failing with 500 on duplicate slugs (which it does
# in this codebase — see hermes-configs/route.ts:upsertConfig 'create').

def push_local_config_to_cloud(
    *,
    slug:        str,
    name:        str = "",
    description: str = "",
) -> dict:
    """Push the local ~/.hermes/config.yaml to the dashboard as a cloud config.

    Returns:
      {ok, mode: 'created'|'updated', slug, name, modelName, url}

    Raises:
      ValueError    — bad input (empty slug, no local config, no token)
                       or yaml unavailable / parse error
      RuntimeError  — dashboard HTTP / network error (caller picks 502)
    """
    if yaml is None:
        raise RuntimeError(
            "PyYAML not installed — Hermes config.yaml reads are unavailable. "
            "Run `pip install pyyaml` and retry."
        )

    # ── Validate inputs ──────────────────────────────────────────────
    slug = (slug or "").strip().lower()
    if not slug:
        raise ValueError("slug is required (e.g. 'my-mac' or 'default')")
    # Dashboard regex: [a-z0-9][a-z0-9-]{0,30}. Validate client-side
    # so we don't waste a round-trip on obvious errors.
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,30}$", slug):
        raise ValueError(
            "slug must be 1-31 chars, [a-z0-9-], start with [a-z0-9]"
        )

    # ── Auth ─────────────────────────────────────────────────────────
    state = _read_state()
    token = (state.get("token") or "").strip()
    if not token:
        raise ValueError(
            "No deploy token saved. Paste one in the Token field first, "
            "or click 'Login Neodomain' on the rail avatar to grant access."
        )

    # ── Read local config.yaml ──────────────────────────────────────
    config_path = _hermes_config_path()
    if not config_path.exists():
        raise ValueError(
            f"No Hermes config at {config_path}. Configure a model "
            f"first (Settings → Providers → pick one)."
        )

    try:
        local = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise ValueError(f"Local config.yaml is not valid YAML: {e}")

    # ── Map local → ConfigBlob ──────────────────────────────────────
    model_default = str(((local.get("model") or {}).get("default") or "")).strip()
    if not model_default:
        raise ValueError(
            "Local config.yaml has no model.default — pick a model "
            "(Settings → Providers) before pushing."
        )

    # provider field: best-effort guess from the model name prefix.
    # Falls back to 'neodomain' (the platform default) for unknown.
    model_lower = model_default.lower()
    if   model_lower.startswith(("claude", "sonnet", "haiku", "opus")):  provider = "anthropic"
    elif model_lower.startswith(("gpt", "o1", "o3", "o4")):              provider = "openai"
    elif model_lower.startswith("deepseek"):                              provider = "deepseek"
    elif model_lower.startswith("gemini"):                                provider = "gemini"
    elif model_lower.startswith(("glm", "z.ai")):                         provider = "zai"
    elif model_lower.startswith("grok"):                                  provider = "xai"
    elif model_lower.startswith("mistral"):                               provider = "mistral"
    else:                                                                 provider = "neodomain"

    system_prompt = str(((local.get("agent") or {}).get("system_prompt") or "")).strip()

    config_blob = {
        "schemaVersion": 1,
        "model":         {"provider": provider, "name": model_default},
        "systemPrompt":  system_prompt,
        # Tools / skills / mcp left as defaults — we don't have a clean
        # 1:1 mapping from local config.yaml's tool config yet. The
        # dashboard's apply-back path also doesn't act on these, so
        # round-tripping is lossless for the fields that matter.
        "tools": {
            "shell":   {"enabled": True,  "blocklist": []},
            "git":     {"enabled": True,  "allowPush": False},
            "browser": {"enabled": False},
            "mcp":     [],
        },
        "skills":   [],
        "metadata": {},
    }

    body = {
        "slug":        slug,
        "name":        (name.strip() or slug)[:60],
        "description": description.strip()[:200],
        "configJson":  config_blob,
    }
    body_bytes = json.dumps(body).encode("utf-8")

    # ── Existence check: pick POST or PUT ─────────────────────────────
    base = _NEOWOW_BASE.rstrip("/")
    check_url = f"{base}/api/me/hermes-configs/{slug}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
        "User-Agent":    "Hermes/neowow-cloud-push",
    }

    exists = False
    try:
        check_req = urllib.request.Request(check_url, headers=headers, method="GET")
        with urllib.request.urlopen(check_req, timeout=15) as resp:
            if resp.status == 200:
                exists = True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            exists = False
        else:
            err = _read_dashboard_err(e)
            raise RuntimeError(f"Pre-flight check failed (HTTP {e.code}): {err}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach app.neowow.studio: {e.reason}")

    # ── Create or update ─────────────────────────────────────────────
    if exists:
        target_url = check_url
        method     = "PUT"
        mode       = "updated"
    else:
        target_url = f"{base}/api/me/hermes-configs"
        method     = "POST"
        mode       = "created"

    req = urllib.request.Request(
        target_url,
        headers={**headers, "Content-Type": "application/json"},
        method=method,
        data=body_bytes,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            # We don't really need the response body (dashboard returns
            # {ok: true}), but reading it ensures the connection closes
            # cleanly before we return.
            resp.read()
    except urllib.error.HTTPError as e:
        err = _read_dashboard_err(e)
        raise RuntimeError(f"{method} failed (HTTP {e.code}): {err}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach app.neowow.studio: {e.reason}")

    return {
        "ok":        True,
        "mode":      mode,             # 'created' or 'updated'
        "slug":      slug,
        "name":      body["name"],
        "modelName": model_default,
        "provider":  provider,
        "url":       f"{base}/account/hermes-configs",
    }


def _read_dashboard_err(http_err) -> str:
    """Pull the user-readable error message out of a dashboard HTTPError.
    Dashboards generally return {"error": "..."} JSON for 4xx/5xx; fall
    back to the raw body for non-JSON responses."""
    body = ""
    try:
        body = http_err.read().decode("utf-8")
        return json.loads(body).get("error") or body
    except Exception:
        return body or str(http_err)


def get_whoami() -> dict:
    """Identity-only proxy of dashboard's /api/me/whoami.

    Returns who's logged in given the saved nws_dt_ deploy token —
    used by the WebUI to render a "Logged in as <nickname>" chip
    above the Token field so the user can see at a glance that the
    token they pasted matches the account they intended.

    Note: balance is intentionally NOT included.  Dashboard's whoami
    surfaces a `_balanceUnavailable` field explaining why (deploy
    tokens don't have credit-balance access via the standard
    Neodomain endpoint; needs a JWT). The UI shows that hint.

    Raises ValueError when no token is saved (caller should display
    the "paste token first" message), RuntimeError on transport.
    """
    return _cloud_request(_WHOAMI_URL)


# ── Neodomain (/agent/*) proxy via JWT ───────────────────────────────────────
#
# The deploy token (nws_dt_) authenticates against dashboard /api/* paths.
# The Neodomain platform itself (story.neodomain.cn, neowow.neodomain.cn,
# ga.neodomain.cn) auths via JWT in an `accessToken` header. After the
# OAuth-in-Hermes flow lands the JWT in our state file, these helpers
# can call /agent/user/points/info, /agent/ai-image-generation/*, etc.,
# directly — no dashboard hop.

# Neodomain's API base. We use the same host the OAuth flow points at
# (neowow.neodomain.cn) — that's the production environment per the
# user's instruction.  /agent/* endpoints all live under it.
_NEODOMAIN_BASE = "https://neowow.neodomain.cn"


# ── JWT lifecycle helpers (Phase β.13) ──────────────────────────────────────
#
# Neodomain enforces single-active-session — when the user logs in on
# another device, prior JWTs are server-side revoked. The old code would
# surface "Token has been revoked" forever because the dead JWT stayed in
# state.json and every subsequent request re-sent it.
#
# Auto-clear strategy: when a request returns 401/403 AND the error body
# mentions "revoked"/"expired"/"invalid", we delete the stored JWT
# AND raise JwtRevokedError. Route handlers map this to a structured
# 401 response so the UI can show "Re-login" instead of a generic blob.

class JwtRevokedError(RuntimeError):
    """Raised when Neodomain/dashboard rejects the stored JWT and we've
    already cleared it. UI should prompt re-login, not retry."""
    def __init__(self, msg: str, upstream_code: int):
        super().__init__(msg)
        self.upstream_code = upstream_code


_REVOKED_HINTS = ("revoked", "expired", "invalid token", "已过期", "已失效", "已撤销")


def _looks_like_revocation(body: str, err: str) -> bool:
    """Heuristic — does this 401/403 actually mean the JWT is dead?
    We can't auto-clear on every 401 because some endpoints return 401
    for unrelated reasons (e.g. missing scope on a deploy_token). So we
    only clear when the body explicitly says "revoked" / "expired" /
    similar."""
    text = (body + " " + err).lower()
    return any(h in text for h in _REVOKED_HINTS)


def _auto_clear_revoked(body: str, err: str, code: int):
    """When 401/403 + revocation hint detected, clear local JWT so the
    next request shows the logged-out state. Raises JwtRevokedError to
    short-circuit the route handler."""
    if _looks_like_revocation(body, err):
        try:
            clear_jwt()
        except Exception:
            logger.debug("clear_jwt during auto-revoke failed", exc_info=True)
        raise JwtRevokedError(
            f"Neowow 登录已失效（{code}）：{err.strip() or '可能在另一台设备重新登录过'}。"
            "已自动清除本地凭据，请点「重新登录 Neowow」。",
            upstream_code=code,
        )


def _neodomain_get(path: str) -> dict:
    """GET <NEODOMAIN_BASE><path> with the saved JWT.

    Surfaces the same exceptions the cloud-config proxy does so route
    handlers can map them uniformly:
      ValueError       → no JWT saved (UI tells user to log in)
      JwtRevokedError  → JWT was rejected as revoked / expired, we
                         already cleared it (UI shows re-login button)
      RuntimeError     → other HTTP / transport error
    """
    jwt = get_jwt()
    if not jwt:
        raise ValueError(
            "未登录 Neodomain。点击下方「登录 Neodomain」按钮先完成授权。"
        )
    url = _NEODOMAIN_BASE + path
    req = urllib.request.Request(
        url,
        headers={
            "accessToken": jwt,
            "Accept":      "application/json",
            "User-Agent":  "Hermes/neowow-jwt-proxy",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
            err = json.loads(body).get("errMessage") or body
        except Exception:
            err = body or str(e)
        # 401 / 403 → JWT likely expired (Neodomain JWTs ~30 days).
        # If the body explicitly mentions revocation/expiry, auto-clear
        # the local JWT + raise JwtRevokedError (route handler maps to
        # structured re-login prompt). Otherwise fall through to a
        # generic 502 — JWT stays in state.json on the assumption that
        # the 401 was caused by something else (server hiccup, scope
        # mismatch, etc.).
        if e.code in (401, 403):
            _auto_clear_revoked(body, err, e.code)
            raise RuntimeError(
                f"Neodomain 拒绝访问 ({e.code})：{err}。可能 JWT 已过期，请重新登录。"
            )
        raise RuntimeError(f"Neodomain API error ({e.code}): {err}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Neodomain: {e.reason}")


# ── Phase β: Coding Plan proxy ──────────────────────────────────────────────
#
# Mirror of get_points_info() but pointed at the dashboard's
# /api/me/plan endpoint (which is Phase α's source of truth for
# tiers + credits, separate from the Neodomain points pool — Coding
# Plan is OUR billing layer on top of Neodomain's tokens).
#
# Used by:
#   • Onboarding wizard — populates the model dropdown when the user
#     selects the Neowow Coding Plan card (see _fetch_neowow_plan_models
#     in onboarding.py — that's the inline copy of this fetch; we keep
#     two because the onboarding side runs BEFORE the API surface is
#     wired up, and inlining there keeps it independent).
#   • Chat UI top-bar chip — shows "Pro · 2342 credits left"
#   • /api/neowow/coding-plan endpoint (wired in routes.py) — exposes
#     it to the static JS as a clean local call.
def _dashboard_get_with_jwt(path: str) -> dict:
    """GET <_NEOWOW_BASE><path> using the saved JWT as Bearer.
    Same error shape as _neodomain_get for callers' uniform handling."""
    jwt = get_jwt()
    if not jwt:
        raise ValueError(
            "未登录 Neowow。点击下方「登录 Neowow」按钮先完成授权。"
        )
    url = _NEOWOW_BASE + path
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {jwt}",
            "Accept":        "application/json",
            "User-Agent":    "Hermes/coding-plan-proxy",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
            err  = (json.loads(body) or {}).get("error") or body
        except Exception:
            err = body or str(e)
        if e.code in (401, 403):
            # Phase β.13: same auto-clear-on-revoke heuristic as
            # _neodomain_get. Dashboard's /api/me/* responses include
            # the original Neodomain "Token has been revoked" text
            # pass-through, so the same hint scan catches both rails.
            _auto_clear_revoked(body, err, e.code)
            raise RuntimeError(
                f"Neowow Coding Plan 拒绝访问 ({e.code})：{err}。"
                "可能 JWT 已过期，请重新登录。"
            )
        raise RuntimeError(f"Neowow Coding Plan API error ({e.code}): {err}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Neowow dashboard: {e.reason}")


def get_coding_plan() -> dict:
    """Return the user's Coding Plan summary as a flat dict the UI can
    render directly:

      {
        planId, planName, creditsLimit, creditsUsed, creditsRemaining,
        cycleEndAt, models: [...], rps, concurrent,
        isTrial, rateLimited
      }

    No transformation needed — dashboard's /api/me/plan response shape
    is already the one the WebUI expects. We just forward it.
    """
    return _dashboard_get_with_jwt("/api/me/plan")


def get_points_info() -> dict:
    """Proxy GET /agent/user/points/info — returns the structured
    points + membership response shape documented in
    /Users/ff/Documents/api/获取余额度.md:

      data: {
        totalAvailablePoints: int,
        pointsDetails: [{pointsType, pointsTypeName, currentPoints,
                         expireTime, ...}, ...],
        membershipInfo: {levelCode, levelName, expireTime,
                         dailyPointsQuota, ...},
      }

    The UI renders this as the balance chip + membership badge (mirror
    of dashboard's UserPoints.tsx, just running locally in Hermes).
    """
    raw = _neodomain_get("/agent/user/points/info")
    if not isinstance(raw, dict) or not raw.get("success") or not raw.get("data"):
        # Surface the platform's errMessage when present; otherwise a
        # generic "no data" so the UI can show something useful.
        msg = (raw or {}).get("errMessage") or "Neodomain 返回空数据"
        raise RuntimeError(msg)
    return raw["data"]


def get_cloud_status() -> dict:
    """Lightweight status for the UI panel — never makes a network call.

    Reads only what's already on disk (last sync record + the
    `neowow_cloud:` section in config.yaml when available). Lets the
    panel render in <100 ms even when offline.
    """
    state = _read_state()
    last = state.get("lastCloudSync") or {}

    cached_blob: dict | None = None
    if yaml is not None:
        try:
            cfg = yaml.safe_load(_hermes_config_path().read_text(encoding="utf-8")) or {}
            cached_blob = cfg.get("neowow_cloud")
        except Exception:
            cached_blob = None

    return {
        "lastSync":  last,
        "cached":    cached_blob,
    }


def _utc_now_iso() -> str:
    """ISO-8601 UTC stamp — same shape the dashboard uses, easy to diff."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ── OSS backup helpers ────────────────────────────────────────────────────────

_OSS_SYNC_SCRIPT = "/opt/hermes-docker/oss-sync.sh"
_OSS_SYNC_LOG    = "/var/log/hermes-oss-sync.log"


def oss_backup_push() -> dict:
    """Manually trigger oss-sync.sh push.

    Runs synchronously (typical duration: 2–10 s depending on session size).
    Returns {ok, duration_ms, message}.
    Raises RuntimeError on script failure.
    """
    import subprocess, time
    if not os.path.isfile(_OSS_SYNC_SCRIPT):
        raise RuntimeError("OSS sync not available on this instance (script not found)")

    t0 = time.monotonic()
    result = subprocess.run(
        [_OSS_SYNC_SCRIPT, "push"],
        capture_output=True, text=True, timeout=120,
    )
    elapsed = int((time.monotonic() - t0) * 1000)

    if result.returncode != 0:
        raise RuntimeError(
            f"oss-sync.sh push exited {result.returncode}: "
            f"{(result.stderr or result.stdout or '').strip()[:300]}"
        )

    return {
        "ok":          True,
        "duration_ms": elapsed,
        "message":     "备份成功",
        "ts":          _utc_now_iso(),
    }


def oss_backup_status() -> dict:
    """Read the last sync timestamp from the OSS sync log.

    Returns {available, lastPush, lastPushTs} where:
      available  — False when the script/log is absent (not a cloud instance)
      lastPush   — human-readable last line from the log (may be empty)
      lastPushTs — ISO timestamp of the last "push: ok" entry, or ""
    """
    if not os.path.isfile(_OSS_SYNC_SCRIPT):
        return {"available": False, "lastPush": "", "lastPushTs": ""}

    last_push_ts = ""
    last_line    = ""
    try:
        if os.path.isfile(_OSS_SYNC_LOG):
            with open(_OSS_SYNC_LOG, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            # Walk backwards to find the last "push: ok" line
            for raw in reversed(lines):
                line = raw.strip()
                if line:
                    last_line = last_line or line
                if "push: ok" in line:
                    # Line format: [2026-05-22T13:00:00+0000] push: ok
                    import re
                    m = re.match(r"\[([^\]]+)\]", line)
                    if m:
                        last_push_ts = m.group(1)
                    break
    except Exception:
        pass

    return {
        "available":  True,
        "lastPush":   last_line,
        "lastPushTs": last_push_ts,
    }
