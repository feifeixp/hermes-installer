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

    Never returns the full token. Masking shape is `nws_dt_…1234` so the
    user can confirm "yes that's the token I pasted" without it being
    copy-paste-able from screenshots.
    """
    state = _read_state()
    token = (state.get("token") or "").strip()
    return {
        "hasToken":     bool(token),
        "maskedToken":  _mask_token(token) if token else "",
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


def _mask_token(t: str) -> str:
    # Show prefix + last 4. Sanity: tokens are 32+ chars, so this won't
    # accidentally reveal everything for a short string.
    if len(t) < 12:
        return "nws_dt_***"
    return f"nws_dt_…{t[-4:]}"


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
