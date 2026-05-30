# 云实例优雅更新（空闲自动 + 用户确认）— 设计文档

**日期：** 2026-05-30
**状态：** 已选定方案 A，待用户复核 spec
**仓库：** hermes-installer（`docker/` + `webui/`）

---

## 背景与问题

每台云 ECS 有每小时 cron（[cloud-init.yaml.template:221](docker/cloud-init.yaml.template)）：

```
0 * * * *  cd /opt/hermes-docker && docker compose pull && docker compose up -d
```

`up -d` 一旦发现 webui 镜像变了就**停旧容器、起新容器**——硬重启,不排空、不看有没有人在用:正在进行的对话流会断,~1–2 分钟不可用。这正是 08:00 cron 杀掉网关那次的根源。

## 目标

让云实例更新**优雅**：检测到新镜像时
1. **空闲就自动更新**（没有活跃会话/任务时静默重建,不打断任何人）；
2. 同时给用户一个 **banner + 「立即更新」**,想马上更新可手动触发；
3. 有人正在用且没点确认 → **不动**,等空闲。

## 非目标
- 不改更新检测「有没有新版本」本身（WebUI 已有 `get_update_notice` / `_version_newer` / docker 镜像检查）。
- 不引入 watchtower（历史上 SDK 不兼容,已废弃）。
- 不做强制更新/宽限期（用户已选「空闲自动」兜底,不需要强制）。

## 关键约束（已探查）
- **webui 容器是加固的**：无 `docker.sock`、`cap_drop: ALL`、`no-new-privileges:true`（[docker-compose.yml.template](docker/docker-compose.yml.template)）。**容器不能自我重建**——真正的 `docker compose up -d` 必须由**宿主机**执行。
- 所以采用**方案 A**：cron 只 `pull`；宿主机加一个 apply-watcher；容器↔宿主用**绑定挂载的控制目录**通信。容器保持加固,不挂 sock。

---

## 架构 / 数据流

```
每小时 cron:  docker compose pull   (只下载新镜像,不重启)         ──► 宿主本地缓存 :latest

控制目录 (bind mount):  host /opt/hermes-docker/control  ↔  容器 /opt/hermes/control
  · activity.json     WebUI→宿主   {"ts": <最近活跃 unix 秒>, "busy": <bool>}   (每 30s 写)
  · apply-requested   WebUI→宿主   touch 文件 (用户点「立即更新」时写)
  · update-available  宿主→WebUI   {"version/imageShort": ...}  (检测到新镜像时写)

apply-watcher cron (每 2 分钟):  /opt/hermes-docker/apply-update.sh
  staged = (运行中容器镜像 id  !=  本地 :latest 镜像 id)
  if not staged: 清理 update-available, exit
  写 update-available  (让 WebUI 弹 banner)
  idle = (activity.busy == false) AND (now - activity.ts >= 600s)
  if (apply-requested 存在) OR idle:
      docker compose pull && docker compose up -d        # 重建到新镜像
      成功后 rm apply-requested, rm update-available

WebUI:
  · 后台线程每 30s 写 activity.json (ts=最近非 /health 请求时间, busy=has_any_running_task())
  · 读 update-available → 经端点暴露给前端 → banner「有新版本,空闲时会自动更新」+「立即更新」
  · POST /api/neowow/apply-update → 写 apply-requested,返回「更新中,约 1–2 分钟」
```

**幂等/安全**：apply-watcher 用镜像 id 比对作 gate,没新镜像就是 no-op；`update-available`/`apply-requested` 在成功重建后清除。容器重建后控制目录在宿主、内容保留。

**决定性默认值**：空闲阈值 `IDLE_SECS=600`(10 分钟)；watcher 间隔 `2 分钟`；控制目录容器内路径 `/opt/hermes/control`。

---

## 文件结构

| 文件 | 改动 |
|---|---|
| `docker/cloud-init.yaml.template` | 每小时 cron 改 `pull` only(去掉 `&& up -d`);新增 apply-watcher cron(`*/2`);write_files 落 `apply-update.sh`;compose 段给 webui 加控制目录 bind mount;启动时 `mkdir -p /opt/hermes-docker/control` |
| `docker/docker-compose.yml.template` | webui `volumes:` 加 `- /opt/hermes-docker/control:/opt/hermes/control` |
| `docker/apply-update.sh` | **新建**：上面的 staged 检测 + idle/确认判定 + `up -d` + 清理,日志写 `/var/log/hermes-update.log` |
| `docker/bootstrap-docker.sh` | 同步上述 cron/脚本/控制目录的安装(它现在装每小时 cron) |
| `webui/api/self_update.py` | **新建**：`CONTROL_DIR` 常量;`read_update_available()`;`request_apply()`(写 apply-requested);`write_activity(ts, busy)`;纯函数 `should_apply(now, activity, apply_requested, idle_secs)`(给宿主脚本逻辑做对照单测的同款判定也放这,Python 侧仅用于测试 + WebUI 读写) |
| `webui/server.py` | 启动一个 `_activity_writer_loop` daemon(每 30s 写 activity.json),mirror 现有 heartbeat 线程 |
| `webui/api/routes.py` | `POST /api/neowow/apply-update` → `request_apply()`;`GET /api/neowow/update-available` → `read_update_available()`(或并入现有 update-notice 端点) |
| `webui/static/neowow.js` + `ui.js` | docker/cloud 模式下,轮询 update-available;有则在现有更新 banner 上显示「立即更新」按钮 → POST apply-update;文案说明「空闲时会自动更新」 |
| `scripts/migrate-fleet-graceful-update.sh` | **新建**：对现有实例一次性下发(改 cron + 落 apply-update.sh + compose 加 bind mount + `up -d` 一次)。经 SSH/RunCommand 跑 |
| `webui/tests/test_self_update.py` | 纯函数单测:`should_apply` 各组合;activity/读写往返;update-available 解析 |
| `docker/tests/` 或 shell 断言 | `apply-update.sh` 的 staged/idle 判定(用桩 docker 命令)——若 CI 无法跑则以 Python `should_apply` 对照逻辑为准 |

---

## 决策逻辑（apply-watcher 与 Python 对照都用同一套）

```
should_apply(now, activity, apply_requested, idle_secs=600) -> bool:
    if apply_requested: return True            # 用户显式确认,立即更新
    if activity is None: return True           # 没有活跃信号(WebUI 没写过)→ 视为空闲
    if activity.busy: return False             # 有进行中任务 → 不动
    return (now - activity.ts) >= idle_secs    # 静默期足够 → 空闲自动更新
```

`should_apply` 抽成纯函数(`self_update.py`),TDD 覆盖;`apply-update.sh` 内用 `jq`/`date` 实现等价逻辑(注释指向该纯函数为「真值表来源」)。

---

## 错误处理 / 安全
- 容器**保持加固**,不挂 docker.sock;特权动作只在宿主脚本里。
- 控制目录里都是无害的小文件(时间戳/touch);`apply-requested` 仅触发「拉官方镜像并重建」,不接受任意命令。
- `apply-update.sh` 任何步骤失败 → 记日志,不清 `apply-requested`(下次 watcher 重试),不影响运行中的容器。
- WebUI 端点失败 / 控制目录不存在(非 docker 环境)→ 端点返回明确状态,banner 不显示「立即更新」,零副作用(桌面版不受影响)。

## 部署 / 车队迁移
- **新实例**：cloud-init 更新后自动具备。
- **现有实例**：host 上已有旧 cron + 旧 compose,需一次性 `migrate-fleet-graceful-update.sh`(改 cron、落脚本、compose 加 bind mount、`up -d` 一次)。
- **鸡生蛋**：本功能要生效,需先把带「activity 写入 + apply 端点 + banner」的新 WebUI 镜像部署一次(这一次仍是旧式重建,之后才优雅)。可在迁移脚本里顺带完成。

## 测试
- **纯函数**(`test_self_update.py`)：`should_apply` 全组合(确认/无信号/busy/静默够/不够);activity 写读往返;update-available 解析容错。
- **手动**：构造一个新 `:latest` → 等 watcher → 有人活跃时不重建、banner 出现 →「立即更新」立刻重建 → 空闲超 10 分钟自动重建；`/var/log/hermes-update.log` 有迹可循。
- 现有 webui 测试全绿。

## 风险
- **镜像 id 比对**：`docker inspect` 运行容器镜像 vs `docker images -q :latest`。tag 重指但 id 同 → 不重建(正确)。实现时以实际 id 字段为准。
- **活跃判定精度**：`busy` 取 `has_any_running_task()`;若有后台任务长期不结束,会一直推迟自动更新——可接受(用户随时可手动「立即更新」)。
- **车队迁移**：宿主侧改动必须下发到每台;迁移脚本是一次性的人工/RunCommand 操作,需谨慎(改 cron + compose)。
- **范围偏大**：本 spec 跨宿主脚本 + cloud-init + WebUI 后端 + 前端 + 迁移。可分两阶段实施:Phase 1(单实例闭环:cron/watcher/compose/WebUI/banner),Phase 2(车队迁移脚本)。
