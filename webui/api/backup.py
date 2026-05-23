"""
Hermes Web UI -- Backup API handlers.

Endpoints (called from api/routes.py):
  GET  /api/backup/local-summary    — sizes of each backupable dir
  POST /api/backup/create           — package + upload to OSS
  GET  /api/backup/list             — proxy to Dashboard /api/me/backups
  POST /api/backup/restore-local    — download + extract from OSS (self-contained)
  POST /api/backup/restore-cloud    — proxy restore-to-instance to Dashboard
  POST /api/backup/delete-proxy     — proxy delete to Dashboard
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
import urllib.error
import urllib.request

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler
    from urllib.parse import ParseResult

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))

def _get_jwt() -> str | None:
    """Read the user's neowow JWT from env (canonical then legacy name)."""
    return (
        os.environ.get("NEOWOW_TOKEN", "").strip()
        or os.environ.get("NEOWOW_CODING_PLAN_API_KEY", "").strip()
        or None
    )

def _dashboard_url() -> str:
    return os.environ.get("NEOWOW_DASHBOARD_URL", "https://app.neowow.studio").rstrip("/")

def _dashboard_get(path: str, jwt: str) -> dict:
    url = f"{_dashboard_url()}{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {jwt}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

def _dashboard_post(path: str, jwt: str, body: dict) -> dict:
    url     = f"{_dashboard_url()}{path}"
    payload = json.dumps(body).encode()
    req     = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

def _hermes_version() -> str:
    try:
        from api.updates import WEBUI_VERSION  # type: ignore
        return str(WEBUI_VERSION)
    except Exception:
        return ""

def _dir_size(path: Path) -> int:
    """Recursive byte count; 0 if path doesn't exist."""
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

def _file_size(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0

# Maps user-facing content key → (type, relative path inside hermes_home)
CONTENT_ITEMS: dict[str, tuple[str, str]] = {
    "sessions":  ("dir",  "sessions"),
    "memories":  ("dir",  "memories"),
    "config":    ("file", "config.yaml"),
    "skills":    ("dir",  "skills"),
    "state_db":  ("file", "state.db"),
    "kanban_db": ("file", "kanban.db"),
}

# Files that must NEVER be included regardless of selection
EXCLUDE_ALWAYS = {".env", "auth.json"}

# ── Local summary ─────────────────────────────────────────────────────────────

def local_summary() -> dict:
    home = _hermes_home()
    summary: dict[str, int] = {}
    for key, (kind, rel) in CONTENT_ITEMS.items():
        p = home / rel
        summary[key] = _dir_size(p) if kind == "dir" else _file_size(p)
    return summary

# ── Create backup ─────────────────────────────────────────────────────────────

def create_backup(label: str, contents: list[str]) -> dict:
    """
    Package selected items into a tar.gz, upload to OSS via Dashboard
    presigned URL, then confirm.

    Returns { ok, backupId, sizeBytes } on success.
    Raises RuntimeError on any failure.
    """
    jwt = _get_jwt()
    if not jwt:
        raise RuntimeError("NEOWOW_TOKEN not set — cannot authenticate with Dashboard")

    valid_contents = [c for c in contents if c in CONTENT_ITEMS]
    if not valid_contents:
        raise RuntimeError("No valid content items selected")

    home = _hermes_home()

    # Step 1: Request presigned upload URL from Dashboard
    presign_resp = _dashboard_post("/api/me/backups", jwt, {
        "label":    label,
        "contents": valid_contents,
    })
    backup_id  = presign_resp["backupId"]
    upload_url = presign_resp["uploadUrl"]
    oss_key    = presign_resp["ossKey"]

    # Step 2: Build tar.gz in a temp file
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        with tarfile.open(tmp_path, "w:gz") as tar:
            for key in valid_contents:
                kind, rel = CONTENT_ITEMS[key]
                p = home / rel
                if not p.exists():
                    logger.info("[backup] skipping %s (not found)", rel)
                    continue
                if p.is_dir():
                    for child in p.rglob("*"):
                        if child.name in EXCLUDE_ALWAYS:
                            continue
                        if child.is_file():
                            arcname = str(child.relative_to(home))
                            tar.add(child, arcname=arcname)
                else:
                    if p.name not in EXCLUDE_ALWAYS:
                        tar.add(p, arcname=rel)

        size_bytes = os.path.getsize(tmp_path)

        # Step 3: Upload to OSS via presigned PUT
        with open(tmp_path, "rb") as f:
            data = f.read()

        req = urllib.request.Request(
            upload_url, data=data, method="PUT",
            headers={"Content-Type": "application/gzip"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            if resp.status not in (200, 204):
                raise RuntimeError(f"OSS PUT returned {resp.status}")

        # Step 4: Confirm with Dashboard (writes TableStore row)
        _dashboard_post(f"/api/me/backups/{backup_id}/confirm", jwt, {
            "label":         label,
            "contents":      valid_contents,
            "sizeBytes":     size_bytes,
            "hermesVersion": _hermes_version(),
            "ossKey":        oss_key,
        })

        return {"ok": True, "backupId": backup_id, "sizeBytes": size_bytes}

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

# ── List backups ───────────────────────────────────────────────────────────────

def list_backups() -> dict:
    jwt = _get_jwt()
    if not jwt:
        return {"backups": [], "error": "not_authenticated"}
    return _dashboard_get("/api/me/backups", jwt)

# ── Restore local (self-contained) ────────────────────────────────────────────

def restore_local(backup_id: str) -> dict:
    """
    1. Gets presigned download URL from Dashboard using stored JWT.
    2. Downloads tar.gz and extracts into ~/.hermes/.
    Before extracting, takes a snapshot of current data.

    Returns { ok, snapshotPath } on success.
    Raises RuntimeError on failure.
    """
    jwt = _get_jwt()
    if not jwt:
        raise RuntimeError("NEOWOW_TOKEN not set")

    # Get presigned download URL from Dashboard
    url_resp = _dashboard_get(f"/api/me/backups/{backup_id}", jwt)
    download_url = url_resp.get("url", "")
    if not download_url:
        raise RuntimeError("Dashboard returned no download URL")

    home = _hermes_home()
    snapshot_dir = home / f"_pre_restore_{backup_id[:12]}"

    # Snapshot current data (best-effort)
    try:
        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir)
        snapshot_dir.mkdir(parents=True)
        for key, (kind, rel) in CONTENT_ITEMS.items():
            p   = home / rel
            dst = snapshot_dir / rel
            if p.is_dir():
                shutil.copytree(p, dst, dirs_exist_ok=True)
            elif p.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
        logger.info("[backup] snapshot saved to %s", snapshot_dir)
    except Exception as e:
        logger.warning("[backup] snapshot failed (continuing): %s", e)

    # Download the archive
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        req = urllib.request.Request(download_url)
        with urllib.request.urlopen(req, timeout=300) as resp:
            with open(tmp_path, "wb") as f:
                shutil.copyfileobj(resp, f)

        # Extract into ~/.hermes/ with safety checks
        with tarfile.open(tmp_path, "r:gz") as tar:
            for member in tar.getmembers():
                # Safety: skip absolute paths and parent traversal
                if member.name.startswith("/") or ".." in member.name:
                    continue
                # Safety: skip excluded filenames
                if Path(member.name).name in EXCLUDE_ALWAYS:
                    continue
                tar.extract(member, path=home, set_attrs=False)  # type: ignore[call-arg]

        return {"ok": True, "snapshotPath": str(snapshot_dir)}

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

# ── Restore cloud (proxy to Dashboard) ───────────────────────────────────────

def restore_cloud(backup_id: str) -> dict:
    jwt = _get_jwt()
    if not jwt:
        raise RuntimeError("NEOWOW_TOKEN not set")
    return _dashboard_post(f"/api/me/backups/{backup_id}/restore-to-instance", jwt, {})

# ── Delete backup (proxy to Dashboard) ───────────────────────────────────────

def delete_backup(backup_id: str) -> dict:
    jwt = _get_jwt()
    if not jwt:
        raise RuntimeError("NEOWOW_TOKEN not set")
    url = f"{_dashboard_url()}/api/me/backups/{backup_id}"
    req = urllib.request.Request(
        url, method="DELETE",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {"ok": True}
