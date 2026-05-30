# 技能面板订阅体验改进 — 设计文档

**日期：** 2026-05-30
**状态：** 已通过设计评审，待用户复核 spec
**仓库：** hermes-installer（`webui/`）

---

## 背景与目标

Hermes WebUI 的「技能」面板有三个 tab：技能列表 / 技能市场 / 我的技能。用户反馈三处体验问题：

1. **「我的技能」名不副实** —— 这个 tab 展示的是用户在市场**订阅**的技能，叫「我的订阅」更准确。
2. **「未同步」徽章易误解** —— 现在每行右侧只显示 `✓ 已同步` 或 `未同步`，用户看不出版本，也不知道怎么同步。应改为显示**当前最新版本**，并在本地版本落后时给一个**同步按钮**；同时把**作者**显示在列表里。
3. **「技能列表」里订阅技能和其他技能混在一起** —— 订阅技能同步到 `_neowow/` 后会出现在「技能列表」，但和本地/外部技能混排、且显示成 `skill-xxxx` slug。应**分组**：订阅技能单独成「我的订阅技能」组，显示人类名 + 作者。

**非目标：**
- 不改技能同步机制本身（周期同步、frontmatter 自愈已在别的 PR 处理）。
- 不改 agent 侧技能调用逻辑（frontmatter `name` 仍是 slug，是调用标识）。
- 不做技能的编辑/发布（那是 dashboard 的事）。

---

## 数据现状（探查结论）

- `GET /api/skills/mine` → `skills.py::get_mine_skills()` 每条已含：`id / name(人类名) / description / version(云端最新) / displayName(作者) / isDefault / subscriberCount / updatedAt / isLocal / content / skillType`。**缺**：本地已同步的版本号。
- 本地 `_neowow/<id>/_neowow.json` 含 `version`（本地版本）、`name`、`displayName`。
- `GET /api/skills` → `routes.py::_skills_list_from_dir()` 每条含 `name(frontmatter/目录名=slug) / description / category / disabled`。`_neowow` 技能的 `category == "_neowow"`（来自子目录名），可据此识别来源；人类名/作者要从该技能目录的 `_neowow.json` 读。
- `name` 同时是 toggle 开关的键（`/api/skills/toggle`），**不能改**，只能新增展示字段。

---

## 一、「我的技能」→「我的订阅」

纯文案：
- `webui/static/index.html`：`data-tab="mine"` 的 tab 按钮文字 `我的技能` → `我的订阅`。
- `webui/static/panels.js`：空状态与注释里的「我的技能」措辞顺带更新（不影响逻辑）。

---

## 二、「我的订阅」每行：作者 + 版本状态 + 同步按钮

### 后端
1. `get_mine_skills()` 每条新增 `localVersion`：
   - 抽一个纯函数 `_local_skill_version(sid: str) -> int`：读 `_neowow_dir()/sid/_neowow.json` 的 `version`，文件不存在/损坏 → 返回 `0`。
   - `get_mine_skills` 用它给每条加 `"localVersion": _local_skill_version(sid)`。
2. 新增 `sync_one_skill(skill_id, *, fetch=_cloud_get_all, write=_write_skill) -> dict`（仿 `restore_skill`，依赖注入便于测试）：
   - 从 `fetch()` 结果里按 id 找到该技能 → `write(target)` → `_refresh_skills_prompt()` + `rebuild_skills_system_prompt()`。
   - 找不到 → `{"ok": False, "error": "..."}`；成功 → `{"ok": True, "id", "name", "version"}`。
3. 新增路由 `POST /api/skills/sync-one`（`routes.py`）：body `{id}` → 校验 `_is_valid_skill_id` → `sync_one_skill(id)`，`j(handler, result)`。注册进 POST 路由表。

### 前端 `_skillsRenderMine`
每行渲染改为：
- 主信息：`name`（人类名）。
- 副信息：`description` + 「作者：{displayName}」（displayName 非空且 != name 时显示）。
- 右侧状态（三态）：
  - `!isLocal` → 文案「最新 v{version}」+ 按钮 **[同步]**
  - `isLocal && localVersion < version` → 文案「本地 v{localVersion} · 最新 v{version}」+ 按钮 **[同步到最新]**
  - `isLocal && localVersion >= version` → 文案「✓ 已是最新 v{version}」（无按钮）
- 新增 `async function skillsSyncOne(id)`：`POST /api/skills/sync-one {id}` → 成功后 `_skillsState.mineLoaded=false; await _skillsLoadMine()` 刷新；失败 toast。

---

## 三、「技能列表」分组：订阅技能单独成组

### 后端 `_skills_list_from_dir`
每条技能 dict 新增：
- `source`：`"subscribed"`（`category == "_neowow"`）或 `"local"`（其余）。
- 当 `source == "subscribed"`：读该技能目录的 `_neowow.json`，加 `title`（人类 `name`）+ `author`（`displayName`）。读不到 → `title` 缺省回退到 slug、`author` 为空。
- **`name` 保持 slug 不变**（toggle 键）。
- 抽纯函数 `_subscribed_meta(skill_dir: Path) -> dict`（读 `_neowow.json` 返回 `{title, author}` 或 `{}`），便于单测。

### 前端 `_skillsRenderList`
- 按 `source` 分两组渲染：
  - 顶部「我的订阅技能」组：`source == "subscribed"`，显示 `title || name` + 「作者：{author}」。
  - 下面「本地技能」组：其余，按原 `category` 展示。
- 每组一个小标题（`<div class="skills-group-title">`），任一组为空则不渲染该组标题。
- toggle 开关、点击进详情逻辑不变（仍按 `name`）。
- CSS：`style.css` 加 `.skills-group-title` 简单样式（小标题灰字 + 间距）。

---

## 文件结构

| 文件 | 改动 |
|---|---|
| `webui/api/skills.py` | `_local_skill_version()`、`get_mine_skills` 加 `localVersion`、`sync_one_skill()` |
| `webui/api/routes.py` | `_subscribed_meta()`、`_skills_list_from_dir` 加 `source/title/author`、`POST /api/skills/sync-one` 路由 |
| `webui/static/index.html` | tab 文案 我的技能→我的订阅 |
| `webui/static/panels.js` | `_skillsRenderMine` 三态行 + `skillsSyncOne`、`_skillsRenderList` 分组 |
| `webui/static/style.css` | `.skills-group-title` |
| `webui/static/i18n.js` | 若有相关 key（同步/作者/最新）顺带加 |
| `webui/tests/test_skills_panel_ux.py` | 新增单测 |

---

## 测试策略（TDD，挑可纯测的单元）

- `_local_skill_version(sid)`：`HERMES_SKILLS_PATH=tmp` + 写 `_neowow.json` → 断言返回 version；缺失/损坏 → 0。
- `sync_one_skill(id, fetch=…, write=…)`：注入假 `fetch`（返回含/不含该 id 的列表）+ 假 `write`（记录调用）→ 断言找到时调用 write 且返回 ok、找不到时返回 error 且不写。（不触网。）
- `_subscribed_meta(skill_dir)`：tmp 目录写 `_neowow.json` → 断言返回 `{title, author}`；无文件 → `{}`。
- 前端纯文案/分组：沿用仓库现有「源码 grep」式断言（如 `panels.js` 含 `skillsSyncOne(`、`我的订阅技能`；`index.html` 含 `我的订阅`）。
- 现有技能相关测试保持绿。

> 注：`get_mine_skills` / `_skills_list_from_dir` 整体涉及网络 / agent 依赖导入，不在单测里整体跑；逻辑下沉到上面几个纯函数来覆盖。

---

## 安全 / 错误处理

- `/api/skills/sync-one`：校验 `_is_valid_skill_id`；`sync_one_skill` 内网络/写盘异常 → 返回 `{ok:False,error}`，前端 toast，不影响面板。
- 分组富化读 `_neowow.json` 失败 → 退回 slug 展示，绝不让「技能列表」整体加载失败。
- 不改 toggle / 调用键（`name`=slug），不破坏现有禁用逻辑与 agent 调用。

---

## 风险

- **`category == "_neowow"` 识别**：依赖 `_skill_category_from_path` 对 `_neowow/<id>/SKILL.md` 返回 `_neowow`。实现时以该函数实际输出为准（必要时改判定为「路径里含 `_neowow` 段」）。
- **frontmatter 自愈的交互**：订阅技能在「技能列表」里 frontmatter `name` 现在是 slug，正好用 `_neowow.json` 的人类名作 `title` 覆盖展示，体验反而更好。
