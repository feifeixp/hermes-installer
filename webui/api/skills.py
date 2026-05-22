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
    └── _neowow.json   ← {id, name, version, syncedAt, displayName,
                          isDefault, ...}

`_neowow.json` is the bookkeeping file we use to detect updates and
decide what to delete on unsubscribe.

Dismissed defaults:
    ~/.hermes/skills/_neowow/_dismissed.json  ← ["skill-abc123", ...]
Skills in this list are skipped during sync (user opted out of a default
skill). Regular subscriptions are unaffected by the dismissed list.

System-prompt layers:
    ~/.hermes/skills/_neowow/_base_prompt.txt   ← from ConfigBlob.systemPrompt
    ~/.hermes/skills/_neowow/_skills_prompt.txt ← auto-generated skills appendix
`rebuild_skills_system_prompt()` merges them and writes to config.yaml.

Phase 1 handles PULL (cloud → local). Phase 1.5 will add the
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

# Cloud bulk endpoint — now returns subscriptions + platform defaults.
_CLOUD_SKILLS_URL = f"{_NEOWOW_BASE}/api/me/skills"

# Filesystem layout. Adjust both constants together if we ever move.
_SKILLS_ROOT_NAME   = "skills"
_NEOWOW_SUBDIR      = "_neowow"
_SKILL_FILE         = "SKILL.md"
_META_FILE          = "_neowow.json"
_DISMISSED_FILE     = "_dismissed.json"
_BASE_PROMPT_FILE   = "_base_prompt.txt"
_SKILLS_PROMPT_FILE = "_skills_prompt.txt"

# Hermes' agent-config can put HERMES_HOME wherever; mirror the same
# resolution path neowow.py uses for the config file.
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


# ── Dismissed list (user opt-out of default skills) ──────────────────────────

def read_dismissed() -> set[str]:
    """Return the set of skill IDs the user has opted out of."""
    path = _neowow_dir() / _DISMISSED_FILE
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x) for x in data if x}
    except Exception:
        logger.warning("[skills] unreadable %s — treating as empty", path)
    return set()


def _write_dismissed(dismissed: set[str]) -> None:
    path = _neowow_dir() / _DISMISSED_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sorted(dismissed), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def dismiss_skill(skill_id: str) -> None:
    """Add a default skill to the dismissed list and remove its local folder."""
    if not _is_valid_skill_id(skill_id):
        raise ValueError(f"Invalid skill id: {skill_id!r}")
    dismissed = read_dismissed()
    dismissed.add(skill_id)
    _write_dismissed(dismissed)
    _delete_local(skill_id)
    rebuild_skills_system_prompt()
    logger.info("[skills] dismissed %s", skill_id)


def restore_skill(skill_id: str) -> dict[str, Any]:
    """Remove a skill from the dismissed list and re-download it from cloud."""
    if not _is_valid_skill_id(skill_id):
        raise ValueError(f"Invalid skill id: {skill_id!r}")
    dismissed = read_dismissed()
    if skill_id not in dismissed:
        return {"ok": True, "note": "not in dismissed list"}
    dismissed.discard(skill_id)
    _write_dismissed(dismissed)

    # Re-fetch from cloud and write locally
    skills = _cloud_get_all()
    target = next((s for s in skills if s.get("id") == skill_id), None)
    if not target:
        return {
            "ok":    False,
            "error": f"{skill_id} not found in cloud (may no longer be default)",
        }
    _write_skill(target)
    rebuild_skills_system_prompt()
    logger.info("[skills] restored %s", skill_id)
    return {"ok": True, "id": skill_id, "name": str(target.get("name") or skill_id)}


# ── Cloud fetch ──────────────────────────────────────────────────────────────

def _cloud_get_all() -> list[dict[str, Any]]:
    """GET /api/me/skills — returns user subscriptions + platform defaults.

    Each entry: id / name / description / content / tags / version /
                displayName / isDefault / createdAt / updatedAt.

    Raises:
      ValueError  when no token is saved
      RuntimeError on transport / HTTP errors
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


# Backward-compat alias
_cloud_get_subscribed = _cloud_get_all


# ── Local filesystem operations ──────────────────────────────────────────────

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
        "isDefault":   bool(skill.get("isDefault")),
        "syncedAt":    _utc_now_iso(),
        "createdAt":   int(skill.get("createdAt") or 0),
        "updatedAt":   int(skill.get("updatedAt") or 0),
    }
    (target / _META_FILE).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _delete_local(sid: str) -> None:
    """Remove ~/.hermes/skills/_neowow/<sid>/. Idempotent."""
    if not _is_valid_skill_id(sid):
        return
    target = _neowow_dir() / sid
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


# ── System prompt injection ───────────────────────────────────────────────────

def save_base_prompt(prompt: str) -> None:
    """Called by neowow.py when it syncs ConfigBlob.systemPrompt.
    Persists the base prompt and triggers a rebuild of agent.system_prompt."""
    nd = _neowow_dir()
    nd.mkdir(parents=True, exist_ok=True)
    (nd / _BASE_PROMPT_FILE).write_text(prompt, encoding="utf-8")
    rebuild_skills_system_prompt()


def rebuild_skills_system_prompt() -> None:
    """Merge _base_prompt.txt + _skills_prompt.txt → config.yaml agent.system_prompt.

    Called after every skill sync, every dismiss/restore, and every
    ConfigBlob sync. Safe to call with either file missing.
    """
    nd = _neowow_dir()
    base_path   = nd / _BASE_PROMPT_FILE
    skills_path = nd / _SKILLS_PROMPT_FILE

    base   = base_path.read_text(encoding="utf-8").strip()   if base_path.exists()   else ""
    skills = skills_path.read_text(encoding="utf-8").strip() if skills_path.exists() else ""

    full = base
    if skills:
        full = (base + "\n\n" + skills) if base else skills

    try:
        _write_agent_system_prompt(full)
    except Exception as e:
        logger.warning("[skills] could not write system_prompt to config.yaml: %s", e)


def _build_skills_prompt(installed: list[dict[str, Any]]) -> str:
    """Generate the skills appendix text from a list of installed skill metas."""
    if not installed:
        return ""
    lines = [
        "## 已安装技能\n",
        "你已预装以下技能，用户可以直接呼叫它们：\n",
    ]
    for s in installed:
        name = str(s.get("name") or s.get("id") or "")
        desc = str(s.get("description") or "")
        if name:
            lines.append(f"- **{name}**{'：' + desc if desc else ''}")
    lines.append("\n如果用户的请求与某个技能的用途匹配，优先按该技能的指令执行。")
    return "\n".join(lines)


def _write_agent_system_prompt(prompt: str) -> None:
    """Write prompt to hermes-agent's config.yaml agent.system_prompt."""
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("[skills] pyyaml not available — skipping system_prompt write")
        return

    try:
        from api.profiles import get_active_hermes_home  # type: ignore[import-not-found]
        hermes_home = get_active_hermes_home()
    except ImportError:
        hermes_home = Path.home() / ".hermes"

    config_path = hermes_home / "hermes-agent" / "config.yaml"
    if not config_path.exists():
        logger.debug("[skills] config.yaml not found at %s, skipping", config_path)
        return

    try:
        existing = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning("[skills] could not parse config.yaml: %s", e)
        return

    if not isinstance(existing, dict):
        existing = {}
    if "agent" not in existing or not isinstance(existing.get("agent"), dict):
        existing["agent"] = {}

    if prompt:
        existing["agent"]["system_prompt"] = prompt
    else:
        existing["agent"].pop("system_prompt", None)

    try:
        config_path.write_text(
            yaml.dump(existing, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
        logger.debug("[skills] wrote system_prompt (%d chars) to %s", len(prompt), config_path)
    except Exception as e:
        logger.warning("[skills] failed writing config.yaml: %s", e)


# ── Public API surface (used by routes.py) ───────────────────────────────────

def get_local_status() -> dict[str, Any]:
    """Inspect-only read of the local _neowow/ folder. Never makes a
    network call — safe to render on every panel open."""
    root     = _neowow_dir()
    dismissed = read_dismissed()

    if not root.exists():
        return {"localSkills": [], "dismissedSkills": [], "rootPath": str(root)}

    items: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        meta = _read_local_meta(child)
        if not meta:
            items.append({
                "id":        child.name,
                "name":      child.name,
                "version":   0,
                "syncedAt":  "",
                "stale":     True,
                "isDefault": False,
            })
            continue
        items.append({
            "id":          str(meta.get("id") or child.name),
            "name":        str(meta.get("name") or child.name),
            "description": str(meta.get("description") or ""),
            "version":     int(meta.get("version") or 0),
            "syncedAt":    str(meta.get("syncedAt") or ""),
            "displayName": str(meta.get("displayName") or ""),
            "isDefault":   bool(meta.get("isDefault")),
        })

    dismissed_items = [
        {"id": sid, "dismissed": True}
        for sid in sorted(dismissed)
        if not (root / sid).exists()
    ]

    return {
        "localSkills":     items,
        "dismissedSkills": dismissed_items,
        "rootPath":        str(root),
    }


def list_cloud_skills() -> list[dict[str, Any]]:
    """Proxy GET /api/me/skills — used by the UI preview panel."""
    raw = _cloud_get_all()
    out: list[dict[str, Any]] = []
    for s in raw:
        out.append({
            "id":          str(s.get("id") or ""),
            "name":        str(s.get("name") or ""),
            "description": str(s.get("description") or ""),
            "version":     int(s.get("version") or 1),
            "displayName": str(s.get("displayName") or ""),
            "tags":        s.get("tags") if isinstance(s.get("tags"), list) else [],
            "isDefault":   bool(s.get("isDefault")),
            "updatedAt":   int(s.get("updatedAt") or 0),
        })
    return out


def sync_all_skills() -> dict[str, Any]:
    """Pull user subscriptions + platform default skills, reconcile local tree.

    isDefault=True skills are installed for all users regardless of subscription.
    Skills in _dismissed.json are skipped (user opted out). Regular subscriptions
    are never skipped.

    Returns summary:
      { added, updated, removed, skipped_dismissed, unchanged, rootPath }
    """
    cloud = _cloud_get_all()
    cloud_by_id: dict[str, dict[str, Any]] = {}
    for s in cloud:
        sid = s.get("id") or ""
        if _is_valid_skill_id(sid):
            cloud_by_id[sid] = s

    dismissed = read_dismissed()

    root = _neowow_dir()
    root.mkdir(parents=True, exist_ok=True)

    local_ids: set[str] = set()
    local_versions: dict[str, int] = {}
    for child in root.iterdir():
        if child.is_dir() and _is_valid_skill_id(child.name):
            local_ids.add(child.name)
            m = _read_local_meta(child)
            local_versions[child.name] = int((m or {}).get("version") or 0)

    added:             list[dict[str, Any]] = []
    updated:           list[dict[str, Any]] = []
    skipped_dismissed: list[str]            = []
    unchanged:         int                  = 0

    for sid, skill in cloud_by_id.items():
        is_default = bool(skill.get("isDefault"))

        # Only skip dismissed for default skills — user subscriptions always sync.
        if is_default and sid in dismissed:
            skipped_dismissed.append(sid)
            continue

        cloud_v = int(skill.get("version") or 1)
        if sid not in local_ids:
            try:
                _write_skill(skill)
                added.append({
                    "id":        sid,
                    "name":      str(skill.get("name") or sid),
                    "isDefault": is_default,
                })
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

    # Remove skills that disappeared from the cloud.
    cloud_ids = set(cloud_by_id.keys())
    removed: list[dict[str, Any]] = []
    for sid in sorted(local_ids):
        if sid in cloud_ids:
            continue
        meta = _read_local_meta(root / sid) or {}
        _delete_local(sid)
        removed.append({"id": sid, "name": str(meta.get("name") or sid)})

    # ── Rebuild system-prompt skills appendix ─────────────────────────────────
    installed_metas: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and _is_valid_skill_id(child.name):
            m = _read_local_meta(child)
            if m:
                installed_metas.append(m)

    skills_prompt = _build_skills_prompt(installed_metas)
    try:
        (root / _SKILLS_PROMPT_FILE).write_text(skills_prompt, encoding="utf-8")
    except Exception as e:
        logger.warning("[skills] could not write %s: %s", _SKILLS_PROMPT_FILE, e)

    rebuild_skills_system_prompt()

    # ── Management README ─────────────────────────────────────────────────────
    try:
        (root / "README.md").write_text(
            "# `_neowow/` — managed by Hermes\n\n"
            "These skills are pulled from your subscriptions on "
            "https://app.neowow.studio. Do NOT edit files here directly — "
            "changes are overwritten on the next sync.\n\n"
            "To dismiss a platform-default skill, use the WebUI settings panel "
            "→ 技能市场同步 → 官方默认技能 → [移除].\n",
            encoding="utf-8",
        )
    except Exception:
        pass

    return {
        "added":             added,
        "updated":           updated,
        "removed":           removed,
        "skipped_dismissed": skipped_dismissed,
        "unchanged":         unchanged,
        "rootPath":          str(root),
    }


# Backward-compat alias
def sync_subscribed_skills() -> dict[str, Any]:
    return sync_all_skills()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


__all__ = (
    "list_cloud_skills",
    "sync_all_skills",
    "sync_subscribed_skills",
    "get_local_status",
    "dismiss_skill",
    "restore_skill",
    "read_dismissed",
    "save_base_prompt",
    "rebuild_skills_system_prompt",
)

_unused_iter: Iterable[Any] = ()
del _unused_iter
