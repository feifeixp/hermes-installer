# 初始化「纯本地 Gemma」选项设计

**日期:** 2026-06-06
**状态:** 设计评审通过,进入实现
**分支:** `feat/onboarding-local-gemma`(基于 main)

## 目标

在 Hermes 初始化向导的「plan(CodingPlan/订阅)」步骤加一个**纯本地 Gemma**选项,作为"不订阅云端 Coding Plan、改用自己电脑上的免费本地模型"的替代。点击后:检测/安装 Ollama → 按内存自动拉取 `gemma4:e2b`/`e4b` → 把 Hermes 配置成走本地 Ollama。

## 范围与硬约束(关键)

- **仅本地/桌面、且 OS ∈ macOS/Linux**。
- **云端 ECS 彻底隔离**:ECS 设了 `HERMES_NEOWOW_ONLY=1`,既装不动(无 GPU/小内存)也不该装。
  - 前端:`local_llm_available` 为假时不渲染该卡片。
  - 后端:安装端点开头硬性检查,`HERMES_NEOWOW_ONLY` 置位 → `403`,即使手动 POST 也拒。
- **Windows**:v1 不显示该卡片(`curl|sh` 不适用)。

## 架构

新增模块 `webui/api/local_gemma.py`(纯逻辑 + 任务运行),路由挂在 `routes.py`,UI 在 `onboarding.js` 的 plan 步加卡片 + 进度面板。配置复用现成 `apply_onboarding_setup`(provider=ollama)。

### 组件 1 — 按内存选模型(纯函数,易测)

```python
RAM_THRESHOLD_BYTES = 16 * 1024**3   # 16 GiB

def pick_gemma_model(total_ram_bytes: int) -> str:
    """≥16GiB → gemma4:e4b(9.6GB,4.5B 有效);否则 gemma4:e2b(7.2GB,2.3B)。"""
    return "gemma4:e4b" if total_ram_bytes >= RAM_THRESHOLD_BYTES else "gemma4:e2b"

def detect_total_ram_bytes() -> int:
    """macOS/Linux: os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')。失败回 0。"""
```

UI 显示自动选了哪个 + 原因,并允许用户**手动覆盖**到另一个(e2b↔e4b)。

### 组件 2 — 可用性判定

```python
def local_llm_available() -> bool:
    """非 neowow_only 且 OS ∈ {darwin, linux}。"""
    import sys, os
    if os.getenv("HERMES_NEOWOW_ONLY", "").strip().lower() in {"1","true","yes"}:
        return False
    return sys.platform in ("darwin", "linux")
```

### 组件 3 — Ollama 探测

```python
def ollama_installed() -> bool:   # shutil.which("ollama") is not None
def ollama_running() -> bool:     # TCP/HTTP 探 http://localhost:11434/api/tags,短超时
```

### 组件 4 — 安装任务(后台线程 + 轮询,不上 SSE)

stdlib HTTP server 上 SSE 麻烦;用**内存任务表 + 轮询**(对齐现有 background 任务风格):

- `start_install_job(model: str) -> job_id`:起一个后台线程:
  1. **已装 Ollama** → 跳过安装;**未装** → **不硬跑 `curl|sh`**(Linux 非 root 会失败),而是把任务标成 `need_manual_install`,返回官方安装指引(`curl -fsSL https://ollama.com/install.sh | sh` 或 ollama.com/download),等用户装好后重新发起。
  2. 确保 `ollama serve` 可达(`ollama_running()`;桌面装好后通常自起)。
  3. `ollama pull <model>` —— 子进程,逐行解析进度,写入任务的 `progress`/`percent`/`log`。
  4. 成功 → 调 `apply_onboarding_setup({"provider":"ollama","model":model,"base_url":"http://localhost:11434/v1","api_key":"","confirm_overwrite":True})` 完成配置 → 任务 `done`。
  - 任何步骤失败 → 任务 `error` + 信息,前端可重试。
- `get_job(job_id) -> {state, percent, log, model, error}`。state ∈ `running|need_manual_install|done|error`。

### 组件 5 — 路由(`routes.py`)

- `GET  /api/onboarding/local-gemma/status` → `{available, ollama_installed, ollama_running, ram_bytes, recommended_model}`(available=False 时前端不显示卡片)。
- `POST /api/onboarding/local-gemma/install` body `{model?}`(model 省略=按内存自动)→ 硬拒 neowow_only;否则 `{job_id}`。
- `GET  /api/onboarding/local-gemma/job?id=` → 任务状态(轮询)。

### 组件 6 — UI(`onboarding.js`,plan 步)

- 拉 `/status`;`available` 为真才渲染「🖥️ 纯本地 Gemma · 免费」卡片,显示推荐模型 + e2b/e4b 切换。
- 点击「安装并使用」→ POST `/install` → 进度面板轮询 `/job`:
  - `need_manual_install` → 显示安装指引 + 「我已安装,继续」(重新 POST)。
  - `running` → 进度条 + 日志尾巴。
  - `done` → 提示完成,推进/关闭向导。
  - `error` → 错误 + 「重试」。

## 数据流

```
plan 步 → GET /status(available?)→ 渲染卡片(推荐 model)
点「安装并使用」→ POST /install → job_id
   后台线程: [已装?跳过:need_manual_install] → ollama pull <model>(进度) → apply_onboarding_setup(ollama) → done
前端轮询 GET /job?id= → 进度/完成/错误
done → Hermes 已配置成本地 Ollama+Gemma,后续对话走 localhost:11434
```

## 错误处理

- `neowow_only`(云端)→ 路由 403,前端本就不显示。
- 未装 Ollama → `need_manual_install` + 指引,不硬跑 `curl|sh`。
- `ollama pull` 失败(网络/磁盘/未知 tag)→ 任务 `error` + 信息 + 重试。
- 子进程异常逐步 try/except,任务状态恒定可读。

## 测试(pytest, py3.11+)

- `pick_gemma_model`:15GiB→e2b;16GiB→e4b;32GiB→e4b;0→e2b(边界)。
- `local_llm_available`:neowow_only=1→False;win32→False;darwin/linux 非 neowow→True(monkeypatch `sys.platform` + env)。
- 安装任务状态机(monkeypatch `shutil.which`/`subprocess`/`apply_onboarding_setup`):
  - 已装 → 跳过安装、调 pull、成功后调 apply_onboarding_setup(provider=ollama, model=选中)。
  - 未装 → state=`need_manual_install`,不调 pull。
  - pull 失败 → state=`error`。
- 路由 neowow_only → 403。

## 非目标(v1 不做)

- Windows 卡片 / Windows 自动装。
- 强行 `curl|sh` 全自动(Linux 非 root 装不动)——改"未装则引导"。
- 多模型管理 / 切换其它本地模型(仅 gemma4 e2b/e4b)。
- 云端任何改动。

## 部署

桌面版功能,随 desktop 构建(PyInstaller / release.yml)。合并到 main 后,云镜像也含代码但被 `local_llm_available` 关掉。注:另有未合并的 `feat/onboarding-3step`(含岗位人格等 17 commits)需与 main 单独对账,不阻塞本功能。
