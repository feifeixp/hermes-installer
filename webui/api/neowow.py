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


def get_status() -> dict:
    """Return whatever the UI needs to render the integration panel.

    Never returns the full token / JWT. Masking shapes:
      • deploy token: `nws_dt_…1234`
      • JWT:          `eyJ…1234` (just enough to confirm presence)
    so the user can verify "yes that's the credential I saved" without
    copy-pasting it back out of a screenshot.
    """
    state = _read_state()
    token = (state.get("token") or "").strip()
    jwt   = (state.get("jwt") or "").strip()
    return {
        "hasToken":     bool(token),
        "maskedToken":  _mask_token(token) if token else "",
        "hasJwt":       bool(jwt),
        "maskedJwt":    _mask_jwt(jwt) if jwt else "",
        "lastDeploy":   state.get("lastDeploy"),
    }


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


def get_jwt() -> str:
    """Return the saved JWT or '' when none. Internal helper for the
    /agent/* proxy paths that need a real user token."""
    state = _read_state()
    return (state.get("jwt") or "").strip()


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
    """Make an authenticated request to the dashboard with the saved
    deploy-token. Returns the parsed JSON body. Raises:
      • ValueError when no token is saved (caller's mistake — surface to UI)
      • RuntimeError on HTTP/transport errors (caller decides how to display)
    """
    state = _read_state()
    token = (state.get("token") or "").strip()
    if not token:
        raise ValueError(
            "No deploy token saved. Paste one in the Token field above first."
        )

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent":    "Hermes/neowow-cloud-config",
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
    new_prompt = (blob.get("systemPrompt") or "").strip()
    if new_prompt:
        existing.setdefault("agent", {})
        existing["agent"]["system_prompt"] = new_prompt
        applied_fields.append("agent.system_prompt")
    else:
        # Empty prompt is a legitimate choice (preset 通用助理 ships ''),
        # so we DO clear an existing one when the cloud says empty.
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


def _neodomain_get(path: str) -> dict:
    """GET <NEODOMAIN_BASE><path> with the saved JWT.

    Surfaces the same exceptions the cloud-config proxy does so route
    handlers can map them uniformly:
      ValueError   → no JWT saved (UI tells user to log in)
      RuntimeError → HTTP / transport error
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
        # Tell the caller so the UI can prompt for re-login instead
        # of showing a generic error.
        if e.code in (401, 403):
            raise RuntimeError(
                f"Neodomain 拒绝访问 ({e.code})：{err}。可能 JWT 已过期，请重新登录。"
            )
        raise RuntimeError(f"Neodomain API error ({e.code}): {err}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Neodomain: {e.reason}")


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
