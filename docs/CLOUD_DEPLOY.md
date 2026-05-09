# 云端部署 Hermes WebUI（chat.neowow.studio）

把 Hermes WebUI 部署到云端服务器，让用户**零安装**通过浏览器使用。
是 hermes-installer 远程模式之 Phase 1 — "WebUI 和 Hermes Agent 都在云端，
共享给登录用户"。

> **本文档面向运维 / 部署人员。** 终端用户怎么使用云端 Hermes 看
> [REMOTE_DEPLOY.md](./REMOTE_DEPLOY.md)。

## 总体架构

```
[用户浏览器]
    │ HTTPS
    ▼
chat.neowow.studio  (Caddy 反代 + LetsEncrypt 自动证书)
    │ HTTP loopback
    ▼
127.0.0.1:7891  (hermes-installer / WebUI server)
    │ in-process Python
    ▼
Hermes Agent (~/.hermes/...)
```

**认证流程**（Neodomain OAuth）：
1. 用户访问 `https://chat.neowow.studio`
2. WebUI 检测无 `neoToken` cookie → 302 跳到 `https://app.neowow.studio/api/oauth/start?return=...`
3. Dashboard 完成 Neodomain OAuth 流程
4. Dashboard 把 `neoToken` cookie 写到 `Domain=.neowow.studio`（**两个子域都能看见**）
5. Dashboard 重定向回 `https://chat.neowow.studio` → 这次 cookie 已就位 → 进入 chat 界面

---

## 系统需求

- **Linux** Debian 11+ / Ubuntu 22.04+（其他发行版需手动调整 apt 命令）
- **2 GB RAM 起步**（系统 ~500 MB + Python venv ~600 MB + Hermes Agent ~500 MB）
- **公网 IP** + 80/443 入站
- **域名**指向你的服务器（A 记录 / AAAA 记录）

阿里云 ECS 推荐：**ecs.t6-c1m2.large**（2 vCPU / 4 GB RAM，¥0.10/h 起步价），
后续根据并发用户数升级。

---

## 一键部署（推荐）

```bash
ssh root@your-ecs

curl -fsSL https://raw.githubusercontent.com/feifeixp/hermes-installer/main/deploy/cloud/bootstrap-cloud.sh \
  | bash -s -- chat.yourdomain.com
```

脚本做的事：
1. 装 Caddy（如果还没装）
2. 创建 `hermes` 系统用户（在 `/opt/hermes`）
3. clone hermes-installer 到 `/opt/hermes/hermes-installer`
4. 跑 `webui/start.sh` 一次装完 Hermes Agent + Python venv
5. 装 systemd unit
6. 写 `/etc/caddy/Caddyfile`（替换域名）
7. 启动两个服务

跑完后访问 `https://chat.yourdomain.com` 应该就能看到登录跳转。

---

## 手动部署（细节控）

如果一键脚本失败、或你要改动配置，看下面的步骤。

### 1. 装 Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

### 2. 创建 hermes 用户 + clone 代码

```bash
sudo useradd -r -m -d /opt/hermes -s /bin/bash hermes
sudo -u hermes git clone https://github.com/feifeixp/hermes-installer.git /opt/hermes/hermes-installer
```

### 3. 一次性跑 webui/start.sh 装依赖

```bash
sudo -u hermes bash -c "cd /opt/hermes/hermes-installer && bash webui/start.sh --foreground"
# 跑到 'Starting Hermes Web UI on http://...' 之后 Ctrl+C 即可
# 不要让它持续跑，systemd 会接管
```

### 4. 装 systemd unit

```bash
sudo cp /opt/hermes/hermes-installer/deploy/cloud/hermes-webui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-webui
```

确认起来了：
```bash
sudo systemctl status hermes-webui      # active (running)
curl http://127.0.0.1:7891/health       # {"ok":true}
```

### 5. 配 Caddy

```bash
sudo cp /opt/hermes/hermes-installer/deploy/cloud/Caddyfile.template /etc/caddy/Caddyfile
sudo sed -i "s|chat.neowow.studio|chat.yourdomain.com|g" /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

确认 HTTPS 通了：
```bash
curl -I https://chat.yourdomain.com    # HTTP/2 302（重定向到 OAuth）
```

---

## 常用运维命令

| 操作 | 命令 |
|------|------|
| 查日志 | `sudo journalctl -u hermes-webui -f` |
| 重启 | `sudo systemctl restart hermes-webui` |
| 查状态 | `sudo systemctl status hermes-webui` |
| 更新代码 | `cd /opt/hermes/hermes-installer && sudo -u hermes git pull && sudo systemctl restart hermes-webui` |
| 重新生成证书 | `sudo systemctl reload caddy` |
| 查 Caddy 日志 | `sudo journalctl -u caddy -f` |

---

## 配置参考

### 环境变量（在 `hermes-webui.service` 里改）

| 变量 | 默认 | 说明 |
|------|------|------|
| `HERMES_WEBUI_AUTH_MODE` | `neodomain` | `none` / `password` / `neodomain` |
| `HERMES_WEBUI_PORT` | `7891` | WebUI 监听端口（loopback only） |
| `HERMES_WEBUI_HOST` | `127.0.0.1` | 监听地址 |
| `HERMES_WEBUI_FOREGROUND` | `1` | systemd 必须 |
| `HERMES_WEBUI_STATE_DIR` | `~/.hermes/webui` | 会话 / 配置 / 用户数据存放 |
| `HERMES_NEODOMAIN_OAUTH_START` | `https://app.neowow.studio/api/oauth/start` | 鉴权入口（覆盖用于自托管 dashboard） |

### 资源限制

`hermes-webui.service` 默认配置：
```
MemoryMax=4G
CPUQuota=200%
```

并发用户多了之后调高，或者升级 ECS。

---

## 多用户共享同一个 Agent — 重要警告

⚠️ **当前 Phase 1 部署所有用户共享同一个 `~/.hermes/`**：
- A 用户的会话历史 B 用户能看到
- 任何人都能用同一个 LLM API key（在 `~/.hermes/.env`）
- 用户隔离靠**对你信任的小群体**（朋友、同事、工作室成员）

如果要给陌生人开放，Phase 2 会做"每用户独立 ECS"模式。
**Phase 1 不要部署给公网随机注册的用户。**

---

## 安全清单

- [ ] HTTPS 证书已自动签发（看 Caddy 日志确认）
- [ ] `HERMES_WEBUI_AUTH_MODE=neodomain` 已设
- [ ] `~/.hermes/.env` 文件权限 `0600`
- [ ] 防火墙只开 80/443
- [ ] `hermes` 用户没有 sudo 权限
- [ ] systemd 限制（`PrivateTmp=yes`、`ProtectSystem=strict`）已生效
- [ ] DNS 反向解析 / SPF 不影响 OAuth 回调

---

## 故障排查

### Caddy 拿不到证书
```
caddy ssl error: ... TLS-ALPN ... failed
```
- 检查 DNS 是否指向本机 IP（`dig +short chat.yourdomain.com`）
- 80 端口是否能从公网访问（云厂商安全组）
- 防火墙：`sudo ufw status`

### `chat.yourdomain.com` 一直跳到 dashboard 又跳回来
- 检查浏览器开发者工具，看 cookie：是否有 `neoToken=...; Domain=.neowow.studio`？
- 如果你的域名**不是 `*.neowow.studio` 子域**，cookie 不会跨域 — 要么改域名，要么改 dashboard 的 cookie domain（`COOKIE_DOMAIN` in `dashboard/src/app/api/oauth/callback/route.ts`）

### "Authentication required" 但 cookie 看着是好的
- 检查 JWT 是否过期：`echo <jwt> | python3 -c "import sys,base64,json; print(json.loads(base64.urlsafe_b64decode(sys.stdin.read().split('.')[1] + '==')))"`
- 看 `exp` 字段，过期了让用户重新登录
- 看 webui 日志：`sudo journalctl -u hermes-webui --since '5 min ago'`

### 503 / 502
- WebUI 进程挂了 → `sudo systemctl status hermes-webui`，看错误
- 端口冲突 → `sudo lsof -i :7891`
- 内存不足（OOM） → `dmesg | grep -i 'killed process'`

---

## 升级到 Phase 2（多租户）

当用户量超过你愿意"信任彼此"的范围（一般 10+），开始考虑 Phase 2：
- 每用户独立 `~/.hermes/<userId>/`
- 路由层根据 JWT userId 选目录
- 资源 quota（每用户 CPU/内存上限）

Phase 2 仍未实现 — 计划在 Phase 1 上线后根据实际反馈安排。
