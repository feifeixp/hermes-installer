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


def _mask_secret(value: str | None) -> str:
    """Mask a credential for display: keep a short visible prefix, hide the rest.

    - Empty / None → "" (nothing to show).
    - Short values (<= 6 chars) → "***" (no safe prefix to reveal).
    - Otherwise → first 6 chars + "***".
    """
    if not value:
        return ""
    if len(value) <= 6:
        return "***"
    return value[:6] + "***"


def get_channels_status() -> dict:
    """Snapshot of each messaging channel for the WebUI (no plaintext secrets).

    Returns a dict keyed by platform name. Each entry reports whether the
    channel is connected and surfaces masked, non-sensitive identifiers so
    the UI can show "linked as cli_ab***" without ever leaking the secret.

    - weixin: connected when WEIXIN_ACCOUNT_ID is set, the platform is
      enabled, and a matching account file exists under
      ~/.hermes/weixin/accounts/<id>.json.
    - feishu / wecom: connected when both id + secret env vars are present
      and the platform is enabled.
    """
    env = _parse_env()

    weixin_account_id = env.get("WEIXIN_ACCOUNT_ID", "")
    weixin_account_file = (
        _hermes_home() / "weixin" / "accounts" / f"{weixin_account_id}.json"
    )
    weixin_connected = bool(
        weixin_account_id
        and is_platform_enabled("weixin")
        and weixin_account_file.exists()
    )

    feishu_app_id = env.get("FEISHU_APP_ID", "")
    feishu_secret = env.get("FEISHU_APP_SECRET", "")
    feishu_connected = bool(
        feishu_app_id and feishu_secret and is_platform_enabled("feishu")
    )

    wecom_bot_id = env.get("WECOM_BOT_ID", "")
    wecom_secret = env.get("WECOM_SECRET", "")
    wecom_connected = bool(
        wecom_bot_id and wecom_secret and is_platform_enabled("wecom")
    )

    return {
        "weixin": {
            "connected": weixin_connected,
            "enabled": is_platform_enabled("weixin"),
            "account_id": weixin_account_id,
        },
        "feishu": {
            "connected": feishu_connected,
            "enabled": is_platform_enabled("feishu"),
            "app_id_masked": _mask_secret(feishu_app_id),
            "has_secret": bool(feishu_secret),
        },
        "wecom": {
            "connected": wecom_connected,
            "enabled": is_platform_enabled("wecom"),
            "bot_id_masked": _mask_secret(wecom_bot_id),
            "has_secret": bool(wecom_secret),
        },
    }


def connect_feishu(*, app_id: str, app_secret: str) -> None:
    """Write Feishu credentials + enable platform. Blank app_secret means
    'keep existing' (so users can update app_id without re-typing the secret)."""
    app_id = (app_id or "").strip()
    if not app_id:
        raise ValueError("FEISHU_APP_ID is required")
    updates = {"FEISHU_APP_ID": app_id, "FEISHU_CONNECTION_MODE": "websocket"}
    if (app_secret or "").strip():
        updates["FEISHU_APP_SECRET"] = app_secret.strip()
    _upsert_env_vars(updates)
    set_platform_enabled("feishu", True)
    restart_gateway()


def disconnect_feishu() -> None:
    _remove_env_vars(["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_CONNECTION_MODE"])
    set_platform_enabled("feishu", False)
    restart_gateway()


def connect_wecom(*, bot_id: str, secret: str) -> None:
    """Write WeCom credentials + enable platform. Blank secret = keep existing."""
    bot_id = (bot_id or "").strip()
    if not bot_id:
        raise ValueError("WECOM_BOT_ID is required")
    updates = {"WECOM_BOT_ID": bot_id}
    if (secret or "").strip():
        updates["WECOM_SECRET"] = secret.strip()
    _upsert_env_vars(updates)
    set_platform_enabled("wecom", True)
    restart_gateway()


def disconnect_wecom() -> None:
    _remove_env_vars(["WECOM_BOT_ID", "WECOM_SECRET"])
    set_platform_enabled("wecom", False)
    restart_gateway()


# Module-level QR session store: qrcode_token -> {"created_at": float}.
# In-memory only; webui restart drops pending QR sessions (user re-scans).
_qr_sessions: dict[str, dict] = {}
_QR_SESSION_TTL = 600  # 10 min


def _ilink_get(endpoint: str) -> dict:
    """GET an iLink endpoint, return parsed JSON. Raises on HTTP/parse error."""
    url = f"{ILINK_BASE_URL}/{endpoint}"
    req = urllib.request.Request(url, headers={
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    })
    with urllib.request.urlopen(req, timeout=QR_HTTP_TIMEOUT) as resp:
        return json.loads(resp.read())


def weixin_qr_start() -> dict:
    """Request a fresh iLink bot QR code. Returns
    {qrcode_token, qrcode_img_url}. Stashes the token for status polling."""
    data = _ilink_get(f"{EP_GET_BOT_QR}?bot_type={QR_BOT_TYPE}")
    token = str(data.get("qrcode") or "")
    img = str(data.get("qrcode_img_content") or "")
    if not token:
        raise RuntimeError("iLink QR response missing qrcode")
    _qr_sessions[token] = {"created_at": time.time()}
    # Opportunistic GC of stale sessions.
    cutoff = time.time() - _QR_SESSION_TTL
    for k in [k for k, v in _qr_sessions.items() if v["created_at"] < cutoff]:
        _qr_sessions.pop(k, None)
    return {"qrcode_token": token, "qrcode_img_url": img}


def _save_weixin_account(account_id: str, token: str, base_url: str, user_id: str) -> None:
    """Persist iLink credentials to ~/.hermes/weixin/accounts/<id>.json."""
    acc_dir = _hermes_home() / "weixin" / "accounts"
    acc_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "token": token,
        "base_url": base_url,
        "user_id": user_id,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = acc_dir / f"{account_id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def weixin_qr_status(token: str) -> dict:
    """Poll iLink QR status for `token`. On 'confirmed', persist the account,
    write WEIXIN_ACCOUNT_ID, enable platform, restart gateway, consume token."""
    if token not in _qr_sessions:
        return {"status": "invalid_token"}
    try:
        data = _ilink_get(f"{EP_GET_QR_STATUS}?qrcode={token}")
    except Exception as exc:
        logger.debug("messaging: weixin QR poll error: %s", exc)
        return {"status": "error", "reason": str(exc)}

    status = str(data.get("status") or "wait")
    if status == "confirmed":
        account_id = str(data.get("ilink_bot_id") or "")
        bot_token = str(data.get("bot_token") or "")
        base_url = str(data.get("baseurl") or ILINK_BASE_URL)
        user_id = str(data.get("ilink_user_id") or "")
        if not account_id or not bot_token:
            return {"status": "error", "reason": "incomplete_credentials"}
        _save_weixin_account(account_id, bot_token, base_url, user_id)
        _upsert_env_vars({"WEIXIN_ACCOUNT_ID": account_id})
        set_platform_enabled("weixin", True)
        _qr_sessions.pop(token, None)
        restart_gateway()
        return {"status": "confirmed", "account_id": account_id}
    return {"status": status}


def disconnect_weixin() -> None:
    """Remove WeChat account + disable platform + restart gateway."""
    env = _parse_env()
    account_id = env.get("WEIXIN_ACCOUNT_ID", "")
    _remove_env_vars(["WEIXIN_ACCOUNT_ID"])
    set_platform_enabled("weixin", False)
    if account_id:
        acc = _hermes_home() / "weixin" / "accounts" / f"{account_id}.json"
        try:
            acc.unlink(missing_ok=True)
        except OSError:
            pass
    restart_gateway()


def restart_gateway() -> None:
    """Signal the gateway supervisor to restart so it picks up new config.

    The supervisor loop (gateway_autostart) auto-relaunches `hermes gateway
    run` ~5s after it exits. We just kill the running process. Best-effort:
    on platforms without pkill / when gateway isn't running, this is a no-op
    and the new config applies on next gateway start."""
    import subprocess
    try:
        subprocess.run(
            ["pkill", "-f", "hermes gateway run"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        )
    except Exception as exc:
        logger.debug("messaging: restart_gateway pkill failed (non-fatal): %s", exc)
