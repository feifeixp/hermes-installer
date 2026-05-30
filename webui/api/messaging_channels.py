"""Hermes WebUI — messaging channel linking (WeChat QR / Feishu / WeCom).

Lets users connect phone messaging platforms from the WebUI:
- WeChat personal: scan an iLink Bot QR code (no public webhook).
- Feishu / WeCom: paste App ID/Secret; websocket long-connect mode.

Writes credentials to ~/.hermes/.env and flips platforms.<name>.enabled
in config.yaml. The gateway supervisor (gateway_autostart) restarts
`hermes gateway run` to pick up the new config.

Spec: docs/superpowers/specs/2026-05-30-messaging-channels-design.md
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# ── iLink QR protocol constants (copied verbatim from agent
#    gateway/platforms/weixin.py — these are stable protocol values). ──
ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0  # 131584, matches agent
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"
QR_BOT_TYPE = "3"
QR_HTTP_TIMEOUT = 8  # seconds


def _hermes_home() -> Path:
    """Active Hermes home (honors HERMES_HOME, else ~/.hermes)."""
    return Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()


def _env_path() -> Path:
    return _hermes_home() / ".env"


def _read_env_lines() -> list[str]:
    p = _env_path()
    if not p.exists():
        return []
    try:
        return p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _parse_env() -> dict[str, str]:
    """Parse ~/.hermes/.env into a dict (last value wins)."""
    out: dict[str, str] = {}
    for line in _read_env_lines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _upsert_env_vars(updates: dict[str, str]) -> None:
    """Set/overwrite env vars in ~/.hermes/.env, preserving other lines.

    Existing keys are replaced in place; new keys appended. The file is
    rewritten atomically (write temp + replace). chmod 0600 best-effort.
    """
    if not updates:
        return
    lines = _read_env_lines()
    remaining = dict(updates)
    out_lines: list[str] = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in remaining:
                out_lines.append(f"{k}={remaining.pop(k)}")
                continue
        out_lines.append(line)
    for k, v in remaining.items():
        out_lines.append(f"{k}={v}")

    p = _env_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    try:
        p.chmod(0o600)
    except OSError:
        pass


def _remove_env_vars(keys: list[str]) -> None:
    """Remove the given keys from ~/.hermes/.env (no-op if absent)."""
    keyset = set(keys)
    lines = _read_env_lines()
    out_lines = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in keyset:
                continue
        out_lines.append(line)
    p = _env_path()
    if not p.exists():
        return
    tmp = p.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
    os.replace(tmp, p)
    try:
        p.chmod(0o600)
    except OSError:
        pass


def _config_yaml_path() -> Path:
    return _hermes_home() / "config.yaml"


def _load_config_yaml() -> dict:
    """Load config.yaml as a dict, or {} if missing/unreadable."""
    p = _config_yaml_path()
    if not p.exists():
        return {}
    try:
        import yaml
        loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        logger.warning("messaging: config.yaml unreadable", exc_info=True)
        return {}


def _save_config_yaml(data: dict) -> None:
    """Atomically write config.yaml."""
    import yaml
    p = _config_yaml_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(tmp, p)


def set_platform_enabled(platform: str, enabled: bool) -> None:
    """Flip platforms.<platform>.enabled in config.yaml, preserving the rest."""
    cfg = _load_config_yaml()
    platforms = cfg.get("platforms")
    if not isinstance(platforms, dict):
        platforms = {}
        cfg["platforms"] = platforms
    section = platforms.get(platform)
    if not isinstance(section, dict):
        section = {}
        platforms[platform] = section
    section["enabled"] = bool(enabled)
    _save_config_yaml(cfg)


def is_platform_enabled(platform: str) -> bool:
    """True iff platforms.<platform>.enabled is truthy in config.yaml."""
    cfg = _load_config_yaml()
    platforms = cfg.get("platforms")
    if not isinstance(platforms, dict):
        return False
    section = platforms.get(platform)
    return bool(isinstance(section, dict) and section.get("enabled"))
