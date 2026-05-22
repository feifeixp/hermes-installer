# 官方默认技能预装 — 设计文档

**日期：** 2026-05-22  
**状态：** 已审批，待实现  
**涉及仓库：** `hermes-installer`（WebUI）、`aliyun-supa`（Dashboard）

---

## 1. 背景与目标

### 问题
HermesAgent 实例开机后技能为空，用户需要手动去 neowow.studio 订阅技能、再手动点"同步"，才能使用市场里的技能。这对新用户的首次体验极不友好。

### 目标
- 平台管理员在 Dashboard 将部分技能标记为"官方默认"
- 所有 Hermes 实例开机自动预装这些技能，无需用户操作
- Agent 的 system prompt 自动包含技能列表，让 agent 知道自己有哪些能力
- 用户可以在 WebUI 里移除不需要的默认技能，移除后不会被重新安装

### 不在本期范围
- 用户订阅技能的开机自动同步（仅官方默认技能自动同步，用户订阅仍需手动触发）
- 技能从本地发布到市场（Phase 1.5 已规划）
- 默认技能的版本锁定（跟随最新版本）

---

## 2. 架构概览

```
┌─────────────────────────────────────────────────────┐
│                  Dashboard (neowow.studio)           │
│                                                     │
│  TableStore: isDefault='true' ← Admin 后台打标       │
│       ↓                                             │
│  GET /api/me/skills  ← 扩展：无论有无订阅，          │
│  isDefault 技能都包含在响应里（带 content）           │
│                                                     │
│  POST /api/admin/skills/[id]/set-default            │
│  GET  /api/public/skills  ← 加 isDefault 字段       │
└─────────────────┬───────────────────────────────────┘
                  │  Bearer token（neodomain 模式已有）
                  ▼
┌─────────────────────────────────────────────────────┐
│          Hermes WebUI（容器启动时，后台线程）          │
│                                                     │
│  _startup_skill_sync()                              │
│    ├─ GET /api/me/skills（订阅 + 官方默认）           │
│    ├─ 跳过 _dismissed.json 里的 ID                   │
│    ├─ 写 ~/.hermes/skills/_neowow/<id>/SKILL.md      │
│    └─ 调用 rebuild_skills_system_prompt()            │
│                                                     │
│  WebUI 设置面板 → "官方默认技能" → [移除] / [恢复]   │
└─────────────────────────────────────────────────────┘
```

**关键设计决策：**
- 官方默认技能与用户订阅技能**走同一套同步流程**，仅 `_neowow.json` 里的 `isDefault` 字段区分来源
- 用户移除的技能 ID 持久化到 `_dismissed.json`，每次同步跳过，**不自动恢复**
- System prompt 采用**双层存储**：base（来自 Dashboard ConfigBlob）+ skills appendix（本地生成），两层独立，通过 `rebuild_skills_system_prompt()` 合并后写入 `config.yaml`

---

## 3. Dashboard 改动（aliyun-supa）

### 3.1 TableStore schema

在 `skill-*` 行新增列：

| 列名 | 类型 | 说明 |
|------|------|------|
| `isDefault` | string `'true'/'false'` | 是否为官方默认技能 |

### 3.2 Admin 打标接口

**新建：** `POST /api/admin/skills/[id]/set-default`

```ts
// Request body
{ "isDefault": boolean }

// Response
{ "ok": true, "id": "skill-abc123", "isDefault": true }
```

- 使用现有 `isAdmin()` 鉴权
- 向 TableStore `updateRow`（列级写入，不覆盖其他字段）

### 3.3 扩展 `GET /api/me/skills`

**当前逻辑：** 只返回 `subscribers` 数组包含该用户 ID 的技能。

**新逻辑：**
```
返回 = (用户已订阅) UNION (isDefault = 'true' 的全部技能)
每条附加字段：
  "isDefault": boolean
  "content":   string   // 已有
```

重叠处理：若某技能同时是 isDefault 且用户已订阅，`isDefault: true`，只返回一条。

扫描实现：`scanRouterTable(PrefixRange.skills)` 已经返回全部 `skill-` 行，在 TypeScript 里 `.filter(r => r.isDefault === 'true')` 筛出默认技能，与订阅集合合并去重。无需额外索引，与 `/api/public/skills` 现有扫描模式一致。

### 3.4 `GET /api/public/skills` 加 `isDefault` 字段

在公开列表的 map 步骤加入：
```ts
isDefault: String(r.isDefault || '').trim() === 'true',
```
用于市场页展示"官方推荐"角标（仅元数据，无 content）。

---

## 4. Hermes WebUI 改动（hermes-installer）

### 4.1 本地文件布局

```
~/.hermes/skills/_neowow/
├── _dismissed.json          ← ["skill-abc123", ...]  用户移除的 ID 列表
├── _base_prompt.txt         ← Dashboard ConfigBlob.systemPrompt 原文
├── _skills_prompt.txt       ← 本地自动生成的技能附录
├── README.md                ← 已有，系统管理说明
└── skill-abc123/
    ├── SKILL.md
    └── _neowow.json         ← 新增 "isDefault": true 字段
```

### 4.2 `api/skills.py` 扩展

#### 新函数：`read_dismissed() → set[str]`
读取 `_dismissed.json`，返回用户已移除的技能 ID 集合。文件不存在时返回空集合。

#### 新函数：`dismiss_skill(skill_id: str) → None`
将 ID 加入 `_dismissed.json`，删除本地 `_neowow/<id>/` 目录。

#### 新函数：`restore_skill(skill_id: str) → dict`
从 `_dismissed.json` 移除 ID，重新从云端拉取并写入本地。

#### `sync_subscribed_skills()` → 重命名/扩展为 `sync_all_skills()`

```python
def sync_all_skills() -> dict:
    """
    同步用户订阅技能 + 官方默认技能。
    GET /api/me/skills 现在返回两者合并列表。
    dismissed 列表里的默认技能跳过（不安装，不删除）。
    """
    dismissed = read_dismissed()
    cloud = _cloud_get_subscribed()   # 已扩展为返回订阅+默认

    # 分类
    default_ids  = {s["id"] for s in cloud if s.get("isDefault")}
    to_skip      = dismissed & default_ids   # 用户移除的默认技能
    to_sync      = [s for s in cloud if s["id"] not in to_skip]

    # 执行写入（逻辑同现有 sync_subscribed_skills）
    ...

    return {
        "added":            added,
        "updated":          updated,
        "removed":          removed,
        "skipped_dismissed": list(to_skip),
        "unchanged":        unchanged,
        "rootPath":         str(root),
    }
```

### 4.3 `server.py` — 启动钩子

在 WebUI HTTP 服务器绑定端口、完成首次健康检查通过后，检查 `_read_state().get("token")` 是否非空（即用户之前已登录，token 存在于磁盘），若有则启动后台线程：

```python
def _startup_skill_sync():
    try:
        result = sync_all_skills()
        rebuild_skills_system_prompt()
        logger.info("[startup] skills sync: +%d -%d skip=%d",
                    len(result["added"]), len(result["removed"]),
                    len(result["skipped_dismissed"]))
    except Exception as e:
        logger.warning("[startup] skills sync failed (non-fatal): %s", e)

# 在 WebUI 启动完成后（端口绑定后）触发
threading.Thread(target=_startup_skill_sync, daemon=True).start()
```

### 4.4 新增路由（`routes.py`）

```
GET  /api/neowow/skills/status
     → { installedDefaults: [...], dismissed: [...], subscribed: [...] }

POST /api/neowow/skills/dismiss  { "id": "skill-abc123" }
     → dismiss_skill(id); rebuild_skills_system_prompt(); { ok: true }

POST /api/neowow/skills/restore  { "id": "skill-abc123" }
     → restore_skill(id); rebuild_skills_system_prompt(); { ok: true }
```

---

## 5. System Prompt 注入

### 5.1 双层存储机制

| 文件 | 写入者 | 内容 |
|------|--------|------|
| `_neowow/_base_prompt.txt` | `neowow.py`（ConfigBlob 同步时） | Dashboard 下发的原始 system prompt |
| `_neowow/_skills_prompt.txt` | `skills.py`（技能同步后） | 自动生成的技能附录 |
| `config.yaml agent.system_prompt` | `rebuild_skills_system_prompt()` | base + skills，两者合并 |

### 5.2 `rebuild_skills_system_prompt()`

```python
def rebuild_skills_system_prompt():
    base   = (_neowow_dir() / "_base_prompt.txt").read_text() if ... else ""
    skills = (_neowow_dir() / "_skills_prompt.txt").read_text() if ... else ""

    full = base.strip()
    if skills.strip():
        full = full + "\n\n" + skills.strip() if full else skills.strip()

    # 写入 hermes-agent config.yaml
    _write_agent_system_prompt(full)
```

`neowow.py` 同步 ConfigBlob 时：先写 `_base_prompt.txt`，再调用 `rebuild_skills_system_prompt()`。

### 5.3 技能附录格式（`_skills_prompt.txt`）

```markdown
## 已安装技能

你已预装以下技能，用户可以直接呼叫它们：

- **代码审查助手**：对代码进行质量审查，指出潜在问题和改进建议
- **中英互译**：高质量中英文双向翻译，保持语义准确

如果用户的请求与某个技能的用途匹配，优先按该技能的指令执行。
```

无已安装技能时 `_skills_prompt.txt` 为空，`config.yaml` 只含 base prompt。

---

## 6. WebUI 设置面板 UI

### 6.1 布局（现有 Neowow 设置面板扩展）

```
┌─────────────────────────────────────────────────────┐
│  🏪 技能市场同步                                     │
│                                                     │
│  ── 官方默认技能（平台预装）────────────────────────  │
│                                                     │
│  ✦ 代码审查助手                          [移除]      │
│    对代码进行质量审查，指出潜在问题...               │
│                                                     │
│  ✦ 中英互译                        [已移除] [恢复]   │
│    高质量中英文双向翻译...（灰色显示）               │
│                                                     │
│  ── 我的订阅技能 ──────────────────────────────────  │
│  ... (现有列表不变)                                 │
│                                                     │
│                          [立即同步订阅技能]          │
└─────────────────────────────────────────────────────┘
```

### 6.2 交互规则

| 动作 | 行为 |
|------|------|
| 点"移除" | toast 确认弹窗 → 确认后 POST /dismiss → 行变灰，按钮变"[恢复]" |
| 点"恢复" | POST /restore → 重新下载 → 行恢复正常 |
| 开机同步完成 | 右下角 toast："已预装 N 个官方技能 [查看]" |
| 无 token / 网络失败 | 静默跳过，不影响 WebUI 启动，不弹错误 |

已移除状态在页面刷新后保持（`_dismissed.json` 持久化在磁盘）。

---

## 7. 错误处理与降级

| 场景 | 处理 |
|------|------|
| 启动时 token 不存在 | 跳过 `_startup_skill_sync()`，不报错 |
| `GET /api/me/skills` 超时 / 500 | 日志 warning，保留已有本地技能，不清空 |
| 单个技能写入失败 | 跳过该技能，继续处理其他，summary 中标记 |
| `config.yaml` 写入失败 | 日志 error，不崩溃 WebUI |
| `_dismissed.json` 损坏 | 降级为空集合（不移除任何已安装技能） |

---

## 8. 实现顺序

1. **Dashboard** — `isDefault` 字段 + admin 打标接口 + `GET /api/me/skills` 扩展
2. **Hermes `skills.py`** — `_dismissed.json` 读写 + `sync_all_skills()` + dismiss/restore
3. **System prompt 注入** — `_base_prompt.txt` + `_skills_prompt.txt` + `rebuild_skills_system_prompt()`
4. **启动钩子** — `server.py` 后台线程
5. **新路由** — `/api/neowow/skills/status|dismiss|restore`
6. **WebUI 面板** — 静态 JS/HTML 扩展现有 Neowow 设置面板
