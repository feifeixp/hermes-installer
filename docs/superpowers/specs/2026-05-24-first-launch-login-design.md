# 首次启动登录引导设计

**日期：** 2026-05-24  
**状态：** 已批准

## 问题背景

Windows EXE 首次启动时，用户可以直接进入 Hermes 主界面，但没有任何引导。用户需要手动进入 Settings → Providers 配置 API 密钥才能使用 AI 对话。这对普通用户门槛过高。

实际上 neowow.studio 提供免费套餐，只需登录账号即可使用，无需任何 API 密钥配置。

## 目标

- 未登录时显示全屏引导 Overlay，提示用户登录 neowow.studio
- 登录成功后自动将 provider 配置为 `neowow-coding-plan`，无需手动操作
- 登录后不再显示 Overlay（直到账号退出登录）
- macOS/Linux/Docker 部署不受任何影响

## 方案

**方案 B：WebUI 内 Onboarding Overlay**

WebUI 加载后检测 JWT 状态，未登录则显示全屏引导页。复用现有 `neowowAvatarClick()` 登录流程，登录成功后调用新 endpoint 自动完成 provider 配置。

## 架构设计

### 组件关系

```
index.html         neowow.js               routes.py / onboarding.py
─────────────      ──────────────          ─────────────────────────────
#onboardingOverlay ←── 显示/隐藏控制        POST /api/neowow/activate-provider
  ↓ 用户点击登录        ↑ 登录成功检测            ↓
  neowowAvatarClick() ──┘                  apply_onboarding_setup(
    （现有弹窗登录流程）                       {"provider":"neowow-coding-plan",
                                              "model": <default>})
```

### 触发条件

| 条件 | 行为 |
|------|------|
| 页面加载，`/api/neowow/status` 返回 `hasJwt=false` | 显示 `#onboardingOverlay` |
| 页面加载，`/api/neowow/status` 返回 `hasJwt=true` | Overlay 不出现（正常启动） |
| 登录成功（轮询检测到 `hasJwt=true`） | 调用 `activate-provider`，显示成功态，淡出 |
| 网络超时（无法到达 `/api/neowow/status`） | Overlay 不显示，降级进入主界面（现有 fallback） |

## 受影响文件

| 文件 | 改动说明 |
|------|---------|
| `webui/static/index.html` | 新增 `#onboardingOverlay` div，置于 `<body>` 末尾，`z-index` 高于主界面 |
| `webui/static/neowow.js` | `neowowBootInit()` 中：`hasJwt=false` 时显示 overlay；新增 `_neowowShowOnboarding()` / `_neowowCompleteOnboarding()` |
| `webui/api/routes.py` | 新增 `POST /api/neowow/activate-provider` 路由 |

## Overlay UI 设计

```
┌──────────────────────────────────────────────┐
│  （全屏，深色背景，与 Boot Overlay 风格一致）  │
│                                              │
│           🧠  Hermes                         │
│                                              │
│       欢迎使用 Hermes Agent                  │
│   登录 neowow.studio 账号，即可免费使用       │
│   AI 对话能力，无需配置任何 API 密钥。        │
│                                              │
│   ┌──────────────────────────────────────┐   │
│   │   登录 / 注册 neowow.studio          │   │  ← sm-btn
│   └──────────────────────────────────────┘   │
│                                              │
│   登录成功后自动配置，直接开始使用            │
│                                              │
└──────────────────────────────────────────────┘
```

**状态变化：**
1. 默认：显示登录按钮
2. 用户点击登录，弹窗关闭后等待轮询：按钮变为 "正在验证..." + spinner
3. 登录成功 + provider 配置完成：显示 "✓ 已就绪，正在启动..." 
4. 短暂停留（0.8s）后淡出

**样式：** 与现有 `#neowowBootOverlay` 一致（深色背景 `radial-gradient`，字体颜色 `#e2e8f0`，`z-index: 99998`，低于 Boot Overlay 的 `99999`）

## 新增 JS 函数（neowow.js）

### `_neowowShowOnboarding()`
- 设置 `#onboardingOverlay` `display:flex`
- 保存已展示标记（避免 Boot Overlay 结束时重复触发）

### `_neowowCompleteOnboarding()`
- 调用 `POST /api/neowow/activate-provider`（fire-and-forget，失败只打 warning）
- 更新按钮为成功态
- 800ms 后触发 `#onboardingOverlay` 淡出动画，`transitionend` 后设 `display:none`

### 修改 `neowowBootInit()`
在现有 `neowowHideBootOverlay()` 调用后插入：
```javascript
if (!hasJwt && networkOk) {
  _neowowShowOnboarding();
}
```

### 修改登录成功后的轮询回调
现有代码在检测到 `hasJwt=true` 后会刷新 avatar 状态。在该回调中追加：
```javascript
const overlay = document.getElementById('onboardingOverlay');
if (overlay && overlay.style.display !== 'none') {
  _neowowCompleteOnboarding();
}
```

## 新增后端路由

### `POST /api/neowow/activate-provider`

**位置：** `webui/api/routes.py`，置于 `/api/neowow/jwt` 路由附近

**实现：**
```python
if parsed.path == "/api/neowow/activate-provider":
    from api.onboarding import (
        _fetch_neowow_plan_models,
        apply_onboarding_setup,
        _NEOWOW_CODING_PLAN_PROVIDER_ID,
    )
    try:
        models, default_model = _fetch_neowow_plan_models()
        model = default_model or (models[0]["id"] if models else "deepseek-v4-flash")
        result = apply_onboarding_setup({
            "provider": _NEOWOW_CODING_PLAN_PROVIDER_ID,
            "model": model,
        })
        return j(handler, {"ok": True, "provider": _NEOWOW_CODING_PLAN_PROVIDER_ID,
                           "model": model})
    except Exception as e:
        logger.warning("[activate-provider] failed: %s", e)
        return bad(handler, str(e), status=500)
```

**注意：** `apply_onboarding_setup` 在 `provider == "neowow-coding-plan"` 且 `api_key` 为空时会自动从 `neowow.json` 读取 JWT，无需额外传参。

## 错误处理

| 场景 | 处理 |
|------|------|
| `activate-provider` 失败 | 前端 fire-and-forget：继续淡出 overlay，用户可进入主界面。后台打 `logger.warning`。 |
| 用户关闭登录弹窗不登录 | spinner 停止，按钮恢复"登录"态，等待用户重试。overlay 不关闭。 |
| 网络不通 | Boot Overlay 已有 `networkOk=false` 降级路径，此时不显示 onboarding overlay。 |

## 不在本设计范围内

- macOS/Linux 启动流程（完全不修改）
- 已有账号的"退出登录后再次显示"逻辑（现有行为：退出后 avatar 变未登录态，下次刷新会重新触发）
- 多账号切换
- 离线模式支持
