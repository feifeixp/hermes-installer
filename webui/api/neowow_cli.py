"""Bundled canvas CLI and UI-only credential binding (no secrets in argv)."""
from __future__ import annotations

import hashlib
import functools
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import threading
import zipfile

from api.config import STATE_DIR

_BUNDLE = Path(__file__).resolve().parents[1] / "assets/neowow-canvas-skill.zip"
_SHA256 = "f5045d235115dcec76fc19a4b8f3b8be1bbfa8518f25100707eb6e28ed4157d1"
_LOCK = threading.RLock()
_GUIDANCE = """
## Studio credential handling — takes precedence over login examples below

The CLI is already installed. For authentication, direct the user to
Settings → Neowow Studio → NeoWoW 画布 → 生成 API Key / 保存并连接.
Never ask for passwords or API keys in chat, read credential files, print
credentials, or run login commands containing a key. The settings form validates
and saves credentials outside the conversation. Use `neowow whoami` to check
access; when unauthorized, send the user back to settings. Do not create or
rotate a key automatically: replacing a key invalidates the account's old key.

## Studio creation and delivery

When a Studio creation request names a sessionId, use that exact project scope
on every CLI command; do not fall back to a working-directory project binding.
Read the required command documentation first. Plan the creation, discover the
model, and preview with --dry-run before asking for the user's cost confirmation.
Use gen --wait or poll --apply to persist terminal results into the canvas.
Report actual status, model, nodeKey, and final result URLs/text. Never label
PENDING or FAILED work as delivered. Display image/video/audio results in the
chat with Markdown previews when supported, plus usable original-file links.
The settings result panel can then extract the same completed nodes.

Video/storyboard creation is script-first by default. Use the current chat model
to develop the story and a complete verbatim dialogue/narration script with
timing, action and emotion; save it as a text node using node create --content.
Show the full script and stop for review. A script/revise stage must not run any
gen command. User-approved existing scripts can proceed without rewriting.
Only after script approval, map every line to numbered shots with feasible
durations, varied camera/action and consistent characters, locations and voices.
Script approval is separate from cost approval: discover supported model limits,
dry-run all clips/reference media, show per-shot and total costs, then wait for
explicit approval before paid generation. Preserve approved dialogue exactly.
Deliver ordered clips with matching lines, actual status, node keys and URLs.
Do not claim the clips are a stitched movie: CLI composition is not documented.
"""


def _home() -> Path:
    from api.profiles import get_active_hermes_home
    return get_active_hermes_home()


def _supports_profile_context() -> bool:
    try:
        import hermes_constants
        from tools.environments import local
        from gateway import session_context
        return (callable(getattr(hermes_constants, "set_hermes_home_override", None))
                and callable(getattr(local, "_inject_context_hermes_home", None))
                and callable(getattr(session_context, "set_session_vars", None))
                and getattr(local.LocalEnvironment.execute, "_neowow_profile_bridge", False))
    except ImportError:
        return False


def _install_terminal_profile_bridge() -> None:
    """Reassert the turn's home after the terminal sources its shared snapshot."""
    try:
        from hermes_constants import get_hermes_home_override, set_hermes_home_override
        from tools.environments.local import LocalEnvironment, _inject_context_hermes_home
    except ImportError:
        return
    original = LocalEnvironment.execute
    if getattr(original, "_neowow_profile_bridge", False):
        return

    @functools.wraps(original)
    def execute(self, command, *args, **kwargs):
        home = get_hermes_home_override()
        if home:
            # BaseEnvironment sources its persisted exports BEFORE this body.
            # Explicitly bind the authoritative home without serializing turns
            # or changing process-global state. No secret is passed in argv.
            command = "export HERMES_HOME=" + shlex.quote(str(home)) + "; " + command
        return original(self, command, *args, **kwargs)

    execute._neowow_profile_bridge = True
    LocalEnvironment.execute = execute


def _require_profile_context() -> None:
    if not _supports_profile_context():
        raise ValueError("当前 Agent 版本不支持安全的画布账号隔离，请更新 Agent 后连接。")


def _binding_available() -> bool:
    # A shared cloud process must never save one user's key for everyone.
    return (os.getenv("HERMES_WEBUI_AUTH_MODE", "").strip().lower() != "neodomain"
            or bool(os.getenv("HERMES_INSTANCE_OWNER_USERID", "").strip()))


def _config_dir(home: Path) -> Path:
    return home / "neowow-cli"


def ensure_installed(home: Path | None = None) -> Path:
    """Offline, idempotent seed outside subscription sync's deletion scope."""
    home = home or _home()
    root = Path(os.getenv("HERMES_SKILLS_PATH") or home / "skills").expanduser()
    target = root / "neowow-canvas"
    with _LOCK:
        _install_terminal_profile_bridge()
        if not (target / "bin/neowow.mjs").is_file():
            if target.exists():
                raise RuntimeError("NeoWoW 技能目录已存在但不完整，请检查后重试。")
            if hashlib.sha256(_BUNDLE.read_bytes()).hexdigest() != _SHA256:
                raise RuntimeError("NeoWoW CLI 安装包校验失败。")
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=root) as staging:
                with zipfile.ZipFile(_BUNDLE) as archive:
                    for entry in archive.infolist():
                        p = Path(entry.filename)
                        if p.is_absolute() or ".." in p.parts or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                            raise RuntimeError("NeoWoW CLI 安装包路径无效。")
                    archive.extractall(staging)
                skill = Path(staging) / "SKILL.md"
                content = skill.read_text(encoding="utf-8")
                end = content.find("\n---", 4) + len("\n---")
                skill.write_text(content[:end] + "\n" + _GUIDANCE + content[end:], encoding="utf-8")
                Path(staging).rename(target)

        # Existing managed copies also need the current Studio workflow rules.
        # Replace only our bounded section; preserve upstream/user skill text.
        skill = target / "SKILL.md"
        content = skill.read_text(encoding="utf-8")
        if _GUIDANCE.strip() not in content:
            start = content.find("## Studio credential handling")
            if start >= 0:
                end = content.find("\n# NeoWoW", start)
                if end >= 0:
                    content = content[:start] + _GUIDANCE.strip() + "\n" + content[end:]
                else:
                    content += "\n" + _GUIDANCE
            else:
                end = content.find("\n---", 4) + len("\n---")
                content = content[:end] + "\n" + _GUIDANCE + content[end:]
            skill.write_text(content, encoding="utf-8")

        bindir = STATE_DIR / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        # Use the active profile inherited by the agent subprocess. No key is
        # ever embedded in the launcher, shell profile, or generated skill.
        code = ("import os, pathlib, shutil, sys\n"
                f"if not {_supports_profile_context()!r} and sys.argv[1:] != ['--help']: sys.exit('Update Hermes Agent before using canvas credentials')\n"
                "home = pathlib.Path(os.environ.get('HERMES_HOME', pathlib.Path.home() / '.hermes'))\n"
                "root = pathlib.Path(os.environ.get('HERMES_SKILLS_PATH', home / 'skills'))\n"
                "os.environ['NEOWOW_CONFIG_DIR'] = str(home / 'neowow-cli')\n"
                "os.environ.pop('NEOWOW_API_KEY', None)\n"
                "os.environ['NEOWOW_BASE'] = 'https://neowow.cn'\n"
                "os.environ.pop('NEOWOW_DEBUG', None)\n"
                "node = shutil.which('node')\n"
                "if not node: sys.exit('NeoWoW CLI requires Node.js >= 18')\n"
                "os.execv(node, [node, str(root / 'neowow-canvas/bin/neowow.mjs'), *sys.argv[1:]])\n")
        if os.name == "nt":
            launcher = bindir / "neowow-launcher.py"
            launcher.write_text(code, encoding="utf-8")
            (bindir / "neowow.cmd").write_text(f'@echo off\r\n"{sys.executable}" "{launcher}" %*\r\n', encoding="utf-8")
        else:
            launcher = bindir / "neowow"
            content = f"#!{sys.executable}\n" + code
            if not launcher.exists() or launcher.read_text(encoding="utf-8") != content:
                fd, name = tempfile.mkstemp(dir=bindir)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as out:
                        out.write(content)
                    os.chmod(name, 0o755)
                    os.replace(name, launcher)
                finally:
                    if os.path.exists(name):
                        os.unlink(name)
        paths = os.environ.get("PATH", "").split(os.pathsep)
        if str(bindir) not in paths:
            os.environ["PATH"] = os.pathsep.join([str(bindir), *paths])
    return target


def get_status() -> dict:
    available = _binding_available()
    home = _home()
    connected = False
    if available:
        try:
            connected = bool(json.loads((_config_dir(home) / "config.json").read_text()).get("apiKey"))
        except (OSError, ValueError, AttributeError):
            pass
    root = Path(os.getenv("HERMES_SKILLS_PATH") or home / "skills").expanduser()
    return {"installed": (root / "neowow-canvas/bin/neowow.mjs").is_file(),
            "runtimeReady": bool(shutil.which("node")) and _supports_profile_context(), "connected": connected,
            "profileContextReady": _supports_profile_context(), "bindingAvailable": available, "keyPageUrl": "https://neowow.cn"}


def save_key(key: str) -> dict:
    _require_profile_context()
    if not _binding_available():
        raise ValueError("共享实例不能保存个人画布 Key，请使用专属 Agent 实例。")
    if not isinstance(key, str) or not key.strip() or len(key) > 4096 or any(c.isspace() for c in key.strip()):
        raise ValueError("请输入有效的画布 API Key。")
    key = key.strip()
    home = _home()
    skill = ensure_installed(home)
    node = shutil.which("node")
    if not node:
        raise ValueError("NeoWoW CLI 需要 Node.js 18 或更新版本。")
    env = dict(os.environ)
    env.pop("NEOWOW_DEBUG", None)
    env.update(NEOWOW_API_KEY=key, NEOWOW_BASE="https://neowow.cn",
               NEOWOW_CONFIG_DIR=str(_config_dir(home)),
               NEOWOW_NO_UPDATE_CHECK="1")
    try:
        result = subprocess.run([node, str(skill / "bin/neowow.mjs"), "whoami"],
                                env=env, capture_output=True, text=True, timeout=25)
        if result.returncode:
            raise ValueError("验证失败，请确认 Key 有效且网络可用。原有连接未更改。")
        profile = json.loads(result.stdout)
        if not isinstance(profile, dict) or not profile.get("userId"):
            raise ValueError("画布账号验证失败，原有连接未更改。")
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        raise ValueError("暂时无法验证画布账号，请稍后重试。") from None
    # Never return CLI stdout/stderr: upstream responses can contain secrets.
    config = {"base": "https://neowow.cn", "apiKey": key,
              "userId": str(profile["userId"])}
    directory = _config_dir(home)
    with _LOCK:
        if directory.is_symlink():
            raise ValueError("画布凭据目录不能是符号链接。")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        fd, name = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(config, out)
            os.replace(name, directory / "config.json")
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return get_status()


def clear_key() -> dict:
    if not _binding_available():
        raise ValueError("共享实例不能管理个人画布 Key。")
    with _LOCK:
        directory = _config_dir(_home())
        if directory.is_symlink():
            raise ValueError("画布凭据目录不能是符号链接。")
        (directory / "config.json").unlink(missing_ok=True)
    return get_status()
