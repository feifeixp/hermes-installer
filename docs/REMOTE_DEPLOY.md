# 远程模式 — 让桌面 Hermes 连云端 WebUI

> **谁该看这个文档：** 已经装了 Hermes Installer 桌面版的用户，想让它
> 直接打开云端 WebUI，而不是在本机跑 Hermes Agent。
>
> **运维 / 部署人员**看 [CLOUD_DEPLOY.md](./CLOUD_DEPLOY.md)。

---

## 三种连接模式

桌面版 Hermes Installer 现在支持三种打开方式：

### 1. 本机模式（默认）
跟之前一样 — Hermes Agent 装在你这台机器上，所有数据本地。
- ✅ 隐私最好（文件不离开本机）
- ✅ 能改本地代码
- ❌ 装一次需要 5-10 分钟
- ❌ 只能在这台机器用

### 2. 云端模式（这个文档讲的）
桌面壳直接打开云端 WebUI，跳过本机安装。
- ✅ 零本机依赖
- ✅ 多设备 — 浏览器也能用同一个 URL
- ❌ 文件操作受限（看不到本机文件）
- ❌ 需要云端服务（你自己部署 OR 用 `chat.neowow.studio` 公共版）

### 3. 直接用浏览器
连桌面 Hermes Installer 都不用装，直接 https://chat.neowow.studio。
- ✅ 完全零安装
- 唯一缺点：要切换的时候要在浏览器收藏夹里找

---

## 怎么切到云端模式

### 在桌面 Hermes Installer 里：

1. 打开 Hermes Installer
2. 点齿轮（⚙️）→ 「连接模式」
3. 选「远程连接」
4. 填 URL：
   - 用我们提供的：`https://chat.neowow.studio`
   - 用你自己部署的：`https://chat.yourdomain.com`
5. （可选）显示名称：`我的 GPU 服务器`
6. 点「保存」
7. **退出 Hermes Installer 重启**

下次打开就是云端 WebUI 了。

### 直接命令行配置（高级）：

```bash
mkdir -p ~/.hermes/webui
cat > ~/.hermes/webui/gateway.json <<EOF
{
  "mode":  "remote",
  "url":   "https://chat.neowow.studio",
  "label": "Neowow Cloud"
}
EOF
```

启动 Hermes Installer。

---

## 切回本机模式

点齿轮 → 连接模式 → 选「本机运行」→ 保存 → 重启。

或者命令行重置：

```bash
hermes-installer --reset-gateway
```

---

## 出问题的应急恢复

如果你保存了一个错误的 URL（比如 typo），重启 Hermes Installer 之后
会卡住或者报错。**应急恢复命令**：

```bash
# Mac / Linux
hermes-installer --reset-gateway

# Windows（在终端 / Powershell）
"Hermes Installer.exe" --reset-gateway

# 或者直接删配置文件
rm ~/.hermes/webui/gateway.json
```

下次启动就回到本机模式了。

---

## 常见问题

### 云端模式下能改本机代码吗？
不能。云端 Hermes Agent 看到的是**云端机器**的文件系统，看不到你的本机。
如果要改本地代码，请用本机模式。

### 云端模式下我的 LLM API key 还需要吗？
**不需要**。云端跑的 Hermes Agent 用云端的 API key（管理员配的），
你只要消耗积分。在 `app.neowow.studio/account` 看积分余额。

### 我能同时用本机和云端两个 Hermes 吗？
可以。`mode=remote` 只影响桌面壳怎么打开 — 你还能开第二个 Hermes
窗口让它本机模式跑（在另一份 hermes-installer 里）。或者干脆浏览器开
云端版，桌面跑本地版，两个并行用。

### 云端的 Session 历史和本机的会同步吗？
不会（Phase 1）。云端 / 本机各自管自己的 `~/.hermes/sessions/`。
- **共享**：账号身份（Neodomain JWT）、技能商城订阅、Hermes 配置（如果你启用了云端配置同步）、积分余额
- **不共享**：会话历史、本地工作目录文件

如果你需要会话历史漫游，可以在 `app.neowow.studio` 的"Hermes 配置"
里把同一份 `config.yaml` 推给本机和云端两边。

### 用 `chat.neowow.studio` 公共版 vs 自己部署，有什么区别？
- 公共版：免费 / 简单 — 但是**所有用户共享同一个 Agent 实例**，
  会话历史互相可见，不适合保密任务
- 自部署：用你自己的 ECS / 服务器，独立账号系统，可以选 GPU 实例。
  看 [CLOUD_DEPLOY.md](./CLOUD_DEPLOY.md) 部署指南

Phase 2（计划中）会上"每用户独立 ECS 实例"模式，解决公共版的隔离
问题。
