# ⚡ Hermes Installer

> Hermes Agent 一键部署 · 桌面安装器 + 现代化 WebUI

![License](https://img.shields.io/badge/license-MIT-purple) ![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey)

---

## 功能特性

### 桌面安装器（`main.py`）

- **自动环境配置**：检测 Python / Git / uv / WSL2，缺少的工具一键安装
- **Hermes Agent 一键安装**：自动克隆 + 创建 venv + 安装依赖（支持国内镜像）
- **pywebview 原生窗口**：macOS WKWebView / Windows Edge WebView2
- 启动后自动打开 Hermes WebUI 对话界面

### AI 对话界面（WebUI）

- 所有消息经 Hermes Agent Gateway（port 8642）处理，具备工具调用、记忆、多轮会话能力
- **首次运行向导**：12+ 提供商（OpenRouter / Anthropic / OpenAI / DeepSeek / Ollama ...），API Key 配置，模型选择
- 流式响应，Markdown 渲染 + 代码高亮
- 对话历史持久化，多会话管理
- 设置面板：模型配置 / 多 Profile / 工具集 / 高级参数
- Gateway 状态实时显示

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
│  ┌──────────────────────────────────────────────────────┐  │
│  │               webui/bootstrap.py                       │  │
│  │                                                       │  │
│  │  · 环境检测 & 自动安装 Hermes Agent                     │  │
│  │  · Python venv 创建 & 依赖安装                         │  │
│  │  · 启动 server.py（动态端口）                           │  │
│  │  · 首次运行 onboarding 向导                            │  │
│  └────────────────────┬─────────────────────────────────┘  │
│                       │                                     │
│  ┌────────────────────▼─────────────────────────────────┐  │
│  │              webui/server.py                           │  │
│  │              ThreadingHTTPServer                       │  │
│  │                                                       │  │
│  │  · AI 对话界面 (chat)                                   │  │
│  │  · 会话管理 / Workspace / Terminal                     │  │
│  │  · 设置 / Provider 配置 / Profile                      │  │
│  └────────────────────┬─────────────────────────────────┘  │
│                       │                                     │
└───────────────────────┼─────────────────────────────────────┘
                        │
                        ▼
          ┌─────────────────────────────────┐
          │  Hermes Agent Gateway (port 8642)│
          │     OpenAI-compatible API        │
          │  /v1/chat/completions  /health   │
          └──────────────┬──────────────────┘
                         │
                         ▼
           ┌───────────────────────────────┐
           │  LLM API (OpenRouter / Anthropic│
           │  / OpenAI / DeepSeek / ...)    │
           └───────────────────────────────┘
```

---

## 项目结构

```
hermes-installer/
├── main.py                 # pywebview 桌面壳入口（启动 WebUI）
├── webui/                  # AI 对话界面（独立 Web 应用）
│   ├── bootstrap.py        #   启动引导（自动安装 + 环境准备）
│   ├── server.py           #   ThreadingHTTPServer 入口
│   ├── start.sh            #   手动启动脚本
│   └── api/                #   API 路由、配置、会话、Gateway 通信
├── app.py                  # [deprecated] FastAPI 后端（保留用于微信登录等端点）
├── index.html              # [deprecated] 旧安装向导前端
├── bundle_source.py        # 离线源码打包工具
├── hermes_installer.spec   # PyInstaller 打包配置
├── fix_annotations.py      # Python 兼容性修复
├── build.sh                # macOS 打包脚本
├── build.bat               # Windows 打包脚本
└── requirements.txt        # Python 依赖
```

---

## 快速开始

### 方式一：开发模式（直接运行）

```bash
# 1. 安装依赖（需要 Python 3.10+）
pip install pywebview

# 2. 启动（自动打开 WebUI）
python main.py
```

安装器会自动检测并安装 Hermes Agent（如果未安装），然后打开 WebUI 对话界面。

### 方式二：手动启动 WebUI

```bash
# 直接启动 WebUI（需要 Hermes Agent 已安装）
cd webui && bash start.sh
# → http://127.0.0.1:8787
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

## 前置要求

| 软件 | 版本 | 说明 |
|------|------|------|
| Python | 3.10+ | 运行环境 |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | 任意 | 安装器可自动安装 |
| LLM API Key | — | OpenRouter / Anthropic / OpenAI / DeepSeek 等至少一个 |

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

## 支持的 LLM 提供商

| 提供商 | API 模式 | 说明 |
|--------|----------|------|
| **OpenRouter** | OpenAI Chat | 多模型路由，推荐入门 |
| **Anthropic** | Anthropic Messages | Claude 系列 |
| **OpenAI** | OpenAI Chat | GPT 系列 |
| **DeepSeek** | OpenAI Chat | DeepSeek V4 等 |
| **Google Gemini** | OpenAI Chat | Gemini 2.5/3.1 系列 |
| **Ollama** | OpenAI Chat | 本地自托管 |
| **LM Studio** | OpenAI Chat | 本地自托管 |
| **Z.AI / GLM** | OpenAI Chat | 智谱 GLM 系列 |
| **xAI (Grok)** | OpenAI Chat | Grok 系列 |
| **Mistral** | OpenAI Chat | Mistral Large 等 |
| **NVIDIA NIM** | OpenAI Chat | NVIDIA 推理服务 |
| 自定义 | OpenAI Chat | 兼容 OpenAI API 格式 |

> **WebUI 首次运行引导**支持以上所有提供商的一键配置。

---

## 路线图

### 🚧 已完成

- [x] 跨平台桌面安装器（macOS + Windows）
- [x] Hermes Agent 一键安装 + 国内镜像加速
- [x] pywebview 原生桌面应用
- [x] WebUI 现代对话界面
- [x] 首次运行 onboarding 向导（12 提供商）

### 📋 计划中

- [ ] **Hermes 技能商店** — 浏览、安装、分享 Hermes Agent 技能
- [ ] **neowow.studio 集成** — 打通 AIGC 创作者生态
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
