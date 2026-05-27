# 客户端崩溃上报系统设计

**日期：** 2026-05-27
**状态：** 已批准

## 问题背景

Hermes Installer (Windows + macOS PyInstaller .exe / .app) 在用户机器上崩溃时，开发侧大多没有任何 telemetry，必须等用户主动报告 + 手工抓 log 才能定位。最近的 v1.4.0 → v1.4.2 修复链就是反复"用户报告 → 我看 log → 改代码 → 发版"的循环，每轮要等用户手动贴 log，效率极低。

服务端已有 `POST /api/client-log` endpoint（Next.js / Cloudflare Workers），主进程 `main.py` 已经在 5 处调用 `_send_crash_report()`：

1. `startup_webview2_missing` — Windows 缺 WebView2 Runtime
2. `startup_pywebview_missing` — pywebview ImportError
3. `startup_pywebview_failed` — `webview.start()` 异常
4. `windows_install_failed` — `_windows_install_agent()` 异常
5. `main_unhandled` — main.py 最外层兜底 catch

**现有体系的 6 个盲区：**

1. WebUI server.py 完全不上报 — 这次 asyncio NameError bug 服务端看不到，全靠手工抓 log
2. log 文件不会随报上传 — 用户的 4.1 MB `webui-server.log` 是手工贴上来的
3. 运行时崩溃不报 — 只覆盖启动阶段；服务跑起来后挂、子进程意外退出都没数据
4. 没有重试 / 排队 — 崩溃时正好断网就丢
5. 没有 PII 过滤 — traceback 里 `C:\Users\FF\...` 直接上传，泄漏用户名 / 文件路径
6. 没有 venv 健康检查上报 — v1.4.2 加的 health check 失败时只本地 log，运营侧看不到污染分布

## 目标

- WebUI server.py 崩溃 100% 被上报（包括 sys.excepthook 兜底和 request handler 500）
- 崩溃报告自动附 log 尾部 ~150 KB（足够诊断，CF Workers body 限制内）
- 网络断时本地排队，下次启动重传，不丢报告
- 客户端 + 服务端双层 PII 过滤
- 12 个 trigger 全部接入（5 现有 + 7 新增）

## 非目标 (Out of Scope)

- 浏览器侧 JS 错误上报（unhandledrejection、SSE 断连等）
- Telemetry 指标（启动成功心跳、版本分布、安装耗时等）
- 第三方 SDK（Sentry / Rollbar）— 强依赖 + 费用 + 失去与 user-journey 系统的集成
- session 回放类工具（OpenReplay 等）

这些都是合理扩展，但属于"客户端可观测性"而非"补全崩溃上报"，应该走独立的 spec。

## 方案选择

考虑过 3 个方案：

### 方案 A：在 main.py 现有 `_send_crash_report` 之上扩展，webui 抄一份近似的

每个进程一份独立实现，~60 行重复代码。优点是各自演进互不耦合，缺点是 PII 过滤规则、queue 路径、重试逻辑容易漂移。

### 方案 B：抽取共享模块 `crash_reporter.py` 放在 repo 根 ✅（选定）

`main.py` 和 `webui/server.py` 都 import 同一个文件。PyInstaller 把 `crash_reporter.py` bundle 到 `_MEI` 临时目录，main.py 通过 `BASE_DIR` 在 sys.path 上找到它；webui 子进程读 `HERMES_INSTALLER_BASE_DIR` 环境变量（main.py 已经在导出），把同样的目录注入自己的 sys.path。

**为什么选 B：**
- 单一事实来源 — 改一处所有地方生效
- PII 过滤规则在 client 端绝对一致
- 跨解释器共享只是 2 行胶水代码（path insert + env 继承），不像看起来那么吓人
- ~80 行总代码，比方案 A 少一半

### 方案 C：换 Sentry / Rollbar 等成熟 SDK

抛弃自己的 `/api/client-log`，全部走 SDK。
- 优点：Python + JS 两侧自动 catch、breadcrumb / release tracking 免费送
- 缺点：(1) 外部依赖 + 网络出口 + 费用；(2) 失去与 user-journey 系统的整合（崩溃事件不再写到 `usr_` 行，admin 云实例 tab 看不到）；(3) 对于"只补全崩溃上报"的 scope 是杀鸡用牛刀

不选。后续若 scope 扩大到全面 telemetry 可再评估。

## 架构

```
crash_reporter.py                          ← 新建在 repo 根
├── PUBLIC API
│   └── report(phase, error, *, traceback=None, log_path=None, extra=None) -> bool
│
├── INTERNAL — Payload 构造
│   ├── _collect_metadata()          → {app, version, platform, pid, python}
│   ├── _read_log_tail(path, max_bytes=150_000)   ← seek+tail，不读大文件全文
│   ├── _sanitize_pii(text)          ← regex 替换，见下
│   └── _attach_jwt(headers)         ← 从 ~/.hermes/webui/neowow.json 读
│
├── INTERNAL — 网络
│   ├── ENDPOINT = "https://app.neowow.studio/api/client-log"
│   ├── TIMEOUT_SECONDS = 8
│   └── _post(payload, headers) → urllib.request（不引 requests）
│
└── INTERNAL — 本地 Queue
    ├── QUEUE_DIR = ~/.hermes/pending-crash-reports/
    ├── MAX_QUEUE_ENTRIES = 20  (FIFO，超出删最旧)
    ├── _enqueue(payload)
    ├── _flush_queue() -> int       ← 启动时调，遍历重传
    └── _drop_oldest_if_full()
```

### 关键设计点

**stdlib-only 单文件**：`main.py` 在 PyInstaller frozen 进程跑、`webui/server.py` 在 venv 跑，两边都不想为这个模块装新包。`urllib.request + json + os + pathlib + threading` 完全够。

**同步接口异步执行**：`report()` 派 daemon thread 跑网络 IO，主线程 `thread.join(timeout=0.5)`。线程内部逻辑：若 POST 成功（HTTP 2xx）→ 返回；若 POST 失败（任何原因）→ `_enqueue(payload)`。主线程 join 后检查 `thread.is_alive()`：未结束 = 当作"已 enqueue 由线程后台处理"返回 False；已结束 = 检查共享 state 看是成功还是 enqueue 了，相应返回 True/False。调用者（如 `sys.excepthook`）不会被 8 秒网络等阻塞。

**跨进程共享的胶水代码**：

```python
# main.py（PyInstaller frozen）
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))  # 已经存在
import crash_reporter as _cr           # bundle 在 PyInstaller root
```

```python
# webui/server.py（venv subprocess）
import sys, os
_installer_dir = os.environ.get("HERMES_INSTALLER_BASE_DIR")  # main.py 已设
if _installer_dir and _installer_dir not in sys.path:
    sys.path.insert(0, _installer_dir)
try:
    import crash_reporter as _cr
except ImportError:
    _cr = None   # docker / dev 环境优雅降级
```

## 数据流

### Payload schema (client → server)

```json
{
  "app":      "hermes-installer",
  "version":  "v1.4.2",
  "platform": "win32",
  "phase":    "webui_startup_crash",
  "error":    "NameError: name 'base_events' is not defined",
  "traceback": "Traceback (most recent call last):\n  File ...",
  "logTail":  "[2026-05-26 22:54:55] Starting server.py...\n...",
  "extra": {
     "pid":            45420,
     "python_version": "3.11.11",
     "_MEI":           "<MEI>",
     "venv_python":    "<USER_HOME>\\.hermes\\hermes-agent\\venv\\Scripts\\python.exe"
  }
}
```

字段约束：

| 字段 | 上限 | 必填 | 过 PII filter |
|---|---|---|---|
| `app` | 64 字符 | ✅ | 否（白名单）|
| `version` | 32 字符 | ✅ | 否 |
| `platform` | 32 字符 | ✅ | 否 |
| `phase` | 64 字符 | ✅ | 否（白名单 enum）|
| `error` | 500 字符 | ✅ | ✅ |
| `traceback` | 5000 字符（从 3000 加大）| ❌ | ✅ |
| `logTail` | 150_000 字符 (~150 KB) | ❌ | ✅ |
| `extra` | 序列化后 5000 字符 | ❌ | ✅ |

CF Workers body 上限 1 MB，最大 payload ≈ 160 KB，远低于限制。

### Phase 白名单 enum

服务端拒收非白名单值，防 phase 字段污染：

```python
PHASES = {
    # 现有 5 个
    "startup_webview2_missing",
    "startup_pywebview_missing",
    "startup_pywebview_failed",
    "windows_install_failed",
    "main_unhandled",
    # 新增 7 个
    "webui_pre_main_import_error",    # webui import 阶段挂（asyncio NameError 走这）
    "webui_startup_crash",            # webui main() 内挂
    "webui_runtime_exception",        # webui request handler 500
    "wait_for_server_timeout",        # main.py _wait_for_server 超时
    "venv_health_check_failed",       # v1.4.2 health check 报错
    "webui_subprocess_exit_unexpected", # 子进程在 webview 运行期间退出
    "windows_install_dir_wiped",      # info 级：自动救援触发统计
}
```

### 时序图（一个崩溃的完整生命周期）

```
        webui server.py                      main.py                   crash_reporter            CF /api/client-log         user-journey tablestore
              │                                  │                            │                          │                          │
   ① 崩溃 (sys.excepthook 触发)                  │                            │                          │                          │
              │── report(phase, error, ...) ───────────────────────────────►│                          │                          │
              │                                  │                            │── _sanitize_pii() ──┐    │                          │
              │                                  │                            │── _read_log_tail() ─┤    │                          │
              │                                  │                            │── _attach_jwt() ────┘    │                          │
              │                                  │                            │── 同步 0.5s 窗口 ──┐    │                          │
              │                                  │                            │   守内部线程 POST    │    │                          │
              │                                  │                            │                     ├───►│                          │
              │                                  │                            │              [A: 网络通]                            │
              │                                  │                            │                     │◄───┤ 204 (always)             │
              │                                  │                            │                     │    ├─► console.log           │
              │                                  │                            │                     │    └─► recordClientError ───►│ usr_<id>.lastClientErrorAt
              │                                  │                            │              [B: 5s 后还没 ack]                      │
              │                                  │                            │                     │  timeout, _enqueue(payload) │
              │                                  │                            │                     │  写 ~/.hermes/pending-...    │
              │                                  │                            │◄────────────────────┘                              │
              │◄─────── return False ────────────────────────────────────────│                                                     │
              ▼                                  │                            │                                                     │
        process dies                             │                            │                                                     │
                                                 │                            │                                                     │
   ② 下次启动                                     │                            │                                                     │
                                                 │── _flush_queue() (main() 开头) ──►                                                │
                                                 │                            │── 遍历 ~/.hermes/pending-...                         │
                                                 │                            │── 每条 _post() (retry budget=1)                      │
                                                 │                            │── 成功 unlink，失败留到下次                            │
```

**跨进程职责**：
- main.py **logging setup 完成后、任何 `_send_crash_report` 调用前**调 `_flush_queue()`（具体位置：`log.info("=== Hermes Installer starting ===")` 之后立刻）。覆盖 webui 上次崩了的报告
- webui server.py 自己不 flush — 它是子进程，flush 由父进程负责，避免双方竞争
- queue 文件命名 = `{epoch_ns}.json`，字典序 = 时间序 = 重传顺序
- queue 写入用 `os.replace()` 原子操作

### PII 过滤规则

客户端先过一遍（第一道防线），服务端再过一遍（防止 bypass）。规则一致：

```python
PII_PATTERNS = [
    # Windows: C:\Users\Alice\foo  →  C:\Users\<USER>\foo
    (r'([A-Za-z]:[\\/])Users[\\/][^\\/\s]+', r'\1Users\\<USER>'),
    # macOS / Linux: /Users/alice/foo  →  /Users/<USER>/foo
    (r'/Users/[^/\s]+', '/Users/<USER>'),
    (r'/home/[^/\s]+',  '/home/<USER>'),
    # API keys (常见结构化前缀)
    (r'sk-[A-Za-z0-9_-]{20,}',           'sk-***REDACTED***'),
    (r'api[_-]?key[=:]["\']?[^\s"\',]+', 'api_key=***REDACTED***'),
    (r'Authorization:\s*Bearer\s+\S+',   'Authorization: Bearer ***REDACTED***'),
    (r'Bearer\s+[A-Za-z0-9._-]{20,}',    'Bearer ***REDACTED***'),
    # neoToken cookie
    (r'neoToken=[^;\s]+',                'neoToken=***REDACTED***'),
    # JWT 形式（3 段 base64url，用作 fallback 网捞漏的）
    (r'\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b', '<JWT_REDACTED>'),
]
```

服务端二重过滤跑在 Next.js route handler 里，pattern 相同，输入是已被客户端处理过的文本（防止客户端被 tamper、或 PII filter 被绕过）。

## Trigger 接入（12 个）

| # | phase | 位置 | hook 方式 | 备注 |
|---|---|---|---|---|
| 1 | `startup_webview2_missing` | main.py | 现有 | 不动 |
| 2 | `startup_pywebview_missing` | main.py | 现有 | 不动 |
| 3 | `startup_pywebview_failed` | main.py | 现有 | 不动 |
| 4 | `windows_install_failed` | main.py | 现有 | 不动 |
| 5 | `main_unhandled` | main.py | 现有 | 不动 |
| 6 | `wait_for_server_timeout` | main.py L944/L985 | 新增：`_wait_for_server()` 返回 False 时调，附 `_win_server_proc.poll()` |
| 7 | `venv_health_check_failed` | main.py `_is_agent_installed()` | 新增：v1.4.2 health check 失败时调，附 stderr tail |
| 8 | `windows_install_dir_wiped` | main.py `_wipe_contaminated_agent_venv()` | 新增（info）：每次成功 wipe 上报一次 |
| 9 | `webui_pre_main_import_error` | webui/server.py 顶部 `sys.excepthook` | 新增：import 阶段挂时（asyncio NameError 这类） |
| 10 | `webui_startup_crash` | webui/server.py 同一 `sys.excepthook` | 新增：main() 内挂时（`_main_started` flag 区分） |
| 11 | `webui_runtime_exception` | webui/api/routes.py handle_get/post wrapping | 新增：request handler 500 兜底，过滤 `_CLIENT_DISCONNECT_ERRORS` |
| 12 | `webui_subprocess_exit_unexpected` | main.py daemon thread | 新增：监控 `_win_server_proc.poll()`，进程意外退出时上报 |

### 4 个最值得展开的实现

**Trigger 9 + 10：webui sys.excepthook**

`webui/server.py` 在 v1.4.0 加的 asyncio preload 之后立刻装 hook：

```python
# webui/server.py 顶部，紧跟 asyncio preload
import asyncio as _asyncio_preload  # v1.4.0

import sys as _sys, os as _os
_installer_dir = _os.environ.get("HERMES_INSTALLER_BASE_DIR")
if _installer_dir and _installer_dir not in _sys.path:
    _sys.path.insert(0, _installer_dir)
try:
    import crash_reporter as _cr
except ImportError:
    _cr = None  # docker / dev 环境降级

_main_started = False

def _excepthook(exc_type, exc_value, tb):
    if _cr is None:
        return _sys.__excepthook__(exc_type, exc_value, tb)
    import traceback as _tb
    phase = "webui_startup_crash" if _main_started else "webui_pre_main_import_error"
    try:
        _cr.report(
            phase=phase,
            error=f"{exc_type.__name__}: {exc_value}",
            traceback="".join(_tb.format_exception(exc_type, exc_value, tb)),
            log_path=_os.environ.get("HERMES_WEBUI_LOG_FILE"),
        )
    except Exception:
        pass  # 上报失败绝不能再抛
    return _sys.__excepthook__(exc_type, exc_value, tb)

_sys.excepthook = _excepthook
```

`_main_started = True` 在 `main()` 第一行设。

**Trigger 11：request handler 兜底**

```python
# webui/api/routes.py 顶部
def _report_handler_crash(method: str, path: str, exc: BaseException) -> None:
    if _cr is None or isinstance(exc, _CLIENT_DISCONNECT_ERRORS):
        return  # 客户端断连不算 crash
    import traceback as _tb
    try:
        _cr.report(
            phase="webui_runtime_exception",
            error=f"{method} {path}: {type(exc).__name__}: {exc}",
            traceback=_tb.format_exc(),
        )
    except Exception:
        pass

# 在 handle_get/post 的最外层（在 _CLIENT_DISCONNECT_ERRORS 之外）：
def handle_get(handler, parsed):
    try:
        ...existing logic...
    except _CLIENT_DISCONNECT_ERRORS:
        raise
    except Exception as exc:
        _report_handler_crash("GET", parsed.path, exc)
        raise
```

**Trigger 12：子进程意外退出 monitor**

```python
# main.py main() webview 起来前
def _monitor_webui_subprocess():
    while True:
        rc = _win_server_proc.poll()
        if rc is None:
            time.sleep(2)
            continue
        log.error("webui server.py died unexpectedly rc=%s", rc)
        _cr.report(
            phase="webui_subprocess_exit_unexpected",
            error=f"server.py exited rc={rc} while webview was running",
            extra={"returncode": rc},
            log_path=str(_LOG_DIR / "webui-server.log"),
        )
        break  # daemon thread 退出

threading.Thread(target=_monitor_webui_subprocess, daemon=True).start()
```

## 错误处理

### 上报本身失败的 6 种情况

| 失败场景 | 处理 | 用户可见性 |
|---|---|---|
| 网络断 / DNS / connect refused | enqueue 到本地，下次启动重传 | 完全不可见 |
| HTTP 5xx | 同上 | 同上 |
| HTTP 4xx (payload 不合法) | enqueue + log.error，但**不重试**（4xx 重试无效）。同一 payload 第 5 次跨启动失败 → 移到 `quarantine/` | 同上 |
| TLS / cert 错误 | enqueue + 重试 | 同上 |
| Queue 写入失败 (磁盘满 / 权限) | log.error 一行算了 — 上报这个错会无限递归 | 同上 |
| Queue 超过 20 条 | 删最旧的（FIFO） | 同上 |

### Queue 边界条件

- **最大 entry 数**：20
- **最大 entry 大小**：200 KB
- **目录总大小上限**：20 × 200 KB ≈ 4 MB
- **文件命名**：`{epoch_nanoseconds}.json`，纯数字字典序 = 时间序 = 重传顺序
- **权限**：0600（含 JWT，不应被同机其他用户读）
- **flush retry budget**：每个 entry 每次 flush 只重传 1 次，失败保留到下次启动再试。文件名后缀 `.attempt-N.json` 记录尝试次数。**不在 flush 时无限重试**（启动卡死风险）。5 次跨启动失败后进 `quarantine/`
- **flush 时间预算**：5 秒上限，超过放弃，剩余 entry 等下次

### 死信处理

某 payload 持续 4xx → 第 5 次失败 → 移到 `~/.hermes/pending-crash-reports/quarantine/`，不再尝试。`quarantine/` 不限上限，但每次 flush log 一行 warning。运维主动看到再 grep。

### `_excepthook` 自己崩了

```python
def _excepthook(exc_type, exc_value, tb):
    if _cr is None:
        return _sys.__excepthook__(exc_type, exc_value, tb)
    try:
        _cr.report(...)
    except Exception:
        pass   # ★ 上报失败绝不能再抛
    return _sys.__excepthook__(exc_type, exc_value, tb)  # 永远走原生
```

**铁律**：上报代码必须 catch 所有 Exception；原生 hook 必须永远跑（否则 Python 退出码错乱，上层难诊断）。

## 测试

### 单元测试 (`tests/test_crash_reporter.py`)

| 测试 | 验证 |
|---|---|
| `test_report_success_204` | mock urlopen 返回 204，`report()` 返回 True，queue 目录空 |
| `test_report_network_fail_enqueues` | mock urlopen raise URLError → 返回 False，queue 目录 1 个文件 |
| `test_report_timeout_enqueues` | mock urlopen sleep 10s → 0.5s 主线程返回 False（仍 enqueue） |
| `test_pii_username_path_filtered` | input `C:\Users\Alice\foo` → payload 含 `C:\Users\<USER>\foo` |
| `test_pii_api_key_redacted` | input `sk-abc123def456ghi789jkl` → 含 `***REDACTED***` |
| `test_pii_jwt_in_traceback_redacted` | input 3 段 base64 `eyJ...` → 不被原样上传 |
| `test_log_tail_reads_last_n_bytes` | 4 MB log 文件 → logTail ≤ 150 KB 且是末尾 |
| `test_log_tail_missing_file_ok` | log_path 不存在 → logTail = None，不抛 |
| `test_queue_evicts_oldest_at_max` | 已有 20 条，第 21 条入 → 最旧那条被删 |
| `test_flush_retries_then_moves_to_dlq` | 同一 entry 5 次失败 → 进 quarantine/ |
| `test_flush_5s_budget_respected` | mock 每次 post 1.5s → flush ≤ 5.5s |
| `test_excepthook_failure_doesnt_swallow_original` | mock `_cr.report` raise → 原生 hook 仍调用 |

### 集成测试

一个端到端 smoke test，真起 mock HTTP server（`http.server` 在随机端口）：

| 步骤 | 验证 |
|---|---|
| 启 mock server 监听 127.0.0.1:RANDOM | |
| `crash_reporter.ENDPOINT` monkeypatch 指向它 | |
| `crash_reporter.report("test_phase", "test error", traceback="...", log_path=...)` | mock server 收到 POST，payload schema 全字段 |
| logTail 字段截到 ≤ 150 KB | |
| Authorization header 含 Bearer 形如 `eyJ...` | |
| 关闭 mock server，再调 `report()` | enqueue 文件出现 |
| 启 mock server，调 `_flush_queue()` | enqueue 文件消费、mock 收到 |

### 服务端测试 (`dashboard/.../client-log.test.ts`)

| 测试 | 验证 |
|---|---|
| 接受 logTail 字段 | payload 含 logTail → 写入 console.log（截到 200 KB）|
| logTail PII 二重过滤 | input 含 `C:\Users\Alice\` → 服务端 log 含 `<USER>` |
| phase 白名单 | 非白名单 phase → 仍 204 但 console.warn |
| JWT 解码失败容忍 | 无 Authorization / 无效 JWT → 仍 204（journey row 不写）|

### 不测的

- 真发到 `https://app.neowow.studio/api/client-log` — CI 不依赖外网
- PyInstaller frozen 环境特殊行为 — `crash_reporter.py` 跟 frozen 无关，由其他测试覆盖

## 影响范围

### 新增文件

- `crash_reporter.py`（repo 根，~150 行 stdlib only）
- `tests/test_crash_reporter.py`
- `tests/test_crash_reporter_integration.py`

### 修改文件

- `main.py` — `_send_crash_report` 替换为 `from crash_reporter import report`；新增 3 个 trigger 调用点
- `webui/server.py` — 顶部装 `sys.excepthook`；`main()` 第一行设 `_main_started = True`
- `webui/api/routes.py` — `handle_get/post/patch/delete/put` 各加 1 层 try/except wrapping
- `hermes_installer.spec` — 显式把 `crash_reporter.py` 加入 `hiddenimports`（PyInstaller 跟随 `import crash_reporter` 静态分析应该能找到，但 `hiddenimports` 是兜底；同时本地 build 后 grep frozen bundle 验证 `crash_reporter` 在 `_MEI/` 根目录可见）
- `dashboard/src/app/api/client-log/route.ts` — 加 `logTail` 字段接收 + 服务端 PII 二重过滤 + phase 白名单

### 用户可见行为

- ✅ 崩溃时不再"无声"丢失 — 服务端可以看到崩在哪
- ✅ admin 云实例 tab 的 `lastClientErrorAt` 字段更准确（更多 trigger 触发）
- ❌ 不增加任何 UI（不弹窗问"是否上报"，用户已经登录 neowow.studio）
- ❌ 不影响崩溃流程时序（0.5s 主线程 join 上限）
- ❌ 不占用可察觉的磁盘（queue 上限 4 MB）

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| `crash_reporter.py` 自己崩、把整个 server 拖死 | 所有 public API 都包 try/except Exception；测试覆盖 |
| Queue 文件污染（手动写入恶意 JSON） | flush 时单条 entry 失败不影响其他；非 JSON 直接 unlink |
| PII filter 误伤合法内容 | filter 只在 traceback / logTail / extra.value 上跑，不动 phase / error 首行；测试覆盖典型 false-positive |
| 上报 endpoint 被滥用 | 已存在的 cap（500 chars error 等）+ 服务端 phase 白名单 |
| Queue 在隐私模式下泄漏 token | queue 文件 0600 权限；JWT 在 attach 时才读，不存进 queue payload |

## 后续路径（不在此 spec）

- 浏览器侧 JS 错误捕获（`window.onerror` + `unhandledrejection`）
- 启动成功 heartbeat（用 phase=`info_startup_success` 复用 endpoint，免新增基础设施）
- session 维度的 breadcrumb（最近 N 个操作）
- 切到 Sentry 等成熟方案的迁移评估（如果未来 scope 扩到全面 telemetry）
