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
