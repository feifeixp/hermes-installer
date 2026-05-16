# Tencent → Aliyun ECS migration playbook

> **Goal**: move `chat.neowow.studio` from the Tencent Cloud box to a
> brand-new Aliyun ECS in Singapore (`ap-southeast-1`), 2 vCPU / 4 GB.
> Co-locate everything on Aliyun (ECS + ACR + OSS) for simpler IAM,
> faster pulls, cheaper egress, RAM-role auth (no static AK/SK).
>
> **Status**: manual playbook for the FIRST Aliyun ECS (this becomes
> chat-<owner-userId>.neowow.studio). After this works, the broker's
> `lib/cloud/aliyun.ts` provider will spawn additional per-user ECSes
> automatically.

---

## Mental model — what talks to what

```
                ┌─────────────────────────────────────┐
                │  GitHub Actions (build-image.yml)   │
                │  ─────────────────────────────────  │
                │  • Triggers on push to main         │
                │  • Builds Docker image              │
                │  • Pushes to ghcr.io + Aliyun ACR   │
                └────────────────┬────────────────────┘
                                 │ docker push
                                 ▼
                ┌─────────────────────────────────────┐
                │  Aliyun ACR (personal edition)      │
                │  crpi-cbjdiuh3frfb6zz6.cn-shanghai. │
                │     personal.cr.aliyuncs.com        │
                │  neowow/hermes-webui:latest         │
                └────────────────┬────────────────────┘
                                 │ docker pull
                                 ▼
        ┌────────────────────────────────────────────────────┐
        │  Aliyun ECS (ap-southeast-1) — chat.neowow.studio  │
        │  ─────────────────────────────────────────────────  │
        │  • Ubuntu 24.04 LTS                                 │
        │  • Docker + docker-compose                          │
        │  • RAM Role: hermes-state-sync (NO AK/SK in .env)   │
        │  • Mounts: hermes_state (volume)                    │
        │  • Caddy:443 → hermes-webui:7891                    │
        └────────────────────────────┬───────────────────────┘
                                     │ HTTPS via OSS internal endpoint
                                     ▼
                ┌─────────────────────────────────────┐
                │  Aliyun OSS — neowow-hermes-state   │
                │  users/<userId>/hermes/             │
                │    sessions/  webui/  workspace/    │
                └─────────────────────────────────────┘
```

GitHub Actions doesn't touch the ECS. The ECS pulls.

---

## Part 1 — one-time Aliyun setup (manual, 30 min)

### Step 1.1 — Create OSS bucket

Aliyun console → **对象存储 OSS** → **创建 Bucket**:

| Field | Value |
|---|---|
| 名称 | `neowow-hermes-state` |
| 地域 | **新加坡 (ap-southeast-1)** ← match ECS region |
| 存储类型 | 标准存储 |
| 读写权限 | **私有** (NEVER public) |
| 版本控制 | 开启 |
| 服务端加密 | OSS 完全托管 |

After creation, note the **内网 Endpoint** (e.g.
`oss-ap-southeast-1-internal.aliyuncs.com`). Use this in ECS env vars
to avoid public-internet egress charges.

### Step 1.2 — Create RAM role (no static AK!)

This is the BIG security upgrade vs. Tencent. The ECS gets credentials
via instance metadata service — no AK/SK in `.env`, automatically
rotated by Aliyun.

**RAM 控制台 → 角色 → 创建角色**:

1. **可信实体类型**: 阿里云服务
2. **角色类型**: 普通服务角色
3. **角色名称**: `hermes-state-sync`
4. **选择受信服务**: ECS (云服务器)

After creation, click into the role → **添加权限** → **新建权限策略**:

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "oss:GetObject",
        "oss:PutObject",
        "oss:DeleteObject",
        "oss:ListObjects",
        "oss:GetBucketLocation"
      ],
      "Resource": [
        "acs:oss:*:*:neowow-hermes-state",
        "acs:oss:*:*:neowow-hermes-state/*"
      ]
    }
  ]
}
```

Name: `hermes-state-sync-oss-policy`. Attach to the role.

### Step 1.3 — Provision the ECS

**云服务器 ECS → 创建实例**:

| Field | Value |
|---|---|
| **付费方式** | 按量付费(测试)或包月(生产) |
| **地域和可用区** | 新加坡 → 随机可用区 |
| **实例规格** | `ecs.t6-c1m2.large` (2 vCPU, 4 GB)<br>or `ecs.c7.large` if t6 unavailable |
| **镜像** | Ubuntu 24.04 64位 |
| **存储** | 系统盘 ESSD PL0 **40 GB** |
| **公网 IP** | 分配,固定带宽 **5 Mbps**(后续可改) |
| **弹性 IP**(可选) | 推荐绑定,这样换 ECS 时 IP 不变 |
| **安全组** | 新建,放行: 22 (SSH限你的 IP), 80 (TCP, 0.0.0.0/0), 443 (TCP+UDP, 0.0.0.0/0) |
| **RAM 角色** | 选 `hermes-state-sync` ← 关键! |
| **登录凭证** | 自定义密码 / 密钥对 |
| **实例名称** | `hermes-chat-cn-singapore-01` |
| **主机名** | `hermes-chat-sg01` |
| **用户数据** | 留空,我们手动安装(避免 cloud-init 调试地狱) |

创建后,记下 **公网 IP** 和 **实例 ID**。

### Step 1.4 — DNS cutover (do this LAST, after Step 2 verifies)

**Cloudflare DNS** → `neowow.studio` 区:

- 暂时不动 `chat.neowow.studio` 的 A 记录(仍指向腾讯 IP)
- 加一条**新**记录: `chat-1928091762437005312.neowow.studio` A → 阿里云 ECS 公网 IP, 代理状态 **DNS only**(灰色云)

这样:
- `chat.neowow.studio` 还在腾讯,生产不受影响
- `chat-<id>.neowow.studio` 是阿里云的(测试用)
- 验证好了再把 `chat.neowow.studio` 切过去

---

## Part 2 — install Hermes on the new ECS (15 min)

### Step 2.1 — SSH in

```bash
ssh root@<阿里云公网 IP>
# 第一次登录时 ECS 已经有 RAM role, 验证一下:
curl -s http://100.100.100.200/latest/meta-data/ram/security-credentials/
# 应该返回: hermes-state-sync
```

### Step 2.2 — Install Docker

```bash
# 用阿里云镜像源加速 (Singapore region, 直接走 apt 也可以)
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo systemctl enable --now docker

# 验证
sudo docker --version
sudo docker compose version
```

### Step 2.3 — Log in to ACR (one-time)

ACR 个人版需要登录。访问凭证在 Aliyun 控制台 → **容器镜像服务** → **实例** → 你的个人版实例 → **访问凭证** → **设置/查看 Registry 登录密码**。

```bash
sudo docker login crpi-cbjdiuh3frfb6zz6.cn-shanghai.personal.cr.aliyuncs.com
# 用户名: 阿里云账号全名 (xxx@aliyun.com)
# 密码: 在控制台设置的 Registry 登录密码 (不是 Aliyun 账号密码)
```

### Step 2.4 — Bootstrap compose + Caddyfile

```bash
sudo mkdir -p /opt/hermes-docker
cd /opt/hermes-docker

# 拉取 docker-compose 模板 + Caddyfile 模板
sudo curl -fsSL https://raw.githubusercontent.com/feifeixp/hermes-installer/main/docker/docker-compose.yml.template -o docker-compose.yml
sudo curl -fsSL https://raw.githubusercontent.com/feifeixp/hermes-installer/main/docker/Caddyfile.template -o Caddyfile

# 替换占位符
DOMAIN="chat-1928091762437005312.neowow.studio"
ACME_EMAIL="ops@neowow.studio"   # 你的运维邮箱
sudo sed -i "s|%DOMAIN%|${DOMAIN}|g" Caddyfile
sudo sed -i "s|%ACME_EMAIL%|${ACME_EMAIL}|g" Caddyfile
```

### Step 2.5 — Configure .env (OSS sync 用 RAM role,不用 AK!)

```bash
sudo tee /opt/hermes-docker/.env > /dev/null <<'EOF'
# ──────────────────────────────────────────────────────────────────
# Aliyun ECS — OSS state sync via RAM role (no AK/SK needed!)
# ──────────────────────────────────────────────────────────────────
OSS_SYNC_ENABLED=1

# RAM role attached to this ECS (Step 1.2). ossutil reads creds from
# instance metadata service — auto-rotated, no secrets in this file.
OSS_RAM_ROLE_NAME=hermes-state-sync

# 留空 AK 表示走 RAM role 路径 — oss-sync-container.sh 自动识别
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=

# 内网 endpoint = 不走公网, 免流量费, 同 region 才有
OSS_ENDPOINT=oss-ap-southeast-1-internal.aliyuncs.com
OSS_BUCKET=neowow-hermes-state
OSS_USER_ID=1928091762437005312
OSS_SYNC_INTERVAL_SECS=300
EOF

sudo chmod 600 /opt/hermes-docker/.env
```

> ⚠️ 这一步要求 oss-sync-container.sh 支持 RAM role 模式 — 我会在
> 下一个 commit 加上。这之前可以临时用 AK/SK(同 Tencent 一样)。

### Step 2.6 — Pull image + start

```bash
cd /opt/hermes-docker
sudo docker compose pull
# 第一次 pull ~2 GB, 跨 region (Shanghai → Singapore) 可能要 5-10 min
# 后续 pull 只下变化的层,30 秒级

sudo docker compose up -d
sleep 30
sudo docker compose logs -f hermes-webui --tail=50
```

期望看到:
```
[entrypoint] OSS sync enabled (bucket=neowow-hermes-state ...)
[oss-sync] verify: ok
[entrypoint] pulling existing state from OSS...
[oss-sync] pull: done
[entrypoint] starting webui via webui/start.sh...
Hermes Web UI listening on http://0.0.0.0:7891
```

### Step 2.7 — Verify HTTPS works

```bash
# 等 Caddy 拿 LE 证书 (~30s)
sleep 30
curl -I https://chat-1928091762437005312.neowow.studio/health
# 应该: HTTP 200 + server: Caddy + valid TLS cert
```

浏览器访问 `https://chat-1928091762437005312.neowow.studio` — 应该跳到 app.neowow.studio OAuth → 登录 → 跳回 → 聊天工作。

---

## Part 3 — Migrate data Tencent → Aliyun (one-time, 5 min)

腾讯云已经有用户聊天记录。Aliyun ECS 是空的。**两边都同步到 OSS**,Aliyun 自动 pull 到。

### Step 3.1 — Force a final push on Tencent

```bash
# SSH 到腾讯云
ssh <腾讯云>
cd /opt/hermes-docker

# 临时也开 OSS sync (腾讯没 RAM role, 用静态 AK/SK)
sudo nano /opt/hermes-docker/.env
# 填:
#   OSS_SYNC_ENABLED=1
#   OSS_ACCESS_KEY_ID=<RAM 用户 AK>     # 见下面的注意
#   OSS_ACCESS_KEY_SECRET=<RAM 用户 secret>
#   OSS_ENDPOINT=oss-ap-southeast-1.aliyuncs.com   # 公网 endpoint (Tencent 用)
#   OSS_BUCKET=neowow-hermes-state
#   OSS_USER_ID=1928091762437005312
sudo chmod 600 /opt/hermes-docker/.env

# 重启容器以让 sync 启动
sudo docker compose pull
sudo docker compose stop hermes-webui
sudo docker compose rm -f hermes-webui
sudo docker compose up -d hermes-webui

# 看 final push 日志
sudo docker compose logs hermes-webui --tail=30 2>&1 | grep -i 'oss-sync'
```

> ⚠️ 注意: 腾讯云没法用 RAM role(不是阿里云环境)。需要创建一个**临时的 RAM 用户** (类型: 子账号, 类型: API only) 给 Tencent 用。完事后(Tencent 退役后)删掉这个用户。

### Step 3.2 — Trigger Aliyun ECS pull

```bash
ssh root@<阿里云 IP>
cd /opt/hermes-docker
sudo docker compose restart hermes-webui
# 容器重启时会再做一次 pull, 应该看到 Tencent push 上去的数据被 pull 下来
sudo docker compose logs hermes-webui --tail=50 2>&1 | grep -i 'oss-sync'
```

验证: 用浏览器登录 `chat-<id>.neowow.studio`,看到的会话列表应该跟腾讯的一致 ✓

### Step 3.3 — DNS cutover

```
Cloudflare DNS 控制台:
1. chat.neowow.studio  A 记录  从 <腾讯 IP> 改为 <阿里云 IP>
2. 5 分钟等 TTL 过期
```

或者更优雅: chat.neowow.studio 切成走 dashboard worker 的 broker landing (前面写的 middleware.ts + chat-landing 那套),broker 把用户重定向到 chat-<id>.neowow.studio (Aliyun ECS)。

### Step 3.4 — 退役腾讯云

确认 Aliyun 工作了 24-48 小时,**没用户报问题**之后:

```bash
ssh <腾讯云>
cd /opt/hermes-docker
sudo docker compose down -v   # ⚠️ 这会删除 volume — 但 OSS 上有备份
```

然后腾讯云控制台 → 实例 → **释放**(销毁)。

---

## Part 4 — GitHub Actions 不需要改

你担心 "github action 怎么连到阿里云" — 不用改。现状已经是:

`.github/workflows/build-image.yml`:
- 每次 push 到 main 时构建
- 已经配置了 push 到 `crpi-cbjdiuh3frfb6zz6.cn-shanghai.personal.cr.aliyuncs.com`
- 你的 GitHub secret `ALIYUN_ACR_USERNAME` + `ALIYUN_ACR_PASSWORD` 已设
- 阿里云 ECS 用 `docker compose pull` 主动拉取

**如果想加速跨 region pull** (cn-shanghai → ap-southeast-1):

选项 A: **保持现状** — 第一次 pull 慢 (~5-10 min), 后续增量快(<1min)。够用。

选项 B: **加 Singapore ACR repo**(需要把个人版升级到企业版),启用镜像同步。复杂度增加一倍,省 5 分钟首次 pull。

选项 C: **多 region 推送** — 改 `build-image.yml` 同时 push 到 Shanghai + Singapore 两个 ACR。

**推荐 A**, 别过早优化。

---

## Part 5 — Future: broker auto-spawn (Phase 2 endgame)

上面是手动配置 ONE Aliyun ECS。Phase 2 真正的形态:

1. 用户登录 `app.neowow.studio` → 点 "开启我的 Session"
2. Dashboard 的 `/api/me/instance/start` 调用 `lib/cloud/aliyun.ts`
3. 该 provider 调用 Aliyun OpenAPI `RunInstances`:
   - 拉起新 ECS (同上,2C 4G)
   - 挂 RAM role hermes-state-sync
   - 用户数据 = `docker/cloud-init.yaml.template` (已存在)
4. cloud-init 在 ECS 上自动:
   - 装 Docker
   - `docker compose pull`
   - 启动 chat-<userId>.neowow.studio
   - OSS pull 用户数据
5. Cloudflare DNS API 注册 `chat-<userId>.neowow.studio` A → 新 ECS IP
6. 用户被 broker 重定向过去, 自动登录

这就是 `lib/cloud/aliyun.ts` 的工作。**等手动 ECS 验证 OK 后**, 我用同样的步骤写 SDK 自动化。

---

## 检查清单

按这个顺序勾选,出问题立刻停下来排查:

- [ ] **A.** OSS bucket 已创建 (region=ap-southeast-1, 私有, 版本控制开)
- [ ] **B.** RAM 角色 hermes-state-sync 已建 + policy 已挂
- [ ] **C.** ECS 已创建, RAM 角色已挂 (实例详情看得到)
- [ ] **D.** SSH 可登录, `curl http://100.100.100.200/latest/meta-data/` 返回元数据
- [ ] **E.** Docker 装好, `docker version` 不报错
- [ ] **F.** `docker login` ACR 成功
- [ ] **G.** `docker compose pull` 成功 (image 是 latest sha)
- [ ] **H.** `.env` 里设了正确的 OSS_* 值, RAM role 在用
- [ ] **I.** `docker compose up -d` 成功, hermes-webui 状态 healthy
- [ ] **J.** Caddy 拿到 LE 证书, `curl -I https://chat-<id>.neowow.studio/health` 返 200
- [ ] **K.** 浏览器走 OAuth 流程能登进去
- [ ] **L.** OSS sync 工作: `_meta/last-synced.json` 在 OSS 控制台看得到, 每 5 分钟刷新
- [ ] **M.** Tencent 数据已 push 到 OSS, Aliyun 容器 pull 到了 (会话列表对得上)
- [ ] **N.** `chat.neowow.studio` DNS 切到阿里云, 实际用 1 天没问题
- [ ] **O.** Tencent 实例释放

每一步遇到问题立刻找我。
