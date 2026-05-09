# 远程部署 — 把 Hermes WebUI 跑在云端

让你的 Hermes Installer 桌面壳连接到云端服务器上部署的 Hermes WebUI，
而不是本机。适合多设备访问、GPU 服务器、或者团队共享一个 Agent。

> **注意范围**：本文档讲的是**路径 A**（远程 WebUI）。本机文件操作、
> Workspace、本机 Terminal 在远程模式下**不可用**（agent 跑在云端，
> 看不到你本机的文件）。如果你需要保留本机文件能力，看 README 的
> "路径 B：仅替换 LLM API"。

---

## 总体架构

```
[本机]                        [云端服务器]
桌面壳 (pywebview)             ├─ Hermes Installer (一份完整代码)
   │                          ├─ Hermes Agent (本地装好)
   │ HTTPS                    ├─ WebUI 服务 (server.py, port 7891)
   ▼                          └─ 反向代理 (Caddy/Nginx) + HTTPS
https://hermes.example.com ──→ port 7891
```

云端跑一份完整的 hermes-installer（Hermes Agent + WebUI 都装在云上），
本机的 Hermes Installer 只是个 pywebview 窗口指向云端的 URL。

---

## 第一步 — 服务端部署

### 选项 A：自有 VPS + Caddy（推荐，5 分钟）

#### 1. 装 Hermes Installer 到云端

```bash
ssh root@your-vps
git clone https://github.com/feifeixp/hermes-installer.git
cd hermes-installer
bash webui/start.sh   # 会装 Hermes Agent + 起 server.py
```

`start.sh` 跑完之后 WebUI 监听在 `127.0.0.1:7891`。

#### 2. 装 Caddy 反代

```bash
# Debian/Ubuntu
sudo apt install -y caddy

# 编辑配置
sudo tee /etc/caddy/Caddyfile <<'EOF'
hermes.example.com {
  reverse_proxy 127.0.0.1:7891
}
EOF

sudo systemctl reload caddy
```

Caddy 会**自动签 LetsEncrypt 证书**（前提：DNS 已经把 `hermes.example.com`
A 记录指向你的服务器 IP，且 80/443 端口开放）。

#### 3. 加访问控制（必须！）

WebUI 自己的认证靠 `HERMES_WEBUI_PASSWORD` 环境变量。开机前先设：

```bash
# 加到 /etc/systemd/system/hermes-webui.service 或 ~/.bashrc
export HERMES_WEBUI_PASSWORD='生成一个强密码'

# 重启 webui 服务
pkill -f 'webui/server.py'
bash webui/start.sh
```

现在访问 `https://hermes.example.com` 会先看到密码登录页。

> 进阶：如果你已经有 Neodomain 账号体系，建议在 Caddy 那一层加个
> OAuth2 proxy（`oauth2-proxy`）做 SSO，比静态密码更安全。

---

### 选项 B：Cloudflare Tunnel（无公网 IP / 不想买域名）

如果你的服务器没有公网 IP，或者懒得弄 DNS + 证书，用 Cloudflare Tunnel：

#### 1. 装 cloudflared

```bash
# 装 cloudflared 二进制
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared

# 登录到 Cloudflare（会弹浏览器）
cloudflared tunnel login
```

#### 2. 创建 tunnel 并绑定子域

```bash
cloudflared tunnel create hermes-prod
# 记下输出的 UUID

cloudflared tunnel route dns hermes-prod hermes.yourdomain.com
```

#### 3. 启动 tunnel

```bash
# ~/.cloudflared/config.yml
tunnel: <UUID>
credentials-file: /root/.cloudflared/<UUID>.json
ingress:
  - hostname: hermes.yourdomain.com
    service: http://127.0.0.1:7891
  - service: http_status:404
```

```bash
cloudflared tunnel run hermes-prod
# 后台跑：systemctl enable --now cloudflared
```

完事 — `https://hermes.yourdomain.com` 已经能访问，**自带 HTTPS 和 DDoS
防护**。还是别忘了设置 `HERMES_WEBUI_PASSWORD`。

> Cloudflare Tunnel 还有个好处：你服务器不用开任何端口给公网，
> 全部走出站连接。

---

### 选项 C：阿里云 FC / 容器（专业部署）

如果你在用阿里云函数计算 / ECS / 容器服务，思路一样：
- 容器跑 hermes-installer 镜像（在 webui/start.sh 之外加 Dockerfile）
- 把 7891 暴露给阿里云的 SLB / API 网关
- 在 SLB 那层做 HTTPS termination + 鉴权

具体配置因平台而异，超出本文档范围。

---

## 第二步 — 本机配置

云端 `https://hermes.example.com` 起来之后，本机 Hermes Installer 怎么连：

### 方法 1：UI 里点点（推荐）

1. 打开 Hermes Installer（仍是本机模式）
2. 点齿轮（⚙️）→ 「连接模式」
3. 选「远程连接」
4. 填 URL：`https://hermes.example.com`
5. （可选）显示名称：`我的 GPU 服务器`
6. 点「保存」
7. **退出 Hermes Installer 重启**

### 方法 2：手动写配置

```bash
mkdir -p ~/.hermes/webui
cat > ~/.hermes/webui/gateway.json <<EOF
{
  "mode":  "remote",
  "url":   "https://hermes.example.com",
  "label": "我的 GPU 服务器"
}
EOF
```

启动 Hermes Installer。

---

## 出问题怎么办

### 配置错了打不开
如果你填了一个错误的 URL（比如打错字 / DNS 还没生效），重启之后
Hermes Installer 会卡住或者报错。**应急恢复**：

```bash
# 终端运行
hermes-installer --reset-gateway

# 或手动删配置
rm ~/.hermes/webui/gateway.json
```

下次启动就回到本机模式。

### 远程 WebUI 加载白屏
打开浏览器直接访问 `https://hermes.example.com`，如果浏览器也白屏，
说明云端 WebUI 没起来：
```bash
ssh root@your-vps
ps aux | grep server.py    # 应该看到 server.py 在跑
curl -I http://127.0.0.1:7891/health    # 应该返回 200
```

### 在本机切回远程后又想切回来
两种方式：
- UI：齿轮 → 连接模式 → 选「本机运行」→ 保存 → 重启
- 或者点「重置为本机模式」按钮

---

## 安全建议

1. **密码必须设**。`HERMES_WEBUI_PASSWORD` 没设的话，任何知道 URL 的人
   都能进来用你的 Agent 花你的钱。
2. **HTTPS 必须开**（Caddy 默认就是；Cloudflare Tunnel 默认就是）。
   不要直接 `http://` 暴露 — Bearer token 会明文传。
3. **.env 文件保密**。云端 `~/.hermes/.env` 里有所有 LLM 提供商的 API
   key，别 push 到 git，文件权限 0600。
4. **更新及时**。Hermes Agent 升级会修安全问题；远程实例记得偶尔
   `cd hermes-agent && git pull`。
