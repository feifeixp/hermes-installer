"""
Hermes ↔ Neowow Studio skill sync.

Pulls the subscriptions the user has on app.neowow.studio and writes
them under `~/.hermes/skills/_neowow/<id>/SKILL.md` so Hermes-agent
picks them up automatically. Companion to the cloud-config sync
(neowow.py) — same auth model (saved nws_dt_ deploy token), same
"Web is the SSOT, Hermes is a read consumer" architecture.

Why a separate `_neowow/` subfolder rather than mixing with the user's
own local skills:
  • Clear ownership: anything under `_neowow/` came from the cloud and
    will be deleted on unsubscribe. Anything outside it is yours.
  • No accidental conflicts with hand-organized category folders
    (apple/, creative/, …) that the user has set up.
  • Easy to wipe: `rm -rf ~/.hermes/skills/_neowow/` resets the cloud
    layer without touching your work.

Each skill ends up as:

    ~/.hermes/skills/_neowow/skill-abc123/
    ├── SKILL.md       ← markdown body verbatim from the dashboard
    └── _neowow.json   ← {id, name, version, syncedAt, displayName, ...}

`_neowow.json` is the bookkeeping file we use to detect updates and
decide what to delete on unsubscribe.

Phase 1 only handles PULL (cloud → local). Phase 1.5 will add the
publish-to-market path (local skill → POST /api/skills).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from api.config import STATE_DIR
from api.neowow import _read_state, _NEOWOW_BASE  # token storage shared with deploy/cloud-config

logger = logging.getLogger(__name__)

# Cloud bulk endpoint added to dashboard alongside this commit.
_CLOUD_SKILLS_URL = f"{_NEOWOW_BASE}/api/me/skills"

# Filesystem layout. Adjust both constants together if we ever move.
_SKILLS_ROOT_NAME = "skills"
_NEOWOW_SUBDIR    = "_neowow"
_SKILL_FILE       = "SKILL.md"
_META_FILE        = "_neowow.json"

# Hermes' agent-config can put HERMES_HOME wherever; mirror the same
# resolution path neowow.py uses for the config file. Defaults to
# ~/.hermes/skills/_neowow/ which matches the deployed-app convention.
def _skills_root() -> Path:
    """Where local skills live (~/.hermes/skills by default)."""
    import os
    env_override = os.getenv("HERMES_SKILLS_PATH")
    if env_override:
        return Path(env_override).expanduser()
    try:
        from api.profiles import get_active_hermes_home  # type: ignore[import-not-found]
        return get_active_hermes_home() / _SKILLS_ROOT_NAME
    except ImportError:
        return Path.home() / ".hermes" / _SKILLS_ROOT_NAME


def _neowow_dir() -> Path:
    return _skills_root() / _NEOWOW_SUBDIR


# ── Cloud fetch ──────────────────────────────────────────────────────────────

def _cloud_get_subscribed() -> list[dict[str, Any]]:
    """GET /api/me/skills using the saved deploy token.

    Returns the dashboard's `skills` array verbatim (each entry has
    id / name / description / content / tags / version / displayName /
    createdAt / updatedAt).

    Raises:
      ValueError when no token is saved (user setup error — surface to UI)
      RuntimeError on transport / HTTP errors (caller decides how to display)
    """
    state = _read_state()
    token = (state.get("token") or "").strip()
    if not token:
        raise ValueError(
            "No deploy token saved. Paste one in the Token field above first."
        )

    req = urllib.request.Request(
        _CLOUD_SKILLS_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent":    "Hermes/neowow-skills-sync",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
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

    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, list):
        raise RuntimeError("neowow returned an unexpected shape (no `skills` array)")
    return skills


# ── Local filesystem operations ──────────────────────────────────────────────

# Skill IDs from the dashboard look like `skill-kwgtob7`. Validate
# tightly so we don't ever build a path like `skills/_neowow/../../etc`.
_ID_RE = re.compile(r"^skill-[a-zA-Z0-9_-]{2,40}$")


def _is_valid_skill_id(s: str) -> bool:
    return bool(_ID_RE.match(s or ""))


def _read_local_meta(skill_dir: Path) -> dict[str, Any] | None:
    meta_path = skill_dir / _META_FILE
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("[skills] unreadable %s — treating as missing", meta_path)
        return None


def _write_skill(skill: dict[str, Any]) -> None:
    """Materialize one cloud skill on disk as <id>/SKILL.md + <id>/_neowow.json."""
    sid = skill.get("id") or ""
    if not _is_valid_skill_id(sid):
        raise ValueError(f"Invalid skill id from cloud: {sid!r}")

    target = _neowow_dir() / sid
    target.mkdir(parents=True, exist_ok=True)

    content = str(skill.get("content") or "").strip()
    if not content:
        # Empty content shouldn't really happen — the dashboard's skill-
        # create form requires it. Don't drop a 0-byte SKILL.md though;
        # write a placeholder so the agent doesn't load garbage.
        content = (
            f"# {skill.get('name') or sid}\n\n"
            f"_(This skill has empty content on app.neowow.studio.)_\n"
        )
    (target / _SKILL_FILE).write_text(content + "\n", encoding="utf-8")

    meta = {
        "id":          sid,
        "name":        str(skill.get("name") or sid),
        "description": str(skill.get("description") or ""),
        "version":     int(skill.get("version") or 1),
        "displayName": str(skill.get("displayName") or ""),
        "tags":        skill.get("tags") if isinstance(skill.get("tags"), list) else [],
        "syncedAt":    _utc_now_iso(),
        "createdAt":   int(skill.get("createdAt") or 0),
        "updatedAt":   int(skill.get("updatedAt") or 0),
    }
    (target / _META_FILE).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _delete_local(sid: str) -> None:
    """Remove ~/.hermes/skills/_neowow/<sid>/ and the metadata. Idempotent."""
    if not _is_valid_skill_id(sid):
        return  # belt-and-suspenders against path-traversal
    target = _neowow_dir() / sid
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


# ── Public API surface (used by routes.py) ───────────────────────────────────

def get_local_status() -> dict[str, Any]:
    """Inspect-only read of the local _neowow/ folder. Never makes a
    network call — safe to render on every panel open."""
    root = _neowow_dir()
    if not root.exists():
        return {"localSkills": [], "rootPath": str(root)}

    items: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        meta = _read_local_meta(child)
        if not meta:
            # Folder without metadata — surface it but flag, so the
            # user can clean up if they accidentally created garbage.
            items.append({
                "id":       child.name,
                "name":     child.name,
                "version":  0,
                "syncedAt": "",
                "stale":    True,
            })
            continue
        items.append({
            "id":          str(meta.get("id") or child.name),
            "name":        str(meta.get("name") or child.name),
            "description": str(meta.get("description") or ""),
            "version":     int(meta.get("version") or 0),
            "syncedAt":    str(meta.get("syncedAt") or ""),
            "displayName": str(meta.get("displayName") or ""),
        })
    return {"localSkills": items, "rootPath": str(root)}


def list_cloud_skills() -> list[dict[str, Any]]:
    """Proxy GET /api/me/skills (with content) — used by the UI's
    "preview cloud subscriptions" panel before the user hits sync."""
    raw = _cloud_get_subscribed()
    # Strip content for the preview path — it can be heavy and the UI
    # only needs metadata to render the list. Sync writes content from
    # a fresh fetch.
    out: list[dict[str, Any]] = []
    for s in raw:
        out.append({
            "id":          str(s.get("id") or ""),
            "name":        str(s.get("name") or ""),
            "description": str(s.get("description") or ""),
            "version":     int(s.get("version") or 1),
            "displayName": str(s.get("displayName") or ""),
            "tags":        s.get("tags") if isinstance(s.get("tags"), list) else [],
            "updatedAt":   int(s.get("updatedAt") or 0),
        })
    return out


def sync_subscribed_skills() -> dict[str, Any]:
    """Pull the user's subscriptions and reconcile the local _neowow/ tree.

    Returns a summary the UI uses to render the post-sync state:

      {
        added:     [{id, name}, …],   newly downloaded
        updated:   [{id, name, fromVersion, toVersion}, …],   version bumped
        removed:   [{id, name}, …],   user unsubscribed → wiped local
        unchanged: int                 same version, no write
        rootPath:  str
      }
    """
    cloud = _cloud_get_subscribed()
    cloud_by_id: dict[str, dict[str, Any]] = {}
    for s in cloud:
        sid = s.get("id") or ""
        if _is_valid_skill_id(sid):
            cloud_by_id[sid] = s

    # Snapshot what we have locally BEFORE writing.
    root = _neowow_dir()
    root.mkdir(parents=True, exist_ok=True)

    local_ids: set[str] = set()
    local_versions: dict[str, int] = {}
    if root.exists():
        for child in root.iterdir():
            if child.is_dir() and _is_valid_skill_id(child.name):
                local_ids.add(child.name)
                m = _read_local_meta(child)
                local_versions[child.name] = int((m or {}).get("version") or 0)

    added:     list[dict[str, Any]] = []
    updated:   list[dict[str, Any]] = []
    unchanged: int = 0

    for sid, skill in cloud_by_id.items():
        cloud_v = int(skill.get("version") or 1)
        if sid not in local_ids:
            try:
                _write_skill(skill)
                added.append({"id": sid, "name": str(skill.get("name") or sid)})
            except Exception as e:
                logger.warning("[skills] failed to write new skill %s: %s", sid, e)
            continue
        local_v = local_versions.get(sid, 0)
        if cloud_v != local_v:
            try:
                _write_skill(skill)
                updated.append({
                    "id":          sid,
                    "name":        str(skill.get("name") or sid),
                    "fromVersion": local_v,
                    "toVersion":   cloud_v,
                })
            except Exception as e:
                logger.warning("[skills] failed to update skill %s: %s", sid, e)
        else:
            unchanged += 1

    # Anything local that isn't in the cloud subscription list anymore
    # was unsubscribed on the dashboard — wipe it. We also drop folders
    # that don't have metadata at all, treating "no meta" as evidence
    # the folder is orphaned (a previous partial sync, etc).
    cloud_ids = set(cloud_by_id.keys())
    removed: list[dict[str, Any]] = []
    for sid in sorted(local_ids):
        if sid in cloud_ids:
            continue
        meta = _read_local_meta(root / sid) or {}
        _delete_local(sid)
        removed.append({"id": sid, "name": str(meta.get("name") or sid)})

    # Finally, persist a top-level marker so the user sees the folder
    # is system-managed (and editing files inside is futile — they'll
    # be overwritten on the next sync).
    try:
        readme = root / "README.md"
        readme.write_text(
            "# `_neowow/` — managed by Hermes\n\n"
            "These skills are pulled from your subscriptions on "
            "https://app.neowow.studio. Do NOT edit files here directly — "
            "changes are overwritten on the next sync.\n\n"
            "To unsubscribe, do it on the dashboard's market page; the "
            "next sync removes the local folder. Files outside of "
            "`_neowow/` are yours and untouched.\n",
            encoding="utf-8",
        )
    except Exception:
        pass

    return {
        "added":     added,
        "updated":   updated,
        "removed":   removed,
        "unchanged": unchanged,
        "rootPath":  str(root),
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# Re-exported for type checkers / callers that prefer them as iterables.
__all__ = (
    "list_cloud_skills",
    "sync_subscribed_skills",
    "get_local_status",
)


# ── Suppress unused-import warning (keep module-level imports tidy) ──────────
_unused_iter: Iterable[Any] = ()
del _unused_iter
