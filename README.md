# ⚡ Hermes Installer

> 一键部署 [Hermes Agent](https://github.com/nousresearch/hermes-agent) 的可视化安装向导 + AI 对话界面

![License](https://img.shields.io/badge/license-MIT-purple) ![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey)

---

## 功能特性

### 安装向导（`index.html`）
- **5 步引导安装**：环境检测 → 自动安装 Hermes Agent → 配置 API 密钥 → 微信 QR 登录 → 完成
- 自动检测 Python / Git / uv 环境
- 支持 MiniMax、Anthropic、OpenRouter API 密钥配置
- 微信 iLink 扫码登录，实时显示二维码
- 安装日志实时 SSE 流式输出

### AI 对话界面（`chat.html`）
- **所有消息经过 Hermes Agent Gateway 处理**（port 8642），具备工具调用、记忆、多轮会话能力
- 流式响应，支持 Markdown 渲染 + 代码高亮（highlight.js）
- 思考过程折叠卡片（MiniMax / Anthropic 推理模型）
- 对话历史本地持久化（localStorage）
- 左下角设置面板：模型配置 / API 密钥 / Gateway 状态 / 高级参数
- 顶部实时显示 Gateway 连接状态

### 桌面应用打包
- **macOS**：`build.sh` → `.app` + `.dmg`（基于 pywebview + WKWebView）
- **Windows**：`build.bat` → `.exe` + `.zip`（基于 pywebview + Edge WebView2）

---

## 架构

```
用户界面 (chat.html)
    │
    ▼
FastAPI 后端 (app.py · port 7891)
    │  /api/chat/stream
    ▼
Hermes Agent Gateway (port 8642)   ← OpenAI-compatible API
    │  /v1/chat/completions
    ▼
LLM API (MiniMax / Anthropic / OpenRouter ...)
```

> Gateway 离线时，对话界面直接报错提示用户，不会静默降级。

---

## 快速开始

### 方式一：直接运行（开发模式）

```bash
# 安装依赖
pip install fastapi "uvicorn[standard]" aiohttp pyyaml python-dotenv qrcode pillow pywebview

# 启动
python app.py
# 浏览器访问 http://localhost:7891
```

### 方式二：打包为桌面应用

**macOS：**
```bash
bash build.sh
# 产物：dist/Hermes Installer.app  +  dist/Hermes-Installer-macOS.dmg
```

**Windows（在 Windows 机器上运行）：**
```bat
build.bat
REM 产物：dist\Hermes Installer\Hermes Installer.exe  +  dist\Hermes-Installer-Windows.zip
```

> 打包要求：Python 3.10+，macOS 需要 `hdiutil`（系统自带）。

---

## 前置要求

| 软件 | 版本 | 说明 |
|------|------|------|
| Python | 3.10+ | 运行环境 |
| [Hermes Agent](https://github.com/nousresearch/hermes-agent) | 任意 | 需先安装（可由本向导自动完成） |
| MiniMax / Anthropic / OpenRouter | — | 至少一个 API Key |

Hermes Agent Gateway（port 8642）需要在 `~/.hermes/config.yaml` 中启用：

```yaml
platforms:
  api_server:
    enabled: true
    extra:
      host: 127.0.0.1
      port: 8642
```

---

## 项目结构

```
hermes-installer/
├── app.py                  # FastAPI 后端（安装 API + 对话代理）
├── index.html              # 安装向导前端（5 步引导）
├── chat.html               # AI 对话界面
├── main.py                 # PyInstaller 入口（pywebview 桌面壳）
├── hermes_installer.spec   # PyInstaller 打包配置
├── build.sh                # macOS 打包脚本
├── build.bat               # Windows 打包脚本
└── requirements.txt        # Python 依赖
```

---

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 安装向导 |
| GET | `/chat` | 对话界面 |
| GET | `/api/check` | 环境检测 |
| GET | `/api/install` | 安装 Hermes（SSE） |
| POST | `/api/config/keys` | 保存 API 密钥 |
| POST | `/api/config/model` | 保存模型配置 |
| GET | `/api/weixin/login` | 微信 QR 登录（SSE） |
| POST | `/api/chat/stream` | 对话流式输出（SSE，经 Gateway） |
| GET | `/api/gateway/health` | 检测 Gateway 是否运行 |
| GET | `/api/status` | Gateway 状态详情 |
| POST | `/api/gateway/restart` | 重启 Hermes Gateway |

---

## 支持的 LLM 提供商

| 提供商 | API 模式 | 说明 |
|--------|----------|------|
| **MiniMax** | Anthropic Messages | 默认，`https://api.minimax.io/anthropic` |
| **Anthropic** | Anthropic Messages | Claude 系列 |
| **OpenRouter** | OpenAI Chat | 多模型路由 |
| 自定义 | 可配置 | 兼容 OpenAI / Anthropic 格式 |

---

## Windows 用户说明

- Windows 11 内置 Edge WebView2，无需额外安装
- Windows 10 用户如遇问题，请安装 [Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
- 首次运行 SmartScreen 提示时，点「更多信息」→「仍要运行」

---

## macOS 用户说明

- 首次运行：右键 → 打开（绕过 Gatekeeper 未签名提示）
- 或在终端执行：`xattr -cr "/Applications/Hermes Installer.app"`

---

## License

MIT © 2025 [feifeixp](https://github.com/feifeixp)
