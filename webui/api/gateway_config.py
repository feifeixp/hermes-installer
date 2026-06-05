"""
Gateway / connection-mode configuration.

Decides whether the NeoMuse runs everything locally (the
default — install Hermes Agent, spawn a local WebUI server, open a
pywebview pointing at localhost) or just opens a remote WebUI URL
served by another machine.

Two modes:
  • "local"   — current behavior. Storage file may be missing or
                contain {"mode":"local"}. main.py runs bootstrap.py +
                server.py; pywebview opens http://127.0.0.1:<port>/.
  • "remote"  — pywebview opens the configured `url` directly. No
                bootstrap, no Hermes Agent install, no local server.
                Auth is handled by the cloud-side WebUI itself
                (Neodomain OAuth login flow).

Storage: ~/.hermes/webui/gateway.json. Same directory as neowow.json
(deploy-token state) — they're sibling installer-level configs.

   {
     "mode":  "remote",
     "url":   "https://hermes.example.com",
     "label": "我的 GPU 服务器"          // friendly name shown in UI
   }

The whole file is rewritten on each save (no partial updates) — simpler
than tracking deltas and the file is < 1 KB so writes are cheap.

Recovery: if the user configures a remote URL that's unreachable, they
can edit this JSON manually OR run `hermes-installer --reset-gateway`
(see main.py) to drop back to local mode.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Lazily resolve STATE_DIR. We can't import from api.config at module load
# time because main.py needs to read this config BEFORE the webui package
# is initialized (in remote mode, we never even import webui). Defer the
# config-module import; fall back to the documented default path when
# webui isn't on the path.
def _state_dir() -> Path:
    try:
        from api.config import STATE_DIR  # type: ignore[import-not-found]
        return STATE_DIR
    except Exception:
        # Mirrors api/config.py's resolution: env override > ~/.hermes/webui.
        env = os.getenv("HERMES_WEBUI_STATE_DIR")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".hermes" / "webui"


def _config_path() -> Path:
    return _state_dir() / "gateway.json"


# ── Read ─────────────────────────────────────────────────────────────────────

def load_gateway_config() -> dict[str, Any]:
    """Return the saved config, or an empty dict equivalent to local mode.

    Always returns a dict with at minimum a `mode` key. Missing file,
    parse errors, and bad shapes all degrade to {"mode": "local"} so
    main.py can run unconditional `cfg["mode"]` / `cfg.get(...)` checks.
    """
    path = _config_path()
    if not path.exists():
        return {"mode": "local"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"mode": "local"}
        # Coerce values to strings; the only legit numeric/bool field
        # would be future timestamps which we'd add separately.
        out = {"mode": str(raw.get("mode") or "local")}
        if raw.get("url"):   out["url"]   = str(raw["url"]).strip()
        if raw.get("label"): out["label"] = str(raw["label"]).strip()
        return out
    except Exception:
        logger.warning("gateway.json unreadable; falling back to local mode", exc_info=True)
        return {"mode": "local"}


def is_remote_mode() -> bool:
    """True iff a non-empty remote URL is configured. main.py uses this
    as the single decision point for the local-vs-remote fork."""
    cfg = load_gateway_config()
    if cfg.get("mode") != "remote":
        return False
    url = (cfg.get("url") or "").strip()
    return bool(url)


def get_remote_url() -> str | None:
    """Return the configured remote URL, or None when not in remote mode."""
    if not is_remote_mode():
        return None
    return (load_gateway_config().get("url") or "").strip()


# ── Write ────────────────────────────────────────────────────────────────────

def save_gateway_config(*, mode: str, url: str = "", label: str = "") -> dict[str, Any]:
    """Persist a new config. Validates inputs and raises ValueError on
    obvious mistakes — the API endpoint surfaces these to the UI verbatim.

    Side effect: chmod 0600 (best-effort; Windows ignores).
    """
    mode = (mode or "").strip().lower()
    if mode not in ("local", "remote"):
        raise ValueError("mode must be 'local' or 'remote'")

    cfg: dict[str, Any] = {"mode": mode}
    if mode == "remote":
        url = (url or "").strip()
        if not url:
            raise ValueError("remote mode requires a non-empty url")
        if not (url.startswith("https://") or url.startswith("http://")):
            raise ValueError("url must start with http:// or https://")
        # Drop trailing slashes — pywebview is fine either way but
        # comparing identical-looking URLs in logs is easier when
        # they're normalized.
        cfg["url"] = url.rstrip("/")
        if label and label.strip():
            cfg["label"] = label.strip()[:60]

    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

    return cfg


def clear_gateway_config() -> None:
    """Delete the config file. Equivalent to "go back to local mode"."""
    path = _config_path()
    if path.exists():
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("could not unlink gateway.json: %s", exc)


__all__ = (
    "load_gateway_config",
    "is_remote_mode",
    "get_remote_url",
    "save_gateway_config",
    "clear_gateway_config",
)
