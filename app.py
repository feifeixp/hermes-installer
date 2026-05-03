"""
Hermes Agent GUI Installer — FastAPI backend
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import AsyncGenerator, Optional

import aiohttp
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# On Windows the official install.ps1 uses %LOCALAPPDATA%\hermes
# On macOS/Linux the official install.sh uses ~/.hermes
if sys.platform == "win32":
    _local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    HERMES_HOME = _local_app_data / "hermes"
else:
    HERMES_HOME = Path.home() / ".hermes"

HERMES_AGENT = HERMES_HOME / "hermes-agent"
HERMES_ENV = HERMES_HOME / ".env"
HERMES_CONFIG = HERMES_HOME / "config.yaml"
# Python executable differs by platform
if sys.platform == "win32":
    HERMES_PYTHON = HERMES_AGENT / "venv" / "Scripts" / "python.exe"
else:
    HERMES_PYTHON = HERMES_AGENT / "venv" / "bin" / "python3"

HERMES_INSTALL_SH  = "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh"
HERMES_INSTALL_PS1 = "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1"

HERMES_GATEWAY_URL = "http://127.0.0.1:8642"   # Hermes Agent API server
SETUP_COMPLETE = HERMES_HOME / ".setup_complete"

ILINK_BASE = "https://ilinkai.weixin.qq.com"
ILINK_HEADERS = {
    "iLink-App-Id": "bot",
    "iLink-App-ClientVersion": "131584",
}

# ---------------------------------------------------------------------------
# Helpers — .env read/write
# ---------------------------------------------------------------------------


def read_env() -> dict:
    """Parse ~/.hermes/.env into a dict, ignoring comments and blank lines."""
    result: dict = {}
    if not HERMES_ENV.exists():
        return result
    for raw_line in HERMES_ENV.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip()
    return result


def write_env_key(key: str, value: str) -> None:
    """Update existing key=value or append it to ~/.hermes/.env."""
    HERMES_HOME.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if HERMES_ENV.exists():
        lines = HERMES_ENV.read_text(encoding="utf-8").splitlines()

    updated = False
    pattern = re.compile(r"^" + re.escape(key) + r"\s*=")
    new_lines: list[str] = []
    for line in lines:
        if pattern.match(line):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f"{key}={value}")

    HERMES_ENV.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers — QR image generation
# ---------------------------------------------------------------------------


def generate_qr_image(data: str) -> str:
    """Return a data:image/png;base64,... string for the given data."""
    import qrcode

    qr = qrcode.QRCode(border=2, box_size=6)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1a1a2e", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# Helpers — version checks
# ---------------------------------------------------------------------------


def _run(cmd: list[str]) -> tuple[bool, str]:
    """Run a command, return (success, combined output)."""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, ""


def check_python() -> dict:
    """Check for Python 3.10+.

    Search order:
    1. Hermes venv Python (installed and working)
    2. Named executables found via expanded PATH (_which)
    3. uv-managed Python (uv python list)
    4. The runtime embedding this script (works when frozen by PyInstaller)
    """
    # Named candidates — try each with expanded PATH
    named = [
        str(HERMES_PYTHON),
        "python3.13", "python3.12", "python3.11", "python3.10",
        "python3", "python",
    ]
    for candidate in named:
        exe = _which(candidate) if not os.sep in candidate else (candidate if os.path.isfile(candidate) else None)
        if not exe:
            continue
        ok, out = _run([exe, "--version"])
        if not ok:
            continue
        m = re.search(r"(\d+)\.(\d+)", out)
        if not m:
            continue
        major, minor = int(m.group(1)), int(m.group(2))
        if (major, minor) >= (3, 10):
            return {"ok": True, "version": m.group(0), "managed_by_uv": False}

    # Check uv-managed Python (uv python list --output-format json or plain)
    uv_bin = _which("uv")
    if uv_bin:
        ok2, out2 = _run([uv_bin, "python", "list"])
        if ok2:
            m2 = re.search(r"3\.(1[0-9])\.(\d+)", out2)
            if m2:
                return {"ok": True, "version": m2.group(0), "managed_by_uv": True}

    # When frozen as PyInstaller exe, sys.executable is the app itself (not python).
    # But sys.version_info still holds the *runtime* Python version.
    if sys.version_info >= (3, 10):
        ver = f"{sys.version_info.major}.{sys.version_info.minor}"
        return {"ok": True, "version": ver, "managed_by_uv": False}

    # Nothing found
    raw = f"{sys.version_info.major}.{sys.version_info.minor}"
    return {"ok": False, "version": raw, "managed_by_uv": False}


def check_git() -> dict:
    exe = _which("git")
    if not exe:
        return {"ok": False, "version": None}
    ok, out = _run([exe, "--version"])
    if ok:
        m = re.search(r"(\d+\.\d+[\.\d]*)", out)
        return {"ok": True, "version": m.group(1) if m else "?"}
    return {"ok": False, "version": None}


def check_uv() -> dict:
    exe = _which("uv")
    if not exe:
        return {"ok": False, "version": None}
    ok, out = _run([exe, "--version"])
    if ok:
        m = re.search(r"(\d+\.\d+[\.\d]*)", out)
        return {"ok": True, "version": m.group(1) if m else "?"}
    return {"ok": False, "version": None}


def check_wsl() -> dict:
    """Windows only: check whether WSL2 is installed and has at least one distro.

    Returns:
        ok          – True when a WSL2 distro is available and ready
        installed   – True when the wsl.exe command exists (WSL installed at all)
        has_v2      – True when at least one distro reports VERSION 2
    """
    if sys.platform != "win32":
        # Not Windows — WSL is irrelevant; treat as satisfied
        return {"ok": True, "installed": True, "has_v2": True}

    try:
        r = subprocess.run(
            ["wsl", "-l", "-v"],
            capture_output=True,
            timeout=20,
        )
        # wsl -l -v emits UTF-16-LE on many Windows builds
        out = ""
        for enc in ("utf-16-le", "utf-8", "gbk"):
            try:
                decoded = r.stdout.decode(enc, errors="replace")
                if decoded.strip():
                    out = decoded
                    break
            except Exception:
                continue

        if r.returncode != 0 or not out.strip():
            return {"ok": False, "installed": True, "has_v2": False}

        # A VERSION 2 column value — look for standalone "2" on any data line
        has_v2 = bool(re.search(r"\b2\b", out))
        return {"ok": has_v2, "installed": True, "has_v2": has_v2}

    except FileNotFoundError:
        # wsl.exe not found — WSL is not installed at all
        return {"ok": False, "installed": False, "has_v2": False}
    except Exception:
        return {"ok": False, "installed": False, "has_v2": False}


def check_hermes_installed() -> tuple[bool, str]:
    """Return (installed, version_string).

    On Windows: checks inside the WSL Linux filesystem via `wsl --`.
    On macOS/Linux: checks the local filesystem directly.
    """
    if sys.platform == "win32":
        # Hermes is installed inside WSL; probe via wsl command
        ok, out = _run([
            "wsl", "--", "bash", "-c",
            "test -f ~/.hermes/hermes-agent/pyproject.toml "
            "&& cat ~/.hermes/hermes-agent/pyproject.toml 2>/dev/null "
            "|| echo __NOT_FOUND__",
        ])
        if not ok or "__NOT_FOUND__" in out:
            return False, ""
        m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', out)
        return True, m.group(1) if m else "unknown"

    # macOS / Linux — local filesystem
    marker = HERMES_AGENT / "pyproject.toml"
    if not marker.exists():
        return False, ""
    # venv Python must also exist — otherwise pip install never finished
    if not HERMES_PYTHON.exists():
        return False, ""
    try:
        content = marker.read_text()
        m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
        return True, m.group(1) if m else "unknown"
    except Exception:
        return True, "unknown"


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Hermes Installer")

BASE_DIR = Path(os.environ.get("HERMES_INSTALLER_BASE_DIR", Path(__file__).parent))
INDEX_HTML = BASE_DIR / "index.html"


@app.get("/")
async def serve_index():
    from fastapi.responses import RedirectResponse
    if SETUP_COMPLETE.exists():
        return RedirectResponse(url="/chat", status_code=302)
    return FileResponse(str(INDEX_HTML), media_type="text/html")


@app.post("/api/setup/complete")
async def mark_setup_complete():
    HERMES_HOME.mkdir(parents=True, exist_ok=True)
    SETUP_COMPLETE.write_text("1", encoding="utf-8")
    return {"ok": True}


# ---------------------------------------------------------------------------
# GET /api/check
# ---------------------------------------------------------------------------


@app.get("/api/check")
async def api_check():
    wsl = check_wsl()
    py  = check_python()
    git = check_git()
    uv  = check_uv()
    installed, version = check_hermes_installed()
    env = read_env()
    env_keys = {
        "MINIMAX_API_KEY":    bool(env.get("MINIMAX_API_KEY",    "").strip()),
        "OPENROUTER_API_KEY": bool(env.get("OPENROUTER_API_KEY", "").strip()),
        "ANTHROPIC_API_KEY":  bool(env.get("ANTHROPIC_API_KEY",  "").strip()),
        "WEIXIN_ACCOUNT_ID":  bool(env.get("WEIXIN_ACCOUNT_ID",  "").strip()),
    }
    return {
        "platform": sys.platform,   # "win32" | "darwin" | "linux"
        "wsl":  wsl,
        "python": py,
        "git":  git,
        "uv":   uv,
        "hermes_installed": installed,
        "hermes_version":   version,
        "env_keys": env_keys,
    }


# ---------------------------------------------------------------------------
# GET /api/install  (SSE)
# ---------------------------------------------------------------------------


def _utf8_env() -> dict:
    """Return a copy of the current env with UTF-8 forced + Windows PATH expanded.

    On Chinese Windows the default console encoding is GBK (cp936).
    Any subprocess that prints non-GBK characters (emoji, CJK outside GBK, …)
    will crash with UnicodeEncodeError unless we override the codec.
    PYTHONUTF8=1  → Python UTF-8 mode (3.7+, affects all I/O)
    PYTHONIOENCODING=utf-8 → explicit stdin/stdout/stderr codec

    When running as a PyInstaller frozen exe the inherited PATH is minimal.
    We prepend the most common install locations for git, uv, and Python so
    that subprocess calls to these tools succeed without requiring the user
    to have them in their system PATH.
    """
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    if sys.platform == "win32":
        user = os.environ.get("USERPROFILE", "C:\\Users\\Default")
        extra_paths = [
            # git (typical install locations)
            r"C:\Program Files\Git\cmd",
            r"C:\Program Files\Git\bin",
            r"C:\Program Files (x86)\Git\cmd",
            # uv (installed via official installer)
            os.path.join(user, r"AppData\Local\Programs\uv"),
            os.path.join(user, ".cargo", "bin"),          # uv via cargo
            os.path.join(user, r"AppData\Roaming\uv\bin"),
            # Python launchers
            os.path.join(user, r"AppData\Local\Programs\Python\Python311"),
            os.path.join(user, r"AppData\Local\Programs\Python\Python312"),
            os.path.join(user, r"AppData\Local\Programs\Python\Python310"),
            os.path.join(user, r"AppData\Local\Programs\Python\Python311\Scripts"),
            os.path.join(user, r"AppData\Local\Programs\Python\Python312\Scripts"),
            # scoop / chocolatey shims
            os.path.join(user, "scoop", "shims"),
            r"C:\ProgramData\chocolatey\bin",
            # winget Python
            os.path.join(user, r"AppData\Local\Microsoft\WindowsApps"),
        ]
        current_path = env.get("PATH", "")
        additions = os.pathsep.join(p for p in extra_paths if os.path.isdir(p))
        if additions:
            env["PATH"] = additions + os.pathsep + current_path

    return env


async def _stream_subprocess(cmd: list[str], cwd: Optional[Path] = None) -> AsyncGenerator[str, None]:
    """Run a subprocess and yield SSE-formatted JSON lines."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(cwd) if cwd else None,
        env=_utf8_env(),
    )

    assert proc.stdout is not None

    while True:
        line_bytes = await proc.stdout.readline()
        if not line_bytes:
            break
        line = line_bytes.decode(errors="replace").rstrip()
        if line:
            payload = json.dumps({"type": "log", "level": "info", "message": line})
            yield f"data: {payload}\n\n"
        await asyncio.sleep(0)

    await proc.wait()
    # yield returncode as a metadata event so callers can check success
    payload = json.dumps({"type": "returncode", "code": proc.returncode})
    yield f"data: {payload}\n\n"


def _which(cmd: str) -> Optional[str]:
    """Find executable using both shutil.which and the expanded PATH from _utf8_env."""
    import shutil
    found = shutil.which(cmd)
    if found:
        return found
    # Also try with expanded PATH
    env = _utf8_env()
    return shutil.which(cmd, path=env.get("PATH", ""))


async def _install_generator() -> AsyncGenerator[str, None]:
    installed, version = check_hermes_installed()
    if installed:
        yield f"data: {json.dumps({'type':'already_installed','version':version})}\n\n"
        yield f"data: {json.dumps({'type':'done','success':True})}\n\n"
        return

    HERMES_HOME.mkdir(parents=True, exist_ok=True)

    def _log(msg: str) -> str:
        return f"data: {json.dumps({'type':'log','level':'info','message':msg})}\n\n"

    def _fail(msg: str) -> str:
        return f"data: {json.dumps({'type':'done','success':False,'message':msg})}\n\n"

    # _stream_subprocess yields {"type":"returncode","code":N} as the last event.
    # We forward log lines to the browser and capture rc via rc_box.
    async def _run_step(cmd: list[str], cwd: Optional[Path] = None, rc_box: Optional[list] = None):
        async for event in _stream_subprocess(cmd, cwd):
            try:
                data = json.loads(event[5:].strip())
                if data.get("type") == "returncode":
                    if rc_box is not None:
                        rc_box[0] = data.get("code", 1)
                    continue
            except Exception:
                pass
            yield event

    # ════════════════════════════════════════════════════════════════════════
    # Windows: Hermes Agent runs inside WSL2 — use the official install.sh
    # ════════════════════════════════════════════════════════════════════════
    if sys.platform == "win32":
        wsl = check_wsl()
        if not wsl["ok"]:
            yield _fail(
                "需要 WSL2 才能在 Windows 上安装 Hermes Agent。\n"
                "请先在「环境检测」页面安装 WSL2，重启电脑后再回来继续安装。"
            )
            return

        INSTALL_SCRIPT = (
            "https://raw.githubusercontent.com/NousResearch/hermes-agent"
            "/main/scripts/install.sh"
        )
        yield _log("检测到 WSL2 ✓")
        yield _log("通过 WSL2 运行官方安装脚本（首次约需 5-10 分钟）...")
        yield _log(f"命令: curl -fsSL {INSTALL_SCRIPT} | bash")

        rc = [0]
        async for e in _run_step(
            ["wsl", "--", "bash", "-c",
             f"curl -fsSL {INSTALL_SCRIPT} | bash"],
            rc_box=rc,
        ):
            yield e

        if rc[0] != 0:
            yield _fail("安装失败，请查看上方日志。\n可尝试在 WSL 终端手动执行安装命令。")
            return

        yield _log("✓ Hermes Agent 安装完成！")
        yield f"data: {json.dumps({'type':'done','success':True})}\n\n"
        return

    # ════════════════════════════════════════════════════════════════════════
    # macOS / Linux: git clone + uv venv (existing flow)
    # ════════════════════════════════════════════════════════════════════════

    # ── Step 1: Get source code ───────────────────────────────────────────
    # Priority: extract bundled zip (no network) → git clone (fallback)
    need_source = not HERMES_AGENT.exists() or not (HERMES_AGENT / "pyproject.toml").exists()

    if need_source:
        # Locate the bundle zip embedded in the installer
        _base = Path(os.environ.get("HERMES_INSTALLER_BASE_DIR", str(Path(__file__).parent)))
        bundle_zip = _base / "hermes_agent_bundle.zip"

        if bundle_zip.exists():
            yield _log(f"正在解压内置源码包（{bundle_zip.stat().st_size // 1024} KB）...")
            try:
                import zipfile, shutil
                if HERMES_AGENT.exists():
                    shutil.rmtree(HERMES_AGENT)
                HERMES_AGENT.mkdir(parents=True, exist_ok=True)

                def _extract():
                    with zipfile.ZipFile(bundle_zip, "r") as zf:
                        zf.extractall(HERMES_AGENT)

                await asyncio.get_event_loop().run_in_executor(None, _extract)
                yield _log("✓ 源码解压完成")
            except Exception as exc:
                yield _fail(f"解压失败: {exc}")
                return
        else:
            # Fallback: git clone，自动尝试多个镜像
            _REPO_PATH = "NousResearch/hermes-agent"
            _MIRRORS = [
                f"https://github.com/{_REPO_PATH}",
                f"https://ghproxy.com/https://github.com/{_REPO_PATH}",
                f"https://mirror.ghproxy.com/https://github.com/{_REPO_PATH}",
                f"https://gitclone.com/github.com/{_REPO_PATH}",
            ]
            # Pre-flight: make sure git is available
            _git_bin = _which("git")
            if not _git_bin:
                yield _fail(
                    "未找到 git 命令。\n"
                    "请先安装 Git for Windows：https://git-scm.com/download/win\n"
                    "安装后重新运行安装器。"
                )
                return
            yield _log(f"✓ 找到 git: {_git_bin}")

            cloned = False
            for _i, _url in enumerate(_MIRRORS):
                _label = "GitHub" if _i == 0 else f"镜像{_i}"
                yield _log(f"正在尝试 {_label} 克隆：{_url}")
                rc = [0]
                async for e in _run_step(
                    [_git_bin, "clone", "--depth=1", _url, str(HERMES_AGENT)],
                    rc_box=rc,
                ):
                    yield e
                if rc[0] == 0:
                    cloned = True
                    break
                yield _log(f"✗ {_label} 失败，尝试下一个镜像...")
                # clean partial clone before retry
                if HERMES_AGENT.exists():
                    import shutil
                    shutil.rmtree(HERMES_AGENT, ignore_errors=True)
            if not cloned:
                yield _fail("所有镜像均无法访问，请检查网络后重试")
                return
    else:
        yield _log("源码已存在，跳过解压...")

    # ── Step 2: Create venv ───────────────────────────────────────────────
    yield _log("正在创建虚拟环境 (Python 3.11)...")

    # Pre-flight: make sure uv is available
    _uv_bin = _which("uv")
    if not _uv_bin:
        yield _fail(
            "未找到 uv 命令。\n"
            "请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/\n"
            "Windows 安装命令（PowerShell）：\n"
            "  powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"\n"
            "安装后重新运行安装器。"
        )
        return
    yield _log(f"✓ 找到 uv: {_uv_bin}")

    venv_dir = HERMES_AGENT / "venv"
    if venv_dir.exists() and not HERMES_PYTHON.exists():
        import shutil
        shutil.rmtree(venv_dir, ignore_errors=True)
        yield _log("检测到损坏的虚拟环境，已清理，重新创建...")

    rc = [0]
    async for e in _run_step(
        [_uv_bin, "venv", "venv", "--python", "3.11"],
        cwd=HERMES_AGENT, rc_box=rc,
    ):
        yield e
    if rc[0] != 0:
        yield _fail("创建虚拟环境失败，请确认已安装 uv 和 Python 3.11")
        return

    # ── Step 3: Install dependencies ──────────────────────────────────────
    yield _log("正在安装依赖包（可能需要几分钟）...")
    rc[0] = 0
    async for e in _run_step(
        [_uv_bin, "pip", "install", "-e", ".[all]", "--python", str(HERMES_PYTHON)],
        cwd=HERMES_AGENT, rc_box=rc,
    ):
        yield e

    if rc[0] != 0:
        yield _log("完整安装失败，尝试基础安装...")
        rc[0] = 0
        async for e in _run_step(
            [_uv_bin, "pip", "install", "-e", ".", "--python", str(HERMES_PYTHON)],
            cwd=HERMES_AGENT, rc_box=rc,
        ):
            yield e

    if rc[0] != 0 or not HERMES_PYTHON.exists():
        yield _fail("依赖安装失败，请查看上方日志")
        return

    yield _log("✓ 安装完成！")
    yield f"data: {json.dumps({'type':'done','success':True})}\n\n"


@app.get("/api/install")
async def api_install():
    return StreamingResponse(
        _install_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /api/install/simple  — one-liner curl install (SSE)
# ---------------------------------------------------------------------------

HERMES_INSTALL_URL = "https://hermes-agent.nousresearch.com/install.sh"


@app.get("/api/install/simple")
async def api_install_simple():
    """Run the official one-liner: curl -fsSL URL | bash  (SSE stream)."""

    async def _gen() -> AsyncGenerator[str, None]:
        installed, version = check_hermes_installed()
        if installed:
            yield f"data: {json.dumps({'type':'log','level':'info','message':f'✓ Hermes Agent 已安装（v{version}），跳过安装'})}\n\n"
            yield f"data: {json.dumps({'type':'done','success':True,'already_installed':True})}\n\n"
            return

        if sys.platform == "win32":
            cmd = ["wsl", "--", "bash", "-c",
                   f"curl -fsSL {HERMES_INSTALL_URL} | bash"]
        else:
            cmd = ["bash", "-c", f"curl -fsSL {HERMES_INSTALL_URL} | bash"]

        async for event in _stream_subprocess(cmd):
            try:
                data = json.loads(event[5:].strip())
            except Exception:
                yield event
                continue
            if data.get("type") == "returncode":
                success = data.get("code", 1) == 0
                if success:
                    yield f"data: {json.dumps({'type':'log','level':'info','message':'✓ 安装完成！'})}\n\n"
                yield f"data: {json.dumps({'type':'done','success':success})}\n\n"
            else:
                yield event

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# GET /api/setup/run  — run `hermes setup` via PTY (SSE)
# POST /api/setup/input — send a line to the running setup process
# ---------------------------------------------------------------------------

import pty as _pty_mod
import fcntl as _fcntl_mod

_setup_master_fd: Optional[int] = None
_setup_proc_ref: Optional[object] = None   # subprocess.Popen handle


class SetupInputModel(BaseModel):
    text: str


@app.get("/api/setup/run")
async def api_setup_run():
    global _setup_master_fd, _setup_proc_ref

    hermes_bin = _find_hermes_bin()
    if not hermes_bin:
        async def _err():
            yield f"data: {json.dumps({'type':'error','message':'找不到 hermes，请先完成安装步骤'})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    env = _utf8_env()
    env["PATH"] = str(hermes_bin.parent) + os.pathsep + env.get("PATH", "")
    env["TERM"] = "xterm-256color"
    env["PYTHONUNBUFFERED"] = "1"

    if sys.platform == "win32":
        # Windows: plain subprocess through WSL (no pty available)
        import subprocess as _sp
        proc = await asyncio.create_subprocess_exec(
            "wsl", "--", str(hermes_bin), "setup",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )

        async def _win_gen():
            assert proc.stdout
            while True:
                chunk = await proc.stdout.read(512)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                yield f"data: {json.dumps({'type':'output','text':text})}\n\n"
            await proc.wait()
            yield f"data: {json.dumps({'type':'done','rc':proc.returncode})}\n\n"

        return StreamingResponse(_win_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # macOS / Linux: allocate a real PTY so interactive prompts work
    master_fd, slave_fd = _pty_mod.openpty()

    # Make master non-blocking for safe reads in the callback
    flags = _fcntl_mod.fcntl(master_fd, _fcntl_mod.F_GETFL)
    _fcntl_mod.fcntl(master_fd, _fcntl_mod.F_SETFL, flags | os.O_NONBLOCK)

    _setup_master_fd = master_fd

    def _preexec():
        import termios as _termios
        os.setsid()
        _fcntl_mod.ioctl(slave_fd, _termios.TIOCSCTTY, 0)
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)

    import subprocess as _sp
    popen_proc = _sp.Popen(
        [str(hermes_bin), "setup"],
        preexec_fn=_preexec,
        env=env,
        close_fds=True,
    )
    os.close(slave_fd)
    _setup_proc_ref = popen_proc

    async def _pty_gen():
        global _setup_master_fd, _setup_proc_ref
        loop = asyncio.get_event_loop()
        q: asyncio.Queue = asyncio.Queue()

        def _on_readable():
            try:
                data = os.read(master_fd, 4096)
                q.put_nowait(data)
            except (OSError, BlockingIOError):
                pass

        def _on_eof():
            q.put_nowait(None)
            try:
                loop.remove_reader(master_fd)
            except Exception:
                pass

        loop.add_reader(master_fd, _on_readable)

        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if popen_proc.poll() is not None:
                    break
                yield f"data: {json.dumps({'type':'ping'})}\n\n"
                continue

            if chunk is None:
                break

            text = chunk.decode("utf-8", errors="replace")
            yield f"data: {json.dumps({'type':'output','text':text})}\n\n"

        try:
            loop.remove_reader(master_fd)
        except Exception:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass

        popen_proc.wait()
        _setup_master_fd = None
        _setup_proc_ref = None
        yield f"data: {json.dumps({'type':'done','rc':popen_proc.returncode})}\n\n"

    return StreamingResponse(
        _pty_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/setup/input")
async def api_setup_input(body: SetupInputModel):
    """Write a line of text to the running hermes setup PTY."""
    global _setup_master_fd
    if _setup_master_fd is not None:
        try:
            os.write(_setup_master_fd, (body.text + "\r").encode("utf-8"))
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "no active setup session"}


# ---------------------------------------------------------------------------
# GET /api/install-tool?tool=uv|python  (SSE — auto-install prerequisites)
# ---------------------------------------------------------------------------


async def _install_tool_generator(tool: str) -> AsyncGenerator[str, None]:
    """Auto-install uv or Python 3.11 and stream progress as SSE."""

    def _log(msg: str) -> str:
        return f"data: {json.dumps({'type': 'log', 'level': 'info', 'message': msg})}\n\n"

    def _fail(msg: str) -> str:
        return f"data: {json.dumps({'type': 'done', 'success': False, 'message': msg})}\n\n"

    def _ok(msg: str = "") -> str:
        return f"data: {json.dumps({'type': 'done', 'success': True, 'message': msg})}\n\n"

    if tool == "uv":
        yield _log("正在安装 uv 包管理器...")
        if sys.platform == "win32":
            yield _log("使用 PowerShell 安装 uv（需要联网，约 10-30 秒）...")
            cmd = [
                "powershell", "-ExecutionPolicy", "Bypass", "-NoProfile",
                "-Command", "irm https://astral.sh/uv/install.ps1 | iex",
            ]
        else:
            yield _log("使用 curl 安装 uv（需要联网，约 10-30 秒）...")
            cmd = ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"]

        rc_box = [1]
        async for evt in _stream_subprocess(cmd):
            try:
                d = json.loads(evt[5:].strip())
                if d.get("type") == "returncode":
                    rc_box[0] = d.get("code", 1)
                    continue
            except Exception:
                pass
            yield evt

        # The uv installer occasionally returns non-zero on minor issues
        # (e.g. fish shell config dir permission error) even though uv itself
        # was installed correctly. Verify by actually locating the binary.
        uv_found = _which("uv")
        if rc_box[0] == 0 or uv_found:
            if uv_found:
                yield _log(f"✓ uv 安装完成！路径: {uv_found}")
            else:
                yield _log("✓ uv 安装完成！")
            yield _ok("uv 已成功安装")
        else:
            if sys.platform == "win32":
                manual = "powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\""
            else:
                manual = "curl -LsSf https://astral.sh/uv/install.sh | sh"
            yield _fail(
                f"uv 安装失败。\n"
                f"请手动安装：https://docs.astral.sh/uv/getting-started/installation/\n"
                f"命令：{manual}"
            )

    elif tool == "python":
        uv_bin = _which("uv")
        if not uv_bin:
            yield _fail("请先安装 uv，再通过 uv 安装 Python 3.11")
            return

        yield _log(f"使用 uv 安装 Python 3.11（首次约需 1-3 分钟）...")
        yield _log(f"uv 路径: {uv_bin}")

        rc_box = [1]
        async for evt in _stream_subprocess([uv_bin, "python", "install", "3.11"]):
            try:
                d = json.loads(evt[5:].strip())
                if d.get("type") == "returncode":
                    rc_box[0] = d.get("code", 1)
                    continue
            except Exception:
                pass
            yield evt

        if rc_box[0] == 0:
            yield _log("✓ Python 3.11 安装完成！")
            yield _ok("Python 3.11 已成功安装")
        else:
            yield _fail(
                "Python 安装失败。\n"
                "请手动安装 Python 3.11：https://www.python.org/downloads/"
            )

    else:
        yield _fail(f"未知工具: {tool}")


@app.get("/api/install-tool")
async def api_install_tool(tool: str):
    return StreamingResponse(
        _install_tool_generator(tool),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# GET /api/open-url?url=...  (open URL in system browser)
# ---------------------------------------------------------------------------


@app.get("/api/open-url")
async def api_open_url(url: str):
    """Open a URL in the system default browser (safe: allows only http/https)."""
    import webbrowser
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Only http/https URLs allowed")
    webbrowser.open(url)
    return {"ok": True}


# ---------------------------------------------------------------------------
# GET /api/install-wsl  (Windows: launch elevated WSL2 installer)
# ---------------------------------------------------------------------------


@app.get("/api/install-wsl")
async def api_install_wsl():
    """Launch an elevated process to run `wsl --install`.

    wsl --install requires administrator privileges and triggers a reboot.
    We launch it via Start-Process ... -Verb RunAs so the UAC prompt appears.
    Returns immediately — the installation happens out-of-process.
    """
    if sys.platform != "win32":
        return {"ok": False, "message": "仅 Windows 需要安装 WSL2"}
    try:
        # Start-Process with -Verb RunAs triggers the UAC elevation prompt
        subprocess.Popen(
            [
                "powershell", "-ExecutionPolicy", "Bypass", "-NoProfile",
                "-WindowStyle", "Normal", "-Command",
                "Start-Process powershell "
                "-ArgumentList '-NoProfile -Command wsl --install' "
                "-Verb RunAs -Wait",
            ],
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return {
            "ok": True,
            "message": (
                "WSL2 安装程序已启动，请在弹出的管理员权限窗口中点击「是」。\n"
                "安装完成后系统需要重启，重启后重新打开本安装向导继续安装。"
            ),
        }
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


# ---------------------------------------------------------------------------
# POST /api/config/model
# ---------------------------------------------------------------------------


class ModelConfig(BaseModel):
    provider: str = "minimax"
    model: str = "MiniMax-M2.5"
    base_url: str = "https://api.minimax.io/anthropic"
    api_mode: str = "anthropic_messages"


@app.post("/api/config/model")
async def api_config_model(cfg: ModelConfig):
    HERMES_HOME.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if HERMES_CONFIG.exists():
        try:
            existing = yaml.safe_load(HERMES_CONFIG.read_text()) or {}
        except Exception:
            existing = {}

    existing.setdefault("model", {})
    existing["model"]["provider"] = cfg.provider
    existing["model"]["default"] = cfg.model
    existing["model"]["base_url"] = cfg.base_url
    existing["model"]["api_mode"] = cfg.api_mode

    HERMES_CONFIG.write_text(yaml.dump(existing, allow_unicode=True, default_flow_style=False))
    return {"success": True}


# ---------------------------------------------------------------------------
# POST /api/config/keys
# ---------------------------------------------------------------------------


class KeysPayload(BaseModel):
    MINIMAX_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""


@app.post("/api/config/keys")
async def api_config_keys(payload: KeysPayload):
    HERMES_HOME.mkdir(parents=True, exist_ok=True)
    if payload.MINIMAX_API_KEY.strip():
        write_env_key("MINIMAX_API_KEY", payload.MINIMAX_API_KEY.strip())
        write_env_key("MINIMAX_PORTAL_API_KEY", payload.MINIMAX_API_KEY.strip())
    if payload.OPENROUTER_API_KEY.strip():
        write_env_key("OPENROUTER_API_KEY", payload.OPENROUTER_API_KEY.strip())
    if payload.ANTHROPIC_API_KEY.strip():
        write_env_key("ANTHROPIC_API_KEY", payload.ANTHROPIC_API_KEY.strip())
    return {"success": True}


# ---------------------------------------------------------------------------
# GET /api/weixin/login  (SSE)
# ---------------------------------------------------------------------------


async def _weixin_login_generator() -> AsyncGenerator[str, None]:
    base_url = ILINK_BASE
    deadline = time.monotonic() + 480
    max_refreshes = 3
    refresh_count = 0

    async def send(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    async with aiohttp.ClientSession() as session:
        # Fetch initial QR
        async def fetch_qr() -> Optional[tuple[str, str]]:
            """Returns (qrcode_token, qrcode_img_content) or None on error."""
            try:
                async with session.get(
                    f"{base_url}/ilink/bot/get_bot_qrcode",
                    params={"bot_type": "3"},
                    headers=ILINK_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    data = await resp.json(content_type=None)
                    qr_token = data.get("qrcode") or data.get("data", {}).get("qrcode")
                    qr_img = data.get("qrcode_img_content") or data.get("data", {}).get("qrcode_img_content")
                    if not qr_token or not qr_img:
                        return None
                    return qr_token, qr_img
            except Exception as exc:
                return None

        result = await fetch_qr()
        if result is None:
            yield await send({"type": "error", "message": "无法获取微信二维码，请检查网络"})
            yield await send({"type": "done", "success": False})
            return

        qr_token, qr_img_url = result

        # Generate PNG from URL content
        try:
            qr_data_str = qr_img_url
            qr_b64 = generate_qr_image(qr_data_str)
            yield await send({"type": "qr", "image": qr_b64})
        except Exception as exc:
            yield await send({"type": "error", "message": f"生成二维码失败: {exc}"})
            yield await send({"type": "done", "success": False})
            return

        yield await send({"type": "status", "message": "等待扫码..."})

        while time.monotonic() < deadline:
            await asyncio.sleep(2)

            try:
                async with session.get(
                    f"{base_url}/ilink/bot/get_qrcode_status",
                    params={"qrcode": qr_token},
                    headers=ILINK_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    data = await resp.json(content_type=None)
            except Exception as exc:
                yield await send({"type": "status", "message": f"轮询错误: {exc}"})
                continue

            status = data.get("status") or data.get("data", {}).get("status") or ""

            if status == "wait":
                yield await send({"type": "status", "message": "等待扫码..."})

            elif status == "scaned":
                yield await send({"type": "status", "message": "已扫码，请在微信中点确认..."})

            elif status == "scaned_but_redirect":
                redirect_host = (
                    data.get("redirect_host")
                    or data.get("data", {}).get("redirect_host")
                    or ""
                )
                if redirect_host:
                    base_url = f"https://{redirect_host}"
                yield await send({"type": "status", "message": "已扫码，正在确认中..."})

            elif status == "expired":
                if refresh_count >= max_refreshes:
                    yield await send({"type": "error", "message": "二维码多次过期，请重试"})
                    yield await send({"type": "done", "success": False})
                    return
                refresh_count += 1
                yield await send({"type": "status", "message": f"二维码已过期，正在刷新 ({refresh_count}/{max_refreshes})..."})
                result = await fetch_qr()
                if result is None:
                    yield await send({"type": "error", "message": "刷新二维码失败"})
                    yield await send({"type": "done", "success": False})
                    return
                qr_token, qr_img_url = result
                try:
                    qr_b64 = generate_qr_image(qr_img_url)
                    yield await send({"type": "qr", "image": qr_b64})
                except Exception:
                    yield await send({"type": "error", "message": "生成新二维码失败"})
                    yield await send({"type": "done", "success": False})
                    return
                yield await send({"type": "status", "message": "等待扫码..."})

            elif status == "confirmed":
                account_id = (
                    data.get("account_id")
                    or data.get("data", {}).get("account_id")
                    or data.get("wxid")
                    or data.get("data", {}).get("wxid")
                    or ""
                )
                token = (
                    data.get("token")
                    or data.get("data", {}).get("token")
                    or data.get("access_token")
                    or data.get("data", {}).get("access_token")
                    or ""
                )
                if account_id:
                    write_env_key("WEIXIN_ACCOUNT_ID", account_id)
                if token:
                    write_env_key("WEIXIN_TOKEN", token)
                yield await send({"type": "status", "message": "登录成功！"})
                yield await send({"type": "done", "success": True, "account_id": account_id})
                return

            else:
                # Unknown status — just keep waiting
                if status:
                    yield await send({"type": "status", "message": f"状态: {status}"})

        yield await send({"type": "error", "message": "登录超时（8分钟），请重新尝试"})
        yield await send({"type": "done", "success": False})


@app.get("/api/weixin/login")
async def api_weixin_login():
    return StreamingResponse(
        _weixin_login_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /api/status
# ---------------------------------------------------------------------------


def _is_process_running(pid_file: Path) -> bool:
    if not pid_file.exists():
        return False
    try:
        raw = pid_file.read_text().strip()
        # PID file may be plain int or JSON {"pid": N, ...}
        try:
            pid = int(raw)
        except ValueError:
            pid = int(json.loads(raw)["pid"])
        os.kill(pid, 0)
        return True
    except (ValueError, KeyError, ProcessLookupError, PermissionError):
        return False


@app.get("/api/status")
async def api_status():
    gateway_pid = HERMES_HOME / "gateway.pid"
    gateway_running = _is_process_running(gateway_pid)
    env = read_env()
    weixin_connected = bool(env.get("WEIXIN_ACCOUNT_ID", "").strip())
    installed, version = check_hermes_installed()
    return {
        "hermes_installed": installed,
        "hermes_version": version,
        "gateway_running": gateway_running,
        "weixin_connected": weixin_connected,
        "env_keys": {
            "MINIMAX_API_KEY": bool(env.get("MINIMAX_API_KEY", "").strip()),
            "OPENROUTER_API_KEY": bool(env.get("OPENROUTER_API_KEY", "").strip()),
            "ANTHROPIC_API_KEY": bool(env.get("ANTHROPIC_API_KEY", "").strip()),
        },
    }


# ---------------------------------------------------------------------------
# POST /api/gateway/restart  — just verify port 8642 is reachable
# NOTE: "hermes gateway" manages messaging platforms (Telegram/Discord/WhatsApp)
#       and is UNRELATED to the Hermes API server on port 8642.
#       We no longer call "hermes gateway restart" here — doing so could
#       disrupt the running Hermes process.  Model config changes in
#       ~/.hermes/config.yaml take effect automatically on the next request.
# ---------------------------------------------------------------------------


@app.post("/api/gateway/restart")
async def api_gateway_restart():
    """Check whether the Hermes API server (port 8642) is reachable."""
    running = await _hermes_gateway_running()
    if running:
        return {"success": True, "output": "Hermes API server is running on port 8642"}
    else:
        return {
            "success": False,
            "output": (
                "Hermes API server (port 8642) is not reachable. "
                "Please start Hermes Agent manually: hermes serve"
            ),
        }


def _find_hermes_bin() -> Optional[Path]:
    """Locate the hermes CLI binary in known locations."""
    candidates = [
        HERMES_AGENT / "venv" / "bin" / "hermes",          # venv install (macOS/Linux)
        HERMES_AGENT / "venv" / "Scripts" / "hermes.exe",   # venv install (Windows)
        HERMES_HOME / "bin" / "hermes",                     # standalone install
        Path.home() / ".local" / "bin" / "hermes",          # pipx / user-local
    ]
    for p in candidates:
        if p.exists():
            return p
    # Fall back to PATH lookup
    import shutil
    found = shutil.which("hermes")
    return Path(found) if found else None


@app.post("/api/hermes/start")
async def api_hermes_start():
    """Launch 'hermes serve' as a detached background process."""
    # Already running?
    if await _hermes_gateway_running():
        return {"ok": True, "message": "已运行"}

    hermes_bin = _find_hermes_bin()
    if not hermes_bin:
        return {"ok": False, "message": "找不到 hermes 可执行文件，请确保 Hermes Agent 已安装"}

    try:
        env = os.environ.copy()
        # Ensure venv bin dir is first in PATH so hermes can find its own deps
        venv_bin = str(hermes_bin.parent)
        env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")

        kwargs: dict = dict(
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True   # detach from our process group

        subprocess.Popen([str(hermes_bin), "serve"], **kwargs)
    except Exception as exc:
        return {"ok": False, "message": f"启动失败: {exc}"}

    # Wait up to 8 s for port 8642 to become reachable
    for _ in range(16):
        await asyncio.sleep(0.5)
        if await _hermes_gateway_running():
            return {"ok": True, "message": "Hermes Agent 已启动"}

    return {"ok": False, "message": "进程已启动，但 port 8642 尚未就绪，请稍等几秒后刷新"}


@app.get("/chat")
async def serve_chat():
    from fastapi.responses import RedirectResponse
    webui_port = os.environ.get("HERMES_WEBUI_PORT", "8787")
    return RedirectResponse(url=f"http://127.0.0.1:{webui_port}/", status_code=302)


# ---------------------------------------------------------------------------
# GET /api/config/read
# ---------------------------------------------------------------------------


@app.get("/api/config/read")
async def api_config_read():
    """Return current model config + env key status."""
    config: dict = {}
    if HERMES_CONFIG.exists():
        try:
            config = yaml.safe_load(HERMES_CONFIG.read_text()) or {}
        except Exception:
            config = {}
    env = read_env()
    model_cfg = config.get("model", {})
    return {
        "model": {
            "provider": model_cfg.get("provider", "minimax"),
            "name": model_cfg.get("default", "MiniMax-M2.5"),
            "base_url": model_cfg.get("base_url", "https://api.minimax.io/anthropic"),
            "api_mode": model_cfg.get("api_mode", "anthropic_messages"),
        },
        "agent": config.get("agent", {}),
        "env_keys": {
            "MINIMAX_API_KEY": bool(env.get("MINIMAX_API_KEY", "").strip()),
            "OPENROUTER_API_KEY": bool(env.get("OPENROUTER_API_KEY", "").strip()),
            "ANTHROPIC_API_KEY": bool(env.get("ANTHROPIC_API_KEY", "").strip()),
            "WEIXIN_ACCOUNT_ID": bool(env.get("WEIXIN_ACCOUNT_ID", "").strip()),
            "TELEGRAM_BOT_TOKEN": bool(env.get("TELEGRAM_BOT_TOKEN", "").strip()),
        },
    }


# ---------------------------------------------------------------------------
# POST /api/config/advanced
# ---------------------------------------------------------------------------


class AdvancedConfig(BaseModel):
    reasoning_effort: str = "medium"
    max_turns: int = 90
    system_prompt: str = ""


@app.post("/api/config/advanced")
async def api_config_advanced(cfg: AdvancedConfig):
    existing: dict = {}
    if HERMES_CONFIG.exists():
        try:
            existing = yaml.safe_load(HERMES_CONFIG.read_text()) or {}
        except Exception:
            existing = {}
    existing.setdefault("agent", {})
    existing["agent"]["reasoning_effort"] = cfg.reasoning_effort
    existing["agent"]["max_turns"] = cfg.max_turns
    HERMES_CONFIG.write_text(yaml.dump(existing, allow_unicode=True, default_flow_style=False))
    return {"success": True}


# ---------------------------------------------------------------------------
# Hermes Gateway helpers
# ---------------------------------------------------------------------------

async def _hermes_gateway_running() -> bool:
    """Return True if the Hermes API server is reachable on port 8642."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{HERMES_GATEWAY_URL}/health",
                timeout=aiohttp.ClientTimeout(total=2),
            ) as r:
                return r.status == 200
    except Exception:
        return False


async def _stream_hermes_gen(req: "ChatRequest") -> AsyncGenerator[str, None]:
    """Route chat through Hermes Agent's OpenAI-compatible gateway (port 8642)."""
    messages = [m for m in req.messages if str(m.get("content", "")).strip()]
    if req.system_prompt:
        messages = [{"role": "system", "content": req.system_prompt}] + messages

    body = {
        "model": "hermes-agent",
        "messages": messages,
        "stream": True,
        "max_tokens": 8192,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{HERMES_GATEWAY_URL}/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                json=body,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    yield f"data: {json.dumps({'type':'error','message':f'Hermes Gateway 错误 {resp.status}: {err[:300]}'})}\n\n"
                    return

                # Buffer and split by newlines — aiohttp chunks may contain
                # multiple SSE lines or partial lines; we must split manually.
                buf = b""
                done = False
                async for raw_chunk in resp.content.iter_any():
                    buf += raw_chunk
                    while b"\n" in buf:
                        raw_line, buf = buf.split(b"\n", 1)
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            done = True
                            break
                        try:
                            evt = json.loads(data_str)
                        except Exception:
                            continue

                        choices = evt.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        text = delta.get("content", "")
                        if text:
                            yield f"data: {json.dumps({'type':'text_delta','text':text})}\n\n"

                    if done:
                        break
                    await asyncio.sleep(0)

                yield f"data: {json.dumps({'type':'done'})}\n\n"
    except Exception as exc:
        yield f"data: {json.dumps({'type':'error','message':str(exc)})}\n\n"


# ---------------------------------------------------------------------------
# POST /api/chat/stream  — SSE streaming chat
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    messages: list  # [{"role": "user"/"assistant", "content": "..."}]
    reasoning_effort: str = "medium"
    system_prompt: str = ""



@app.get("/api/gateway/health")
async def api_gateway_health():
    """Check if Hermes Agent gateway (port 8642) is running."""
    running = await _hermes_gateway_running()
    return {"running": running, "url": HERMES_GATEWAY_URL}


@app.post("/api/chat/stream")
async def api_chat_stream(req: ChatRequest):
    # 所有请求必须经过 Hermes Agent Gateway（port 8642）
    if not await _hermes_gateway_running():
        async def _no_gateway() -> AsyncGenerator[str, None]:
            msg = "⚠️ Hermes Agent Gateway 未运行（port 8642）。\n\n请重新启动 Hermes Agent，或返回安装向导重新配置。"
            yield "data: " + json.dumps({"type": "error", "message": msg}) + "\n\n"
        return StreamingResponse(
            _no_gateway(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    gen = _stream_hermes_gen(req)
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import threading
    import webbrowser

    threading.Timer(1.0, lambda: webbrowser.open("http://localhost:7891")).start()
    uvicorn.run(app, host="127.0.0.1", port=7891, log_level="warning")
