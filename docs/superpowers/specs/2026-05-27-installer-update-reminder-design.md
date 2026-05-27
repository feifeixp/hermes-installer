# 客户端版本更新提醒设计

**日期：** 2026-05-27
**状态：** 已批准

## 问题背景

每次发布 hermes-installer 新版本后，用户没有任何主动提示，需要：
- 自己去 GitHub Releases 看
- 或访问 landing page 看到旧版下载链接才意识到
- 或者直到老版本崩了才想起升级

GitHub 在国内访问慢/不稳定，所以已经有 `mirror-to-oss.yml` workflow 自动把 release assets 镜像到阿里云 OSS。但用户不知道 OSS 已经有新版了。

当前已有基础设施：
- ✅ GitHub Releases API（webui/api/neowow.py 已经用于 Docker 镜像更新检查）
- ✅ `main.py:_get_app_version()` 返回当前 installer 版本
- ✅ PyInstaller spec 把 `VERSION` 写入 .exe/.app 元数据
- ✅ WebUI 设置面板有"检查更新"按钮调 `/api/updates/check?force=1`
- ✅ Mirror-to-OSS workflow：发布触发 → 上传到 `oss://neowow/hermes/<version>/*` + `oss://neowow/hermes/latest/*`

唯一缺的：**主动通知用户**他装的版本过时了，并且 OSS 镜像已经准备好可下载。

## 目标

- WebUI 启动时检查 GitHub 是否有 hermes-installer 新 release
- 验证 OSS 镜像同步完成（HEAD 请求验通）才提示用户
- 顶部 banner 形式通知，可"查看更新内容"/"下载新版"/"跳过这个版本"/"稍后"
- "跳过这个版本" 持久化（settings.json），下一个版本前不再提示
- 不做自动下载/安装（超出当前 scope）

## 非目标 (Out of Scope)

- 自动下载并安装新版本（需要签名验证、增量更新、回滚等大量基础设施）
- 桌面原生通知（系统托盘 / Push Notification）
- 浏览器侧的"试用预览版"开关（让用户可选订阅 prerelease 通知）
- WebUI 主进程的自更新（webui/api/updates.py 已处理）
- Docker 镜像更新（webui/api/neowow.py:_check_docker_update 已处理）

这些都是合理的后续扩展，但当前 scope 是"基础提醒"。

## 方案选择

考虑过 3 个方案：

### 方案 A：WebUI 内置 banner ✅（选定）

启动时调一个新的 webui API → 检查 GitHub + 验证 OSS → 决定是否渲染 banner。点击 banner 按钮在浏览器中打开 OSS 下载链接。

**优点：**
- 复用现有 webui UI 框架（banner CSS 已有 reconnect-banner / offline-banner pattern）
- 不阻塞启动流程
- 用户可以选择"稍后"或"跳过"控制频率

**缺点：**
- 用户必须打开 webui 才会看到提示
- 不会在用户没打开 webui 的状态下提醒

### 方案 B：main.py 启动时原生对话框

main.py 启动时检查，有新版就 pywebview 弹原生对话框。

**缺点：** 比较侵入，影响启动流程，用户每次启动都被打断。

### 方案 C：菜单栏 "帮助 → 检查更新"

只在用户主动点击时才检查，最不打扰。

**缺点：** 大多数用户不会主动去点 → 等于没提醒机制。

## 架构

```
WebUI 启动 (boot.js)
     │
     │  GET /api/installer-update/check
     ▼
webui/api/installer_update.py
     │
     ├─► GET https://api.github.com/repos/feifeixp/hermes-installer/releases/latest
     │       (15 分钟 TTL 缓存，避免 GitHub API rate limit)
     │
     │   Response: tag_name, body (release notes), assets[], prerelease
     │
     ├─► 解析当前平台对应的 asset name
     │       macOS  → "Hermes-Installer-macOS.dmg"
     │       Windows → "Hermes-Installer-Windows.zip"
     │
     ├─► HEAD https://neowow.oss-cn-hangzhou.aliyuncs.com/hermes/<tag>/<asset>
     │       (验证 OSS mirror 同步完成；404 = 还没 mirror，等下次)
     │
     ├─► 对比当前版本 (从 main._get_app_version / HERMES_INSTALLER_VERSION env)
     │
     └─► 返回 JSON
              │
              ▼
{
  "current_version":   "v1.4.2",
  "latest_version":    "v1.4.3",
  "update_available":  true,
  "oss_ready":         true,
  "is_prerelease":     false,
  "release_notes":     "## What's Changed\n- ...",
  "release_notes_url": "https://github.com/feifeixp/hermes-installer/releases/tag/v1.4.3",
  "download_url":      "https://neowow.oss-cn-hangzhou.aliyuncs.com/hermes/v1.4.3/Hermes-Installer-Windows.zip",
  "fallback_url":      "https://github.com/feifeixp/hermes-installer/releases/tag/v1.4.3"
}
```

前端拿到结果后，**客户端**判断条件（不在服务端做，保持 `/check` 接口纯净 + 可缓存）：

```js
// settings 来自现有的 /api/settings 调用（webui 已有），boot.js 启动时
// 已经把 settings 存到 window.S 或类似的全局
const settings = await fetchSettings();  // 已有
const upd = await fetch('/api/installer-update/check').then(r => r.json());

if (upd.update_available
    && upd.oss_ready
    && !upd.is_prerelease
    && upd.latest_version !== settings.installer_skipped_version) {
  renderBanner(upd);
}
```

把 skipped 检查放客户端的理由：
- 服务端 `/check` 可以缓存（多用户场景里也安全）
- 客户端已经有 settings 数据
- 减少服务端逻辑分支

### 关键模块

| 文件 | 角色 |
|---|---|
| `webui/api/installer_update.py` | 新建。`check_installer_update()` 函数：调 GitHub Releases API → HEAD OSS → 返回 dict。带 TTL 缓存。Server-side 逻辑。 |
| `webui/api/routes.py` | 新增 2 个路由：`GET /api/installer-update/check` 转调 installer_update.check()；`POST /api/installer-update/skip` 写 settings.json |
| `webui/api/config.py` | `_SETTINGS_DEFAULTS` 加 `"installer_skipped_version": ""`（默认空字符串）|
| `webui/static/boot.js` | 启动时 fetch `/api/installer-update/check` → 决定是否渲染 banner |
| `webui/static/index.html` | 新 banner DOM 节点（默认 display:none） |
| `webui/static/style.css` | banner 样式（复用现有 `.reconnect-banner` 配色变量） |
| `webui/static/i18n.js` | 新增 banner 文案的中英文翻译 key |

### 版本对比

GitHub 返回 tag 如 `v1.4.3`。当前版本通过 `_get_app_version()` 拿到 `v1.4.2` 或 `1.4.2-3-g6d5a4b1c`（dev mode）。

简单字符串比较对 PyInstaller bundled 版本（始终 `v<x.y.z>` 严格 semver）够用。对 dev mode（git describe 输出含 `-N-g<sha>` 后缀）会判定为"非标准格式"直接返回 `update_available: false`（避免开发者看到"有新版"提示）。

实现：
```python
def _is_clean_semver(v: str) -> bool:
    return bool(re.fullmatch(r'v\d+\.\d+\.\d+', v.strip()))

def _compare_versions(current: str, latest: str) -> bool:
    if not _is_clean_semver(current) or not _is_clean_semver(latest):
        return False  # dev mode / unknown → don't suggest update
    # Strip leading 'v', split by dot, compare as int tuples
    c = tuple(int(x) for x in current.lstrip('v').split('.'))
    l = tuple(int(x) for x in latest.lstrip('v').split('.'))
    return l > c
```

### 平台 asset 名映射

```python
PLATFORM_ASSETS = {
    "darwin": "Hermes-Installer-macOS.dmg",
    "win32":  "Hermes-Installer-Windows.zip",
}
```

Linux 用户：API 返回 `update_available: false`（不存在 Linux 安装器）。

## UI Banner 设计

```
┌─────────────────────────────────────────────────────────────────────────┐
│  🚀  Hermes Installer v1.5.0 已发布（当前 v1.4.2）                       │
│      • Windows: 修复 Microsoft Store Python 兼容                         │
│      • 自动崩溃上报，开发侧能更快定位问题                                  │
│      [📥 下载新版]   [📋 查看更新内容]   [跳过这个版本]   ✕ 稍后        │
└─────────────────────────────────────────────────────────────────────────┘
```

**位置：** webui 顶部，session/chat 区域上方，跟 `reconnect-banner` / `offline-banner` 同一层（已有的 CSS 模式）。新 ID `#installerUpdateBanner`，新 class `.installer-update-banner`。

**展示规则：**
- `update_available && oss_ready && !is_prerelease && skipped_version !== latest_version`
- 默认展开。点 "✕ 稍后" → banner DOM 用 `display:none` 隐藏，状态只存 in-memory（关闭/刷新页面后下次启动会重新出现）
- 点 "跳过这个版本" → POST `/api/installer-update/skip` 把版本号存 webui 的 `settings.json`（`STATE_DIR/settings.json` 由现有 `load_settings()/save_settings()` 维护），下次启动 `latest_version === skipped_version` → 不弹。下一个新版本（不同的 tag）会重新弹。
- 设置面板可加"重置更新提醒"按钮（清空 `installer_skipped_version`）— 但本 spec 暂不做，可以后续加。

**3 个动作按钮：**

| 按钮 | 行为 |
|---|---|
| 📥 下载新版 | `window.open(download_url, '_blank')` —— 浏览器开 OSS 直链下载（系统默认浏览器会弹下载对话框）|
| 📋 查看更新内容 | 展开 banner 下方一个 markdown 渲染区域显示 release_notes（用现有 `marked.min.js`）|
| 跳过这个版本 | POST 设置 → 隐藏 banner |
| ✕ 稍后 | 仅前端 hide，不发请求 |

**Markdown 渲染：** 复用现有 `streaming-markdown` 库（webui 已经自托管 npm:streaming-markdown@0.2.15，messages.js 用它做流式消息渲染）。release notes 是静态文本，用一次性 parse 而不是 streaming，最终结果挂到 banner 下方一个可折叠的 `<div>` 里。

## 数据流时序

```
浏览器 boot.js          /api/installer-update/check        GitHub API                       OSS HEAD
       │                          │                            │                              │
   启动 ─────►                    │                            │                              │
       │ fetch /api/...           │                            │                              │
       │─────────────────────────►│                            │                              │
       │                          │  cache hit (<15min)?       │                              │
       │                          │     yes → return cached    │                              │
       │                          │     no  ↓                  │                              │
       │                          │── GET /releases/latest ───►│                              │
       │                          │                            │                              │
       │                          │◄────── 200 + payload ──────│                              │
       │                          │                            │                              │
       │                          │── compute asset url ──►    │                              │
       │                          │── HEAD OSS asset ────────────────────────────────────────►│
       │                          │                            │                              │
       │                          │◄────── 200 (or 404) ──────────────────────────────────────│
       │                          │                            │                              │
       │                          │  cache result for 15min    │                              │
       │◄───── 200 JSON ──────────│                            │                              │
       │                          │                            │                              │
   if update_available            │                            │                              │
     && oss_ready                 │                            │                              │
     && !is_prerelease            │                            │                              │
     && latest != skipped:        │                            │                              │
   renderBanner()                 │                            │                              │
       │                          │                            │                              │
   user clicks 跳过版本           │                            │                              │
       │ POST /api/...//skip      │                            │                              │
       │─────────────────────────►│                            │                              │
       │                          │── settings.json:           │                              │
       │                          │   installer_skipped_version = "v1.5.0"                    │
       │◄────── 204 ──────────────│                            │                              │
```

## 错误处理

### 失败场景

| 场景 | 处理 |
|---|---|
| 无网络 / GitHub API 不可达 | API 返回 `{ok: false, reason: "network"}`，前端不渲染 banner |
| GitHub 返回 rate-limited (403 secondary) | 缓存层 hit 返回上次结果。若无缓存，前端不渲染 |
| GitHub 有新 release 但 OSS 还没同步完（HEAD 404）| `oss_ready: false`，前端不渲染。15 分钟 TTL 过后下次启动再试 |
| GitHub 返回的版本是 prerelease | `is_prerelease: true`，前端不渲染（除非用户开了 dev 模式 — 本 spec 不做）|
| 当前版本 == latest 版本 | `update_available: false`，前端不渲染 |
| dev 模式（current_version 不是干净 semver）| `update_available: false`（don't false-positive）|
| 用户点 "跳过这个版本" 但 settings.json 写入失败 | 接口返回 500，前端 toast 错误，但 banner 仍按需求隐藏（前端层面的） |

### 缓存策略

`installer_update.check()` 用 module-level dict 缓存：

```python
_CACHE_TTL_SECONDS = 900  # 15 minutes
_check_cache: dict = {}   # {result: dict, fetched_at: float}
```

- TTL 内重复调用直接返回缓存
- 第一次启动 + 15 分钟后任意调用 → 重新去 GitHub + OSS
- 不需要持久化（启动时拉一次足够，关闭后重新启动重新查）

## 测试

### 单元测试 (`webui/tests/test_installer_update.py`)

| 测试 | 验证 |
|---|---|
| `test_check_returns_update_available_when_newer` | mock GitHub API 返回 v1.5.0，current = v1.4.2 → update_available=True |
| `test_check_returns_false_when_same_version` | mock 返回 v1.4.2，current = v1.4.2 → update_available=False |
| `test_check_skips_prerelease` | mock 返回 v1.5.0-rc1 prerelease=true → is_prerelease=True |
| `test_check_returns_oss_not_ready_when_404` | mock HEAD 返回 404 → oss_ready=False |
| `test_check_uses_cached_result_within_ttl` | 第一次 hit GitHub，第二次（15 分钟内）走缓存，第三次（>15 分钟后）重新 hit |
| `test_check_handles_github_rate_limit` | mock 返回 403 → 优雅降级 ok=False |
| `test_check_handles_invalid_current_version` | current_version = "1.4.2-3-g6d5a4b" → update_available=False |
| `test_check_platform_asset_mapping_darwin` | sys.platform="darwin" → download_url ends with `.dmg` |
| `test_check_platform_asset_mapping_win32` | sys.platform="win32" → download_url ends with `.zip` |
| `test_check_returns_false_on_linux` | sys.platform="linux" → update_available=False |
| `test_skip_endpoint_writes_settings` | POST `/api/installer-update/skip` body=`{version:"v1.5.0"}` → settings.json `installer_skipped_version` = "v1.5.0" |
| `test_skip_endpoint_validates_version_format` | POST 非 `v<x.y.z>` 格式 → 400 |
| `test_check_handles_missing_asset_for_platform` | GitHub returns release with no .dmg asset → oss_ready=False (no URL to HEAD) |
| `test_compare_versions_handles_2_digit_numbers` | v1.10.0 > v1.9.0（字符串比较会错误地说 9 > 10，整数 tuple 比较才对）|

### 集成测试

mock GitHub API + mock OSS HEAD HTTP server，验证：
- GitHub 200 + OSS 200 → 完整 payload
- GitHub 200 + OSS 404 → oss_ready=False
- GitHub 404 (no releases) → graceful

### 不测的

- 真正的 GitHub API → CI 不依赖外网
- 浏览器侧渲染 → 手动验证（同 crash-reporter 的方式）

## 影响范围

### 新增文件

- `webui/api/installer_update.py`（核心逻辑，~150 LOC）
- `webui/tests/test_installer_update.py`（单测）
- `webui/tests/test_installer_update_integration.py`（集成测试）

### 修改文件

- `webui/api/routes.py` — 新增 2 个路由（GET /check, POST /skip）
- `webui/api/config.py` — `_SETTINGS_DEFAULTS` 加 `"installer_skipped_version": ""`
- `webui/static/boot.js` — 启动时调 fetch，决定渲染
- `webui/static/index.html` — 新 banner DOM 节点
- `webui/static/style.css` — banner 样式
- `webui/static/i18n.js` — 新增 banner 文案的 i18n key（中英文 + 已有的 10+ locales 占位）

### 用户可见行为

- ✅ 启动 webui 看到新版本提示（如果有），可一键打开下载
- ✅ 选择"跳过这个版本"后该版本不再弹（下个版本会重新弹）
- ❌ 不会在没打开 webui 时主动通知
- ❌ 不会自动下载或安装

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| GitHub API rate limit 触发 | 15 分钟 TTL 缓存 + 优雅降级（不弹 banner）|
| OSS HEAD 慢导致 webui 启动延迟 | check 是异步的，前端 fetch 不阻塞页面渲染，banner 后续动态出现 |
| 版本格式不一致导致误判 | `_is_clean_semver` 严格校验 `v<x.y.z>` 格式，dev mode 直接 skip |
| 用户点"跳过"误操作想反悔 | 设置面板里有"重新启用更新提醒"按钮，清空 skipped_version |
| OSS 镜像延迟同步导致点击 404 | 已通过 HEAD 验证规避；万一发生，下载页 404 时浏览器自然报错 |

## 后续路径（不在此 spec）

- 自动下载安装（需要：签名验证 / 增量更新 / 回滚机制 / 跨平台 installer 链路）
- prerelease "试用通道"开关（设置面板加 toggle）
- 桌面原生通知（系统托盘）
- 多套源备选（GitHub Releases + OSS + 其他 CDN 自动 failover）
