# 云端部署 Hermes WebUI（chat.neowow.studio）

把 Hermes WebUI 部署到云端服务器，让用户**零安装**通过浏览器使用。
是 hermes-installer 远程模式之 Phase 1 — "WebUI 和 Hermes Agent 都在云端，
共享给登录用户"。

> **本文档面向运维 / 部署人员。** 终端用户怎么使用云端 Hermes 看
> [REMOTE_DEPLOY.md](./REMOTE_DEPLOY.md)。

---

## 总体架构

```
[用户浏览器]
    │ HTTPS
    ▼
chat.neowow.studio  (Caddy 反代 + LetsEncrypt 自动证书)
    │ HTTP，docker compose 内部网络
    ▼
hermes-webui:7891   (hermes-installer image — Hermes Agent + WebUI 同进程)
```

整套就是 **2 个 Docker 容器**：Caddy（外）+ hermes-webui（内）。
镜像里把 Hermes Agent + Python venv + ~700 MB 的 wheels 全烤好了，部署
**只需要 docker pull + docker compose up**，跳过过去 10-15 分钟的现场安装。

**认证流程**（Neodomain OAuth）：
1. 用户访问 `https://chat.neowow.studio`
2. WebUI 检测无 `neoToken` cookie → 302 跳到 `https://app.neowow.studio/api/oauth/start?return=...`
3. Dashboard 完成 Neodomain OAuth 流程
4. Dashboard 把 `neoToken` cookie 写到 `Domain=.neowow.studio`（**两个子域都能看见**）
5. Dashboard 重定向回 `https://chat.neowow.studio` → 这次 cookie 已就位 → 进入 chat 界面

---

## 系统需求

- **Linux** Debian 11+ / Ubuntu 22.04+ / CentOS / Rocky / 几乎任何 Docker 跑得起来的发行版
- **2 GB RAM 起步**（容器占用 ~1.5 GB；4 GB 富裕）
- **公网 IP** + 80/443 入站
- **域名**指向你的服务器（A 记录指向 ECS 公网 IP；不能用 Cloudflare 代理 / 橘色云图标）

阿里云 ECS 推荐：**ecs.t6-c1m2.large**（2 vCPU / 4 GB RAM）或者更高。

---

## 一键部署

```bash
ssh root@your-ecs

curl -fsSL https://raw.githubusercontent.com/feifeixp/hermes-installer/main/docker/bootstrap-docker.sh \
  | sudo bash -s -- chat.yourdomain.com [you@example.com]
```

第二个参数是 LetsEncrypt 通知邮箱（可选）。

脚本做的事：
1. 检测/安装 Docker（用 `get.docker.com` 官方一键脚本）
2. 检测最优镜像源（中国 → Aliyun ACR；海外 → ghcr.io）
3. 写 `/opt/hermes-docker/docker-compose.yml` + `Caddyfile`，替换好你的域名
4. `docker compose pull` + `up -d`
5. 等两个服务都健康

预计 **2-5 分钟**完成（绝大多数时间在拉镜像）。中国 ECS 第一次拉 ~1 GB 镜像，
平均 3 分钟。

---

## 部署前 checklist

打钩之后再跑 bootstrap，少走弯路：

- [ ] **DNS 已配好**：`chat.yourdomain.com` A 记录直接指向你 ECS 公网 IP
- [ ] **Cloudflare 关代理**：如果用 Cloudflare DNS，云图标必须**灰色**（DNS only）；否则 LE 拿不到证书
- [ ] **AAAA 记录已删除**：除非你真有 IPv6 + 也指向 ECS（绝大多数情况删了它）
- [ ] **云平台安全组放行**：80 / 443 入方向 0.0.0.0/0 允许
- [ ] **磁盘 ≥ 20 GB**：镜像 ~1 GB + 系统 + 状态卷
- [ ] **可访问 Docker 镜像源**：脚本会自动选；中国服务器选 Aliyun ACR

---

## 验收

部署完跑一下：
```bash
curl -fsSL https://raw.githubusercontent.com/feifeixp/hermes-installer/main/docker/verify-deploy.sh \
  | sudo bash -s -- chat.yourdomain.com
```

预期所有项 ✓。如果某项 ✗ 看[故障排查](#故障排查)。

或者手动：
```bash
# 1. 容器都在跑吗
cd /opt/hermes-docker
docker compose ps    # 两个 Up，hermes-webui 是 healthy

# 2. WebUI 自己活着吗
docker compose exec hermes-webui curl -fsS http://127.0.0.1:7891/health
# 期望: {"status":"ok",...}

# 3. Caddy 拿到证书了吗
docker compose logs caddy 2>&1 | grep -E 'obtain|error' | tail -5
# 期望看到: "successfully obtained certificate"

# 4. 公网能访问吗
curl -I https://chat.yourdomain.com
# 期望: HTTP/2 302 (跳到 OAuth 登录)
```

---

## 常用运维命令

| 操作 | 命令（在 `/opt/hermes-docker/` 下跑） |
|------|---|
| 看服务状态 | `docker compose ps` |
| 跟 WebUI 日志 | `docker compose logs -f hermes-webui` |
| 跟 Caddy 日志（TLS / HTTP） | `docker compose logs -f caddy` |
| 跟 Watchtower 日志（自动更新） | `docker compose logs -f watchtower` |
| **手动**升级到最新镜像 | `docker compose pull && docker compose up -d` |
| 重启所有服务 | `docker compose restart` |
| 备份用户状态 | `docker run --rm -v hermes_state:/data -v $PWD:/backup alpine tar czf /backup/state-$(date +%F).tar.gz -C /data .` |
| **彻底清空**（destructive） | `docker compose down -v` |
| 进容器调试 | `docker compose exec hermes-webui bash` |

## 自动更新（Watchtower）

部署里默认启用了 **Watchtower** — 它会**每小时**轮询 registry，发现 `hermes-webui:latest` 有新 digest 就**自动 pull + recreate** 容器。

```
GitHub Actions build 完
   ↓ push image to ghcr.io + Aliyun ACR
   ↓ ECS 上 watchtower 下次轮询时发现新版本
   ↓ docker pull
   ↓ 优雅停 hermes-webui 容器
   ↓ 启动新容器
   ↓ healthcheck 通过 → 继续服务
   ↓ 删除旧镜像（节省磁盘）
   
[期间 5-10 秒 502，绝大多数用户感知不到]
```

### 配置（在 `/opt/hermes-docker/.env`）

```bash
# 轮询间隔（秒）。默认 3600 = 1 小时。
WATCHTOWER_POLL_INTERVAL=3600

# 时区，影响日志时间戳
WATCHTOWER_TZ=Asia/Shanghai

# 通知 URL（可选 — Slack / Telegram / Email / 自定义 webhook）
# 格式见 https://containrrr.dev/watchtower/notifications/
# 例子（Slack）: slack://token@channel
# 例子（Telegram）: telegram://token@telegram?chats=@channel
# WATCHTOWER_NOTIFICATION_URL=
```

改完跑 `docker compose up -d watchtower` 让新配置生效。

### 私有镜像（ACR / GHCR private）

如果你 Aliyun ACR 命名空间设为**私有**，Watchtower 拉镜像需要 docker 登录凭证。在 ECS 上**做一次** docker login：

```bash
# 阿里云 ACR
docker login --username=<你的用户名> registry.cn-shanghai.aliyuncs.com
# 输入固定密码

# 或者 ghcr.io
echo $GITHUB_TOKEN | docker login ghcr.io -u <github-username> --password-stdin
```

`/root/.docker/config.json` 会写入凭证，Watchtower 自动用它。

> 推荐设为**公开** — 你的镜像不包含敏感信息（只有 hermes-installer 代码 + 公开依赖），公开后省去登录维护。

### 暂时禁用自动更新

```bash
cd /opt/hermes-docker
docker compose stop watchtower
```

要恢复就 `docker compose start watchtower`。

### 手动触发立即检查

```bash
docker compose exec watchtower /watchtower --run-once
```

这条不影响后续轮询，只是马上跑一次。

### 排除某个容器不让自动更新

Watchtower 只更新带 `com.centurylinklabs.watchtower.enable=true` 标签的容器。我们的 compose 文件**只**给 `hermes-webui` 加了这标签。**Caddy 不会被自动更新**（保护 LE 证书 / ACME 状态）。

要更新 Caddy 镜像，手动跑：
```bash
docker compose pull caddy
docker compose up -d caddy
```

---

## 配置覆盖

### 改环境变量

编辑 `/opt/hermes-docker/docker-compose.yml`，改 `hermes-webui` 的 `environment:`，
然后 `docker compose up -d` 重建容器（state volume 会保留）。

| 变量 | 默认 | 说明 |
|------|------|------|
| `HERMES_WEBUI_AUTH_MODE` | `neodomain` | `none` / `password` / `neodomain` |
| `HERMES_WEBUI_PORT` | `7891` | 内部端口（容器外不直接暴露，Caddy 反代） |
| `HERMES_WEBUI_STATE_DIR` | `/opt/hermes/.hermes/webui` | 容器内状态目录，挂载在 hermes_state volume |
| `HERMES_NEODOMAIN_OAUTH_START` | `https://app.neowow.studio/api/oauth/start` | 自托管 dashboard 时改这个 |

### 改 Caddyfile（域名 / TLS / 路径）

编辑 `/opt/hermes-docker/Caddyfile`，然后：
```bash
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

不需要重启容器。

### 切换 / 升级镜像版本

`/opt/hermes-docker/.env` 里改：
```
HERMES_WEBUI_IMAGE=registry.cn-shanghai.aliyuncs.com/neowow/hermes-webui:main-abc1234
```
然后 `docker compose pull && docker compose up -d`。可以钉到具体 commit 实现回滚。

---

## ⚠️ 多用户共享同一个 Agent — 重要警告

⚠️ **当前 Phase 1 部署所有用户共享同一个 `~/.hermes/`**：
- A 用户的会话历史 B 用户能看到
- 任何人都能用同一个 LLM API key（在 `~/.hermes/.env`）
- 用户隔离靠**对你信任的小群体**（朋友、同事、工作室成员）

如果要给陌生人开放，Phase 2 会做"每用户独立 ECS"模式（用同一个 Docker 镜像 +
ECS-per-user 编排）。**Phase 1 不要部署给公网随机注册的用户。**

---

## 安全清单

- [ ] HTTPS 证书已自动签发（看 Caddy 日志确认）
- [ ] `HERMES_WEBUI_AUTH_MODE=neodomain` 已设
- [ ] 防火墙只开 80/443
- [ ] Docker daemon 用 root 跑，但 `hermes-webui` 容器内进程用 hermes 用户（容器内 UID 1500）
- [ ] state volume 权限锁定（Docker 默认 root:root，容器内 chown 为 hermes）
- [ ] 升级有节奏：CI 推主分支自动 build；你定期 `docker compose pull && up -d`

---

## 故障排查

### 容器启不来 / 立即退出
```bash
docker compose ps             # 看 State / Status
docker compose logs hermes-webui --tail 100
```
常见原因：
- 容器 OOM（4 GB 不够）→ 升级 ECS 或加 swap
- 状态目录权限错乱 → `docker compose down && docker volume rm hermes_state && docker compose up -d`（**清空用户状态**）
- 镜像拉不下来 → 检查 `/opt/hermes-docker/.env` 里 image 是否正确

### Caddy 拿不到 LetsEncrypt 证书
```bash
docker compose logs caddy 2>&1 | grep -i acme | tail -20
```
常见原因：
- DNS 没指对 → `dig @8.8.8.8 chat.yourdomain.com A` 看是否是你 ECS IP
- AAAA 记录还指向 Cloudflare → 删了 AAAA
- 80 端口没开 → 云控制台改安全组
- 中间人 / 反代（Cloudflare 橘色云）→ 切灰色

### `chat.yourdomain.com` 一直跳到 dashboard 又跳回来（OAuth 死循环）
- 检查浏览器开发者工具 → Application → Cookies：是否有 `neoToken=...; Domain=.neowow.studio`？
- 如果你的域名**不是 `*.neowow.studio` 子域**，cookie 不会跨域 — 要么改域名，要么改 dashboard 的 cookie domain（`COOKIE_DOMAIN` in `dashboard/src/app/api/oauth/callback/route.ts`）

### 升级后状态丢失
不应该发生 — `hermes_state` volume 是命名 volume，`docker compose up -d` 不会动它。
如果丢了：
- 看 `docker volume ls | grep hermes_state` 还在不在
- 之前是不是跑过 `docker compose down -v`（destructive）

### 想完全重来
```bash
cd /opt/hermes-docker
docker compose down -v        # 清掉 volume — 销毁会话 / 证书
rm -rf /opt/hermes-docker
# 然后重跑 bootstrap-docker.sh
```

---

## 升级到 Phase 2（多租户）

当用户量超过你愿意"信任彼此"的范围（一般 10+），开始考虑 Phase 2：每用户独立 ECS
实例。Phase 2 用同一个 Docker 镜像（`hermes-webui:latest`）+ ECS-per-user 编排，
不需要重新构建镜像。

详见 [`PHASE_2_DESIGN.md`](./PHASE_2_DESIGN.md)。

---

## 自托管你自己的 Docker 镜像

如果你不想依赖 `feifeixp/hermes-webui`（fork 了想用自己的版本）：

1. fork hermes-installer 仓库
2. 修 `.github/workflows/build-image.yml` 里的镜像名为你的
3. 在你 fork 的 GitHub Actions 中运行
4. 部署时用 `HERMES_REGISTRY=your-registry.com/your-namespace` 跑 bootstrap-docker.sh

或者直接在 ECS 上 build：
```bash
git clone https://github.com/feifeixp/hermes-installer.git /opt/hermes-installer
cd /opt/hermes-installer
docker build -f docker/Dockerfile.webui -t hermes-webui:local .
# 然后在 docker-compose.yml 里改 image: hermes-webui:local
```
