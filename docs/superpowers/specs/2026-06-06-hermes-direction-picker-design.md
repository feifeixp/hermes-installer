# Hermes 初始化「方向选择」设计

**日期:** 2026-06-06
**状态:** 设计评审通过(scope C / 技能来源 A),进入实现

## 目标

初始化向导中让用户选择一个「方向」(岗位),一键套用该方向的整套工作环境:
**岗位人格(SOUL.md) + 推荐模型 + 默认工作区 + 技能包**。首发 3 个方向,
manifest 数据驱动,可随时扩展。

## 范围决策(已确认)

- **scope C:** 方向 = 人格 + 技能包 + 模型/工作区偏好(完整预设)。
- **技能来源 A:** v1 技能包从**现有**市场技能里挑(可为空);人格+模型+工作区是主体,技能后补。
- **首发 3 个方向:** 编剧 / 导演 / AIGC 动画师。
- **单选 + 可切换:** 初始化时选一个;之后可在设置里切换(重跑 apply,幂等)。

## 关键约束(勘察发现)

Hermes 后端**不能**代用户「按 id 订阅」市场技能——`sync_all_skills()` 只能同步
用户**已订阅**的技能(`GET /api/me/skills`)。因此 apply-direction 的技能步骤为:
对 manifest 里每个 `skill_id`,用用户 JWT 调 **dashboard** 的订阅接口(best-effort),
再 `sync_all_skills()`。**v1 的 `skill_ids` 留空**,该循环为 no-op——等技能curate 好
再往 manifest 填,无需改代码。这与「技能来源 A」一致。

## 架构 / 改动范围

全部在 Hermes WebUI(`/Users/ff/hermes-installer/webui/`)。dashboard 端不动。

### 组件 1 — 方向清单(manifest)

**新建:** `webui/api/direction_manifest.py`(纯 Python dict,仿 `default_personalities.py` 风格)。

```python
DIRECTIONS = {
  "screenwriter": {
    "name": "编剧", "emoji": "✍️",
    "summary": "剧本结构、三幕、人物弧光、对白打磨",
    "soul": "<岗位人格 SOUL.md 全文>",   # 内联,见组件4
    "skill_ids": [],                      # v1 留空(来源 A)
    "model": "",                          # 空=不覆盖用户已选模型;后续可填推荐模型 id
    "workspace": "scripts",
  },
  "director":     { "name": "导演", "emoji": "🎬", "summary": "分镜、场面调度、节奏、视听语言", "soul": "...", "skill_ids": [], "model": "", "workspace": "scenes" },
  "animator":     { "name": "AIGC动画师", "emoji": "🎞️", "summary": "图像/视频提示词、风格控制、分镜转画面", "soul": "...", "skill_ids": [], "model": "", "workspace": "animations" },
}

def get_direction(direction_id: str) -> dict | None: return DIRECTIONS.get((direction_id or "").lower())
def list_directions() -> list[dict]:
    return [{"id": k, "name": v["name"], "emoji": v["emoji"], "summary": v["summary"]} for k, v in DIRECTIONS.items()]
```

### 组件 2 — 后端 apply(扩展 `apply_onboarding_setup`)

**改:** `webui/api/onboarding.py:1347` `apply_onboarding_setup(body)`。在 provider/model 写入 +
`reload_config()` 之后,读取 `body.get("direction")`,若非空则调用新函数 `apply_direction(direction_id)`:

```python
def apply_direction(direction_id: str) -> dict:
    d = get_direction(direction_id)
    if not d: return {"applied": False, "reason": "unknown direction"}
    # 1) 人格 → ~/.hermes/SOUL.md(直接写文件,复用 memory write 的落盘路径)
    (Path.home() / ".hermes" / "SOUL.md").write_text(d["soul"], encoding="utf-8")
    # 2) 模型(可选)→ 只有 manifest 指定了 model 才覆盖 config.yaml 的 model.default
    if d.get("model"): _set_config_model_default(d["model"])
    # 3) 工作区(可选)→ settings.default_workspace 下建/选该方向子目录(best-effort)
    # 4) 技能(best-effort,v1 多为 no-op):对 d["skill_ids"] 逐个调 dashboard 订阅(用 get_jwt()),再 sync_all_skills()
    for sid in d.get("skill_ids", []):
        _dashboard_subscribe_skill(sid, get_jwt())   # best-effort, 吞错
    if d.get("skill_ids"): sync_all_skills()
    # 5) 记录选择
    save_settings({"user_direction": direction_id})
    return {"applied": True, "direction": direction_id}
```

- `_set_config_model_default`:写 `config.yaml` 的 `model["default"]`(复用 onboarding.py:1473 现有写法)。
- `_dashboard_subscribe_skill(sid, jwt)`:POST 到 dashboard 技能订阅接口(best-effort,失败仅 log)。v1 因 skill_ids 空,不会被调用。

### 组件 3 — 设置项

**改:** `webui/api/config.py:4684` `_SETTINGS_DEFAULTS` 增 `"user_direction": ""`。

### 组件 4 — 内容:3 份岗位 SOUL.md

用 `hermes-persona-zh` skill 起草 3 份**岗位**(非人名)中文人格,内联进 manifest 的 `soul` 字段
(或放 `docker/assets/personas/SOUL/<id>.role.SOUL.md` 由 manifest 读取——二选一,实现期定;
倾向内联以减少文件查找依赖)。每份覆盖:身份、专长、工作方式、输出风格、协作语气。

### 组件 5 — 新增只读接口(给前端选择器)

**改:** `webui/api/routes.py` 增 `GET /api/onboarding/directions` → 返回 `list_directions()`。

### 组件 6 — onboarding 前端(`webui/static/onboarding.js`)

- `ONBOARDING.steps` 在 `setup` 之后、`workspace` 之前插入 `'direction'`。
- `ONBOARDING.form` 增 `direction:''`。
- 新增 `direction` 步渲染:拉 `/api/onboarding/directions`,卡片式单选(emoji+名称+简介)+「跳过」。
- `nextOnboardingStep()` 在该步把选中值写入 `ONBOARDING.form.direction`。
- 最终 apply 的 POST(onboarding.js:465 `/api/onboarding/setup`)body 增 `direction: ONBOARDING.form.direction`。
- 设置页(可后续切换):提供一个「切换方向」入口复用同一选择器 + 调 apply(本期可只做 onboarding,设置页切换列为紧后续)。

## 数据流

```
向导 direction 步选「导演」
  → 完成向导 → POST /api/onboarding/setup { provider, model, ..., direction:'director' }
       → apply_onboarding_setup: 写 provider/model → reload → apply_direction('director')
            ├ 写 ~/.hermes/SOUL.md(导演岗位人格)
            ├ (有则)写 config.yaml model.default
            ├ (有则)订阅+同步技能(v1 空→跳过)
            └ save_settings(user_direction='director')
  → agent CLI 下次注入 SOUL.md → 助手即「导演」身份
```

## 错误处理

- 未知/空 direction → apply 跳过(向导可「跳过」该步,不阻塞完成)。
- SOUL.md 写失败 → 记 log,不阻断向导完成(provider/model 已生效)。
- 技能订阅/同步失败 → best-effort,逐个吞错 + log,不影响人格/模型生效。

## 测试(pytest)

- `direction_manifest`:`get_direction` 命中/未命中/大小写;`list_directions` 返回 3 条且含 id/name/emoji/summary。
- `apply_direction`(用 monkeypatch 隔离文件/网络):
  - 已知方向 → 写了 SOUL.md(断言文件内容=manifest soul)+ `save_settings(user_direction=...)` 被调。
  - 未知方向 → `{applied: False}`,不写文件。
  - skill_ids 为空 → 不调订阅/同步(no-op)。
- 现有 onboarding/skills/config 测试回归不破。

## 非目标(本期不做)

- 多选方向、AI 按描述生成自定义方向。
- 为每个方向**新建**专属技能(技能来源 A:只用现有,且 v1 留空)。
- 设置页切换方向的完整 UI(如工期紧,先只做 onboarding 选择;切换列为紧后续)。
- dashboard 端任何改动。

## 部署

Hermes WebUI:合并到 `main` → GitHub Actions 构建镜像(path filter `webui/**`)→ 云端实例每小时整点 `docker compose pull` 自动拉取。pytest 在 CI 跑。
