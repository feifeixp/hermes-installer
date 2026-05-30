# 消息渠道关联 Implementation Plan（微信扫码 / 飞书 / 企业微信）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hermes WebUI 新增「消息渠道」tab，让用户关联手机消息平台 —— 微信个人号扫码连接、飞书 / 企业微信填凭据连接（含折叠教学）。

**Architecture:** 新 stdlib-only 模块 `webui/api/messaging_channels.py` 负责：(1) 微信 iLink QR 代理（webui 后端用 urllib 直连 ilinkai.weixin.qq.com 的 2 个 endpoint，前端轮询 webui）；(2) 读写 `~/.hermes/.env`（FEISHU_*/WECOM_*/WEIXIN_ACCOUNT_ID）+ `config.yaml` 的 `platforms.<name>.enabled`；(3) secret masking。前端新 panel `messaging.js` 渲染 3 个 channel 卡片。复用现有 gateway autostart（supervisor loop 会自动重启 `hermes gateway run` 拾取新配置）。

**Tech Stack:** Python 3.11 stdlib (urllib.request, json, os, re, threading)。前端 vanilla JS + 一个轻量 QR 渲染库。pytest（webui/tests）跑在 `/Users/ff/hermes-installer/.build_venv/bin/python`。

**Spec:** `docs/superpowers/specs/2026-05-30-messaging-channels-design.md`

---

## 关键技术事实（实现前必读）

从 agent 源码 (`gateway/platforms/weixin.py`, `wecom.py`, `feishu.py`) + webui 现有基础设施确认：

1. **iLink QR 协议常量**（抄进 messaging_channels.py）：
   - `ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"`
   - `ILINK_APP_ID = "bot"`
   - `CHANNEL_VERSION = "2.2.0"`
   - `ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0`（= 131584）
   - `EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"`
   - `EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"`
   - QR 默认 `bot_type=3`
   - GET headers：`{"iLink-App-Id": "bot", "iLink-App-ClientVersion": "131584"}`
2. **iLink QR 状态机**（GET get_qrcode_status?qrcode=<hex> 返回 `status`）：
   - `"wait"`（注意不是 "waiting"）→ 等待扫码
   - `"scaned"` → 已扫，待手机确认
   - `"scaned_but_redirect"` → 附带 `redirect_host`，后续轮询切到该 host
   - `"expired"` → 二维码过期
   - `"confirmed"` → 附带 `ilink_bot_id`(=account_id) / `bot_token`(=token) / `baseurl` / `ilink_user_id`
3. **get_bot_qrcode 返回**：`{qrcode: "<hex token>", qrcode_img_content: "<可扫码 liteapp URL>"}`。前端用 qrcode_img_content 这个 URL 画二维码，微信扫它。
4. **企业微信字段**（wecom.py line 155-160）：`WECOM_BOT_ID` + `WECOM_SECRET`（required），`WECOM_WEBSOCKET_URL` 有默认值 `wss://openws.work.weixin.qq.com`（用户不填）。
5. **飞书字段**：`FEISHU_APP_ID` + `FEISHU_APP_SECRET` + `FEISHU_CONNECTION_MODE=websocket`。
6. **平台启用需要两处**：(a) `~/.hermes/.env` 写 env 凭据；(b) `config.yaml` 的 `platforms.<name>.enabled: true`（gateway run 读 config.yaml 决定加载哪些 adapter）。webui 已有 `_load_yaml_config_file` / `_save_yaml_config_file` / `_get_config_path`（routes.py line 1041-1044 已 import）。
7. **gateway 重启**：`gateway_autostart.py` 的 supervisor loop（`hermes gateway run 2>&1; ... restarting in 5s`）会在 gateway 进程退出后 5s 自动重启。所以"重启 gateway"= `pkill -f "hermes gateway run"`（supervisor 自动拉起，拾取新 config.yaml + .env）。非云端本地模式 gateway 可能未跑 supervisor —— 那种情况配置写入后下次 gateway 启动时生效，UI 提示"配置已保存，重启 Hermes 后生效"。
8. **.env 写入**：无现成的"写单个 key"helper（`_reload_dotenv` 只读不写）。本 plan Task 1 新建 `_upsert_env_vars()` / `_remove_env_vars()`。
9. **GET dispatch 锚点**：`/api/gateway/status`（routes.py line 5216）。POST 锚点：`/api/gateway/config`（line 7453）。
10. **panel 注册**：panels.js line 246 的 `MAIN_VIEW_PANELS` 数组 + line 260 的 lazy-load hook 模式（参考 `server` → `serverAdminLoad()`）。
11. **测试 venv**：`/Users/ff/hermes-installer/.build_venv/bin/python -m pytest`。webui 测试用 `sys.path.insert(0, str(Path(__file__).parent.parent))` 再 `from api import ...`。

---

## File Structure

| 文件 | Action | 责任 |
|---|---|---|
| `webui/api/messaging_channels.py` | Create | iLink QR 协议 + 代理；.env upsert/remove；config.yaml platform enable/disable；channel 状态聚合 + secret masking。~280 LOC |
| `webui/tests/test_messaging_channels.py` | Create | 单元测试（env helper / masking / config.yaml toggle / QR 代理 mock） |
| `webui/api/routes.py` | Modify | wire 6 路由（GET channels + GET weixin qr status；POST weixin qr start / weixin disconnect / feishu config / feishu disconnect / wecom config / wecom disconnect） |
| `webui/static/messaging.js` | Create | tab 渲染 + 微信 QR 状态机 + 飞书/企微表单 + 教学折叠 |
| `webui/static/vendor/qrcode.min.js` | Create | 轻量 QR 渲染库 |
| `webui/static/index.html` | Modify | rail tab（2 处）+ #mainMessaging DOM |
| `webui/static/style.css` | Modify | channel 卡片 / 徽章 / QR modal / details 样式 |
| `webui/static/i18n.js` | Modify | en + zh 文案 |
| `webui/static/panels.js` | Modify | 注册 'messaging' panel + lazy-load hook |

---

## PHASE 1 — 后端核心 (messaging_channels.py)

### Task 1: scaffold 模块 + env helper（upsert / remove）

**Files:**
- Create: `/Users/ff/hermes-installer/webui/api/messaging_channels.py`
- Create: `/Users/ff/hermes-installer/webui/tests/test_messaging_channels.py`

- [ ] **Step 1: 创建模块骨架 + env helper**

Create `webui/api/messaging_channels.py` with EXACTLY:

```python
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
```

Create `webui/tests/test_messaging_channels.py` with EXACTLY:

```python
"""Unit tests for messaging_channels module."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from api import messaging_channels as mc


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point HERMES_HOME at a temp dir so env/config writes are sandboxed."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    yield tmp_path


def test_upsert_env_creates_file(isolated_home):
    mc._upsert_env_vars({"FOO": "bar"})
    assert (isolated_home / ".env").read_text(encoding="utf-8") == "FOO=bar\n"


def test_upsert_env_replaces_existing_key(isolated_home):
    (isolated_home / ".env").write_text("FOO=old\nBAZ=keep\n", encoding="utf-8")
    mc._upsert_env_vars({"FOO": "new"})
    parsed = mc._parse_env()
    assert parsed["FOO"] == "new"
    assert parsed["BAZ"] == "keep"


def test_upsert_env_appends_new_key(isolated_home):
    (isolated_home / ".env").write_text("FOO=bar\n", encoding="utf-8")
    mc._upsert_env_vars({"NEW": "val"})
    parsed = mc._parse_env()
    assert parsed == {"FOO": "bar", "NEW": "val"}


def test_remove_env_vars(isolated_home):
    (isolated_home / ".env").write_text("A=1\nB=2\nC=3\n", encoding="utf-8")
    mc._remove_env_vars(["B"])
    parsed = mc._parse_env()
    assert parsed == {"A": "1", "C": "3"}


def test_remove_env_vars_noop_when_absent(isolated_home):
    mc._remove_env_vars(["NOPE"])  # no .env file
    assert not (isolated_home / ".env").exists()
```

- [ ] **Step 2: Run tests — all 5 PASS.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_messaging_channels.py -v
```

Expected: 5 passed.

- [ ] **Step 3: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/messaging_channels.py webui/tests/test_messaging_channels.py
git commit -m "feat(messaging): scaffold module + .env upsert/remove helpers"
```

---

### Task 2: config.yaml platform enable/disable helper

**Files:**
- Modify: `webui/api/messaging_channels.py`
- Modify: `webui/tests/test_messaging_channels.py`

- [ ] **Step 1: Append failing tests.**

Append to `webui/tests/test_messaging_channels.py`:

```python
import yaml as _yaml_test  # noqa: E402


def _write_config(home, data):
    (home / "config.yaml").write_text(_yaml_test.safe_dump(data), encoding="utf-8")


def test_set_platform_enabled_creates_section(isolated_home):
    _write_config(isolated_home, {"platforms": {}})
    mc.set_platform_enabled("feishu", True)
    cfg = _yaml_test.safe_load((isolated_home / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["platforms"]["feishu"]["enabled"] is True


def test_set_platform_disabled(isolated_home):
    _write_config(isolated_home, {"platforms": {"weixin": {"enabled": True}}})
    mc.set_platform_enabled("weixin", False)
    cfg = _yaml_test.safe_load((isolated_home / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["platforms"]["weixin"]["enabled"] is False


def test_set_platform_preserves_other_platforms(isolated_home):
    _write_config(isolated_home, {"platforms": {"telegram": {"enabled": True, "extra": {"x": 1}}}})
    mc.set_platform_enabled("wecom", True)
    cfg = _yaml_test.safe_load((isolated_home / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["platforms"]["telegram"]["enabled"] is True
    assert cfg["platforms"]["telegram"]["extra"]["x"] == 1
    assert cfg["platforms"]["wecom"]["enabled"] is True


def test_is_platform_enabled(isolated_home):
    _write_config(isolated_home, {"platforms": {"feishu": {"enabled": True}, "wecom": {"enabled": False}}})
    assert mc.is_platform_enabled("feishu") is True
    assert mc.is_platform_enabled("wecom") is False
    assert mc.is_platform_enabled("weixin") is False  # absent → False
```

- [ ] **Step 2: Run — 4 new FAIL with AttributeError.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_messaging_channels.py -v -k "platform"
```

Expected: FAIL with `module 'api.messaging_channels' has no attribute 'set_platform_enabled'`.

- [ ] **Step 3: Add config.yaml helpers to messaging_channels.py.**

Append to `webui/api/messaging_channels.py`:

```python
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
```

- [ ] **Step 4: Run tests — all PASS.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_messaging_channels.py -v
```

Expected: 9 passed (5 + 4).

- [ ] **Step 5: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/messaging_channels.py webui/tests/test_messaging_channels.py
git commit -m "feat(messaging): config.yaml platform enable/disable helpers"
```

---

### Task 3: channel 状态聚合 + secret masking

**Files:**
- Modify: `webui/api/messaging_channels.py`
- Modify: `webui/tests/test_messaging_channels.py`

- [ ] **Step 1: Append failing tests.**

```python
def test_mask_secret_short(isolated_home):
    assert mc._mask_secret("ab") == "***"
    assert mc._mask_secret("") == ""
    assert mc._mask_secret(None) == ""


def test_mask_secret_long(isolated_home):
    # Keep a short visible prefix; mask the rest.
    assert mc._mask_secret("cli_a1b2c3d4e5") == "cli_a1***"


def test_get_channels_status_empty(isolated_home):
    status = mc.get_channels_status()
    assert status["weixin"]["connected"] is False
    assert status["feishu"]["connected"] is False
    assert status["wecom"]["connected"] is False


def test_get_channels_status_feishu_connected(isolated_home):
    (isolated_home / ".env").write_text(
        "FEISHU_APP_ID=cli_abc123\nFEISHU_APP_SECRET=topsecret\n", encoding="utf-8")
    _write_config(isolated_home, {"platforms": {"feishu": {"enabled": True}}})
    status = mc.get_channels_status()
    assert status["feishu"]["connected"] is True
    assert status["feishu"]["app_id_masked"] == "cli_ab***"
    assert status["feishu"]["has_secret"] is True
    # Never leak plaintext secret.
    assert "topsecret" not in str(status)


def test_get_channels_status_wecom_connected(isolated_home):
    (isolated_home / ".env").write_text(
        "WECOM_BOT_ID=bot_xyz\nWECOM_SECRET=wcsecret\n", encoding="utf-8")
    _write_config(isolated_home, {"platforms": {"wecom": {"enabled": True}}})
    status = mc.get_channels_status()
    assert status["wecom"]["connected"] is True
    assert status["wecom"]["bot_id_masked"] == "bot_xy***"
    assert status["wecom"]["has_secret"] is True
    assert "wcsecret" not in str(status)


def test_get_channels_status_weixin_connected(isolated_home):
    (isolated_home / ".env").write_text("WEIXIN_ACCOUNT_ID=acc_123\n", encoding="utf-8")
    _write_config(isolated_home, {"platforms": {"weixin": {"enabled": True}}})
    acc_dir = isolated_home / "weixin" / "accounts"
    acc_dir.mkdir(parents=True)
    (acc_dir / "acc_123.json").write_text('{"token":"t"}', encoding="utf-8")
    status = mc.get_channels_status()
    assert status["weixin"]["connected"] is True
    assert status["weixin"]["account_id"] == "acc_123"
```

- [ ] **Step 2: Run — FAIL with AttributeError.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_messaging_channels.py -v -k "mask or channels_status"
```

- [ ] **Step 3: Add masking + status to messaging_channels.py.**

Append to `webui/api/messaging_channels.py`:

```python
def _mask_secret(value: str | None) -> str:
    """Mask a secret for display: short prefix + ***. Empty stays empty."""
    if not value:
        return ""
    if len(value) <= 3:
        return "***"
    return value[:6] + "***" if len(value) > 6 else value[:2] + "***"


def get_channels_status() -> dict:
    """Aggregate connection status for the 3 channels. Never returns
    plaintext secrets — only masked id prefixes + has_secret booleans."""
    env = _parse_env()

    feishu_id = env.get("FEISHU_APP_ID", "")
    feishu_connected = bool(feishu_id and env.get("FEISHU_APP_SECRET")) and is_platform_enabled("feishu")

    wecom_id = env.get("WECOM_BOT_ID", "")
    wecom_connected = bool(wecom_id and env.get("WECOM_SECRET")) and is_platform_enabled("wecom")

    weixin_account = env.get("WEIXIN_ACCOUNT_ID", "")
    weixin_connected = bool(weixin_account) and is_platform_enabled("weixin")

    return {
        "weixin": {
            "connected": weixin_connected,
            "account_id": weixin_account or None,
        },
        "feishu": {
            "connected": feishu_connected,
            "app_id_masked": _mask_secret(feishu_id) if feishu_id else None,
            "has_secret": bool(env.get("FEISHU_APP_SECRET")),
        },
        "wecom": {
            "connected": wecom_connected,
            "bot_id_masked": _mask_secret(wecom_id) if wecom_id else None,
            "has_secret": bool(env.get("WECOM_SECRET")),
        },
    }
```

- [ ] **Step 4: Run tests — all PASS.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_messaging_channels.py -v
```

Expected: 15 passed (9 + 6).

- [ ] **Step 5: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/messaging_channels.py webui/tests/test_messaging_channels.py
git commit -m "feat(messaging): channel status aggregation + secret masking"
```

---

### Task 4: 飞书 / 企微 connect + disconnect

**Files:**
- Modify: `webui/api/messaging_channels.py`
- Modify: `webui/tests/test_messaging_channels.py`

- [ ] **Step 1: Append failing tests.**

```python
def test_connect_feishu_writes_env_and_enables(isolated_home):
    mc.connect_feishu(app_id="cli_x", app_secret="sec_y")
    env = mc._parse_env()
    assert env["FEISHU_APP_ID"] == "cli_x"
    assert env["FEISHU_APP_SECRET"] == "sec_y"
    assert env["FEISHU_CONNECTION_MODE"] == "websocket"
    assert mc.is_platform_enabled("feishu") is True


def test_connect_feishu_blank_secret_keeps_existing(isolated_home):
    mc.connect_feishu(app_id="cli_x", app_secret="orig")
    mc.connect_feishu(app_id="cli_x2", app_secret="")  # blank = keep
    env = mc._parse_env()
    assert env["FEISHU_APP_ID"] == "cli_x2"
    assert env["FEISHU_APP_SECRET"] == "orig"


def test_connect_feishu_requires_app_id(isolated_home):
    with pytest.raises(ValueError):
        mc.connect_feishu(app_id="", app_secret="s")


def test_disconnect_feishu(isolated_home):
    mc.connect_feishu(app_id="cli_x", app_secret="sec")
    mc.disconnect_feishu()
    env = mc._parse_env()
    assert "FEISHU_APP_ID" not in env
    assert "FEISHU_APP_SECRET" not in env
    assert mc.is_platform_enabled("feishu") is False


def test_connect_wecom_writes_env_and_enables(isolated_home):
    mc.connect_wecom(bot_id="bot_x", secret="sec_y")
    env = mc._parse_env()
    assert env["WECOM_BOT_ID"] == "bot_x"
    assert env["WECOM_SECRET"] == "sec_y"
    assert mc.is_platform_enabled("wecom") is True


def test_connect_wecom_blank_secret_keeps_existing(isolated_home):
    mc.connect_wecom(bot_id="bot_x", secret="orig")
    mc.connect_wecom(bot_id="bot_x2", secret="")
    env = mc._parse_env()
    assert env["WECOM_BOT_ID"] == "bot_x2"
    assert env["WECOM_SECRET"] == "orig"


def test_disconnect_wecom(isolated_home):
    mc.connect_wecom(bot_id="bot_x", secret="sec")
    mc.disconnect_wecom()
    env = mc._parse_env()
    assert "WECOM_BOT_ID" not in env
    assert "WECOM_SECRET" not in env
    assert mc.is_platform_enabled("wecom") is False
```

- [ ] **Step 2: Run — FAIL with AttributeError.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_messaging_channels.py -v -k "feishu or wecom"
```

- [ ] **Step 3: Add connect/disconnect to messaging_channels.py.**

Append:

```python
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


def disconnect_feishu() -> None:
    _remove_env_vars(["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_CONNECTION_MODE"])
    set_platform_enabled("feishu", False)


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


def disconnect_wecom() -> None:
    _remove_env_vars(["WECOM_BOT_ID", "WECOM_SECRET"])
    set_platform_enabled("wecom", False)
```

- [ ] **Step 4: Run tests — all PASS.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_messaging_channels.py -v
```

Expected: 22 passed (15 + 7).

- [ ] **Step 5: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/messaging_channels.py webui/tests/test_messaging_channels.py
git commit -m "feat(messaging): feishu/wecom connect + disconnect"
```

---

### Task 5: 微信 iLink QR 代理（start + status）+ disconnect

**Files:**
- Modify: `webui/api/messaging_channels.py`
- Modify: `webui/tests/test_messaging_channels.py`

- [ ] **Step 1: Append failing tests.**

```python
from unittest.mock import MagicMock, patch  # noqa: E402


def _ilink_resp(payload: dict):
    """Build a urlopen-style mock returning JSON bytes."""
    import json as _json
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    resp.read.return_value = _json.dumps(payload).encode("utf-8")
    return resp


def test_weixin_qr_start_returns_token_and_img(isolated_home):
    with patch.object(mc.urllib.request, "urlopen",
                      MagicMock(return_value=_ilink_resp({
                          "qrcode": "hex_token_abc",
                          "qrcode_img_content": "https://liteapp.example/scan",
                      }))):
        out = mc.weixin_qr_start()
    assert out["qrcode_token"] == "hex_token_abc"
    assert out["qrcode_img_url"] == "https://liteapp.example/scan"
    # token暂存
    assert "hex_token_abc" in mc._qr_sessions


def test_weixin_qr_status_wait(isolated_home):
    mc._qr_sessions["tok1"] = {"created_at": time.time()}
    with patch.object(mc.urllib.request, "urlopen",
                      MagicMock(return_value=_ilink_resp({"status": "wait"}))):
        out = mc.weixin_qr_status("tok1")
    assert out["status"] == "wait"


def test_weixin_qr_status_unknown_token(isolated_home):
    out = mc.weixin_qr_status("never_seen")
    assert out["status"] == "invalid_token"


def test_weixin_qr_status_confirmed_persists_account(isolated_home):
    mc._qr_sessions["tok2"] = {"created_at": time.time()}
    with patch.object(mc.urllib.request, "urlopen",
                      MagicMock(return_value=_ilink_resp({
                          "status": "confirmed",
                          "ilink_bot_id": "acc_999",
                          "bot_token": "secret_token",
                          "baseurl": "https://ilinkai.weixin.qq.com",
                          "ilink_user_id": "u_1",
                      }))), \
         patch.object(mc, "restart_gateway", MagicMock()):
        out = mc.weixin_qr_status("tok2")
    assert out["status"] == "confirmed"
    assert out["account_id"] == "acc_999"
    # account json written
    acc = isolated_home / "weixin" / "accounts" / "acc_999.json"
    assert acc.exists()
    saved = json.loads(acc.read_text(encoding="utf-8"))
    assert saved["token"] == "secret_token"
    # env + platform enabled
    assert mc._parse_env()["WEIXIN_ACCOUNT_ID"] == "acc_999"
    assert mc.is_platform_enabled("weixin") is True
    # token consumed
    assert "tok2" not in mc._qr_sessions


def test_weixin_disconnect(isolated_home):
    # set up a connected weixin
    mc._upsert_env_vars({"WEIXIN_ACCOUNT_ID": "acc_x"})
    mc.set_platform_enabled("weixin", True)
    acc_dir = isolated_home / "weixin" / "accounts"
    acc_dir.mkdir(parents=True)
    (acc_dir / "acc_x.json").write_text("{}", encoding="utf-8")
    with patch.object(mc, "restart_gateway", MagicMock()):
        mc.disconnect_weixin()
    assert "WEIXIN_ACCOUNT_ID" not in mc._parse_env()
    assert mc.is_platform_enabled("weixin") is False
    assert not (acc_dir / "acc_x.json").exists()


@pytest.fixture(autouse=True)
def clear_qr_sessions():
    mc._qr_sessions.clear()
    yield
    mc._qr_sessions.clear()
```

- [ ] **Step 2: Run — FAIL with AttributeError.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_messaging_channels.py -v -k "weixin_qr or weixin_disconnect"
```

- [ ] **Step 3: Add QR proxy + disconnect + restart_gateway to messaging_channels.py.**

Append:

```python
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
```

Also wire `restart_gateway()` into feishu/wecom connect/disconnect — Edit the 4 functions from Task 4 to call `restart_gateway()` at the end. Find:

```python
    _upsert_env_vars(updates)
    set_platform_enabled("feishu", True)
```

Replace with:

```python
    _upsert_env_vars(updates)
    set_platform_enabled("feishu", True)
    restart_gateway()
```

Find:

```python
    _remove_env_vars(["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_CONNECTION_MODE"])
    set_platform_enabled("feishu", False)
```

Replace with:

```python
    _remove_env_vars(["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_CONNECTION_MODE"])
    set_platform_enabled("feishu", False)
    restart_gateway()
```

Find:

```python
    _upsert_env_vars(updates)
    set_platform_enabled("wecom", True)
```

Replace with:

```python
    _upsert_env_vars(updates)
    set_platform_enabled("wecom", True)
    restart_gateway()
```

Find:

```python
    _remove_env_vars(["WECOM_BOT_ID", "WECOM_SECRET"])
    set_platform_enabled("wecom", False)
```

Replace with:

```python
    _remove_env_vars(["WECOM_BOT_ID", "WECOM_SECRET"])
    set_platform_enabled("wecom", False)
    restart_gateway()
```

**Note:** Task 4 tests don't patch `restart_gateway`, so calling `pkill` during those tests is harmless (pkill of a non-existent process just returns non-zero, swallowed). Re-run Task 4 tests after this to confirm they still pass.

- [ ] **Step 4: Run full suite — all PASS.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_messaging_channels.py -v
```

Expected: 27 passed (22 + 5).

- [ ] **Step 5: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/messaging_channels.py webui/tests/test_messaging_channels.py
git commit -m "feat(messaging): weixin iLink QR proxy + disconnect + gateway restart"
```

---

## PHASE 2 — 路由

### Task 6: wire GET 路由（channels status + weixin QR status）

**Files:**
- Modify: `webui/api/routes.py`

- [ ] **Step 1: Add GET routes before the /api/gateway/status block.**

In `webui/api/routes.py`, find (line ~5216):

```python
    if parsed.path == "/api/gateway/status":
```

Insert BEFORE it:

```python
    if parsed.path == "/api/messaging/channels":
        from api import messaging_channels as _mc
        try:
            return j(handler, _mc.get_channels_status())
        except Exception as exc:
            logger.exception("messaging channels status failed")
            return j(handler, {"error": str(exc)}, status=500)

    if parsed.path == "/api/messaging/weixin/qr/status":
        from api import messaging_channels as _mc
        token = parse_qs(parsed.query).get("token", [""])[0].strip()
        if not token:
            return bad(handler, "token required", 400)
        try:
            return j(handler, _mc.weixin_qr_status(token))
        except Exception as exc:
            logger.exception("messaging weixin qr status failed")
            return j(handler, {"status": "error", "reason": str(exc)}, status=500)

```

- [ ] **Step 2: Verify syntax.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -c "import ast; ast.parse(open('api/routes.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/routes.py
git commit -m "feat(routes): wire GET messaging channels + weixin qr status"
```

---

### Task 7: wire POST 路由（weixin qr start/disconnect + feishu/wecom config/disconnect）

**Files:**
- Modify: `webui/api/routes.py`

- [ ] **Step 1: Add POST routes before the /api/gateway/config POST block.**

In `webui/api/routes.py`, find the POST handler's gateway config block (line ~7453):

```python
    if parsed.path == "/api/gateway/config":
        from api.gateway_config import save_gateway_config, clear_gateway_config
```

Insert BEFORE it:

```python
    if parsed.path == "/api/messaging/weixin/qr/start":
        from api import messaging_channels as _mc
        try:
            return j(handler, _mc.weixin_qr_start())
        except Exception as exc:
            logger.exception("messaging weixin qr start failed")
            return bad(handler, str(exc), status=502)

    if parsed.path == "/api/messaging/weixin/disconnect":
        from api import messaging_channels as _mc
        try:
            _mc.disconnect_weixin()
            return j(handler, {"ok": True})
        except Exception as exc:
            logger.exception("messaging weixin disconnect failed")
            return bad(handler, str(exc), status=500)

    if parsed.path == "/api/messaging/feishu/config":
        from api import messaging_channels as _mc
        try:
            _mc.connect_feishu(
                app_id=str((body or {}).get("app_id", "")),
                app_secret=str((body or {}).get("app_secret", "")),
            )
            return j(handler, {"ok": True})
        except ValueError as exc:
            return bad(handler, str(exc), status=400)
        except Exception as exc:
            logger.exception("messaging feishu config failed")
            return bad(handler, str(exc), status=500)

    if parsed.path == "/api/messaging/feishu/disconnect":
        from api import messaging_channels as _mc
        try:
            _mc.disconnect_feishu()
            return j(handler, {"ok": True})
        except Exception as exc:
            logger.exception("messaging feishu disconnect failed")
            return bad(handler, str(exc), status=500)

    if parsed.path == "/api/messaging/wecom/config":
        from api import messaging_channels as _mc
        try:
            _mc.connect_wecom(
                bot_id=str((body or {}).get("bot_id", "")),
                secret=str((body or {}).get("secret", "")),
            )
            return j(handler, {"ok": True})
        except ValueError as exc:
            return bad(handler, str(exc), status=400)
        except Exception as exc:
            logger.exception("messaging wecom config failed")
            return bad(handler, str(exc), status=500)

    if parsed.path == "/api/messaging/wecom/disconnect":
        from api import messaging_channels as _mc
        try:
            _mc.disconnect_wecom()
            return j(handler, {"ok": True})
        except Exception as exc:
            logger.exception("messaging wecom disconnect failed")
            return bad(handler, str(exc), status=500)

```

**Note:** `body` is already parsed in `handle_post` via `read_body(handler)` near the top of the function (the gateway/config block uses `(body or {}).get(...)`). Confirm `body` is in scope at the insertion point by checking the surrounding code — the `/api/gateway/config` block immediately below uses `body`, so it is.

- [ ] **Step 2: Verify syntax + routes reachable.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -c "import ast; ast.parse(open('api/routes.py').read()); print('OK')"
```

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -c "
import sys; sys.path.insert(0, '.')
import inspect, api.routes as r
g = inspect.getsource(r.handle_get); p = inspect.getsource(r.handle_post)
for path in ['/api/messaging/channels','/api/messaging/weixin/qr/status']:
    assert path in g, path
for path in ['/api/messaging/weixin/qr/start','/api/messaging/weixin/disconnect','/api/messaging/feishu/config','/api/messaging/feishu/disconnect','/api/messaging/wecom/config','/api/messaging/wecom/disconnect']:
    assert path in p, path
print('all 8 routes wired OK')
"
```

Expected: `all 8 routes wired OK`

- [ ] **Step 3: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/api/routes.py
git commit -m "feat(routes): wire POST messaging weixin/feishu/wecom config routes"
```

---

### Task 8: routes-wired regression test

**Files:**
- Create: `webui/tests/test_messaging_routes_wired.py`

- [ ] **Step 1: Write the test.**

Create `webui/tests/test_messaging_routes_wired.py` with EXACTLY:

```python
"""Regression: the 8 messaging routes stay wired in routes.py handlers."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api import routes


def test_get_messaging_routes_wired():
    src = inspect.getsource(routes.handle_get)
    assert '/api/messaging/channels' in src
    assert '/api/messaging/weixin/qr/status' in src


def test_post_messaging_routes_wired():
    src = inspect.getsource(routes.handle_post)
    for path in [
        '/api/messaging/weixin/qr/start',
        '/api/messaging/weixin/disconnect',
        '/api/messaging/feishu/config',
        '/api/messaging/feishu/disconnect',
        '/api/messaging/wecom/config',
        '/api/messaging/wecom/disconnect',
    ]:
        assert path in src, path
```

- [ ] **Step 2: Run — PASS.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_messaging_routes_wired.py -v
```

Expected: 2 passed.

- [ ] **Step 3: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/tests/test_messaging_routes_wired.py
git commit -m "test(messaging): routes-wired regression test"
```

---

## PHASE 3 — 前端

### Task 9: QR 渲染库 + i18n 文案

**Files:**
- Create: `webui/static/vendor/qrcode.min.js`
- Modify: `webui/static/i18n.js`

- [ ] **Step 1: Vendor a tiny QR library.**

Download the standalone, dependency-free `qrcode-generator` (davidshimjs/qrcodejs is DOM-based; use `kazuhikoarase/qrcode-generator` UMD which exposes `qrcode(typeNumber, errorCorrectionLevel)`). Fetch the minified single-file build:

```bash
cd /Users/ff/hermes-installer/webui/static/vendor 2>/dev/null || mkdir -p /Users/ff/hermes-installer/webui/static/vendor && cd /Users/ff/hermes-installer/webui/static/vendor
curl -fsSL "https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.js" -o qrcode.min.js
head -5 qrcode.min.js
wc -c qrcode.min.js
```

Expected: a JS file ~20KB exposing global `qrcode`. (If the CDN fetch fails in the build environment, the implementer should fetch the file content from https://github.com/kazuhikoarase/qrcode-generator/blob/master/js/qrcode.js and save it.)

- [ ] **Step 2: Verify the lib exposes `qrcode` and renders.**

```bash
node -e "
global.window = {};
require('/Users/ff/hermes-installer/webui/static/vendor/qrcode.min.js');
const qr = (global.qrcode || global.window.qrcode)(0, 'M');
qr.addData('https://example.com'); qr.make();
console.log('module count:', qr.getModuleCount());
console.log('svg tag present:', qr.createSvgTag().slice(0,20));
"
```

Expected: prints a module count (e.g. 21) + `<svg` prefix.

- [ ] **Step 3: Add i18n keys (en).**

In `webui/static/i18n.js`, find the en locale (search `en: {`). Add near the end of the en block (before its closing `},`):

```javascript
    messaging_tab: 'Messaging',
    messaging_title: 'Messaging Channels',
    messaging_subtitle: 'Link your phone messaging apps so Hermes can chat there.',
    messaging_status_unconfigured: 'Not configured',
    messaging_status_connected: 'Connected',
    messaging_status_connecting: 'Connecting…',
    messaging_status_error: 'Error',
    messaging_btn_connect: 'Connect',
    messaging_btn_configure: 'Configure',
    messaging_btn_save_connect: 'Save & connect',
    messaging_btn_disconnect: 'Disconnect',
    messaging_btn_regenerate_qr: 'Regenerate QR',
    messaging_weixin_name: 'WeChat (personal)',
    messaging_weixin_scan_hint: 'Scan with WeChat on your phone',
    messaging_weixin_scaned: 'Scanned — confirm on your phone',
    messaging_weixin_expired: 'QR code expired',
    messaging_weixin_connected: '✓ WeChat connected (account {account})',
    messaging_weixin_qr_failed: 'Failed to get QR code, please retry',
    messaging_feishu_name: 'Feishu (Lark)',
    messaging_feishu_app_id: 'App ID',
    messaging_feishu_app_secret: 'App Secret',
    messaging_wecom_name: 'WeCom (Enterprise WeChat)',
    messaging_wecom_bot_id: 'Bot ID',
    messaging_wecom_secret: 'Secret',
    messaging_secret_keep: '••••••（leave blank to keep current）',
    messaging_teaching_toggle: '📖 Setup guide (click to expand)',
    messaging_saved_restart_hint: 'Saved. The bot will come online shortly.',
```

- [ ] **Step 4: Add i18n keys (zh).**

Find the zh locale (`zh: {`). Add near the end of the zh block:

```javascript
    messaging_tab: '消息渠道',
    messaging_title: '消息渠道',
    messaging_subtitle: '关联你的手机消息应用，让 Hermes 在上面对话。',
    messaging_status_unconfigured: '未配置',
    messaging_status_connected: '已连接',
    messaging_status_connecting: '连接中…',
    messaging_status_error: '错误',
    messaging_btn_connect: '连接',
    messaging_btn_configure: '配置',
    messaging_btn_save_connect: '保存并连接',
    messaging_btn_disconnect: '断开',
    messaging_btn_regenerate_qr: '重新生成二维码',
    messaging_weixin_name: '微信（个人）',
    messaging_weixin_scan_hint: '请用手机微信扫码',
    messaging_weixin_scaned: '已扫码，请在手机上确认',
    messaging_weixin_expired: '二维码已过期',
    messaging_weixin_connected: '✓ 微信已连接（账号 {account}）',
    messaging_weixin_qr_failed: '获取二维码失败，请重试',
    messaging_feishu_name: '飞书',
    messaging_feishu_app_id: 'App ID',
    messaging_feishu_app_secret: 'App Secret',
    messaging_wecom_name: '企业微信',
    messaging_wecom_bot_id: '机器人 ID',
    messaging_wecom_secret: 'Secret',
    messaging_secret_keep: '••••••（留空表示不修改）',
    messaging_teaching_toggle: '📖 接入教学（点击展开）',
    messaging_saved_restart_hint: '已保存，机器人稍后上线。',
```

- [ ] **Step 5: Verify JS syntax.**

```bash
node -e "
const fs=require('fs');
try{new Function(fs.readFileSync('/Users/ff/hermes-installer/webui/static/i18n.js','utf-8'));console.log('i18n.js parses OK');}
catch(e){console.log('SYNTAX ERROR:',e.message);process.exit(1);}
"
grep -c "messaging_tab" /Users/ff/hermes-installer/webui/static/i18n.js
```

Expected: `i18n.js parses OK` + count `2`.

- [ ] **Step 6: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/static/vendor/qrcode.min.js webui/static/i18n.js
git commit -m "feat(messaging): vendor QR lib + i18n keys (en + zh)"
```

---

### Task 10: rail tab + #mainMessaging DOM + CSS

**Files:**
- Modify: `webui/static/index.html`
- Modify: `webui/static/style.css`

- [ ] **Step 1: Find the 服务器 rail tab as anchor.**

```bash
grep -n 'data-panel="server"' /Users/ff/hermes-installer/webui/static/index.html
```

Note the 2 line numbers (rail + mobile sidebar).

- [ ] **Step 2: Add the messaging rail tab (desktop rail).**

In `webui/static/index.html`, find the desktop rail server button (the one with `class="rail-btn nav-tab has-tooltip"` and `data-panel="server"`). Immediately AFTER that `</button>`, add:

```html
    <button class="rail-btn nav-tab has-tooltip" data-panel="messaging" onclick="switchPanel('messaging',{fromRailClick:true})" data-tooltip="消息渠道" data-i18n-title="messaging_tab" aria-label="消息渠道"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg></button>
```

- [ ] **Step 3: Add the messaging mobile-sidebar tab.**

Find the mobile sidebar server button (`class="nav-tab has-tooltip has-tooltip--bottom"` with `data-panel="server"` and a `<span class="nav-tab-label">`). Immediately AFTER that `</button>`, add:

```html
    <button class="nav-tab has-tooltip has-tooltip--bottom" data-panel="messaging" onclick="switchPanel('messaging',{fromRailClick:true})" data-tooltip="消息渠道" data-i18n-title="messaging_tab" aria-label="消息渠道"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg><span class="nav-tab-label" data-i18n="messaging_tab">消息渠道</span></button>
```

- [ ] **Step 4: Find #mainServer as anchor for the view div.**

```bash
grep -n 'id="mainServer"' /Users/ff/hermes-installer/webui/static/index.html
```

- [ ] **Step 5: Add #mainMessaging view div.**

In `webui/static/index.html`, find the `<div id="mainServer" class="main-view"` element. After its closing `</div>` (the one that closes the whole mainServer view), add:

```html
    <div id="mainMessaging" class="main-view" style="overflow-y:auto">
      <div class="messaging-wrap">
        <div class="messaging-header">
          <h1 style="font-size:22px;font-weight:700;margin:0" data-i18n="messaging_title">消息渠道</h1>
          <p class="messaging-subtitle" data-i18n="messaging_subtitle">关联你的手机消息应用，让 Hermes 在上面对话。</p>
        </div>
        <div id="messagingCards" class="messaging-cards">
          <div class="messaging-loading" data-i18n="loading">加载中…</div>
        </div>
      </div>
    </div>
```

- [ ] **Step 6: Add CSS.**

In `webui/static/style.css`, find the `main.main.showing-server > #mainServer{` rule. Right AFTER it, add:

```css
  main.main > #mainMessaging{display:none;}
  main.main.showing-messaging > #mainMessaging{display:flex;overflow-y:auto;}
  .messaging-wrap{max-width:760px;margin:0 auto;padding:24px 20px;width:100%;}
  .messaging-header{margin-bottom:20px;}
  .messaging-subtitle{font-size:13px;color:var(--muted);margin:6px 0 0;}
  .messaging-cards{display:flex;flex-direction:column;gap:16px;}
  .messaging-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px;}
  .messaging-card-head{display:flex;align-items:center;gap:12px;margin-bottom:12px;}
  .messaging-card-icon{font-size:24px;}
  .messaging-card-name{font-weight:600;font-size:15px;flex:1;}
  .messaging-badge{font-size:11px;font-weight:600;padding:3px 10px;border-radius:999px;}
  .messaging-badge.unconfigured{background:rgba(148,163,184,.15);color:#94a3b8;}
  .messaging-badge.connected{background:rgba(34,197,94,.15);color:#22c55e;}
  .messaging-badge.connecting{background:rgba(59,130,246,.15);color:#3b82f6;}
  .messaging-badge.error{background:rgba(239,68,68,.15);color:#ef4444;}
  .messaging-card-body{font-size:13px;color:var(--text);}
  .messaging-form-row{display:flex;flex-direction:column;gap:4px;margin-bottom:10px;}
  .messaging-form-row label{font-size:12px;color:var(--muted);}
  .messaging-form-row input{padding:8px;border:1px solid var(--border2);border-radius:6px;background:var(--code-bg);color:var(--text);font-size:13px;}
  .messaging-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;}
  .messaging-btn{padding:7px 14px;border:1px solid var(--border);background:transparent;color:var(--text);border-radius:8px;font-size:13px;font-weight:500;cursor:pointer;}
  .messaging-btn.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600;}
  .messaging-btn:hover{background:var(--hover-bg);}
  .messaging-btn.primary:hover{background:var(--accent-hover);}
  .messaging-teaching{margin-top:12px;font-size:12px;color:var(--muted);}
  .messaging-teaching summary{cursor:pointer;user-select:none;padding:4px 0;}
  .messaging-teaching ol{margin:8px 0 0 18px;line-height:1.7;}
  .messaging-qr-box{display:flex;flex-direction:column;align-items:center;gap:10px;padding:14px;}
  .messaging-qr-box svg{width:200px;height:200px;}
  .messaging-qr-hint{font-size:13px;color:var(--text);}
```

- [ ] **Step 7: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/static/index.html webui/static/style.css
git commit -m "feat(messaging): rail tab + #mainMessaging DOM + CSS"
```

---

### Task 11: panels.js — 注册 'messaging' panel + lazy-load hook

**Files:**
- Modify: `webui/static/panels.js`

- [ ] **Step 1: Add 'messaging' to MAIN_VIEW_PANELS toggle list.**

In `webui/static/panels.js`, find (line ~246):

```javascript
    ['settings','skills','memory','tasks','kanban','workspaces','profiles','insights','logs','backup','server'].forEach(p => {
```

Replace with:

```javascript
    ['settings','skills','memory','tasks','kanban','workspaces','profiles','insights','logs','backup','server','messaging'].forEach(p => {
```

- [ ] **Step 2: Add lazy-load hook.**

Find (line ~260):

```javascript
  if (nextPanel === 'server' && typeof serverAdminLoad === 'function') {
    serverAdminLoad();
  }
```

Right AFTER that block, add:

```javascript
  if (nextPanel === 'messaging' && typeof messagingLoad === 'function') {
    messagingLoad();
  }
```

- [ ] **Step 3: Verify JS syntax.**

```bash
node -e "
const fs=require('fs');
try{new Function(fs.readFileSync('/Users/ff/hermes-installer/webui/static/panels.js','utf-8'));console.log('panels.js parses OK');}
catch(e){console.log('SYNTAX ERROR:',e.message);process.exit(1);}
"
```

Expected: `panels.js parses OK`

- [ ] **Step 4: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/static/panels.js
git commit -m "feat(messaging): register messaging panel + lazy-load hook"
```

---

### Task 12: messaging.js — 渲染 + 微信 QR 状态机 + 飞书/企微表单

**Files:**
- Create: `webui/static/messaging.js`
- Modify: `webui/static/index.html`（加 `<script>` 引用 + qrcode lib）

- [ ] **Step 1: Create messaging.js.**

Create `webui/static/messaging.js` with EXACTLY:

```javascript
// ── Messaging channels panel ──────────────────────────────────────────────
// Renders 3 channel cards (WeChat QR / Feishu / WeCom). Reached via the
// 消息渠道 rail tab. Calls /api/messaging/* routes.
// Spec: docs/superpowers/specs/2026-05-30-messaging-channels-design.md
(function () {
  let _weixinPoll = null;

  function $(id) { return document.getElementById(id); }
  function tx(key, vars) {
    let s = (typeof t === 'function' ? (t(key) || key) : key);
    if (vars) for (const k in vars) s = s.split('{' + k + '}').join(vars[k]);
    return s;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function badge(state) {
    const map = {
      connected: 'messaging_status_connected',
      connecting: 'messaging_status_connecting',
      error: 'messaging_status_error',
      unconfigured: 'messaging_status_unconfigured',
    };
    return `<span class="messaging-badge ${state}">${esc(tx(map[state] || 'messaging_status_unconfigured'))}</span>`;
  }

  async function fetchStatus() {
    try {
      const r = await fetch('/api/messaging/channels');
      if (!r.ok) return null;
      return await r.json();
    } catch (e) { return null; }
  }

  // ── WeChat QR ──
  function stopWeixinPoll() {
    if (_weixinPoll) { clearInterval(_weixinPoll); _weixinPoll = null; }
  }

  function renderQr(container, imgUrl) {
    container.innerHTML = '';
    try {
      const qr = (window.qrcode || qrcode)(0, 'M');
      qr.addData(imgUrl);
      qr.make();
      container.innerHTML = qr.createSvgTag({ cellSize: 5, margin: 4 });
    } catch (e) {
      const a = document.createElement('a');
      a.href = imgUrl; a.target = '_blank'; a.rel = 'noopener';
      a.textContent = imgUrl;
      container.appendChild(a);
    }
  }

  async function startWeixinQr(box) {
    stopWeixinPoll();
    box.innerHTML = `<div class="messaging-qr-hint">${esc(tx('messaging_status_connecting'))}</div>`;
    let started;
    try {
      const r = await fetch('/api/messaging/weixin/qr/start', { method: 'POST' });
      if (!r.ok) throw new Error('start failed');
      started = await r.json();
    } catch (e) {
      box.innerHTML = `<div class="messaging-qr-hint">${esc(tx('messaging_weixin_qr_failed'))}</div>`;
      return;
    }
    const token = started.qrcode_token;
    const imgUrl = started.qrcode_img_url || started.qrcode_token;
    box.innerHTML = `<div class="messaging-qr-box"><div class="qr-svg"></div>
      <div class="messaging-qr-hint">${esc(tx('messaging_weixin_scan_hint'))}</div></div>`;
    renderQr(box.querySelector('.qr-svg'), imgUrl);

    _weixinPoll = setInterval(async () => {
      let st;
      try {
        const r = await fetch('/api/messaging/weixin/qr/status?token=' + encodeURIComponent(token));
        st = await r.json();
      } catch (e) { return; }
      const status = st.status;
      if (status === 'scaned' || status === 'scaned_but_redirect') {
        const hint = box.querySelector('.messaging-qr-hint');
        if (hint) hint.textContent = tx('messaging_weixin_scaned');
      } else if (status === 'expired') {
        stopWeixinPoll();
        box.innerHTML = `<div class="messaging-qr-hint">${esc(tx('messaging_weixin_expired'))}</div>
          <button class="messaging-btn" id="weixinRegenBtn">${esc(tx('messaging_btn_regenerate_qr'))}</button>`;
        const b = $('weixinRegenBtn');
        if (b) b.onclick = () => startWeixinQr(box);
      } else if (status === 'confirmed') {
        stopWeixinPoll();
        messagingLoad();  // re-render whole panel; weixin now connected
      } else if (status === 'invalid_token' || status === 'error') {
        stopWeixinPoll();
        box.innerHTML = `<div class="messaging-qr-hint">${esc(tx('messaging_weixin_qr_failed'))}</div>
          <button class="messaging-btn" id="weixinRegenBtn">${esc(tx('messaging_btn_regenerate_qr'))}</button>`;
        const b = $('weixinRegenBtn');
        if (b) b.onclick = () => startWeixinQr(box);
      }
    }, 2000);
  }

  function weixinCard(s) {
    const connected = s.connected;
    const state = connected ? 'connected' : 'unconfigured';
    let body;
    if (connected) {
      body = `<div class="messaging-card-body">
        ${esc(tx('messaging_weixin_connected', { account: s.account_id || '' }))}
        <div class="messaging-actions">
          <button class="messaging-btn" id="weixinDisconnectBtn">${esc(tx('messaging_btn_disconnect'))}</button>
        </div></div>`;
    } else {
      body = `<div class="messaging-card-body">
        <div class="messaging-actions"><button class="messaging-btn primary" id="weixinConnectBtn">${esc(tx('messaging_btn_connect'))}</button></div>
        <div id="weixinQrArea"></div></div>`;
    }
    return `<div class="messaging-card">
      <div class="messaging-card-head"><span class="messaging-card-icon">💬</span>
        <span class="messaging-card-name">${esc(tx('messaging_weixin_name'))}</span>${badge(state)}</div>
      ${body}</div>`;
  }

  function feishuCard(s) {
    const state = s.connected ? 'connected' : 'unconfigured';
    const secretPlaceholder = s.has_secret ? tx('messaging_secret_keep') : '';
    return `<div class="messaging-card">
      <div class="messaging-card-head"><span class="messaging-card-icon">🐦</span>
        <span class="messaging-card-name">${esc(tx('messaging_feishu_name'))}</span>${badge(state)}</div>
      <div class="messaging-card-body">
        <div class="messaging-form-row"><label>${esc(tx('messaging_feishu_app_id'))}</label>
          <input id="feishuAppId" value="${esc(s.app_id_masked ? '' : '')}" placeholder="cli_..."></div>
        <div class="messaging-form-row"><label>${esc(tx('messaging_feishu_app_secret'))}</label>
          <input id="feishuAppSecret" type="password" placeholder="${esc(secretPlaceholder)}"></div>
        <div class="messaging-actions">
          <button class="messaging-btn primary" id="feishuSaveBtn">${esc(tx('messaging_btn_save_connect'))}</button>
          ${s.connected ? `<button class="messaging-btn" id="feishuDisconnectBtn">${esc(tx('messaging_btn_disconnect'))}</button>` : ''}
        </div>
        <details class="messaging-teaching"><summary>${esc(tx('messaging_teaching_toggle'))}</summary>
          <ol>
            <li>open.feishu.cn 开发者后台 →「创建企业自建应用」</li>
            <li>「凭证与基础信息」复制 App ID + App Secret 填到上面</li>
            <li>「权限管理」开启 im:message + im:message:send_as_bot</li>
            <li>「事件与回调」订阅方式选「长连接」，订阅 im.message.receive_v1</li>
            <li>「版本管理与发布」创建版本 → 申请发布（管理员审批）</li>
            <li>回这里点「保存并连接」，飞书里拉机器人进群 / 私聊 @它</li>
          </ol></details>
      </div></div>`;
  }

  function wecomCard(s) {
    const state = s.connected ? 'connected' : 'unconfigured';
    const secretPlaceholder = s.has_secret ? tx('messaging_secret_keep') : '';
    return `<div class="messaging-card">
      <div class="messaging-card-head"><span class="messaging-card-icon">🏢</span>
        <span class="messaging-card-name">${esc(tx('messaging_wecom_name'))}</span>${badge(state)}</div>
      <div class="messaging-card-body">
        <div class="messaging-form-row"><label>${esc(tx('messaging_wecom_bot_id'))}</label>
          <input id="wecomBotId" placeholder="bot_..."></div>
        <div class="messaging-form-row"><label>${esc(tx('messaging_wecom_secret'))}</label>
          <input id="wecomSecret" type="password" placeholder="${esc(secretPlaceholder)}"></div>
        <div class="messaging-actions">
          <button class="messaging-btn primary" id="wecomSaveBtn">${esc(tx('messaging_btn_save_connect'))}</button>
          ${s.connected ? `<button class="messaging-btn" id="wecomDisconnectBtn">${esc(tx('messaging_btn_disconnect'))}</button>` : ''}
        </div>
        <details class="messaging-teaching"><summary>${esc(tx('messaging_teaching_toggle'))}</summary>
          <ol>
            <li>work.weixin.qq.com 管理后台 →「应用管理」创建智能机器人 / 自建应用</li>
            <li>复制 Bot ID + Secret 填到上面</li>
            <li>接收消息选 websocket 长连接模式</li>
            <li>回这里点「保存并连接」，企业微信里 @机器人 即可对话</li>
          </ol></details>
      </div></div>`;
  }

  async function postJson(url, payload) {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    let data = null;
    try { data = await r.json(); } catch (e) { /* */ }
    return { ok: r.ok, data };
  }

  function wireActions() {
    const wc = $('weixinConnectBtn');
    if (wc) wc.onclick = () => startWeixinQr($('weixinQrArea'));
    const wd = $('weixinDisconnectBtn');
    if (wd) wd.onclick = async () => { await postJson('/api/messaging/weixin/disconnect'); messagingLoad(); };

    const fs = $('feishuSaveBtn');
    if (fs) fs.onclick = async () => {
      const res = await postJson('/api/messaging/feishu/config', {
        app_id: ($('feishuAppId') || {}).value || '',
        app_secret: ($('feishuAppSecret') || {}).value || '',
      });
      if (!res.ok && res.data && res.data.error) { alert(res.data.error); return; }
      if (typeof showToast === 'function') showToast(tx('messaging_saved_restart_hint'));
      messagingLoad();
    };
    const fd = $('feishuDisconnectBtn');
    if (fd) fd.onclick = async () => { await postJson('/api/messaging/feishu/disconnect'); messagingLoad(); };

    const ws = $('wecomSaveBtn');
    if (ws) ws.onclick = async () => {
      const res = await postJson('/api/messaging/wecom/config', {
        bot_id: ($('wecomBotId') || {}).value || '',
        secret: ($('wecomSecret') || {}).value || '',
      });
      if (!res.ok && res.data && res.data.error) { alert(res.data.error); return; }
      if (typeof showToast === 'function') showToast(tx('messaging_saved_restart_hint'));
      messagingLoad();
    };
    const wcd = $('wecomDisconnectBtn');
    if (wcd) wcd.onclick = async () => { await postJson('/api/messaging/wecom/disconnect'); messagingLoad(); };
  }

  // Global entry — called by panels.js lazy-load hook on tab switch.
  window.messagingLoad = async function messagingLoad() {
    stopWeixinPoll();
    const cards = $('messagingCards');
    if (!cards) return;
    const s = await fetchStatus();
    if (!s) {
      cards.innerHTML = `<div class="messaging-loading">${esc(tx('messaging_status_error'))}</div>`;
      return;
    }
    cards.innerHTML = weixinCard(s.weixin) + feishuCard(s.feishu) + wecomCard(s.wecom);
    if (typeof applyI18n === 'function') applyI18n();
    wireActions();
  };
})();
```

- [ ] **Step 2: Add script tags to index.html.**

```bash
grep -n 'src="server-admin.js"\|src="panels.js"\|src="vendor/' /Users/ff/hermes-installer/webui/static/index.html | head -5
```

Find where `server-admin.js` is included via `<script>`. Right after that line, add (the qrcode lib must load before messaging.js):

```html
    <script src="vendor/qrcode.min.js"></script>
    <script src="messaging.js"></script>
```

If server-admin.js isn't script-tagged (e.g. bundled differently), add the two lines right before the closing `</body>` instead — match whatever pattern the other panel JS files use.

- [ ] **Step 3: Verify JS syntax.**

```bash
node -e "
const fs=require('fs');
try{new Function(fs.readFileSync('/Users/ff/hermes-installer/webui/static/messaging.js','utf-8'));console.log('messaging.js parses OK');}
catch(e){console.log('SYNTAX ERROR:',e.message);process.exit(1);}
"
```

Expected: `messaging.js parses OK`

- [ ] **Step 4: Commit.**

```bash
cd /Users/ff/hermes-installer
git add webui/static/messaging.js webui/static/index.html
git commit -m "feat(messaging): messaging.js panel render + QR state machine + forms"
```

---

## PHASE 4 — 验证 + PR

### Task 13: full suite + smoke + push + PR

**Files:** (none modified — verification only)

- [ ] **Step 1: Run full messaging test suite.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest \
  tests/test_messaging_channels.py tests/test_messaging_routes_wired.py -v 2>&1 | tail -15
```

Expected: 29 passed (27 + 2).

- [ ] **Step 2: All static JS parse checks.**

```bash
for f in i18n.js panels.js messaging.js vendor/qrcode.min.js; do
  node -e "const fs=require('fs');try{new Function(fs.readFileSync('/Users/ff/hermes-installer/webui/static/$f','utf-8'));console.log('$f OK');}catch(e){console.log('$f ERR:',e.message);process.exit(1);}"
done
```

Expected: all 4 print `OK`.

- [ ] **Step 3: ast check routes.py.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -c "import ast; ast.parse(open('api/routes.py').read()); print('routes.py OK')"
```

- [ ] **Step 4: Confirm no regression in a couple existing webui tests.**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_installer_update.py --timeout=30 2>&1 | tail -3
```

Expected: existing tests still pass.

- [ ] **Step 5: Push branch + open PR.**

```bash
cd /Users/ff/hermes-installer
git push -u origin feat/messaging-channels
gh pr create --base main --head feat/messaging-channels \
  --title "✨ 消息渠道：微信扫码 / 飞书 / 企业微信" \
  --body "$(cat <<'EOF'
Implements docs/superpowers/specs/2026-05-30-messaging-channels-design.md.

## Summary
- 新 WebUI「消息渠道」tab（左侧 rail），3 个 channel 卡片
- **微信（个人）**：点连接 → iLink Bot QR 扫码（webui 后端 stdlib urllib 直连 ilinkai.weixin.qq.com，前端轮询 webui + JS 渲染二维码）
- **飞书 / 企业微信**：填 App ID/Secret（或 Bot ID/Secret）→ websocket 长连接模式，附折叠教学步骤
- 配置写 ~/.hermes/.env + config.yaml platforms.<name>.enabled，重启 gateway supervisor 拾取
- Secret 永不明文回显（masked + has_secret 布尔）

## Backend (stdlib only)
- 新 webui/api/messaging_channels.py：.env upsert/remove、config.yaml platform toggle、iLink QR 代理、channel 状态聚合 + masking
- 8 个新路由 /api/messaging/*

## Tests
- 27 单元测试 + 2 routes-wired regression
- 所有 static JS parse 通过

## 范围外（future）
- 其他平台（telegram/discord/钉钉等 adapter 已存在但本次不做 UI）
- 微信公众号 / 飞书企微 webhook 回调模式（需公网域名）

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: PR URL printed.

- [ ] **Step 6: Manual e2e checklist (post-deploy, on a cloud instance or local).**

- [ ] 「消息渠道」tab 出现在左侧 rail
- [ ] 微信卡片点连接 → 二维码渲染 → 手机扫码 → 确认 → 显示「✓ 微信已连接」
- [ ] 飞书卡片填 App ID/Secret → 保存并连接 → 徽章转「已连接」
- [ ] 企微卡片同上
- [ ] 断开后徽章转「未配置」，gateway 不再连该平台
- [ ] `GET /api/messaging/channels` 响应里**无**明文 secret
- [ ] 中英文切换文案正确

---

## Self-Review Notes

**Spec coverage:**

| Spec section | Task(s) |
|---|---|
| §1 架构总览 | Task 1-5（后端）+ 10-12（前端） |
| §3 微信 QR 流程 + iLink 契约 | Task 5（后端代理）+ Task 12（前端状态机） |
| §4.1 飞书字段 | Task 4 (connect_feishu) |
| §4.2 企微字段 | Task 4 (connect_wecom) |
| §4.3 教学步骤 | Task 12（messaging.js details 块） |
| §4.4 飞书/企微路由 | Task 7 |
| §4.5 状态查询路由 | Task 6 |
| §5 UI 行为 | Task 10（DOM/CSS）+ Task 12（render/wire） |
| §6 错误/边界 | Task 5（invalid_token / incomplete_credentials / error）+ Task 12（expired/error 重生成） |
| §7 测试 | Task 1-5（单元）+ Task 8（routes-wired）+ Task 13（suite+smoke） |
| §8 i18n | Task 9 |

**Type/name consistency:** function names (`weixin_qr_start` / `weixin_qr_status` / `connect_feishu` / `disconnect_feishu` / `connect_wecom` / `disconnect_wecom` / `disconnect_weixin` / `get_channels_status` / `set_platform_enabled` / `is_platform_enabled` / `restart_gateway` / `_upsert_env_vars` / `_remove_env_vars` / `_parse_env` / `_mask_secret` / `_qr_sessions`) consistent across backend tasks + route calls + frontend fetch paths. env keys (`FEISHU_APP_ID`/`FEISHU_APP_SECRET`/`FEISHU_CONNECTION_MODE`/`WECOM_BOT_ID`/`WECOM_SECRET`/`WEIXIN_ACCOUNT_ID`) consistent. Route paths `/api/messaging/*` consistent between Task 6/7 (wire), Task 8 (test), Task 12 (frontend fetch).

**Notes for implementer:**
- 测试用 venv `/Users/ff/hermes-installer/.build_venv/bin/python -m pytest`。
- routes.py 巨大（13k+ 行）；用 `if parsed.path == "..."` 块插在指定锚点前，遵循该文件既有风格。
- `_mask_secret` 边界：len<=3→"***"；len 4-6→`v[:2]+"***"`；len>6→`v[:6]+"***"`。测试已锁定（"ab"→"***"，"cli_a1b2c3d4e5"→"cli_a1***"，"cli_abc123"(9 chars)→"cli_ab***"，"bot_xyz"(7 chars)→"bot_xy***"）。
- restart_gateway 用 pkill；本地无 gateway supervisor 时是无害 no-op，配置下次启动生效。
- QR 库优先 CDN 拉 qrcode-generator@1.4.4；拉不到则从 GitHub 源码保存。验证它 export 全局 `qrcode(typeNumber, ecLevel)` 且有 `.createSvgTag()`。
