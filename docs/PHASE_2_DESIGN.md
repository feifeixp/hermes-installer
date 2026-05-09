# Phase 2 设计文档 — 按需 ECS / 每用户独立实例

> **状态**：设计阶段。本文档指导后续实现。所有关键决策都列在
> "Open questions" 段，开干前必须 confirm。
>
> **前置**：Phase 1（共享 chat.neowow.studio）已上线并稳定运行。

---

## 1. 目标

把 Phase 1 的"所有用户共享一个 Hermes Agent"升级为"每用户一台独立
ECS"。架构上向 Codespaces / Gitpod 看齐 — 用户点击「开启我的 Session」，
云端动态拉起一台 ECS，连接、使用、闲置自动关停。

### 1.1 解决 Phase 1 的什么问题

| Phase 1 问题 | Phase 2 解决方式 |
|---|---|
| 用户互相看到彼此的会话 | 每人一台 VM，文件系统天然隔离 |
| 一个用户跑长任务卡死所有人 | 各自的 CPU / 内存配额 |
| 按服务器固定成本 → 无法商业化 | 按 ECS 时长扣积分，转嫁成本到使用者 |
| 不能给陌生人开放 | 隔离做到位之后可以正常 SaaS |
| 单一硬件配置 → 重度用户不爽 | 用户选 CPU / GPU 实例规格 |

### 1.2 不在 Phase 2 范围

- 用户**自带**机器（BYO 设备）→ Phase 3
- 跨 region / 多云容灾 → Phase 4+
- 团队共享一台 ECS（多个 userId 共用）→ Phase 4+

---

## 2. 架构概览

```
                    ┌─────────────────────────────────────┐
                    │       app.neowow.studio (CF)        │
                    │  ├─ Dashboard UI (existing)         │
                    │  ├─ Broker API   (NEW: spawner)     │
                    │  └─ Auth: Neodomain JWT (existing)  │
                    └────────┬────────────────────────────┘
                             │
              ┌──────────────┼──────────────────┐
              │              │                  │
              ▼              ▼                  ▼
    ┌────────────────┐ ┌────────────────┐ ┌────────────────┐
    │   TableStore   │ │  Aliyun ECS    │ │      OSS       │
    │ ─────────────  │ │ ─────────────  │ │ ─────────────  │
    │ instances{}    │ │ Per-user VMs   │ │ Per-user state │
    │ ├─userId→IP    │ │ from custom    │ │ ~/.hermes/<id>/│
    │ ├─state        │ │ image          │ │   sessions/    │
    │ ├─lastSeenAt   │ │                │ │   config.yaml  │
    │ └─specChosen   │ │                │ │   .env         │
    └────────────────┘ └────────────────┘ └────────────────┘

                             ▲
                             │ DNS A record per session
                             │ u-<userId>.chat.neowow.studio
                             │
                    ┌────────┴────────┐
                    │  User browser   │
                    └─────────────────┘
```

### 2.1 用户旅程

```
1. 用户登录 app.neowow.studio (Neodomain OAuth — Phase 1 已有)
2. 用户点击 "开启我的 Session" 按钮
3. Dashboard's POST /api/me/instance/start:
   ├─ 查 instances 表，看是否已有活实例
   ├─ 若有：直接返回 url
   └─ 若无：
      ├─ ECS API: RunInstance (从自定义镜像)
      ├─ 等 cloud-init 报告就绪 (~30-60s)
      ├─ DNS API: 设置 u-<userId>.chat.neowow.studio A 记录
      ├─ 写 instances 表
      └─ 返回 { url, instanceId, expiresAt }
4. 浏览器打开 https://u-<userId>.chat.neowow.studio
   ├─ neoToken cookie 跨子域可见 (Domain=.neowow.studio)
   ├─ WebUI 校验 JWT
   └─ 加载 chat 界面
5. 用户聊一会，agent 调工具，文件操作 — 所有 IO 在这台 ECS 上
6. 闲置 N 分钟：
   ├─ Broker 心跳监测 (轮询 WebUI 的 last-activity 端点)
   ├─ Broker 触发 sync-and-shutdown
   ├─ ECS 上：sync ~/.hermes/<userId>/ → OSS
   ├─ ECS 关机 (StopInstance — 保留实例配置但不计费)
   └─ DNS 记录保留 (下次 start 时刷新)
7. 用户再次开始 → 步骤 3，但实例已存在则 StartInstance (10-15s) +
   从 OSS 恢复 ~/.hermes/<userId>/，比首次快很多
```

---

## 3. 组件分解

### 3.1 Broker API (Dashboard 新增)

**文件位置**: `dashboard/src/app/api/me/instance/`

| Endpoint | 方法 | 作用 |
|---|---|---|
| `/api/me/instance/start` | POST | 拉起或唤醒用户实例，返回 url |
| `/api/me/instance/status` | GET | 查询当前实例状态（spawning/running/stopped） |
| `/api/me/instance/stop` | POST | 主动停机（用户点"结束 Session"） |
| `/api/me/instance/heartbeat` | POST | 实例上报最近活动时间（防止误判闲置） |

**Auth**: 全部走现有 `resolveCaller` (JWT)。复用 Phase 1 的 scope 系统：
- 加新 scope `instance:run` — 默认勾选，给所有 deploy token
- 加新 scope `instance:choose-spec` — 选高规格 / GPU 时需要
- `instance:run` 缺失时返回 403

### 3.2 TableStore: instances 表

复用现有 `router_table`，加新 PK 前缀 `inst_`：

```
PK: inst_<userId>
attrs:
  state:        spawning | running | stopping | stopped | error
  ecsInstanceId: i-bp1xxxxxxxx  (Aliyun ECS instance id)
  ecsRegion:    cn-shanghai
  publicIp:     1.2.3.4
  spec:         ecs.t6-c1m2.large | ecs.gn6v-c8g1.2xlarge | ...
  createdAt:    ISO timestamp
  lastSeenAt:   ISO timestamp (heartbeat)
  expiresAt:    ISO timestamp (planned shutdown)
  errorMessage: string | null
```

### 3.3 OSS state bucket

新建 bucket `neowow-hermes-state`（cn-shanghai，私有）。

**目录结构**：
```
neowow-hermes-state/
└── users/
    └── <userId>/
        └── hermes/
            ├── config.yaml
            ├── .env                   ← Encrypted with KMS, key per user
            ├── sessions/
            │   ├── <sessionId>.sqlite
            │   └── ...
            ├── skills/
            │   └── _neowow/           ← Same as Phase 1's skills sync
            └── webui/                 ← gateway.json, profile, etc.
```

**同步策略**：
- **拉取（spin-up）**：从 OSS 全量 rsync 到 ECS `/opt/hermes/.hermes/`
- **回写（spin-down）**：从 ECS rsync 到 OSS（只回写 sessions/ + webui/，不回 .env 因为没改）

### 3.4 自定义 ECS 镜像

用 Packer 构建一个 Aliyun 自定义镜像，预装：
- 基础 OS（Ubuntu 22.04）
- Caddy（反代 + LetsEncrypt — 但通配符证书要 DNS-01）
- Python 3.13 + uv
- hermes-installer 仓库（`/opt/hermes/hermes-installer`）
- Hermes Agent + venv（关键！把 Phase 1 那 5-10 分钟装机时间烤进镜像里）
- systemd unit `hermes-webui.service`
- **cloud-init 脚本** 处理 per-instance 启动逻辑

**Packer 配置**:
```hcl
source "alicloud-ecs" "hermes" {
  region          = "cn-shanghai"
  image_name      = "hermes-webui-{{timestamp}}"
  source_image    = "ubuntu_22_04_x64_20G_alibase_*.vhd"
  instance_type   = "ecs.t6-c1m2.large"
  ssh_username    = "root"
  io_optimized    = true
  internet_charge_type = "PayByTraffic"
  internet_max_bandwidth_out = 5
}

build {
  sources = ["source.alicloud-ecs.hermes"]
  provisioner "shell" {
    scripts = ["scripts/install-base.sh",
               "scripts/install-hermes.sh",
               "scripts/configure-systemd.sh"]
  }
}
```

**镜像更新流程**：
- hermes-installer 仓库每次 main 推送 → GitHub Action 触发 Packer build
- 新镜像 tagged `hermes-webui-<commit-sha>`
- 旧实例继续用旧镜像直到下次 spin-down → 重启时拉新镜像
- 紧急情况下 broker 可以强制全部用户的下次 start 拉新镜像

### 3.5 ECS cloud-init 脚本

每个实例启动时由 Aliyun 自动跑：

```yaml
#cloud-config
write_files:
  - path: /etc/hermes/instance.env
    content: |
      USER_ID="{{ user_id }}"
      OSS_PREFIX="users/{{ user_id }}/hermes"
      INSTANCE_ID="{{ instance_id }}"
      BROKER_URL="https://app.neowow.studio/api/internal/instance"

runcmd:
  # 1. 从 OSS 拉用户状态
  - ossutil cp -r oss://neowow-hermes-state/$OSS_PREFIX/ /opt/hermes/.hermes/
  - chown -R hermes:hermes /opt/hermes/.hermes/

  # 2. 解密 .env (KMS)
  - aliyun kms decrypt --ciphertext-blob $(cat /opt/hermes/.hermes/.env.enc) \
      --output text > /opt/hermes/.hermes/.env

  # 3. 启动 WebUI
  - systemctl start hermes-webui

  # 4. 通知 broker 就绪
  - curl -X POST $BROKER_URL/ready -d "instanceId=$INSTANCE_ID"
```

### 3.6 子域 + 通配符证书

每用户独立子域 `u-<userId>.chat.neowow.studio`。需要：
- DNS：`*.chat.neowow.studio` 通配符 A 记录指向**ECS IP**？— 不行，每用户 IP 不同
- 实际方案：每个实例**独立的 A 记录**，由 broker 在 spin-up 时通过阿里云 DNS API 写入

**TLS 证书**：
- 每个实例的 Caddy 自己签 LetsEncrypt（HTTP-01 challenge）
- 限制：LetsEncrypt 5 个证书 / 域 / 周。如果用户多到一周内开 5 个新实例就 rate-limit 了
- 解决：用通配符证书 `*.chat.neowow.studio`（DNS-01 challenge），broker 集中签发并分发到 ECS

通配符方案要权衡：cert 私钥放 ECS 上有泄漏风险。建议 broker 维护证书 + 短 TTL 分发。

---

## 4. 关键决策记录

### 决策 1：路径 vs 子域路由
**选择**：子域 `u-<userId>.chat.neowow.studio`
**拒绝**：路径 `chat.neowow.studio/u/<userId>/`
**理由**：
- WebUI 代码默认 root path，改 base path 会影响很多 absolute href
- Cookie 隔离：子域可以独立，路径不能（同源策略）
- 子域写 DNS 一次成本可控

### 决策 2：单进程多租户 vs 每用户 VM
**选择**：每用户独立 VM (Phase 2)
**拒绝**：单 ECS 内 routing-by-userId 多租户
**理由**：
- 多租户改动量大（每个 storage / API endpoint 都要改）
- VM 隔离更强（kernel 级 vs 应用级）
- 资源 quota 直接靠 VM 规格，不用自己实现
- 缺点：冷启动延迟 — 接受，加 pre-warm pool 可缓解

### 决策 3：状态持久化在 OSS vs 持久化磁盘
**选择**：OSS（按使用付费）+ rsync
**拒绝**：每用户挂载持久化云盘
**理由**：
- 云盘绑定 ECS — 跨 region / 跨实例移植困难
- 云盘最低 20 GB，每个用户都开就贵
- OSS 按对象付费，闲置用户接近零成本
- 缺点：spin-up 时多 5-10s rsync 开销 — 可接受

### 决策 4：用 Packer 镜像 vs 每次 cloud-init 装机
**选择**：Packer 自定义镜像
**拒绝**：通用 Ubuntu 镜像 + 启动时装机
**理由**：
- Phase 1 经验：装机 5-10 分钟。每次 spin-up 都装就太慢
- 镜像构建一次，启动只跑 cloud-init（pull state + start service）— ~30s
- 缺点：镜像更新流程复杂 — 用 GitHub Actions 自动化

### 决策 5：闲置阈值
**选择**：默认 15 分钟无 heartbeat → 关机
**拒绝**：不自动关机；或更短（5 分钟）
**理由**：
- 5 分钟太激进，用户切换 tab 写邮件就会断
- 永不关机就是 Phase 1，没意义
- 15 分钟覆盖大多数"中场休息"，用户要长跑可手动延长

### 决策 6：计费模型
**选择**：按 ECS 时长 + LLM token 双维度
- ECS：1 小时 = X 积分（X 由 spec 决定）
- LLM：现有 dashboard 已经做了

**拒绝**：包月不限时
**理由**：
- 包月需要预付，门槛高
- 按时长激励用户主动停机，省成本

---

## 5. 实施顺序（推荐）

### M1 — 镜像构建（1 周）
- Packer 配置 + Aliyun ECS provisioner
- 三个 shell provisioner: install-base / install-hermes / configure-systemd
- 在 cn-shanghai 跑通一次出第一版镜像
- GitHub Actions 自动化镜像构建（hermes-installer main 推送触发）

**完工标志**: 用阿里云控制台从镜像启一台实例，30 秒内 `https://<ip>:7891/health` 返回 200。

### M2 — Broker API（1 周）
- `dashboard/src/app/api/me/instance/{start,stop,status,heartbeat}/route.ts`
- 集成阿里云 ECS SDK + DNS SDK + OSS SDK
- TableStore `inst_<userId>` schema + CRUD
- 单元测试 + 模拟阿里云 API 的 fakes

**完工标志**: 通过 dashboard 的 API 能拉起 / 停掉一台真实 ECS，DNS 自动设置 + 拆除。

### M3 — Cloud-init + State sync（1 周）
- 写 `/etc/cloud-init.d/hermes-bootstrap.sh`
- ossutil 集成（pull on start, push on stop）
- KMS 加密 `.env`（每用户独立 key）
- Sync 测试：故意 dirty state，spin-down + spin-up，验证恢复

**完工标志**: 实例 reboot 后用户的会话历史完整恢复。

### M4 — Dashboard UI（3-5 天）
- `/account` 加「开始 Session」按钮
- Spinning 状态显示（"准备中... 30s 内就绪"）
- 实例列表 + 主动停机 / 强制重启 按钮
- 闲置警告 + 续命按钮

**完工标志**: 端到端从 dashboard 启动到聊天。

### M5 — 计费（3-5 天）
- ECS 时长 → 积分 转换
- 余额不足时禁止 start
- 后端记录每个实例的开始 / 结束时间
- 历史账单页

**完工标志**: 用户能看到自己花了多少积分。

### M6 — 通配符证书 + Pre-warm pool（可选优化）
- 通配符 cert via DNS-01
- Pre-warm pool（保持 N 个实例 idle 等用户）
- 缩短冷启动到 5-10 秒

**完工标志**: 95% 的用户首次 click → 浏览器加载 < 10 秒。

---

## 6. Open questions（实施前必须 confirm）

1. **阿里云 region 选择** — cn-shanghai 还是 cn-hangzhou？影响 ECS / OSS / TableStore 的同 region 优惠
2. **首版支持的 ECS 规格** — 只放 ecs.t6-c1m2.large 一种？还是开 GPU（gn6v）选项？后者贵 10x
3. **闲置阈值参数化** — 让用户自己选 5/15/30/60 分钟？还是固定 15
4. **通配符证书 vs 单证书** — M1 上线时哪种？通配符要解决私钥分发；单证书有 LE rate limit
5. **DNS provider** — 阿里云 DNS API 还是 Cloudflare？前者和 ECS 同域好管，后者已经在用
6. **失败的实例处理** — spin-up 失败（ECS 创建错、cloud-init 卡死）的恢复 — 自动重试 N 次还是直接报错让用户重试？
7. **数据保留策略** — 用户半年没登录，他的 OSS 状态保留还是清理？涉及隐私 + 成本
8. **多设备同时登** — 用户在桌面 + 手机同时打开，是连同一个实例还是各自起？后者会导致状态冲突
9. **管理员 override** — 管理员 / 开发能 ssh 进任意用户的实例做调试吗？涉及隐私权衡
10. **欠费处理** — 实例运行中余额清零，立即关机还是允许 grace period？

---

## 7. 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 阿里云 ECS API quota 不够 | 中 | 高 | 提前申请配额；分多个 region |
| LetsEncrypt rate limit | 中 | 中 | 用通配符证书 |
| OSS sync 数据丢失 | 低 | 高 | 双向校验 + 用户主动备份按钮 |
| ECS 镜像漏洞 / 0day | 低 | 高 | 镜像每周 rebuild + 滚动更新 |
| 用户欠费跑路留下僵尸实例 | 中 | 中 | 余额清零立即关机 |
| 高 GPU 实例被滥用挖矿 | 中 | 高 | CPU/GPU 使用率监控 + 阈值告警 |
| Dashboard broker 单点故障 | 中 | 高 | 横向扩展（Cloudflare Workers 已是无状态） |

---

## 8. 总工作量估计

```
M1 (镜像)        █████  1 周
M2 (broker)      █████  1 周
M3 (cloud-init)  █████  1 周
M4 (UI)          ███    3-5 天
M5 (计费)        ███    3-5 天
M6 (优化, 可选)  ████   1 周
————————————————————————————————
合计             4-5 周（不含可选项 6）
                 5-6 周（含可选项 6）
```

加上联调测试 + 处理 Open Questions 的 buffer，**实际 6-8 周**到稳定可上线。

---

## 9. 与 Phase 1 / Phase 3 的关系

**和 Phase 1 共存**：
- Phase 1 的共享 chat.neowow.studio 保留作为 free tier
- 用户可以同时使用：免费用 Phase 1，重要工作用 Phase 2 私有实例
- 同一套身份（Neodomain JWT）+ 积分体系

**为 Phase 3 (BYO 设备) 铺路**：
- Phase 2 的 broker 已经会"分配实例"
- Phase 3 把"分配阿里云 ECS" 替换为"分配用户提供的设备"
- TableStore inst 表加字段 `provider: aliyun-ecs | byo-<deviceId>`
- 文件桥接 / 反向隧道是 Phase 3 独立工作量

---

## 10. 下一步

1. **Open questions 你 confirm 答案** — 我整理成检查表逐项过
2. 我开 PR `phase-2-spike` — 先把 M1 (镜像) 跑通，看实际工时是不是 1 周
3. M1 跑完之后，再决定是按上面 6 周节奏推 M2-M5，还是中间抽几个并行做

设计文档先这样。读完有疑问 / 反对 / 想改的地方提出来，我改完再开始。
