# ⚡ Hermes Installer

> Hermes Agent 一键部署 · 可视化安装向导 + 现代 AI 对话界面

![License](https://img.shields.io/badge/license-MIT-purple) ![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey)

---

## 功能特性

### 安装向导
- **环境检测**：自动检测 Python / Git / uv / WSL2 环境
- **一键安装**：自动克隆 + 创建 venv + 安装依赖（支持国内镜像）
- **API 密钥配置**：MiniMax、Anthropic、OpenRouter 统一管理
- **Hermes 初始化**：通过 PTY 交互式运行 `hermes setup`
- 安装日志实时 SSE 流式输出

### AI 对话界面（WebUI）
- 所有消息经 Hermes Agent Gateway（port 8642）处理，具备工具调用、记忆、多轮会话能力
- 流式响应，Markdown 渲染 + 代码高亮
- 对话历史持久化
- 内置设置面板：模型配置 / API 密钥 / 高级参数
- 顶部实时显示 Gateway 连接状态

### 桌面应用打包
- **macOS**：`.app` + `.dmg`（pywebview + WKWebView 原生窗口）
- **Windows**：`.exe` + `.zip`（pywebview + Edge WebView2 原生窗口）

---

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│                      main.py (桌面壳)                         │
│                    pywebview / 浏览器 fallback                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────────────┐    ┌───────────────────────────┐  │
│  │  app.py (FastAPI)     │    │  webui/server.py (纯 stdlib)│  │
│  │  port 7891            │    │  port 动态                  │  │
│  │                       │    │                           │  │
│  │  · 安装向导 API        │    │  · AI 对话界面              │  │
│  │  · 环境检测            │    │  · 设置 / 配置              │  │
│  │  · API 密钥管理        │    │  · 会话管理                │  │
│  │  · /chat → 302 → webui│    │                           │  │
│  └──────────┬────────────┘    └───────────┬───────────────┘  │
│             │                             │                  │
└─────────────┼─────────────────────────────┼──────────────────┘
              │                             │
              ▼                             ▼
    ┌─────────────────────────────────────────────┐
    │        Hermes Agent Gateway (port 8642)       │
    │           OpenAI-compatible API               │
    │        /v1/chat/completions  /health          │
    └────────────────────┬────────────────────────┘
                         │
                         ▼
         ┌───────────────────────────────┐
         │  LLM API (MiniMax / Anthropic  │
         │         / OpenRouter / ...)    │
         └───────────────────────────────┘
```

> **两个服务独立运行**：安装器用 installer 的 Python 环境，WebUI 用 hermes-agent 的 venv。Gateway 离线时界面直接提示用户，不会静默降级。

---

## 项目结构

```
hermes-installer/
├── main.py                 # pywebview 桌面壳入口（启动 app + webui）
├── app.py                  # FastAPI 后端（安装 API + Gateway 代理）
├── index.html              # 安装向导前端（5 步引导）
├── webui/                  # AI 对话界面（独立 Web 应用）
│   ├── server.py           #   ThreadingHTTPServer 入口（纯 stdlib）
│   ├── api/                #   API 路由、配置、会话、Gateway 通信
│   └── static/             #   前端静态资源
├── hermes_installer.spec   # PyInstaller 打包配置
├── bundle_source.py        # 离线源码打包工具
├── fix_annotations.py      # Python 兼容性修复（from __future__）
├── build.sh                # macOS 打包脚本
├── build.bat               # Windows 打包脚本
├── sync-webui.sh           # WebUI 上游同步脚本（手动触发）
├── requirements.txt        # Python 依赖
```

---

## 快速开始

### 方式一：开发模式（直接运行）

```bash
# 1. 安装依赖（需要 Python 3.10+）
pip install -r requirements.txt

# 2. 启动（自动打开浏览器）
python main.py
```

### 方式二：分别启动两个服务

```bash
# 终端 1：安装向导
python app.py
# → http://localhost:7891

# 终端 2：AI 对话界面（需要 Hermes Agent 已安装）
cd webui && ~/.hermes/hermes-agent/venv/bin/python server.py
# → http://127.0.0.1:<动态端口>
```

### 方式三：打包为桌面应用

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

---

## API 端点 (app.py)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 安装向导（已完成安装则重定向到 WebUI） |
| GET | `/chat` | 重定向到 WebUI 对话界面 |
| GET | `/api/check` | 环境检测（Python / Git / uv / Hermes / API Key） |
| GET | `/api/install` | 安装 Hermes Agent（SSE 流式） |
| GET | `/api/install/simple` | 一键安装（官方脚本，SSE） |
| GET | `/api/install-tool?tool=uv\|python` | 自动安装 uv 或 Python |
| POST | `/api/config/keys` | 保存 API 密钥 |
| POST | `/api/config/model` | 保存模型配置 |
| POST | `/api/config/advanced` | 保存高级参数 |
| GET | `/api/config/read` | 读取当前配置 |
| GET | `/api/weixin/login` | 微信 iLink 扫码登录（SSE） |
| GET | `/api/setup/run` | 运行 `hermes setup`（PTY，SSE） |
| POST | `/api/setup/input` | 向 setup 会话发送输入 |
| POST | `/api/chat/stream` | 对话流式输出（SSE，经 Gateway） |
| GET | `/api/gateway/health` | 检测 Gateway 运行状态 |
| GET | `/api/status` | Hermes Agent 综合状态 |
| POST | `/api/gateway/restart` | 检测 Gateway 是否可达 |
| POST | `/api/hermes/start` | 启动 Hermes Agent（`hermes serve`） |
| GET | `/api/install-wsl` | Windows WSL2 安装 |
| POST | `/api/setup/complete` | 标记安装完成 |
| GET | `/api/open-url?url=...` | 在系统浏览器打开链接 |

---

## 前置要求

| 软件 | 版本 | 说明 |
|------|------|------|
| Python | 3.10+ | 运行环境 |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | 任意 | 需先安装（可由本向导自动完成） |
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

## WebUI 上游同步

`webui/` 是通过 `git subtree` 从 [nesquena/hermes-webui](https://github.com/nesquena/hermes-webui) 引入的。

### 自动同步（推荐）

GitHub Actions 每天自动检查上游更新，有更新时自动创建 PR：
- `.github/workflows/sync-webui.yml`
- PR 会出现在仓库 Pull Requests 列表，审核后合并即可

### 手动同步

```bash
bash sync-webui.sh
# 检查 git diff 后推送
git push origin main
```

---

## 支持的 LLM 提供商

| 提供商 | API 模式 | 说明 |
|--------|----------|------|
| **MiniMax** | Anthropic Messages | 默认，`https://api.minimax.io/anthropic` |
| **Anthropic** | Anthropic Messages | Claude 系列 |
| **OpenRouter** | OpenAI Chat | 多模型路由 |
| 自定义 | 可配置 | 兼容 OpenAI / Anthropic 格式 |

---

## 路线图

### 🚧 进行中

- [x] 跨平台安装向导（macOS + Windows）
- [x] Hermes Agent 一键安装 + 国内镜像加速
- [x] pywebview 原生桌面应用
- [x] WebUI 现代对话界面
- [ ] **Hermes 技能商店** — 浏览、安装、分享 Hermes Agent 技能
- [ ] **neowow.studio 集成** — 打通 AIGC 创作者生态，发布/获取 AI 技能

### 📋 计划中

- [ ] Windows 打包 CI/CD（GitHub Actions）
- [ ] 应用自动更新
- [ ] 多语言支持（i18n）
- [ ] 离线安装包（内置 Python + Hermes Agent）

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
