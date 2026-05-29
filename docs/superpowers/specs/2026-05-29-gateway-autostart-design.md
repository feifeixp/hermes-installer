# 网关随容器自动启动（gateway auto-start）— 设计文档

**日期：** 2026-05-29
**状态：** 已通过设计评审，待写实现计划
**仓库：** hermes-installer（`webui/server.py`）

---

## 背景与问题

云端单容器部署里，平台网关（WeCom Bot 等）是 hermes-agent 的**前台进程** `hermes gateway run`，由用户/agent **手动**在容器内用一个 while-true 循环包起来跑：

```
bash -lic 'cd /opt/hermes/.hermes/hermes-agent && while true; do source venv/bin/activate && hermes gateway run 2>&1; echo "Gateway died ... restarting in 5s"; sleep 5; done'
```

- WebUI（`server.py`）**只检测**网关存活（读 `gateway.pid` / `gateway_state.json`），从不启动它。
- 单容器 `docker/docker-compose.yml.template` 里**没有 gateway service**，entrypoint 也不拉起。
- 手动循环能扛网关**崩溃**（5s 重拉），但扛不住**容器重建**：每小时 `/etc/cron.d/hermes-auto-update` 跑 `docker compose pull && up -d`，一旦拉到新镜像就重建容器，连循环带网关一起 SIGKILL，且不自动恢复 → Bot 掉线，需手动再起。

**触发实例：** 最近连推 3 个镜像（技能修复），08:00 那次 cron 重建容器，杀掉了网关。

## 目标
让网关**随容器自动起、扛得住容器重建**——把「网关守护循环」焊进 WebUI 容器启动。无需手动维护、无需改镜像 entrypoint / 宿主机 cron。

## 非目标
- 不改 `hermes gateway run` 本身（agent 内部）。
- 不做多平台逐一管理 UI（`hermes gateway run` 一进程按配置读平台，沿用现状）。
- 不改每小时 auto-update（它是 Bot 掉线的触发器，但本期只解决「网关自恢复」；是否改为手动升级是另一个功能/计划）。

---

## 设计

在 `webui/server.py` 启动流程里新增一个**守护线程**（与现有 `_startup_skill_sync` 同一位置，监听就绪后启动），逻辑：

1. **判断该不该跑网关（持久信号）**：读 root 级 agent home 下的 `gateway_state.json`。
   - 文件存在且 `gateway_state == "running"` → 该实例配过并跑过网关、且不是被主动停掉的（容器被 SIGKILL 重建时来不及写 `stopped`，所以重建后仍是 `running`）→ **应当拉起**。
   - 文件不存在 → 从没配过网关 → **不启动**（避免空转失败）。
   - `gateway_state == "stopped"` → 用户主动停了 → **不启动**（尊重意图）。
2. **防重复启动**：若已有 `hermes gateway run` 进程在跑（`gateway.pid` 指向的进程存活，或 `pgrep -f "hermes gateway run"`）→ **跳过**。这样不会跟现存的手动 while-true 循环打架（手动循环在下次容器重建后自然消失，焊进去的守护接管）。
3. **拉起守护循环**：以 `subprocess`（daemon，非阻塞）跑与手动循环等价的命令：
   ```
   bash -lic 'cd <AGENT_DIR> && while true; do source venv/bin/activate && hermes gateway run 2>&1; echo "[gateway-supervisor] died at $(date) - restart in 5s"; sleep 5; done'
   ```
   - `<AGENT_DIR>` = root agent home 下的 `hermes-agent` 目录（用 server.py 已有的 root-home 解析，避免 profile-scoped 路径；网关是 root 级单例，见 `agent_health.py` 注释）。
   - 输出重定向到日志（`logger` / stdout），便于排查。
4. **线程非阻塞**：daemon 线程，启动后立即返回，绝不拖慢 WebUI 接受请求（与 `_startup_skill_sync` 同样的容错：任何异常只 `logger.warning`，不影响 WebUI 启动）。

### 为什么放 server.py 而不是 entrypoint / 宿主机 cron
- WebUI 是容器主进程，`server.py` 每次容器启动都跑 → 自然「随容器起」；容器重建 → 新 server.py → 守护重启。
- compose `restart: unless-stopped`：WebUI 崩溃 → 容器重启 → 守护重启。
- 纯 `server.py` 单文件改动，重建镜像后所有实例自动获得；不动 cloud-init / 宿主机 cron / 镜像 entrypoint。

---

## 文件结构

| 文件 | 改动 |
|---|---|
| `webui/server.py` | 加 `_startup_gateway_supervisor()` 守护线程（紧挨 `_startup_skill_sync` 的线程启动处）+ 一个判断/启动的小辅助 |
| `webui/api/agent_health.py`（只读复用）| 复用其 root-home / gateway.pid / gateway_state.json 解析常量与 helper（不改逻辑）|
| `webui/tests/test_gateway_autostart.py` | 单测：`gateway_state.json` 各状态 → 是否应启动；已有进程 → 跳过 |

辅助函数（建议新放在 `webui/api/agent_health.py` 或 server.py 内）：
- `gateway_should_autostart() -> bool`：读 `gateway_state.json` 返回是否应拉起（running=True，stopped/缺失=False）。
- `gateway_process_alive() -> bool`：`gateway.pid` 存活 或 `pgrep -f "hermes gateway run"`。
- `agent_dir_for_gateway() -> Path`：root agent home / `hermes-agent`。

---

## 错误处理 / 安全
- 守护线程内所有异常 `logger.warning`，绝不影响 WebUI 启动（沿用 `_startup_skill_sync` 容错）。
- 防重复：已有网关进程则跳过，避免 `gateway.lock` 冲突 + 双进程。
- 主动停的网关（`stopped`）不被强行拉起。
- 没配网关的实例（无 `gateway_state.json`）完全不启动，零副作用。

## 测试
- **单测**（`test_gateway_autostart.py`，pytest）：mock root home 目录，写不同 `gateway_state.json`（running / stopped / 缺失 / 损坏 JSON），断言 `gateway_should_autostart()` 返回值；mock `gateway.pid`/pgrep 断言 `gateway_process_alive()` 与「已运行则跳过」。**不真正起子进程**（subprocess 启动用注入/打桩隔离）。
- **手动**：重建容器（`docker compose up -d --force-recreate`）→ 等待 WebUI 启动 → `docker exec hermes-webui sh -c 'pgrep -f "hermes gateway run"'` 有进程、`gateway_state.json` 回到 running、WeCom Bot 恢复响应。
- 现有 webui 测试全绿。

## 部署
- hermes-installer 改 → build-image 重建镜像 → 实例 `docker compose pull && up -d`（或每小时 cron / 将来的手动升级 UI）。
- 现存带手动 while-true 循环的实例：下次重建后手动循环消失，焊进去的守护接管，从此自恢复。

## 风险
- **`gateway_state.json` 字段/语义假设**：依赖 `gateway_state == "running"`（agent_health.py 已这么读）。若 agent 改了语义需同步——实现时以 agent_health 现有常量为准。
- **venv / agent 路径**：用 root agent home（非 profile-scoped），与 agent_health 的 gateway 单例路径一致。
- **创建后凭据被删**：`hermes gateway run` 会失败、循环 5s 重试刷日志。可接受（YAGNI）；用户主动停可写 `stopped` 止住。
