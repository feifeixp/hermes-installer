# Windows 原生安装流程设计

**日期：** 2026-05-24
**状态：** 已批准

## 问题背景

当前 Windows EXE 在首次启动时无法正常运行：

1. `bootstrap.py` 的 `main()` 第一行调用 `ensure_supported_platform()`，对 native Windows 直接抛出 `RuntimeError`
2. `main.py` 用 `stdout=DEVNULL, stderr=DEVNULL` 启动 bootstrap.py，错误完全不可见
3. `main.py` 等待 port 8787 超时（300秒），然后打开 pywebview 窗口
4. 用户看到"127.0.0.1 拒绝连接"

原始安装依赖 GitHub（`git clone`），国内用户无法访问。`hermes_agent_bundle.zip` 已经捆绑在 EXE 里（源码 72MB），缺的只是 Python wheels。

## 目标

- 首次启动时自动完成 hermes-agent 安装，无需用户手动操作
- 不依赖 GitHub（源码已捆绑）
- 不依赖系统 Python（uv 自动管理 Python 版本）
- 适配国内网络（使用清华 PyPI 镜像）
- 所有安装过程实时显示在控制台窗口，全量写入日志文件
- macOS/Linux 流程**不受任何影响**

## 方案选择

选择**方案 A：uv + 清华 PyPI 镜像**

- uv 是单文件 Rust 二进制（~8MB），在 CI 构建时下载并捆绑进 EXE
- 用户无需下载 uv，无需访问 GitHub
- uv 自动下载并管理 Python 3.11（无需系统 Python）
- pip install 使用清华镜像（`pypi.tuna.tsinghua.edu.cn`），国内速度快且稳定
- EXE 体积增加约 8MB（可接受）

## 架构设计

### 启动流程（Windows，新）

```
EXE 启动
  │
  ├── [现有] WebView2 检查 → 缺失则弹下载链接 + 退出
  │
  ├── [新增] _is_agent_installed()
  │         检查 ~/.hermes/hermes-agent/venv/Scripts/python.exe 存在
  │         且能 `import run_agent`（<1秒）
  │
  ├── [新增 — 首次启动] _windows_install_agent()
  │         步骤 1/3：清理残留目录（若存在）→ 解压 hermes_agent_bundle.zip → ~/.hermes/hermes-agent/
  │         步骤 2/3：uv venv ~/.hermes/hermes-agent/venv --python 3.11
  │         步骤 3/3：uv pip install -e . （清华镜像，约 1-2 分钟）
  │         全程实时输出到控制台 + %APPDATA%\Hermes\hermes-startup.log
  │
  ├── [新增] _start_webui_server_windows(port, host)
  │         直接启动 server.py，绕过 bootstrap.py 平台检查
  │         venv_python WEBUI_DIR/server.py
  │         cwd = ~/.hermes/hermes-agent/
  │         stdout/stderr → %APPDATA%\Hermes\webui-server.log
  │
  └── [现有] 等待 port 8787 → 打开 pywebview 窗口
```

### 启动流程（macOS/Linux）

**与当前完全一致，不修改任何逻辑。**

## 受影响文件

| 文件 | 改动说明 |
|------|---------|
| `main.py` | 新增三个函数：`_is_agent_installed()`、`_windows_install_agent()`、`_start_webui_server_windows()`；`main()` 中 Windows 路径调用新流程 |
| `hermes_installer.spec` | datas 列表新增 `("tools/uv.exe", "tools")`（仅 IS_WIN 且文件存在时） |
| `.github/workflows/build.yml` | Windows job 新增步骤：PyInstaller 前下载 uv.exe 到 `tools/` |
| `build.bat` | 新增步骤：PowerShell 下载 uv.exe 到 `tools/`（本地构建用） |
| `bootstrap.py` | **不修改** |

## 关键实现细节

### uv.exe 捆绑

CI 构建时（GitHub Actions 能访问 GitHub）下载最新 uv for Windows：
```
https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip
```
提取 `uv.exe` 到 `tools/uv.exe`，PyInstaller 将其打包到 `tools/` 子目录。

运行时通过 `sys._MEIPASS / "tools" / "uv.exe"` 访问。

### uv 安装命令

Python 的获取策略（按优先级）：
1. 系统 Python ≥3.11（`shutil.which("python3.11/3.12/3.13/python3/python")`）→ 直接用
2. uv 自动下载 Python（从 GitHub 下载 python-build-standalone，约 30MB，需访问 GitHub）

国内 GitHub 访问可能受限，因此优先尝试系统 Python，避免不必要的下载。

```bash
# 创建 venv（传入已找到的系统 Python 路径，或让 uv 自动管理）
uv venv ~/.hermes/hermes-agent/venv --python <python_path_or_"3.11">

# 安装 hermes-agent 及其核心依赖（清华镜像，国内速度快）
uv pip install -e ~/.hermes/hermes-agent/ \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple/ \
    --extra-index-url https://pypi.org/simple/
```

### 实时输出

uv 命令通过 `subprocess.Popen(stdout=PIPE, stderr=STDOUT)` 启动，主线程逐行读取：
```python
for line in proc.stdout:
    text = line.decode("utf-8", errors="replace").rstrip()
    print(text, flush=True)       # → 控制台窗口（console=True）
    log.info("[uv] %s", text)     # → hermes-startup.log
```

### server.py 直接启动

```python
agent_dir = Path.home() / ".hermes" / "hermes-agent"
venv_python = agent_dir / "venv" / "Scripts" / "python.exe"
env = {
    **os.environ,
    "HERMES_WEBUI_PORT": str(port),
    "HERMES_WEBUI_HOST": host,
    "HERMES_WEBUI_AGENT_DIR": str(agent_dir),
    "PYTHONUNBUFFERED": "1",
    "PYTHONUTF8": "1",
}
proc = subprocess.Popen(
    [str(venv_python), str(WEBUI_DIR / "server.py")],
    cwd=str(agent_dir),
    env=env,
    stdout=open(_LOG_DIR / "webui-server.log", "ab"),
    stderr=subprocess.STDOUT,
)
```

`server.py` 被以脚本方式调用，Python 自动将 `WEBUI_DIR` 加入 `sys.path`，webui 内部的 `from api.xxx import` 正常工作。`HERMES_WEBUI_AGENT_DIR` 由 `server.py` 读取后加入 `sys.path`，使 `run_agent` 可导入。

### 错误处理

| 失败点 | 处理 |
|--------|------|
| `hermes_agent_bundle.zip` 不存在 | `_alert()` 弹窗 + `_send_crash_report()` + `sys.exit(1)` |
| uv.exe 不存在（捆绑版和系统版都没有） | `_alert()` 弹窗说明 + 退出 |
| uv venv 失败 | 捕获 returncode，显示最后几行 uv 输出到弹窗 |
| uv pip install 失败 | 同上，提示用户检查网络/日志 |
| venv_python 不存在（安装后验证） | `_alert()` + 退出 |
| server.py 启动后 port 超时 | 现有逻辑不变（读 webui-server.log 附加到错误弹窗） |

## 日志文件位置

| 文件 | 内容 |
|------|------|
| `%APPDATA%\Hermes\hermes-startup.log` | main.py 全量日志 + uv 安装输出（现有文件，内容增强） |
| `%APPDATA%\Hermes\webui-server.log` | server.py 的 stdout/stderr |

## 不在本设计范围内

- macOS/Linux 安装流程改动
- Windows 离线安装（预打包 wheels）— 可作为后续优化
- WebUI 安装进度 UI（当前使用控制台窗口，足够用于调试）
- hermes-agent 自动升级机制
