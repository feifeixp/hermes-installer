# Windows Native Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Windows EXE install and launch hermes-agent automatically on first run, without needing GitHub access, using a bundled uv.exe and Tsinghua PyPI mirror.

**Architecture:** Bundle `uv.exe` in the EXE during CI build; on first Windows launch, extract the hermes-agent source (already bundled as `hermes_agent_bundle.zip`), create a venv with uv, install deps from Tsinghua mirror, then start `server.py` directly—bypassing `bootstrap.py`'s `ensure_supported_platform()` check. macOS/Linux paths are completely untouched.

**Tech Stack:** Python subprocess, zipfile, uv (Rust binary), PyInstaller datas, GitHub Actions PowerShell

---

## File Map

| File | Change |
|------|--------|
| `.github/workflows/build.yml` | Windows job: download `uv.exe` before PyInstaller |
| `build.bat` | Download `uv.exe` to `tools/` before PyInstaller (local build) |
| `hermes_installer.spec` | Add `tools/uv.exe` to `datas` (Windows only) |
| `main.py` | Add 5 new functions before `main()`; restructure `main()` Windows path |
| `tests/test_windows_install.py` | New — unit tests for the 3 pure-logic helpers |

---

### Task 1: Build pipeline — download uv.exe for Windows

**Files:**
- Modify: `.github/workflows/build.yml` (Windows job, before PyInstaller step)
- Modify: `build.bat` (before step 3.5 bundle_source.py)

- [ ] **Step 1: Add uv download to GitHub Actions Windows job**

Open `.github/workflows/build.yml`. Find the Windows job's `- name: Bundle hermes-agent source` step. Insert a new step **before** it:

```yaml
      - name: Download uv.exe for bundling
        shell: pwsh
        run: |
          $ErrorActionPreference = "Stop"
          # Download latest uv for Windows x64
          $url = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
          Write-Host "Downloading uv from $url ..."
          Invoke-WebRequest -Uri $url -OutFile uv_download.zip -UseBasicParsing
          New-Item -ItemType Directory -Force -Path tools | Out-Null
          Expand-Archive -Path uv_download.zip -DestinationPath uv_tmp -Force
          # The zip contains uv.exe at the root
          Copy-Item uv_tmp\uv.exe tools\uv.exe -Force
          Remove-Item uv_download.zip, uv_tmp -Recurse -Force
          $size = [math]::Round((Get-Item tools\uv.exe).Length / 1MB, 1)
          Write-Host "✓ tools\uv.exe ready ($size MB)"
```

- [ ] **Step 2: Add uv download to build.bat (local Windows builds)**

Open `build.bat`. Find the line `echo  → 安装打包依赖（首次约 1-2 分钟）...`. Insert a new section **before** it (after the `pip upgrade` block):

```bat
REM ── 2.5 Prepare uv.exe (bundle into installer) ───────────────────────────
echo  → 准备 uv 安装工具...
if not exist tools mkdir tools
if not exist tools\uv.exe (
    echo    正在从 GitHub 下载 uv.exe...
    powershell -Command ^
        "$url='https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip';" ^
        "Invoke-WebRequest -Uri $url -OutFile uv_dl.zip -UseBasicParsing;" ^
        "Expand-Archive uv_dl.zip -DestinationPath uv_tmp -Force;" ^
        "Copy-Item uv_tmp\uv.exe tools\uv.exe -Force;" ^
        "Remove-Item uv_dl.zip,uv_tmp -Recurse -Force;" ^
        "Write-Host 'uv.exe ready'"
    if not exist tools\uv.exe (
        echo  ❌ uv.exe 下载失败，请检查网络后重试
        pause
        exit /b 1
    )
) else (
    echo    ✓ uv.exe 已存在，跳过下载
)
echo  ✓ uv 准备完成
```

- [ ] **Step 3: Verify by hand (local)**

```bash
# On macOS/Linux — simulate the download manually to verify the URL works
curl -L https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip -o /tmp/uv_test.zip
unzip -l /tmp/uv_test.zip | grep uv.exe
# Expected output should show: uv.exe at root of zip
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/build.yml build.bat
git commit -m "build: download uv.exe for Windows before PyInstaller"
```

---

### Task 2: PyInstaller spec — bundle uv.exe into EXE

**Files:**
- Modify: `hermes_installer.spec` (the `datas` section inside `Analysis`)

- [ ] **Step 1: Read current datas section**

Open `hermes_installer.spec`. Find the `datas=` argument inside `Analysis(...)`. It currently ends with:

```python
    datas=(
        _webui_datas
        # Bundle zip is optional: present → offline install; absent → git clone at runtime
        + ([("hermes_agent_bundle.zip", ".")] if Path("hermes_agent_bundle.zip").exists() else [])
    ),
```

- [ ] **Step 2: Add tools/uv.exe to datas**

Replace that block with:

```python
    datas=(
        _webui_datas
        # Bundle zip is optional: present → offline install; absent → git clone at runtime
        + ([("hermes_agent_bundle.zip", ".")] if Path("hermes_agent_bundle.zip").exists() else [])
        # uv.exe: Windows-only install tool, bundled so users don't need internet for uv itself
        + ([("tools/uv.exe", "tools")] if IS_WIN and Path("tools/uv.exe").exists() else [])
    ),
```

- [ ] **Step 3: Verify spec parses correctly**

```bash
cd /Users/ff/hermes-installer
python -c "
from pathlib import Path
import sys
IS_WIN = False  # simulate non-Windows
exec(open('hermes_installer.spec').read().split('a = Analysis')[0])
print('spec header OK')
"
# Expected: spec header OK  (no syntax errors)
```

- [ ] **Step 4: Commit**

```bash
git add hermes_installer.spec
git commit -m "build(spec): bundle tools/uv.exe into Windows EXE"
```

---

### Task 3: Helper functions — `_is_agent_installed`, `_find_system_python`, `_run_uv`

**Files:**
- Modify: `main.py` — insert 3 new functions in the "Crash reporting helpers" region (after `_check_webview2_windows`, before `_open_native_window`)
- Create: `tests/test_windows_install.py`

- [ ] **Step 1: Create test file**

```bash
mkdir -p /Users/ff/hermes-installer/tests
```

Create `/Users/ff/hermes-installer/tests/test_windows_install.py`:

```python
"""
Unit tests for Windows-specific install helpers in main.py.

These run on all platforms (macOS/Linux in CI). subprocess calls are mocked
so no actual installation happens during tests.
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Add repo root to path so we can import from main
sys.path.insert(0, str(Path(__file__).parent.parent))


# ── _is_agent_installed ───────────────────────────────────────────────────


def test_is_agent_installed_missing_venv(tmp_path):
    """Returns False when venv/Scripts/python.exe doesn't exist."""
    with patch("pathlib.Path.home", return_value=tmp_path):
        from main import _is_agent_installed
        assert _is_agent_installed() is False


def test_is_agent_installed_import_fails(tmp_path):
    """Returns False when venv exists but run_agent import fails."""
    venv_py = tmp_path / ".hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()

    with patch("pathlib.Path.home", return_value=tmp_path), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr=b"ModuleNotFoundError: run_agent")
        from main import _is_agent_installed
        assert _is_agent_installed() is False
        mock_run.assert_called_once()


def test_is_agent_installed_ok(tmp_path):
    """Returns True when venv exists and run_agent import succeeds."""
    venv_py = tmp_path / ".hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()

    with patch("pathlib.Path.home", return_value=tmp_path), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        from main import _is_agent_installed
        assert _is_agent_installed() is True


def test_is_agent_installed_timeout(tmp_path):
    """Returns False (not raises) when subprocess times out."""
    venv_py = tmp_path / ".hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()

    with patch("pathlib.Path.home", return_value=tmp_path), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired("python", 10)):
        from main import _is_agent_installed
        assert _is_agent_installed() is False


# ── _find_system_python ───────────────────────────────────────────────────


def test_find_system_python_found(monkeypatch):
    """Returns path when a Python ≥3.11 is on PATH."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/python3.13" if name == "python3.13" else None)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        from main import _find_system_python
        result = _find_system_python()
    assert result == "/usr/bin/python3.13"


def test_find_system_python_old_version(monkeypatch):
    """Returns None when only Python <3.11 is available."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/python3" if name == "python3" else None)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)  # version check fails
        from main import _find_system_python
        result = _find_system_python()
    assert result is None


def test_find_system_python_none_found(monkeypatch):
    """Returns None when no Python is on PATH."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    from main import _find_system_python
    assert _find_system_python() is None


# ── _run_uv ───────────────────────────────────────────────────────────────


def test_run_uv_success(tmp_path, capsys):
    """Streams output and returns on success."""
    uv_exe = tmp_path / "uv.exe"
    uv_exe.touch()

    mock_proc = MagicMock()
    mock_proc.stdout = iter([b"Resolved 15 packages\n", b"Installed 15 packages\n"])
    mock_proc.wait.return_value = None
    mock_proc.returncode = 0

    with patch("subprocess.Popen", return_value=mock_proc):
        from main import _run_uv
        _run_uv(uv_exe, ["pip", "install", "-e", "."], error_prefix="install failed")

    captured = capsys.readouterr()
    assert "Resolved 15 packages" in captured.out
    assert "Installed 15 packages" in captured.out


def test_run_uv_failure_raises(tmp_path):
    """Raises RuntimeError with last output lines when uv exits non-zero."""
    uv_exe = tmp_path / "uv.exe"
    uv_exe.touch()

    mock_proc = MagicMock()
    mock_proc.stdout = iter([b"error: network unreachable\n"])
    mock_proc.wait.return_value = None
    mock_proc.returncode = 1

    with patch("subprocess.Popen", return_value=mock_proc):
        from main import _run_uv
        with pytest.raises(RuntimeError, match="network unreachable"):
            _run_uv(uv_exe, ["pip", "install", "."], error_prefix="install failed")
```

- [ ] **Step 2: Run tests — expect import errors (functions don't exist yet)**

```bash
cd /Users/ff/hermes-installer
pip install pytest --quiet 2>/dev/null || true
python -m pytest tests/test_windows_install.py -v 2>&1 | head -30
# Expected: ImportError or AttributeError — _is_agent_installed not defined yet
```

- [ ] **Step 3: Add the three helper functions to main.py**

Open `main.py`. Find the line `# ══ Native window — pywebview` (around line 372). Insert the following block **immediately before** it:

```python
# ══════════════════════════════════════════════════════════════════════════
# Windows install helpers
# ══════════════════════════════════════════════════════════════════════════

def _is_agent_installed() -> bool:
    """Return True if the hermes-agent venv exists and run_agent is importable.

    Windows-only check. Fast (<1 s) — runs on every startup to decide
    whether to show the install wizard.
    """
    venv_python = (
        Path.home() / ".hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe"
    )
    if not venv_python.exists():
        log.info("agent not installed: venv python not found at %s", venv_python)
        return False
    agent_dir = Path.home() / ".hermes" / "hermes-agent"
    try:
        result = subprocess.run(
            [str(venv_python), "-c", "import run_agent; print('ok')"],
            capture_output=True,
            timeout=10,
            env={**os.environ, "PYTHONPATH": str(agent_dir)},
        )
        if result.returncode == 0:
            log.info("agent installed and importable at %s", venv_python)
            return True
        log.info(
            "agent check failed (rc=%s): %s",
            result.returncode,
            result.stderr.decode("utf-8", errors="replace")[:200],
        )
        return False
    except Exception as exc:
        log.info("agent check exception: %s", exc)
        return False


def _find_system_python() -> "str | None":
    """Find a system Python ≥3.11 on Windows PATH.

    Returns the executable path, or None if not found. Used as a hint
    to uv so it doesn't need to download Python from the internet.
    """
    for name in ("python3.13", "python3.12", "python3.11", "python3", "python"):
        found = shutil.which(name)
        if not found:
            continue
        try:
            result = subprocess.run(
                [found, "-c",
                 "import sys; v=sys.version_info; exit(0 if v>=(3,11) else 1)"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                log.info("found system Python ≥3.11: %s", found)
                return found
        except Exception:
            continue
    log.info("no system Python ≥3.11 found — uv will manage its own Python")
    return None


def _run_uv(uv_exe: Path, args: "list[str]", error_prefix: str = "uv 命令失败") -> None:
    """Run a uv command, streaming output to console + log.

    Raises RuntimeError (with last 10 output lines) on non-zero exit.
    """
    cmd = [str(uv_exe)] + args
    log.info("Running uv: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "UV_NO_PROGRESS": "1", "PYTHONUTF8": "1"},
    )
    output_lines: list[str] = []
    for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip()
        print(f"    {line}", flush=True)
        log.info("[uv] %s", line)
        output_lines.append(line)
    proc.wait()
    if proc.returncode != 0:
        tail = "\n".join(output_lines[-10:])
        raise RuntimeError(f"{error_prefix} (exit {proc.returncode}):\n{tail}")
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd /Users/ff/hermes-installer
python -m pytest tests/test_windows_install.py -v
# Expected: all 10 tests PASS
```

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_windows_install.py
git commit -m "feat(win): add _is_agent_installed, _find_system_python, _run_uv helpers"
```

---

### Task 4: Windows installer — `_windows_install_agent` + `_start_webui_server_windows`

**Files:**
- Modify: `main.py` — add 2 more functions immediately after `_run_uv`

- [ ] **Step 1: Add `_windows_install_agent` to main.py**

Immediately after the `_run_uv` function (still in the Windows install helpers block), add:

```python
def _windows_install_agent() -> None:
    """First-run Windows setup: extract bundle → create venv → pip install.

    Prints step-by-step progress to the console window (console=True in spec).
    All output is also written to hermes-startup.log via _run_uv.
    Raises RuntimeError with a user-readable message on any failure.
    """
    agent_dir = Path.home() / ".hermes" / "hermes-agent"
    venv_dir = agent_dir / "venv"

    # ── Locate hermes_agent_bundle.zip ────────────────────────────────────
    bundle_zip = BASE_DIR / "hermes_agent_bundle.zip"
    if not bundle_zip.exists():
        raise RuntimeError(
            f"找不到安装包：{bundle_zip}\n"
            "请重新下载最新版 Hermes Installer。"
        )

    # ── Locate uv.exe ─────────────────────────────────────────────────────
    uv_exe = BASE_DIR / "tools" / "uv.exe"
    if not uv_exe.exists():
        uv_sys = shutil.which("uv")
        if uv_sys:
            uv_exe = Path(uv_sys)
            log.info("Using system uv: %s", uv_exe)
        else:
            raise RuntimeError(
                "找不到 uv 安装工具。\n"
                "请下载最新版 Hermes Installer（已内置 uv）。\n"
                "或访问 https://github.com/astral-sh/uv/releases 手动安装 uv。"
            )

    # ── Step 1: Extract bundle ─────────────────────────────────────────────
    print("\n[1/3] 正在解压 hermes-agent 源码...", flush=True)
    log.info("Extracting %s → %s", bundle_zip, agent_dir)
    if agent_dir.exists():
        log.info("Removing previous (possibly incomplete) agent dir: %s", agent_dir)
        shutil.rmtree(agent_dir, ignore_errors=True)
    agent_dir.mkdir(parents=True, exist_ok=True)
    import zipfile as _zipfile
    with _zipfile.ZipFile(bundle_zip) as zf:
        zf.extractall(agent_dir)
    log.info("Extraction complete (%d files)", sum(1 for _ in agent_dir.rglob("*")))
    print("      ✓ 解压完成", flush=True)

    # ── Step 2: Create venv ────────────────────────────────────────────────
    print("[2/3] 正在创建 Python 虚拟环境...", flush=True)
    py_hint = _find_system_python()
    py_arg = py_hint if py_hint else "3.11"
    log.info("Creating venv at %s, python arg: %s", venv_dir, py_arg)
    _run_uv(uv_exe, ["venv", str(venv_dir), "--python", py_arg],
            error_prefix="创建虚拟环境失败")
    print("      ✓ 虚拟环境创建完成", flush=True)

    # ── Step 3: Install dependencies ──────────────────────────────────────
    print("[3/3] 正在安装依赖（使用清华镜像，约 1-3 分钟请耐心等待）...", flush=True)
    venv_python_path = venv_dir / "Scripts" / "python.exe"
    _run_uv(uv_exe, [
        "pip", "install",
        "-e", str(agent_dir),
        "--python", str(venv_python_path),
        "--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple/",
        "--extra-index-url", "https://pypi.org/simple/",
    ], error_prefix="依赖安装失败")
    print("\n      ✓ 安装完成！Hermes 即将启动...\n", flush=True)
    log.info("Windows agent install complete — venv at %s", venv_dir)


def _start_webui_server_windows(port: int, host: str) -> subprocess.Popen:
    """Start server.py directly using the hermes-agent venv Python.

    Bypasses bootstrap.py (which blocks native Windows).
    Logs server stdout/stderr to %APPDATA%/Hermes/webui-server.log.
    Returns the Popen object; caller uses proc.pid for cleanup tracking.
    Raises RuntimeError if venv python or server.py is missing.
    """
    agent_dir = Path.home() / ".hermes" / "hermes-agent"
    venv_python = agent_dir / "venv" / "Scripts" / "python.exe"
    server_py = WEBUI_DIR / "server.py"

    if not venv_python.exists():
        raise RuntimeError(
            f"venv Python 未找到：{venv_python}\n"
            "请删除 ~/.hermes/hermes-agent/ 后重启 Hermes 重新安装。"
        )
    if not server_py.exists():
        raise RuntimeError(
            f"server.py 未找到：{server_py}\n"
            "请重新下载 Hermes Installer。"
        )

    env = os.environ.copy()
    env["HERMES_WEBUI_PORT"] = str(port)
    env["HERMES_WEBUI_HOST"] = host
    env["HERMES_WEBUI_AGENT_DIR"] = str(agent_dir)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"

    server_log_path = _LOG_DIR / "webui-server.log"
    log.info(
        "Starting server.py: python=%s server=%s cwd=%s log=%s",
        venv_python, server_py, agent_dir, server_log_path,
    )
    # Open in append mode so logs survive across restarts
    server_log_fh = open(server_log_path, "ab")  # noqa: SIM115 (kept open for subprocess lifetime)
    proc = subprocess.Popen(
        [str(venv_python), str(server_py)],
        cwd=str(agent_dir),
        env=env,
        stdout=server_log_fh,
        stderr=subprocess.STDOUT,
    )
    log.info("server.py PID=%s — log at %s", proc.pid, server_log_path)
    return proc
```

- [ ] **Step 2: Run existing tests to make sure nothing broke**

```bash
cd /Users/ff/hermes-installer
python -m pytest tests/test_windows_install.py -v
# Expected: all 10 tests still PASS
```

- [ ] **Step 3: Smoke-test the new functions parse correctly**

```bash
python -c "
import sys; sys.path.insert(0, '.')
# Just import — verify no syntax errors
import main
print('_windows_install_agent:', main._windows_install_agent)
print('_start_webui_server_windows:', main._start_webui_server_windows)
print('OK')
"
# Expected: OK
```

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(win): add _windows_install_agent and _start_webui_server_windows"
```

---

### Task 5: Wire into `main()` — restructure Windows launch path

**Files:**
- Modify: `main.py` — the `main()` function body (lines ~632–735)

- [ ] **Step 1: Replace the "Launch bootstrap.py" section with a platform-conditional block**

In `main()`, find this entire block (from `# ── Launch bootstrap.py` through `server_pids = _pids_on_port(port) if ready else []`):

```python
    # ── Launch bootstrap.py ──────────────────────────────────────────────
    # bootstrap.py handles everything:
    #   1. Detect hermes-agent installation
    #   2. Install hermes-agent if missing (git clone + venv + pip install)
    #   3. Create WebUI venv + install deps if needed
    #   4. Start server.py on the target port
    #   5. Health-check, then exit (server.py keeps running detached)
    #
    # We run bootstrap.py in a daemon thread so the main thread can show a
    # loading state in the window while bootstrap does its work.

    python_exe = _find_bootstrap_python()

    if not BOOTSTRAP_PY.exists():
        _alert("Hermes Installer",
               f"找不到 WebUI 启动脚本。\n路径：{BOOTSTRAP_PY}\n"
               f"请确认 webui/ 目录与 main.py 在同一文件夹下。")
        sys.exit(1)

    env = os.environ.copy()
    env["HERMES_WEBUI_PORT"] = str(port)
    env["HERMES_WEBUI_HOST"] = host
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"

    log.info("Launching bootstrap.py: %s %s", python_exe, BOOTSTRAP_PY)

    # Launch as detached child — bootstrap.py spawns server.py and exits,
    # server.py continues running
    try:
        proc = subprocess.Popen(
            [python_exe, str(BOOTSTRAP_PY), str(port), "--host", host],
            cwd=str(WEBUI_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(sys.platform != "win32"),
        )
    except FileNotFoundError:
        _alert("Hermes Installer",
               f"找不到 Python 解释器。\n尝试的路径：{python_exe}\n"
               f"请安装 Python 3.10+ 后重试。")
        sys.exit(1)
    except Exception as exc:
        _alert("Hermes Installer", f"无法启动 WebUI：{exc}")
        sys.exit(1)

    log.info("bootstrap.py PID=%s — waiting for WebUI on port %d (timeout=%ds)",
             proc.pid, port, WEBUI_STARTUP_TIMEOUT)

    # Wait for the WebUI server to be ready
    # bootstrap.py installs hermes-agent + deps first, so this can take a while
    ready = _wait_for_server(port, timeout=WEBUI_STARTUP_TIMEOUT)
    if not ready:
        # Server might still be starting — give it another 30s and try anyway
        log.warning("Port %d not ready after %ds, trying anyway in 30s",
                    port, WEBUI_STARTUP_TIMEOUT)
        time.sleep(30)
        ready = _wait_for_server(port, timeout=10)

    # ── Capture server.py PIDs so we can clean them up on exit ──────────────
    # bootstrap.py spawns server.py detached and then exits. Without explicit
    # cleanup, server.py would survive window close and accumulate as orphans
    # on every launch — eventually holding the port and forcing the user
    # through the conflict dialog every time.
    server_pids = _pids_on_port(port) if ready else []
    log.info("WebUI server PIDs to terminate on exit: %s", server_pids)
```

Replace it with:

```python
    # ── Launch WebUI server ──────────────────────────────────────────────
    if sys.platform == "win32":
        # ── Windows: install if needed, start server.py directly ────────
        # bootstrap.py has ensure_supported_platform() that blocks Windows.
        # We handle install + launch inline instead.
        if not _is_agent_installed():
            log.info("First run: hermes-agent not installed — starting Windows setup")
            print("\n" + "=" * 56, flush=True)
            print("   Hermes 首次启动 — 正在安装必要组件", flush=True)
            print("   日志保存在：" + str(_LOG_PATH), flush=True)
            print("=" * 56 + "\n", flush=True)
            try:
                _windows_install_agent()
            except Exception as exc:
                import traceback as _tb
                tb = _tb.format_exc()
                log.exception("Windows install failed: %s", exc)
                _send_crash_report("windows_install_failed", str(exc), {"traceback": tb[:2000]})
                _alert(
                    "Hermes 安装失败",
                    f"首次安装 hermes-agent 时出错：\n\n{exc}\n\n"
                    f"请检查网络连接后重试。\n"
                    f"详细日志：{_LOG_PATH}",
                )
                sys.exit(1)

        log.info("Windows: starting server.py directly (bypassing bootstrap.py)")
        try:
            _win_server_proc = _start_webui_server_windows(port, host)
        except Exception as exc:
            log.exception("Failed to start server.py on Windows: %s", exc)
            _alert(
                "Hermes 启动失败",
                f"无法启动 WebUI 服务：\n\n{exc}\n\n"
                f"日志：{_LOG_PATH}",
            )
            sys.exit(1)

        server_pids = [_win_server_proc.pid]
        log.info("Windows server PID=%s — waiting for WebUI on port %d (timeout=%ds)",
                 _win_server_proc.pid, port, WEBUI_STARTUP_TIMEOUT)
        ready = _wait_for_server(port, timeout=WEBUI_STARTUP_TIMEOUT)

    else:
        # ── macOS / Linux: existing bootstrap.py path (unchanged) ────────
        python_exe = _find_bootstrap_python()

        if not BOOTSTRAP_PY.exists():
            _alert("Hermes Installer",
                   f"找不到 WebUI 启动脚本。\n路径：{BOOTSTRAP_PY}\n"
                   f"请确认 webui/ 目录与 main.py 在同一文件夹下。")
            sys.exit(1)

        env = os.environ.copy()
        env["HERMES_WEBUI_PORT"] = str(port)
        env["HERMES_WEBUI_HOST"] = host
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"

        log.info("Launching bootstrap.py: %s %s", python_exe, BOOTSTRAP_PY)

        try:
            proc = subprocess.Popen(
                [python_exe, str(BOOTSTRAP_PY), str(port), "--host", host],
                cwd=str(WEBUI_DIR),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            _alert("Hermes Installer",
                   f"找不到 Python 解释器。\n尝试的路径：{python_exe}\n"
                   f"请安装 Python 3.10+ 后重试。")
            sys.exit(1)
        except Exception as exc:
            _alert("Hermes Installer", f"无法启动 WebUI：{exc}")
            sys.exit(1)

        log.info("bootstrap.py PID=%s — waiting for WebUI on port %d (timeout=%ds)",
                 proc.pid, port, WEBUI_STARTUP_TIMEOUT)

        ready = _wait_for_server(port, timeout=WEBUI_STARTUP_TIMEOUT)
        if not ready:
            log.warning("Port %d not ready after %ds, trying anyway in 30s",
                        port, WEBUI_STARTUP_TIMEOUT)
            time.sleep(30)
            ready = _wait_for_server(port, timeout=10)

        # bootstrap.py spawns server.py detached then exits — find it by port
        server_pids = _pids_on_port(port) if ready else []

    log.info("WebUI server PIDs to terminate on exit: %s", server_pids)
```

- [ ] **Step 2: Verify Python syntax is valid**

```bash
cd /Users/ff/hermes-installer
python -m py_compile main.py && echo "syntax OK"
# Expected: syntax OK
```

- [ ] **Step 3: Run all tests**

```bash
python -m pytest tests/test_windows_install.py -v
# Expected: all 10 tests PASS
```

- [ ] **Step 4: Smoke-test main module imports correctly**

```bash
python -c "
import main
# Verify the new functions are all present
assert hasattr(main, '_is_agent_installed')
assert hasattr(main, '_find_system_python')
assert hasattr(main, '_run_uv')
assert hasattr(main, '_windows_install_agent')
assert hasattr(main, '_start_webui_server_windows')
print('All functions present — OK')
"
# Expected: All functions present — OK
```

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(win): wire Windows install+launch path into main()

First-run: extracts hermes_agent_bundle.zip, creates venv via bundled
uv.exe, installs deps from Tsinghua mirror. Subsequent runs: starts
server.py directly, bypassing bootstrap.py's platform check.
macOS/Linux path unchanged."
```

---

### Task 6: Push and verify CI builds successfully

**Files:**
- No code changes — CI validation only

- [ ] **Step 1: Push everything to origin**

```bash
cd /Users/ff/hermes-installer
git push origin main
```

- [ ] **Step 2: Watch the GitHub Actions Windows build**

```bash
gh run list --limit 5
# Note the run ID for the latest run triggered by the push
gh run watch <run-id>
# Expected: macOS job PASS, Windows job PASS
# If Windows fails, check logs with:
gh run view <run-id> --log | grep -A 20 "Download uv"
```

- [ ] **Step 3: Verify uv.exe made it into the Windows artifact**

After the build passes, download the artifact:
```bash
gh run download <run-id> --name Hermes-Installer-Windows --dir /tmp/hermes-win-test
ls -lh /tmp/hermes-win-test/
# Expected: Hermes-Installer-Windows.zip present
```

On a Windows machine (or VM), extract and run `Hermes Installer.exe`. Expected console output on first run:
```
========================================================
   Hermes 首次启动 — 正在安装必要组件
   日志保存在：C:\Users\<user>\AppData\Roaming\Hermes\hermes-startup.log
========================================================

[1/3] 正在解压 hermes-agent 源码...
      ✓ 解压完成
[2/3] 正在创建 Python 虚拟环境...
    Using CPython 3.13.x
    Creating virtual environment at: ...
      ✓ 虚拟环境创建完成
[3/3] 正在安装依赖（使用清华镜像，约 1-3 分钟请耐心等待）...
    Resolved 42 packages in ...
    Installed 42 packages in ...
      ✓ 安装完成！Hermes 即将启动...
```

Then the WebUI window opens with the Hermes UI loading.

- [ ] **Step 4: Verify second launch skips install**

Close and re-open the EXE. The install steps should NOT appear. The window should open within ~10 seconds.

- [ ] **Step 5: Verify log files exist**

On the Windows machine:
```
C:\Users\<user>\AppData\Roaming\Hermes\hermes-startup.log   ← main.py + uv output
C:\Users\<user>\AppData\Roaming\Hermes\webui-server.log     ← server.py stdout/stderr
```

Both files should contain useful output for debugging.

- [ ] **Step 6: Tag a release if all looks good**

```bash
git tag v$(date +%Y.%m.%d) -m "Windows native install via uv + Tsinghua mirror"
git push origin --tags
# This triggers the release upload in build.yml
```
