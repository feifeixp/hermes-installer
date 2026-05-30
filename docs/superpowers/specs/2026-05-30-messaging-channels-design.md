# 消息渠道关联设计（微信扫码 / 飞书 / 企业微信）

> **Scope**: Hermes WebUI 新增「消息渠道」tab，让用户关联手机消息平台 —— 微信个人号扫码连接，飞书 / 企业微信填凭据连接（含教学）。底层 gateway adapter (`gateway/platforms/{weixin,feishu,wecom}.py`) 全已存在，本 spec 只补 WebUI 配置层。

**Repo affected**: `hermes-installer` (全部改动在 webui，零 backend 跨 repo)

**Decisions reached during brainstorming** (2026-05-30):

| 决策点 | 选择 |
|---|---|
| 平台范围 | 微信 + 飞书 + 企业微信，三个一起做 |
| UI 入口 | 左侧主导航新增「消息渠道」rail tab（跟 服务器/技能 同级） |
| 飞书/企微连接模式 | WebSocket 长连接（不碰公网回调/域名备案） |
| 微信 QR 对接 | WebUI 后端 stdlib urllib 直连 iLink QR API（不依赖 aiohttp） |
| 二维码渲染 | 前端 JS QR 库（~5KB）把 liteapp URL 画成图 |
| QR token 暂存 | webui 内存 dict（重启丢失=重扫，可接受） |
| Secret 回显 | masked（`cli_a1b2***` + `has_secret: true`），明文永不出后端 |

---

## 1. 架构总览

```
新 tab #mainMessaging（左侧 rail 注册 'messaging' panel）
  → 3 个 channel 卡片：微信 / 飞书 / 企业微信
  → 每卡片状态徽章：未配置(灰) / 已连接(绿) / 连接中(蓝) / 错误(红)
  → 微信卡片「连接」→ 扫码 modal
  → 飞书/企微卡片「配置」→ 表单 + 折叠式教学步骤

数据流：
  WebUI 写 → ~/.hermes/.env (FEISHU_* / WECOM_*)
           + ~/.hermes/weixin/accounts/<id>.json (微信)
  → 复用 gateway_autostart 重启 gateway 拾取新配置
  → 复用 GET /api/gateway/status + SSE 反馈连接状态
```

**复用的现成基础设施**（不重造）：
- `webui/api/gateway_config.py` + `GET/POST /api/gateway/config`
- `webui/api/gateway_autostart.py`（gateway 重启）
- `webui/api/gateway_watcher.py` + SSE status stream
- `~/.hermes/.env` 写入（config.py 已有 .env 处理）
- `gateway/platforms/weixin.py` 的 iLink 协议常量 + `save_weixin_account()`

---

## 2. 改动文件

| 文件 | Action | 责任 |
|---|---|---|
| `webui/api/messaging_channels.py` | Create | 3 channel 配置读写 + 微信 QR 代理（stdlib urllib）+ secret masking |
| `webui/api/routes.py` | Modify | wire 6 个新路由 |
| `webui/static/messaging.js` | Create | tab 渲染 + 微信 QR 状态机 + 飞书/企微表单 + 教学折叠 |
| `webui/static/index.html` | Modify | rail tab 按钮（2 处：rail + mobile）+ #mainMessaging DOM |
| `webui/static/style.css` | Modify | channel 卡片 / 徽章 / QR modal / 教学 details 样式 |
| `webui/static/i18n.js` | Modify | en + zh 文案（channel 名 / 徽章 / 按钮 / 教学步骤） |
| `webui/static/panels.js` | Modify | 注册 'messaging' 到 MAIN_VIEW_PANELS + lazy-load hook |
| `webui/static/vendor/qrcode.min.js` | Create | 轻量 QR 渲染库（~5KB） |
| `webui/tests/test_messaging_*.py` | Create | 4 个 pytest |

---

## 3. 微信扫码流程（iLink QR 直连）

### 3.1 iLink QR 协议（从 weixin.py 确认的契约）

```
常量（抄到 messaging_channels.py）：
  ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
  EP_GET_BOT_QR  = "ilink/bot/get_bot_qrcode"
  EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"
  headers: {"iLink-App-Id": ILINK_APP_ID, "iLink-App-ClientVersion": str(...)}
  （ILINK_APP_ID / ILINK_APP_CLIENT_VERSION / CHANNEL_VERSION 从 weixin.py 抄常量值）

流程：
  1. GET {ILINK_BASE_URL}/{EP_GET_BOT_QR}?bot_type=<N>
     → { qrcode: "<hex token>", qrcode_img_content: "<可扫码 liteapp URL>" }
  2. 轮询 GET {ILINK_BASE_URL}/{EP_GET_QR_STATUS}?qrcode=<hex token>
     → { status: "waiting"|"scaned"|"scaned_but_redirect"|"expired"|"confirmed", ... }
     confirmed 时附带: { ilink_bot_id: "<account_id>", token: "<bot token>" }
  3. confirmed → 写 ~/.hermes/weixin/accounts/<account_id>.json
                 (account_id, token, base_url=ILINK_BASE_URL)
               + 写 WEIXIN_ACCOUNT_ID=<account_id> 到 ~/.hermes/.env
               + 重启 gateway
```

### 3.2 WebUI 对接（webui 后端代理 iLink，前端轮询 webui）

```
浏览器                    webui 后端                      iLink API
  ├─ 点"连接微信" ───────>│                               │
  │                        ├─ POST /api/messaging/weixin/qr/start
  │                        ├──── GET get_bot_qrcode ─────>│
  │                        │<─── {qrcode, qrcode_img} ────┤
  │<── {qrcode_token,       │  webui 内存暂存 token→{created_at}
  │     qrcode_img_url} ─────┤
  ├─ JS 渲染二维码图        │
  ├─ 每 2s 轮询 ──────────>│
  │  GET .../weixin/qr/     ├──── GET get_qrcode_status ──>│
  │  status?token=xxx       │<─── {status,...} ────────────┤
  │<── {status} ────────────┤  confirmed→落盘+写.env+重启gateway
  ├─ 显示"✓ 已连接"         │
```

### 3.3 微信 QR 后端路由（3 个）

| 路由 | 作用 |
|---|---|
| `POST /api/messaging/weixin/qr/start` | 调 iLink get_bot_qrcode；返回 `{qrcode_token, qrcode_img_url}`；webui 内存暂存 token |
| `GET /api/messaging/weixin/qr/status?token=xxx` | 校验 token 在暂存且未过期 → 代理 iLink get_qrcode_status；status=confirmed 时落盘 account + 写 .env + 重启 gateway + 清暂存；返回 `{status, account_id?}` |
| `POST /api/messaging/weixin/disconnect` | 删 account json + 清 WEIXIN_ACCOUNT_ID + 重启 gateway |

### 3.4 微信 account 落盘

复用 agent 的 `save_weixin_account(hermes_home, account_id, token, base_url)` —— 若 agent 在 sys.path 可 import；否则在 messaging_channels.py 抄一份（写 `~/.hermes/weixin/accounts/<id>.json`，shape: `{account_id, token, base_url, saved_at}`）。

### 3.5 前端状态机（messaging.js 微信卡片）

```
idle → 点连接 → POST /qr/start → 渲染二维码 + "请用微信扫码"
  → 每 2s 轮询 /qr/status:
     waiting             → 保持
     scaned/scaned_but_redirect → "已扫码，请在手机确认"
     expired             → "二维码已过期" + 重新生成按钮 (停轮询)
     confirmed           → "✓ 微信已连接 (account xxx)" (停轮询, 刷新 channel 状态)
  → 网络错误 → "获取二维码失败" + 重试
```

---

## 4. 飞书 / 企业微信配置流程

两者都走 WebSocket 长连接（不碰公网回调）。

### 4.1 飞书字段（写 ~/.hermes/.env）

| 字段 | env | 来源 |
|---|---|---|
| App ID | `FEISHU_APP_ID` | 开发者后台 → 凭证与基础信息 |
| App Secret | `FEISHU_APP_SECRET` | 同上 |
| 连接模式 | `FEISHU_CONNECTION_MODE=websocket` | 后端固定写死（不让用户选） |

### 4.2 企业微信字段（确认自 wecom.py docstring）

| 字段 | env | 来源 |
|---|---|---|
| Bot ID | `WECOM_BOT_ID` | 企业微信管理后台智能机器人（required） |
| Bot Secret | `WECOM_SECRET` | 同上（required） |
| Websocket URL | `WECOM_WEBSOCKET_URL` | 可选，留空用默认（不在表单暴露，除非高级） |

wecom.py 是 "smart-robot adapter using websocket callback protocol" —— 默认就是 websocket 长轮询，无需额外模式开关。

### 4.3 教学步骤（卡片内 `<details>` 折叠，默认收起）

实现时参照 `~/.hermes/hermes-agent/website/docs/user-guide/messaging/feishu.md` + `wecom.md` 官方步骤校准。草稿：

**飞书**：
```
1. open.feishu.cn 开发者后台 →「创建企业自建应用」
2. 应用 →「凭证与基础信息」复制 App ID + App Secret，填到上面
3. 「权限管理」开启 im:message + im:message:send_as_bot
4. 「事件与回调」订阅方式选「长连接」(不配 webhook URL)，订阅 im.message.receive_v1
5. 「版本管理与发布」创建版本 → 申请发布（管理员审批）
6. 回这里点「保存并连接」
7. 飞书里拉机器人进群 / 私聊 @它 即可对话
```

**企业微信**：
```
1. work.weixin.qq.com 管理后台 →「应用管理」创建智能机器人 / 自建应用
2. 复制 Bot ID + Secret，填到上面
3. 接收消息选 websocket 长连接模式
4. 回这里点「保存并连接」
5. 企业微信里 @机器人 即可对话
```

### 4.4 飞书/企微后端路由（4 个）

| 路由 | 作用 |
|---|---|
| `POST /api/messaging/feishu/config` | body `{app_id, app_secret?}` → 写 FEISHU_APP_ID + FEISHU_APP_SECRET + FEISHU_CONNECTION_MODE=websocket → 重启 gateway。app_secret 留空=不改 |
| `POST /api/messaging/feishu/disconnect` | 清 FEISHU_* → 重启 gateway |
| `POST /api/messaging/wecom/config` | body `{bot_id, secret?}` → 写 WECOM_BOT_ID + WECOM_SECRET → 重启 gateway。secret 留空=不改 |
| `POST /api/messaging/wecom/disconnect` | 清 WECOM_* → 重启 gateway |

### 4.5 状态查询路由（1 个，3 channel 共用）

| 路由 | 返回 |
|---|---|
| `GET /api/messaging/channels` | `{weixin: {connected, account_id?}, feishu: {connected, app_id_masked?, has_secret}, wecom: {connected, bot_id_masked?, has_secret}}` |

connected 判定：对应 env keys / account json 都存在 → true。**明文 secret 永不出现在响应**。

---

## 5. UI 行为汇总

| 元素 | 行为 |
|---|---|
| 状态徽章 | 未配置(灰) / 已连接(绿) / 连接中(蓝) / 错误(红)，从 `/channels` + `/gateway/status` 推导 |
| 字段预填 | 已配置的从 `/channels` 读回（secret 显示 `••••已配置`，明文不回显） |
| 「保存并连接」 | POST config → 重启 gateway → 轮询 `/gateway/status` 看是否连上 → 更新徽章 |
| 「断开」 | POST disconnect → 重启 gateway → 徽章转灰 |
| 教学步骤 | `<details>` 折叠，默认收起 |
| 微信卡片 | 「连接」开 QR modal；已连接显示 account + 「断开」 |

---

## 6. 错误 / 边界场景

| 场景 | 处理 |
|---|---|
| 微信 QR 拉取失败（iLink 不可达/超时） | "获取二维码失败，请重试" + 重试按钮，不卡死 |
| 微信 QR 过期未扫 | status=expired → "二维码已过期" + 一键重新生成 |
| 微信轮询中 webui 重启 | 内存 token 丢失 → 轮询返回 token 失效 → 前端重新生成 |
| 微信 confirmed 但 account_id/token 不全 | 后端返回 error，不写 .env，前端"连接异常，请重扫" |
| 飞书/企微凭据填错 | gateway 重启后连不上 → `/gateway/status` error → "连接失败，请检查凭据" |
| gateway 重启失败 | 后端返回 500 + reason，前端"网关重启失败"，配置仍已写入（下次手动重启拾取） |
| 本地（非云端）打开 tab | 正常工作 — gateway 本地也跑，配置写本地 `~/.hermes/.env` |
| Linux 无 qrcode 渲染库 | 前端 JS 渲染，不依赖后端，无影响 |
| 微信 gateway 需 aiohttp | QR 流程本身用 stdlib urllib 不需要；gateway 运行 adapter 需要 → 卡片显示依赖缺失警告 / 教学提示首次自动装 |
| 并发两 tab 配同 channel | .env atomic write，last-write-wins，可接受 |
| 只改 App ID 不改 secret | 表单 secret 留空 = 不覆盖，只更新填了的字段 |

---

## 7. 测试策略

hermes-installer pytest (webui/tests) + 现有 venv `/Users/ff/hermes-installer/.build_venv/bin/python`：

| 测试 | 类型 | 覆盖 |
|---|---|---|
| `test_messaging_channels_status.py` | unit | `GET /channels` 正确反映 .env/account json 状态；secret masking 不泄露明文 |
| `test_messaging_weixin_qr.py` | unit | mock iLink urlopen：qr/start 返回 token+img；qr/status 各状态透传；confirmed 落盘 account + 写 .env |
| `test_messaging_config_write.py` | unit | feishu/wecom config 正确写 .env（含 FEISHU_CONNECTION_MODE=websocket）；disconnect 清 keys；masked secret 留空不覆盖 |
| `test_messaging_routes_wired.py` | unit | 6 新路由都在 routes.py 注册（ast 检查） |
| JS syntax | static | messaging.js / i18n.js `new Function()` 解析通过 |
| 手动 e2e | manual | (1) tab 出现；(2) 微信扫码连接；(3) 飞书填凭据连上；(4) 徽章正确；(5) 断开后 gateway 不再连；(6) secret 不在 `/channels` 明文 |

---

## 8. i18n

en + zh（其他 fallback en）。键前缀 `messaging_`。覆盖：3 channel 名 + 4 状态徽章 + 按钮（连接/配置/保存并连接/断开/重新生成二维码）+ 教学步骤标题 + QR 状态提示文案。

---

## 9. 不在本 spec 范围内

- **其他平台**（Telegram/Discord/Slack/钉钉/QQ 等 adapter 已存在但本次不做 UI）— 同一 channel-card 模式扩展是 future work
- **微信公众号 / 服务号**（需企业认证 + 备案）— 本次只做个人微信 iLink
- **飞书/企微 webhook 回调模式**（需公网域名）— 本次只做 websocket 长连接
- **群消息策略 UI**（DM_POLICY/GROUP_POLICY 等高级字段）— 走默认值，高级配置仍可手编 .env

---

## 10. 自审

- ✅ Placeholder：教学正文标注"实现时参照官方 docs 校准"，非占位符；其余无 TBD/TODO
- ✅ 内部一致性：env key 名（FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_CONNECTION_MODE / WECOM_BOT_ID/WECOM_SECRET / WEIXIN_ACCOUNT_ID）全文一致；6 路由前缀 `/api/messaging/` 一致
- ✅ Scope：单一 WebUI 改动，可独立 ship，零 backend 跨 repo，底层 adapter 全已存在
- ✅ Ambiguity：错误场景 §6 表格明示；secret masking 规则明确（留空不覆盖 + masked 回显）
- ⚠️ 实现期待确认：wecom 确切字段（已从 docstring 确认 = BOT_ID+SECRET）；iLink 常量值（ILINK_APP_ID 等，实现时从 weixin.py 抄）
