# macOS Offline First-Install (fix fresh-Mac hang) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make first-time install on a clean Mac succeed reliably by installing hermes-agent from the bundled `hermes_agent_bundle.zip` + a bundled `uv` binary (mirroring the proven Windows path), instead of `curl github/install.sh | bash` which hangs on git / Xcode-CLT dialog / github reachability. Also surface install progress + errors (today they go to `/dev/null`).

**Architecture:** Add a macOS branch to `main.py` that, on first run (no healthy agent venv), runs a new `_macos_install_agent()` — extract bundle → `uv venv` (uv-managed Python 3.11) → `uv pip install -e agent` (CN-mirror first) → patch provider — exactly like `_windows_install_agent()`. CI builds a macOS `uv` binary into `tools/uv` and the spec bundles it. `bootstrap.py`'s network installer remains only as a last-resort fallback when the bundle/uv are absent. main.py's macOS subprocess launch stops swallowing stdout/stderr into a log file so failures are diagnosable.

**Tech Stack:** Python (PyInstaller frozen app), `uv` (astral), bash, GitHub Actions (macos-latest runner), zipfile.

---

## File Structure

| File | Responsibility |
|---|---|
| `main.py` | Add `_macos_install_agent()` (extract bundle + uv venv + uv pip install + patch); add `_is_agent_installed_posix()` health check; route the macOS branch of `main()` through it before launching bootstrap; stop DEVNULL-ing the macOS bootstrap subprocess. |
| `.github/workflows/build.yml` (macos job) | Download a pinned `uv` macOS binary into `tools/uv` before PyInstaller so the spec bundles it. |
| `hermes_installer.spec` | Bundle `tools/uv` (macOS/Linux) the same way `tools/uv.exe` is bundled for Windows. |
| `webui/tests/test_macos_install.py` (new) | Unit tests for the bundle-extract + uv-arg-construction helpers (pure functions, no network). |

**Design note:** keep `_macos_install_agent()` a sibling of `_windows_install_agent()` in `main.py` (they share `_run_uv`, `_clean_subprocess_env`, the patch step). Do NOT refactor the Windows function — just mirror it. Extract the two genuinely-shared, pure pieces (uv pip-install arg list builder; agent-venv path resolver) so they can be unit-tested without spawning uv.

---

## Task 1: Extract shared pure helpers (testable, no behavior change)

**Files:**
- Modify: `main.py` (add two module-level helpers near `_run_uv`, ~line 733)
- Test: `webui/tests/test_macos_install.py`

- [ ] **Step 1: Write the failing test**

```python
# webui/tests/test_macos_install.py
"""Unit tests for macOS offline-install pure helpers in main.py.

main.py lives at the repo root and imports pywebview at module load, which
isn't available in the test venv. So we load ONLY the two pure helper
functions by exec'ing main.py's source in a stubbed module namespace —
no import side effects, no pywebview needed.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_MAIN = Path(__file__).resolve().parent.parent.parent / "main.py"


def _load_pure_helpers():
    """Return a namespace containing only the named pure helper functions
    from main.py, without executing its module-level side effects."""
    src = _MAIN.read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted = {"_uv_pip_install_args", "_agent_venv_python"}
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert len(funcs) == len(wanted), f"missing helpers: {wanted - {f.name for f in funcs}}"
    module = ast.Module(body=funcs, type_ignores=[])
    ns: dict = {"Path": Path, "sys": sys}
    exec(compile(module, str(_MAIN), "exec"), ns)
    return ns


def test_uv_pip_install_args_uses_cn_mirror_first():
    ns = _load_pure_helpers()
    args = ns["_uv_pip_install_args"]("/agent", "/agent/venv/bin/python")
    # editable install of the agent dir
    assert "-e" in args and "/agent" in args
    # CN mirror is the PRIMARY index (the hang fix relies on this)
    i = args.index("--index-url")
    assert args[i + 1] == "https://mirrors.aliyun.com/pypi/simple/"
    # pypi.org present only as a fallback extra-index
    assert "https://pypi.org/simple/" in args
    # first-index strategy (avoids cross-mirror 403 on wheels)
    assert "first-index" in args


def test_agent_venv_python_posix_layout():
    ns = _load_pure_helpers()
    p = ns["_agent_venv_python"](Path("/home/x/.hermes/hermes-agent"), is_windows=False)
    assert p == Path("/home/x/.hermes/hermes-agent/venv/bin/python")


def test_agent_venv_python_windows_layout():
    ns = _load_pure_helpers()
    p = ns["_agent_venv_python"](Path("C:/u/.hermes/hermes-agent"), is_windows=True)
    assert p == Path("C:/u/.hermes/hermes-agent/venv/Scripts/python.exe")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ff/hermes-installer && .build_venv/bin/python -m pytest webui/tests/test_macos_install.py -q`
Expected: FAIL — `AssertionError: missing helpers: {'_uv_pip_install_args', '_agent_venv_python'}`

- [ ] **Step 3: Add the helpers in `main.py`** (insert immediately above `def _run_uv` at ~line 733)

```python
def _agent_venv_python(agent_dir: "Path", *, is_windows: bool) -> "Path":
    """Path to the hermes-agent venv's Python for the given OS layout."""
    if is_windows:
        return agent_dir / "venv" / "Scripts" / "python.exe"
    return agent_dir / "venv" / "bin" / "python"


def _uv_pip_install_args(agent_dir: str, venv_python: str) -> "list[str]":
    """uv args to install the agent editable, CN-mirror-first.

    Identical mirror policy to the Windows path (_windows_install_agent):
    Aliyun primary (reliable wheel downloads), USTC/Huawei/PyPI fallbacks,
    first-index strategy to avoid cross-mirror 403 on .whl downloads.
    """
    return [
        "pip", "install",
        "-e", agent_dir,
        "--python", venv_python,
        "--index-strategy", "first-index",
        "--index-url", "https://mirrors.aliyun.com/pypi/simple/",
        "--extra-index-url", "https://mirrors.ustc.edu.cn/pypi/simple/",
        "--extra-index-url", "https://repo.huaweicloud.com/repository/pypi/simple/",
        "--extra-index-url", "https://pypi.org/simple/",
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ff/hermes-installer && .build_venv/bin/python -m pytest webui/tests/test_macos_install.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add main.py webui/tests/test_macos_install.py
git commit -m "refactor(install): extract testable uv-args + venv-path helpers"
```

---

## Task 2: macOS bundle-install function

**Files:**
- Modify: `main.py` (add `_macos_install_agent()` + `_is_agent_installed_posix()` near `_windows_install_agent`, ~line 758)

- [ ] **Step 1: Add `_is_agent_installed_posix()`** (insert above `_macos_install_agent`)

```python
def _is_agent_installed_posix() -> bool:
    """True iff the hermes-agent venv exists and can import run_agent.

    POSIX twin of _is_agent_installed() (which is Windows-pathed). Used by
    the macOS first-run branch to decide install-vs-launch.
    """
    venv_python = _agent_venv_python(
        Path.home() / ".hermes" / "hermes-agent", is_windows=False
    )
    if not venv_python.exists():
        log.info("agent not installed: venv python not found at %s", venv_python)
        return False
    try:
        probe = subprocess.run(
            [str(venv_python), "-c", "import run_agent"],
            capture_output=True, text=True, timeout=30,
            env=_clean_subprocess_env(),
        )
    except Exception as exc:
        log.info("agent health probe failed to run: %s", exc)
        return False
    if probe.returncode == 0:
        log.info("agent installed and healthy at %s", venv_python)
        return True
    log.info("agent venv unhealthy (rc=%s): %s", probe.returncode, probe.stderr[:300])
    return False
```

- [ ] **Step 2: Add `_macos_install_agent()`** (insert directly below it)

```python
def _macos_install_agent() -> None:
    """First-run macOS/Linux setup: extract bundle -> uv venv -> uv pip install
    -> patch. Mirrors _windows_install_agent so a clean Mac never needs git,
    Xcode CLT, or a github clone (the things that hang the curl|bash path).

    Prints progress to stdout (captured to the bootstrap log by main()).
    Raises RuntimeError with a user-readable message on any failure.
    """
    agent_dir = Path.home() / ".hermes" / "hermes-agent"

    bundle_zip = BASE_DIR / "hermes_agent_bundle.zip"
    if not bundle_zip.exists():
        raise RuntimeError(
            f"找不到安装包：{bundle_zip}\n请重新下载最新版 Hermes Installer。"
        )

    # uv: bundled at tools/uv (no extension on POSIX); fall back to system uv.
    uv_exe = BASE_DIR / "tools" / "uv"
    if not uv_exe.exists():
        uv_sys = shutil.which("uv")
        if uv_sys:
            uv_exe = Path(uv_sys)
            log.info("Using system uv: %s", uv_exe)
        else:
            raise RuntimeError(
                "找不到 uv 安装工具。请下载最新版 Hermes Installer（已内置 uv）。"
            )
    try:
        os.chmod(uv_exe, 0o755)  # bundled binary may lose +x through zip/copy
    except OSError:
        pass

    print("\n[1/3] 正在解压 hermes-agent 源码...", flush=True)
    log.info("Extracting %s -> %s", bundle_zip, agent_dir)
    if agent_dir.exists():
        shutil.rmtree(agent_dir, ignore_errors=True)
    agent_dir.mkdir(parents=True, exist_ok=True)
    import zipfile as _zipfile
    with _zipfile.ZipFile(bundle_zip) as zf:
        zf.extractall(agent_dir)
    print("      ✓ 解压完成", flush=True)

    print("[2/3] 正在创建 Python 虚拟环境...", flush=True)
    venv_dir = agent_dir / "venv"
    log.info("Creating venv at %s (uv-managed python 3.11)", venv_dir)
    _run_uv(uv_exe, ["venv", str(venv_dir),
                     "--python", "3.11",
                     "--python-preference", "only-managed"],
            error_prefix="创建虚拟环境失败")
    print("      ✓ 虚拟环境创建完成", flush=True)

    print("[3/3] 正在安装依赖（多镜像，约 1-3 分钟）...", flush=True)
    venv_python = _agent_venv_python(agent_dir, is_windows=False)
    _run_uv(uv_exe, _uv_pip_install_args(str(agent_dir), str(venv_python)),
            error_prefix="依赖安装失败")

    print("[3.5/3] 正在注入 neowow-coding-plan provider...", flush=True)
    patch_script = BASE_DIR / "docker" / "patch_hermes_agent.py"
    if patch_script.exists():
        try:
            subprocess.run(
                [str(venv_python), str(patch_script), "--agent-dir", str(agent_dir)],
                capture_output=True, encoding="utf-8", errors="replace", timeout=60,
                env=_clean_subprocess_env(extra={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}),
                check=True,
            )
            print("      ✓ provider 注入完成", flush=True)
        except Exception as exc:
            log.exception("patch_hermes_agent failed: %s", exc)
            raise RuntimeError(f"为 hermes-agent 注入 provider 失败：\n{exc}") from exc
    else:
        log.warning("patch script not found at %s — skipping", patch_script)

    print("\n      ✓ 安装完成！Hermes 即将启动...\n", flush=True)
    log.info("macOS agent install complete — venv at %s", venv_dir)
```

- [ ] **Step 3: Verify it imports (syntax/symbol check)**

Run: `cd /Users/ff/hermes-installer && python3 -c "import ast; ast.parse(open('main.py').read()); print('main.py OK')"`
Expected: `main.py OK`

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(install): _macos_install_agent — offline bundle install (no git/CLT)"
```

---

## Task 3: Route the macOS branch through the offline installer + stop swallowing output

**Files:**
- Modify: `main.py` `main()` macOS/Linux `else` branch (~line 1460-1500)

- [ ] **Step 1: Replace the macOS launch block.** Find this exact block (currently ~line 1460):

```python
    else:
        # ── macOS / Linux: existing bootstrap.py path (unchanged) ────────
        python_exe = _find_bootstrap_python()
```

Insert, immediately after that comment line and before `python_exe = _find_bootstrap_python()`:

```python
        # ── First-run offline install (macOS/Linux) ──────────────────────
        # On a clean Mac there's no agent venv. Install from the bundled
        # zip + bundled uv instead of bootstrap.py's `curl github | bash`,
        # which hangs on git / the Xcode CLT consent dialog / github
        # reachability. Only attempt when we actually ship a bundle (frozen
        # app); dev runs without a bundle fall through to bootstrap.py.
        _bundle = BASE_DIR / "hermes_agent_bundle.zip"
        if _bundle.exists() and not _is_agent_installed_posix():
            existing = Path.home() / ".hermes" / "hermes-agent" / "venv"
            if existing.exists():
                log.info("agent venv unhealthy — wiping for clean reinstall")
            try:
                _macos_install_agent()
            except Exception as exc:
                log.exception("macOS offline install failed: %s", exc)
                if _crash_reporter is not None:
                    try:
                        _crash_reporter.report(phase="macos_install", error=str(exc),
                                               log_path=str(_LOG_PATH))
                    except Exception:
                        pass
                _alert("Hermes 安装失败",
                       f"首次安装未完成：\n{exc}\n\n日志：\n{_LOG_PATH}")
                sys.exit(1)
```

- [ ] **Step 2: Stop DEVNULL-ing bootstrap output.** In the same `else` branch, find:

```python
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
```

Replace with (write the subprocess output to a log the user — and we — can read):

```python
                stdout=_bootstrap_log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
```

And immediately BEFORE the `proc = subprocess.Popen(` line in that branch, add:

```python
        _bootstrap_log_path = _LOG_DIR / "bootstrap.log"
        _bootstrap_log_fh = open(_bootstrap_log_path, "ab")  # noqa: SIM115 — lives for subprocess
        log.info("bootstrap.py stdout/stderr -> %s", _bootstrap_log_path)
```

- [ ] **Step 3: Syntax check**

Run: `cd /Users/ff/hermes-installer && python3 -c "import ast; ast.parse(open('main.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(install): macOS first-run uses offline installer; log bootstrap output"
```

---

## Task 4: Bundle a macOS `uv` binary in CI + spec

**Files:**
- Modify: `.github/workflows/build.yml` (macos job, before the PyInstaller step ~line 58)
- Modify: `hermes_installer.spec` (datas, near the `tools/uv.exe` line ~87)

- [ ] **Step 1: Add a CI step to fetch uv for macOS.** In `.github/workflows/build.yml`, in the `macos:` job, immediately AFTER `- name: Install deps` (line 52-53) and BEFORE `- name: Bundle hermes-agent source`, insert:

```yaml
      - name: Bundle uv binary (macOS)
        run: |
          set -euo pipefail
          # pip already pulled uv in via setup; locate it and copy into tools/
          # so the spec can bundle it (mirrors Windows tools/uv.exe).
          pip install uv
          UV_BIN="$(python -c 'import shutil,sys; print(shutil.which("uv") or "")')"
          if [ -z "$UV_BIN" ]; then echo "ERROR: uv not found after pip install"; exit 1; fi
          mkdir -p tools
          cp "$UV_BIN" tools/uv
          chmod +x tools/uv
          echo "✓ bundled uv: $(tools/uv --version)"
```

- [ ] **Step 2: Bundle `tools/uv` in the spec.** In `hermes_installer.spec`, find:

```python
        + ([("tools/uv.exe", "tools")] if IS_WIN and Path("tools/uv.exe").exists() else [])
```

Add directly below it:

```python
        # tools/uv: macOS/Linux uv binary (CI's "Bundle uv binary" step copies
        # it here). Lets first-run install create the agent venv + pip install
        # offline from the bundle, with no git / Xcode CLT / github clone.
        + ([("tools/uv", "tools")] if (not IS_WIN) and Path("tools/uv").exists() else [])
```

- [ ] **Step 3: Validate both files**

Run:
```bash
cd /Users/ff/hermes-installer
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/build.yml')); print('yaml OK')"
python3 -c "import ast; ast.parse(open('hermes_installer.spec').read()); print('spec OK')"
```
Expected: `yaml OK` then `spec OK`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/build.yml hermes_installer.spec
git commit -m "ci(macos): bundle a uv binary so first-run install is offline"
```

---

## Task 5: Full verification + PR

- [ ] **Step 1: Run the new unit tests + confirm no syntax regressions**

Run:
```bash
cd /Users/ff/hermes-installer
.build_venv/bin/python -m pytest webui/tests/test_macos_install.py -q
python3 -c "import ast; ast.parse(open('main.py').read()); print('main.py OK')"
```
Expected: `3 passed`, then `main.py OK`

- [ ] **Step 2: Grep-confirm the macOS branch no longer hard-depends on curl|bash as the FIRST path**

Run: `grep -n "_macos_install_agent\|_is_agent_installed_posix\|hermes_agent_bundle" main.py`
Expected: shows the new function defs + the `main()` call site (≥3 hits).

- [ ] **Step 3: Push + open PR**

```bash
git push -u origin feat/macos-offline-install
gh pr create --base main --head feat/macos-offline-install \
  --title "fix(macos): 首次安装离线化(用内置 bundle,去掉 git/Xcode CLT 依赖)" \
  --body "Fresh-Mac first install hung because bootstrap.py ran \`curl github/install.sh | bash\` (needs git + Xcode CLT dialog + github). Now macOS first-run extracts the bundled hermes_agent_bundle.zip + bundled uv and installs offline (CN-mirror PyPI only) — mirroring the proven Windows path. Also logs bootstrap output instead of DEVNULL. CI bundles a macOS uv binary."
```

- [ ] **Step 4: Wait for CI green (the real proof — it builds on macos-latest with the new uv-bundling step)**

Run: `gh pr checks <N>` (poll). Expected: macos pass, windows pass.

---

## Self-Review

**1. Spec coverage** (the two user asks):
- ✅ "用内置 bundle 跳过网络克隆" → Tasks 2+3 (extract bundle, no curl|bash first) + Task 4 (bundle uv so it's truly offline of git/github).
- ✅ "把安装进度/错误显给用户" → Task 3 Step 2 (bootstrap output → `bootstrap.log` not DEVNULL) + `_macos_install_agent` prints progress + `_alert` on failure + crash report.

**2. Placeholder scan:** No TBD/TODO; every code step has complete code. ✅

**3. Type consistency:** `_agent_venv_python(agent_dir, *, is_windows)` and `_uv_pip_install_args(agent_dir, venv_python)` signatures match between Task 1 (def + test) and Task 2 (callers). `_run_uv(uv_exe, args, error_prefix=...)` matches the existing main.py:733 signature. `BASE_DIR`, `_clean_subprocess_env`, `_alert`, `_crash_reporter`, `_LOG_PATH`, `_LOG_DIR`, `log` are all existing main.py globals. ✅

**Known limitation (documented, not a gap):** PyPI wheels (~700MB) are still fetched at first run — that network dependency is intrinsic to a Python app and is served by reliable CN mirrors; the hang we fix is specifically github-clone + git + Xcode-CLT-dialog. If the user is fully offline, install still can't complete (same as Windows). A future enhancement could bundle wheels too, but that's out of scope (YAGNI).
