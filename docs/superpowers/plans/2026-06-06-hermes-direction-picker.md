# Hermes 方向选择 实现计划

> REQUIRED SUB-SKILL: executing-plans (inline). Steps use `- [ ]`.

**Goal:** 初始化向导加「方向」步,选岗位 → 套人格(SOUL.md)+模型+工作区(+空技能包)。

**Architecture:** Hermes WebUI 单仓库。manifest 数据 + apply_onboarding_setup 扩展 + 只读 directions 路由 + onboarding.js 加一步。3 份岗位 SOUL.md 内联进 manifest。

**Tech Stack:** Python stdlib HTTP server / pytest / 原生 JS onboarding。

---

### Task 0(创意核心,先做+用户复核): 3 份岗位人格 SOUL.md

- 用 `anthropic-skills:hermes-persona-zh` skill 起草 编剧/导演/AIGC动画师 三份中文**岗位**人格。
- 覆盖:身份、专长、工作方式、输出风格、协作语气。
- **给用户过目 → 定稿。** 定稿文本内联进 Task 1 的 manifest `soul` 字段。

---

### Task 1: direction_manifest.py + 测试

**Files:** Create `webui/api/direction_manifest.py`;Test `webui/tests/test_direction_manifest.py`

- [ ] **T1.1 写失败测试:**
```python
from api.direction_manifest import get_direction, list_directions
def test_list_has_three_with_fields():
    ds = list_directions()
    assert {d["id"] for d in ds} == {"screenwriter","director","animator"}
    for d in ds: assert d["name"] and d["emoji"] and d["summary"]
def test_get_direction_case_insensitive_and_miss():
    assert get_direction("Director")["name"] == "导演"
    assert get_direction("nope") is None
    assert get_direction("") is None
def test_soul_present_for_all():
    for k in ("screenwriter","director","animator"):
        assert len(get_direction(k)["soul"]) > 50
```
- [ ] **T1.2 跑红。**
- [ ] **T1.3 实现 manifest**(DICT 含 3 方向 + Task0 定稿 soul;skill_ids=[];model='';workspace 名;get_direction 小写化;list_directions 投影 id/name/emoji/summary)。
- [ ] **T1.4 跑绿。提交。**

---

### Task 2: apply_direction + 接入 apply_onboarding_setup + settings key

**Files:** Modify `webui/api/onboarding.py`(新增 `apply_direction` + 在 `apply_onboarding_setup` 末尾按 `body.get("direction")` 调用 + `_set_config_model_default` helper);Modify `webui/api/config.py:4684`(`_SETTINGS_DEFAULTS` 加 `"user_direction": ""`);Test `webui/tests/test_apply_direction.py`

- [ ] **T2.1 写失败测试(monkeypatch 隔离文件/网络):**
```python
def test_apply_known_writes_soul_and_settings(tmp_path, monkeypatch):
    # monkeypatch Path.home()→tmp_path; stub save_settings; stub sync_all_skills
    from api import onboarding
    res = onboarding.apply_direction("director")
    assert res["applied"] is True
    soul = (tmp_path/".hermes"/"SOUL.md").read_text(encoding="utf-8")
    assert "导演" in soul or len(soul) > 50
def test_apply_unknown_noop():
    from api import onboarding
    assert onboarding.apply_direction("nope")["applied"] is False
def test_empty_skill_ids_skips_sync(monkeypatch):
    # assert sync_all_skills NOT called when skill_ids == []
    ...
```
- [ ] **T2.2 跑红。**
- [ ] **T2.3 实现:** `apply_direction(direction_id)`(写 SOUL.md;若 manifest.model 非空则 `_set_config_model_default`;skill_ids 非空才订阅+sync,逐个吞错;`save_settings({"user_direction": id})`;未知→`{applied:False}`)。在 `apply_onboarding_setup` 的 `reload_config()` 之后:`d = body.get("direction"); if d: apply_direction(d)`(吞错,不阻断向导)。config.py 加默认键。
- [ ] **T2.4 跑绿 + 现有 onboarding/config 测试回归。提交。**

---

### Task 3: GET /api/onboarding/directions 路由

**Files:** Modify `webui/api/routes.py`(在 onboarding 路由附近加 GET 分支 → `list_directions()`)

- [ ] **T3.1** 加路由:`if parsed.path == "/api/onboarding/directions": return j(handler, {"directions": list_directions()})`。
- [ ] **T3.2** 手测:`curl localhost:<port>/api/onboarding/directions` 返回 3 条。提交。

---

### Task 4: onboarding.js 加「方向」步

**Files:** Modify `webui/static/onboarding.js`

- [ ] **T4.1** `steps` 在 `setup` 后插 `'direction'`;`form` 加 `direction:''`。
- [ ] **T4.2** 新增渲染:进入该步时 `GET /api/onboarding/directions`,卡片单选(emoji+name+summary)+「跳过」按钮(置空 direction)。
- [ ] **T4.3** `nextOnboardingStep()` 在 `direction` 步把选中写入 `form.direction`;最终 `/api/onboarding/setup` POST body 加 `direction: ONBOARDING.form.direction`。
- [ ] **T4.4** 手测(本地起 webui,走向导看到方向步 + 选中后 SOUL.md 落地)。提交。

---

## Self-Review
- Spec 覆盖:manifest=T1;apply+settings=T2;route=T3;前端=T4;人格内容=T0。✓
- 类型一致:direction id 用 `screenwriter|director|animator` 全程一致;`get_direction`/`list_directions` 命名一致。✓
- YAGNI:skill_ids 空、model 空(不覆盖)、设置页切换列为紧后续。✓
