"""Canvas bundle, profile isolation and credential disclosure regressions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest


@pytest.fixture
def cli(monkeypatch, tmp_path):
    from api import neowow_cli
    monkeypatch.setattr(neowow_cli, "_supports_profile_context", lambda: True)
    home = tmp_path / "profile"
    monkeypatch.setattr(neowow_cli, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(neowow_cli, "_home", lambda: home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_SKILLS_PATH", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_AUTH_MODE", raising=False)
    monkeypatch.delenv("HERMES_INSTANCE_OWNER_USERID", raising=False)
    return neowow_cli


def test_offline_bundle_and_launcher(cli):
    target = cli.ensure_installed()
    assert (target / "commands/gen.md").is_file()
    text = (target / "SKILL.md").read_text()
    assert text.startswith("---\nname: neowow-canvas")
    assert "Never ask for passwords or API keys in chat" in text
    (target / "custom-note.txt").write_text("preserve")
    cli.ensure_installed()
    assert (target / "custom-note.txt").read_text() == "preserve"
    if shutil.which("node") and os.name != "nt":
        result = subprocess.run([str(cli.STATE_DIR / "bin/neowow"), "--help"],
                                env={**os.environ, "NEOWOW_NO_UPDATE_CHECK": "1"},
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert "neowow" in result.stdout.lower()


def test_bundle_integrity_before_install(cli, monkeypatch):
    monkeypatch.setattr(cli, "_SHA256", "invalid")
    with pytest.raises(RuntimeError, match="校验失败"):
        cli.ensure_installed()
    assert not (cli._home() / "skills/neowow-canvas").exists()


def test_save_uses_environment_and_never_returns_key(cli, monkeypatch):
    monkeypatch.setenv("NEOWOW_DEBUG", "1")
    key = "test-canvas-secret-do-not-disclose"
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert key not in " ".join(argv)
        assert kwargs["env"]["NEOWOW_API_KEY"] == key
        assert "NEOWOW_DEBUG" not in kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"userId": "123", "apiKey": key}), stderr=key)
    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/mock/node")
    result = cli.save_key(key)
    assert result["connected"]
    assert key not in json.dumps(result)
    assert len(calls) == 1
    saved = cli._config_dir(cli._home()) / "config.json"
    assert json.loads(saved.read_text())["apiKey"] == key
    if os.name != "nt":
        assert saved.stat().st_mode & 0o777 == 0o600
        assert saved.parent.stat().st_mode & 0o777 == 0o700
    assert key not in (cli._home() / "skills/neowow-canvas/SKILL.md").read_text()


@pytest.mark.parametrize("returncode,stdout", [(3, ""), (0, "invalid-json"), (0, '{}')])
def test_failed_verification_preserves_existing_key(cli, monkeypatch, returncode, stdout):
    directory = cli._config_dir(cli._home())
    directory.mkdir(parents=True)
    saved = directory / "config.json"
    saved.write_text('{"apiKey":"previous-secret"}')
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/mock/node")
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=returncode, stdout=stdout, stderr="secret-password-key"))
    with pytest.raises(ValueError) as error:
        cli.save_key("new-secret")
    assert "secret-password-key" not in str(error.value)
    assert json.loads(saved.read_text())["apiKey"] == "previous-secret"


def test_timeout_does_not_disclose_environment(cli, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/mock/node")
    def run(*a, **kw):
        raise subprocess.TimeoutExpired(["secret"], 25, output="secret", stderr="secret")
    monkeypatch.setattr(cli.subprocess, "run", run)
    with pytest.raises(ValueError) as error:
        cli.save_key("secret")
    assert "secret" not in str(error.value)
    assert not (cli._config_dir(cli._home()) / "config.json").exists()


def test_shared_cloud_cannot_read_save_or_clear_personal_key(cli, monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_AUTH_MODE", "neodomain")
    assert not cli.get_status()["bindingAvailable"]
    assert not cli.get_status()["connected"]
    for action in (lambda: cli.save_key("secret"), cli.clear_key):
        with pytest.raises(ValueError, match="共享实例"):
            action()
    monkeypatch.setenv("HERMES_INSTANCE_OWNER_USERID", "123")
    assert cli.get_status()["bindingAvailable"]


def test_keys_are_scoped_to_profile_and_clear_is_idempotent(cli, monkeypatch, tmp_path):
    a, b = cli._home(), tmp_path / "profile-b"
    directory = cli._config_dir(a)
    directory.mkdir(parents=True)
    (directory / "config.json").write_text('{"apiKey":"a-secret"}')
    monkeypatch.setattr(cli, "_home", lambda: b)
    assert not cli.get_status()["connected"]
    cli.clear_key()
    assert (directory / "config.json").is_file()
    monkeypatch.setattr(cli, "_home", lambda: a)
    assert cli.get_status()["connected"]
    assert not cli.clear_key()["connected"]
    assert not cli.clear_key()["connected"]


def test_frontend_key_only_in_form_not_chat_or_storage():
    static = Path(__file__).resolve().parents[1] / "static"
    html = (static / "index.html").read_text()
    assert 'type="password" id="neowowCanvasKey"' in html
    js = (static / "neowow.js").read_text()
    save = js.split("window.neowowCanvasSave =", 1)[1].split("window.neowowCanvasClear =", 1)[0]
    assert "input.value = ''" in save
    for forbidden in ("localStorage", "sessionStorage", "console.", "sendMessage", "--api-key"):
        assert forbidden not in save


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher execution")
def test_launcher_uses_turn_profile_and_ignores_global_key(cli, tmp_path):
    cli.ensure_installed()
    other = tmp_path / "other-profile"
    cli.ensure_installed(other)
    bindir = tmp_path / "fake-runtime"
    bindir.mkdir()
    node = bindir / "node"
    node.write_text(f"#!{sys.executable}\nimport json,os,sys\n"
                    "print(json.dumps({'config':os.environ['NEOWOW_CONFIG_DIR'],"
                    "'hasEnvKey':'NEOWOW_API_KEY' in os.environ,'script':sys.argv[1]}))\n")
    node.chmod(0o755)
    env = {**os.environ, "HERMES_HOME": str(other), "NEOWOW_API_KEY": "global-key", "PATH": str(bindir)}
    result = subprocess.run([str(cli.STATE_DIR / "bin/neowow"), "whoami"],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["config"] == str(other / "neowow-cli")
    assert not data["hasEnvKey"]
    assert data["script"] == str(other / "skills/neowow-canvas/bin/neowow.mjs")


def test_http_status_and_invalid_key_response(test_server):
    base = "http://127.0.0.1:" + os.environ["HERMES_WEBUI_TEST_PORT"]
    with urllib.request.urlopen(base + "/api/neowow/cli/status") as response:
        status = json.load(response)
    assert isinstance(status["connected"], bool)
    assert "apiKey" not in status
    request = urllib.request.Request(base + "/api/neowow/cli/key",
        data=json.dumps({"apiKey": "invalid secret key"}).encode(),
        headers={"Content-Type": "application/json", "Origin": base}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 400
    assert "invalid secret key" not in error.value.read().decode()


def test_legacy_agent_cannot_bind_canvas_credentials(cli, monkeypatch):
    monkeypatch.setattr(cli, "_supports_profile_context", lambda: False)
    called = []
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kw: called.append(args))
    assert not cli.get_status()["runtimeReady"]
    with pytest.raises(ValueError, match="账号隔离"):
        cli.save_key("test-private-key")
    assert not called
