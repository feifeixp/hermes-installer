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

HERMES_HOME = Path.home() / ".hermes"
HERMES_AGENT = HERMES_HOME / "hermes-agent"
HERMES_ENV = HERMES_HOME / ".env"
HERMES_CONFIG = HERMES_HOME / "config.yaml"
# Python executable differs by platform
if sys.platform == "win32":
    HERMES_PYTHON = HERMES_AGENT / "venv" / "Scripts" / "python.exe"
else:
    HERMES_PYTHON = HERMES_AGENT / "venv" / "bin" / "python3"
HERMES_REPO = "https://github.com/nousresearch/hermes-agent"

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
    # Prefer hermes venv Python (3.11+), fall back to system Python
    candidates = [
        str(HERMES_PYTHON),
        "python3.11", "python3.12", "python3.13",
        sys.executable,
    ]
    for candidate in candidates:
        ok, out = _run([candidate, "--version"])
        if ok:
            m = re.search(r"(\d+\.\d+[\.\d]*)", out)
            version = m.group(1) if m else "?"
            parts = version.split(".")
            major, minor = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
            if (major, minor) >= (3, 10):
                return {"ok": True, "version": version}
    # Return the system Python version even if < 3.10
    ok, out = _run([sys.executable, "--version"])
    m = re.search(r"(\d+\.\d+[\.\d]*)", out) if ok else None
    return {"ok": False, "version": m.group(1) if m else None}


def check_git() -> dict:
    ok, out = _run(["git", "--version"])
    if ok:
        m = re.search(r"(\d+\.\d+[\.\d]*)", out)
        return {"ok": True, "version": m.group(1) if m else "?"}
    return {"ok": False, "version": None}


def check_uv() -> dict:
    ok, out = _run(["uv", "--version"])
    if ok:
        m = re.search(r"(\d+\.\d+[\.\d]*)", out)
        return {"ok": True, "version": m.group(1) if m else "?"}
    return {"ok": False, "version": None}


def check_hermes_installed() -> tuple[bool, str]:
    """Return (installed, version_string)."""
    marker = HERMES_AGENT / "pyproject.toml"
    if not marker.exists():
        return False, ""
    # Try to read version from pyproject.toml
    try:
        content = marker.read_text()
        m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
        version = m.group(1) if m else "unknown"
        return True, version
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
    return FileResponse(str(INDEX_HTML), media_type="text/html")


# ---------------------------------------------------------------------------
# GET /api/check
# ---------------------------------------------------------------------------


@app.get("/api/check")
async def api_check():
    py = check_python()
    git = check_git()
    uv = check_uv()
    installed, version = check_hermes_installed()
    env = read_env()
    env_keys = {
        "MINIMAX_API_KEY": bool(env.get("MINIMAX_API_KEY", "").strip()),
        "OPENROUTER_API_KEY": bool(env.get("OPENROUTER_API_KEY", "").strip()),
        "ANTHROPIC_API_KEY": bool(env.get("ANTHROPIC_API_KEY", "").strip()),
        "WEIXIN_ACCOUNT_ID": bool(env.get("WEIXIN_ACCOUNT_ID", "").strip()),
    }
    return {
        "python": py,
        "git": git,
        "uv": uv,
        "hermes_installed": installed,
        "hermes_version": version,
        "env_keys": env_keys,
    }


# ---------------------------------------------------------------------------
# GET /api/install  (SSE)
# ---------------------------------------------------------------------------


async def _stream_subprocess(cmd: list[str], cwd: Optional[Path] = None) -> AsyncGenerator[str, None]:
    """Run a subprocess and yield SSE-formatted JSON lines."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(cwd) if cwd else None,
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


async def _install_generator() -> AsyncGenerator[str, None]:
    installed, version = check_hermes_installed()
    if installed:
        payload = json.dumps({"type": "already_installed", "version": version})
        yield f"data: {payload}\n\n"
        payload = json.dumps({"type": "done", "success": True})
        yield f"data: {payload}\n\n"
        return

    HERMES_HOME.mkdir(parents=True, exist_ok=True)

    # Step 1 — clone
    if not HERMES_AGENT.exists():
        yield f"data: {json.dumps({'type':'log','level':'info','message':f'Cloning {HERMES_REPO}...'})}\n\n"
        clone_cmd = ["git", "clone", "--depth=1", HERMES_REPO, str(HERMES_AGENT)]
        return_code = None
        proc = await asyncio.create_subprocess_exec(
            *clone_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
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
        return_code = proc.returncode
        if return_code != 0:
            payload = json.dumps({"type": "done", "success": False, "message": "git clone failed"})
            yield f"data: {payload}\n\n"
            return
    else:
        yield f"data: {json.dumps({'type':'log','level':'info','message':'Repository already cloned, pulling latest...'})}\n\n"
        pull_proc = await asyncio.create_subprocess_exec(
            "git", "pull",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(HERMES_AGENT),
        )
        assert pull_proc.stdout is not None
        while True:
            line_bytes = await pull_proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode(errors="replace").rstrip()
            if line:
                payload = json.dumps({"type": "log", "level": "info", "message": line})
                yield f"data: {payload}\n\n"
            await asyncio.sleep(0)
        await pull_proc.wait()

    # Step 2 — create venv + install
    yield f"data: {json.dumps({'type':'log','level':'info','message':'Creating virtual environment...'})}\n\n"
    venv_proc = await asyncio.create_subprocess_exec(
        "uv", "venv", "venv",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(HERMES_AGENT),
    )
    assert venv_proc.stdout is not None
    while True:
        line_bytes = await venv_proc.stdout.readline()
        if not line_bytes:
            break
        line = line_bytes.decode(errors="replace").rstrip()
        if line:
            payload = json.dumps({"type": "log", "level": "info", "message": line})
            yield f"data: {payload}\n\n"
        await asyncio.sleep(0)
    await venv_proc.wait()

    yield f"data: {json.dumps({'type':'log','level':'info','message':'Installing dependencies (this may take a minute)...'})}\n\n"
    pip_proc = await asyncio.create_subprocess_exec(
        "uv", "pip", "install", "-e", ".",
        "--python", str(HERMES_AGENT / "venv" / "bin" / "python3"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(HERMES_AGENT),
    )
    assert pip_proc.stdout is not None
    while True:
        line_bytes = await pip_proc.stdout.readline()
        if not line_bytes:
            break
        line = line_bytes.decode(errors="replace").rstrip()
        if line:
            payload = json.dumps({"type": "log", "level": "info", "message": line})
            yield f"data: {payload}\n\n"
        await asyncio.sleep(0)
    await pip_proc.wait()
    rc = pip_proc.returncode

    if rc != 0:
        payload = json.dumps({"type": "done", "success": False, "message": "pip install failed"})
        yield f"data: {payload}\n\n"
        return

    yield f"data: {json.dumps({'type':'log','level':'info','message':'Installation complete!'})}\n\n"
    payload = json.dumps({"type": "done", "success": True})
    yield f"data: {payload}\n\n"


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
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
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
# POST /api/gateway/restart
# ---------------------------------------------------------------------------


@app.post("/api/gateway/restart")
async def api_gateway_restart():
    # Locate hermes executable (differs by platform)
    if sys.platform == "win32":
        hermes_bin = HERMES_AGENT / "venv" / "Scripts" / "hermes.exe"
    else:
        hermes_bin = Path.home() / ".local" / "bin" / "hermes"
    if not hermes_bin.exists():
        hermes_bin_str = "hermes"   # fall back to PATH
    else:
        hermes_bin_str = str(hermes_bin)

    try:
        proc = await asyncio.create_subprocess_exec(
            hermes_bin_str, "gateway", "restart",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        rc = proc.returncode
        output = stdout.decode(errors="replace") if stdout else ""
        return {"success": rc == 0, "output": output}
    except asyncio.TimeoutError:
        return {"success": False, "output": "timeout"}
    except FileNotFoundError:
        return {"success": False, "output": "hermes command not found"}
    except Exception as exc:
        return {"success": False, "output": str(exc)}


# ---------------------------------------------------------------------------
# GET /chat  — serve chat UI
# ---------------------------------------------------------------------------

CHAT_HTML = BASE_DIR / "chat.html"


@app.get("/chat")
async def serve_chat():
    return FileResponse(str(CHAT_HTML), media_type="text/html")


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
# POST /api/chat/stream  — SSE streaming chat
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    messages: list  # [{"role": "user"/"assistant", "content": "..."}]
    reasoning_effort: str = "medium"
    system_prompt: str = ""


async def _stream_anthropic_gen(
    req: ChatRequest, model_name: str, base_url: str, api_key: str, provider: str
) -> AsyncGenerator[str, None]:
    """Stream via Anthropic Messages API (MiniMax / Anthropic compatible)."""
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    is_minimax = "minimax.io" in base_url or "minimaxi.com" in base_url
    if is_minimax:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        headers["x-api-key"] = api_key
        headers["anthropic-beta"] = "interleaved-thinking-2025-05-14"

    messages = [m for m in req.messages if str(m.get("content", "")).strip()]

    body: dict = {
        "model": model_name,
        "max_tokens": 8192,
        "messages": messages,
        "stream": True,
    }
    if req.system_prompt:
        body["system"] = req.system_prompt

    effort_map = {"low": 4000, "medium": 8000, "high": 16000}
    if req.reasoning_effort != "off" and not is_minimax:
        body["thinking"] = {
            "type": "enabled",
            "budget_tokens": effort_map.get(req.reasoning_effort, 8000),
        }
        body["temperature"] = 1

    url = base_url.rstrip("/") + "/v1/messages"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    yield f"data: {json.dumps({'type':'error','message':f'API错误 {resp.status}: {err[:300]}'})}\n\n"
                    return

                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        evt = json.loads(data_str)
                    except Exception:
                        continue

                    evt_type = evt.get("type", "")

                    if evt_type == "content_block_delta":
                        delta = evt.get("delta", {})
                        if delta.get("type") == "thinking_delta":
                            text = delta.get("thinking", "")
                            if text:
                                yield f"data: {json.dumps({'type':'reasoning_delta','text':text})}\n\n"
                        elif delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield f"data: {json.dumps({'type':'text_delta','text':text})}\n\n"
                    elif evt_type == "message_stop":
                        break

                    await asyncio.sleep(0)

                yield f"data: {json.dumps({'type':'done'})}\n\n"
    except Exception as exc:
        yield f"data: {json.dumps({'type':'error','message':str(exc)})}\n\n"


async def _stream_openai_gen(
    req: ChatRequest, model_name: str, base_url: str, api_key: str
) -> AsyncGenerator[str, None]:
    """Stream via OpenAI-compatible Chat Completions API."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    messages = [m for m in req.messages if str(m.get("content", "")).strip()]
    if req.system_prompt:
        messages = [{"role": "system", "content": req.system_prompt}] + messages

    body = {
        "model": model_name,
        "messages": messages,
        "stream": True,
        "max_tokens": 8192,
    }

    url = base_url.rstrip("/") + "/chat/completions"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    yield f"data: {json.dumps({'type':'error','message':f'API错误 {resp.status}: {err[:300]}'})}\n\n"
                    return

                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        evt = json.loads(data_str)
                    except Exception:
                        continue

                    delta = evt.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content", "")
                    if text:
                        yield f"data: {json.dumps({'type':'text_delta','text':text})}\n\n"

                    await asyncio.sleep(0)

                yield f"data: {json.dumps({'type':'done'})}\n\n"
    except Exception as exc:
        yield f"data: {json.dumps({'type':'error','message':str(exc)})}\n\n"


@app.post("/api/chat/stream")
async def api_chat_stream(req: ChatRequest):
    config: dict = {}
    if HERMES_CONFIG.exists():
        try:
            config = yaml.safe_load(HERMES_CONFIG.read_text()) or {}
        except Exception:
            pass
    env = read_env()
    model_cfg = config.get("model", {})
    provider = model_cfg.get("provider", "minimax")
    model_name = model_cfg.get("default", "MiniMax-M2.5")
    base_url = model_cfg.get("base_url", "https://api.minimax.io/anthropic")
    api_mode = model_cfg.get("api_mode", "anthropic_messages")

    key_map: dict[str, list[str]] = {
        "minimax": ["MINIMAX_API_KEY", "MINIMAX_PORTAL_API_KEY"],
        "minimax-cn": ["MINIMAX_CN_API_KEY"],
        "anthropic": ["ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN"],
        "openrouter": ["OPENROUTER_API_KEY"],
    }
    api_key = ""
    for k in key_map.get(provider, ["MINIMAX_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"]):
        v = env.get(k, "").strip()
        if v:
            api_key = v
            break

    if not api_key:
        async def _no_key() -> AsyncGenerator[str, None]:
            yield f"data: {json.dumps({'type':'error','message':'未配置 API Key，请在左下角设置中添加'})}\n\n"
        return StreamingResponse(_no_key(), media_type="text/event-stream")

    if api_mode == "anthropic_messages":
        gen = _stream_anthropic_gen(req, model_name, base_url, api_key, provider)
    else:
        gen = _stream_openai_gen(req, model_name, base_url, api_key)

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
