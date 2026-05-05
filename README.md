# ⚡ Hermes Installer

> [Hermes Agent](https://github.com/NousResearch/hermes-agent) 桌面客户端 — 一键安装 + 原生 AI 对话界面

[![License](https://img.shields.io/badge/license-MIT-purple)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey)](#)
[![Release](https://img.shields.io/github/v/release/feifeixp/hermes-installer?color=%237c3aed)](https://github.com/feifeixp/hermes-installer/releases)

---

## 📦 下载使用

> **推荐方式：直接下载打包好的桌面应用，双击即用。**

| 平台 | 下载 | 说明 |
|------|------|------|
| **macOS** | [⬇ Hermes-Installer-macOS.dmg](https://github.com/feifeixp/hermes-installer/releases/latest) | 双击 .dmg → 拖入 Applications → 右键打开 |
| **Windows** | 即将支持 | 正在适配 GitHub Actions Windows 构建 |

### 首次使用

1. 下载 `.dmg` 并打开，把 `Hermes Installer` 拖到 `Applications`
2. **首次打开**：右键 App → 打开（绕过 Gatekeeper）
3. App 自动检测环境 → 点击安装 → 配置 API Key → 完成
4. 下次打开直接进入 AI 对话界面，无需重复配置

> ⚠️ 请勿直接 `python main.py` 运行——已打包为原生桌面应用，通过 release 页面下载。

---

## ✨ 特性

- **智能引导**：打开即检测 Hermes 是否已安装，已装直接进对话，未装走 3 步安装向导
- **一键安装**：自动克隆 Hermes Agent、创建 venv、安装依赖（支持国内镜像加速）
- **原生桌面**：macOS WKWebView 原生窗口，非浏览器套壳
- **AI 对话**：流式响应、Markdown 渲染、代码高亮、多轮会话、Token 用量
- **中文优先**：完整简体中文界面（上游 webui 870 键中文本地化）

---

## 🛠 开发者（从源码构建）

需要 Python 3.10+ 和 pip。

```bash
git clone https://github.com/feifeixp/hermes-installer.git
cd hermes-installer

# macOS
bash build.sh
# → dist/Hermes-Installer-macOS.dmg

# Windows
build.bat
# → dist\Hermes-Installer-Windows.zip
```

### 上游同步

`webui/` 通过 `git subtree` 从 [nesquena/hermes-webui](https://github.com/nesquena/hermes-webui) 引入，每日自动检查更新：

```bash
# 手动触发同步
bash sync-webui.sh
```

GitHub Actions 每天自动检查上游并创建 PR —— 审核合并即可。

---

## 🗺 路线图

- [x] macOS 桌面应用 + 自动构建发布
- [x] Hermes Agent 一键安装 + 国内镜像加速
- [x] 完整中文界面
- [x] WebUI 上游自动同步
- [ ] **Windows 桌面应用 + CI 自动发布**
- [ ] **Hermes 技能商店** — 浏览、安装、分享 Hermes Agent 技能
- [ ] **neowow.studio 集成** — 打通 AIGC 创作者生态
- [ ] 应用内自动更新

---

## 🖥 macOS 用户说明

- 首次运行：右键 → 打开（绕过 Gatekeeper 未签名提示）
- 或终端执行：`xattr -cr "/Applications/Hermes Installer.app"`

---

## 🪟 Windows 用户说明

- Windows 11 内置 Edge WebView2，无需额外安装
- Windows 10 用户如遇问题，安装 [Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
- 首次运行 SmartScreen 提示时，点「更多信息」→「仍要运行」

---

## 📐 架构

```
┌──────────────────────────────────────────┐
│         Hermes Installer.app              │
│         pywebview 原生窗口                 │
├──────────────────────────────────────────┤
│  app.py (FastAPI :7891)                   │
│  · 安装向导 API   · 环境检测               │
│  · API Key 管理   · Gateway 代理          │
├──────────────────────────────────────────┤
│  webui/server.py (动态端口)               │
│  · AI 对话界面    · 设置面板              │
│  · 会话管理       · 文件浏览              │
└──────────────┬───────────────────────────┘
               │
               ▼
     Hermes Agent Gateway (:8642)
               │
               ▼
     LLM API (MiniMax / Anthropic / ...)
```

---

## License

MIT © 2025 [feifeixp](https://github.com/feifeixp)
