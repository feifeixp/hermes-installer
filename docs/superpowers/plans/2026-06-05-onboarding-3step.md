# 3-Step Onboarding Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 5-step first-run wizard (system→setup→workspace→password→finish) with a 3-step flow **① 登录 Neowow → ② 套餐 → ③ 选人格 → 启动**, in layout A (top stepper + centered card).

**Architecture:** Pure refactor of the existing wizard — same overlay markup (`#onboardingOverlay`/`#onboardingSteps`/`#onboardingBody`/`#onboardingNotice`/back+next buttons), same bootstrap entry (`loadOnboardingWizard`), same backend endpoints. We change `webui/static/onboarding.js` (steps array, per-step renderers, nav gating, finish) and add i18n keys + CSS. Persona is applied on finish via the existing `POST /api/memory/write {section:"soul"}`. Workspace/model/password steps are removed; sensible defaults are written silently on finish. No new backend routes required.

**Tech Stack:** Vanilla JS (no framework), Python stdlib http handler backend, pytest with static-assertion + HTML/JS-parse tests (repo convention — see `webui/tests/test_onboarding_static.py`).

**Reused endpoints (all already exist):**
- `GET /api/onboarding/status` — wizard bootstrap data (includes `setup` provider/models, `system`, `workspaces`, `settings`, `completed`).
- `POST /api/onboarding/setup` `{provider,model,api_key?,base_url?}` — API-key path.
- `POST /api/onboarding/complete` `{}` — sets `onboarding_completed`.
- `POST /api/neowow/oauth/launch` `{return_url}` — opens browser OAuth (routes.py:7231 → `neowow.launch_oauth`).
- `GET /api/neowow/status` — login state; field `hasJwt` truthy when logged in. `neowow.js` already polls this and fires `neoSessionUpdated`.
- `GET /api/personas/presets` — `[{id,name,summary,content}]` (16 presets).
- `POST /api/memory/write` `{section:"soul",content}` — writes `~/.hermes/SOUL.md` (routes.py:6322 → `_handle_memory_write`).

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `webui/static/onboarding.js` | The wizard | Rewrite steps array, per-step renderers, nav gating, finish |
| `webui/static/boot.js` | Bootstrap | `loadOnboardingWizard()` form init for new fields (lines 417-438) |
| `webui/static/i18n.js` | UI strings | Add 3-step keys (en + zh); keep old keys |
| `webui/static/style.css` | Styling | Add `.onboarding-plan-*` + `.onboarding-persona-*` classes |
| `webui/tests/test_onboarding_static.py` | Static guards | Update step assertions; add new ones |

`onboarding.js` stays one file (it's the established pattern; ~800 lines). We are net-removing code (3 steps < 5).

---

## Task 1: i18n keys for the 3 new steps (en + zh)

**Files:**
- Modify: `webui/static/i18n.js` (English block ~973-1010; Chinese block — find `onboarding_step_system_title` in the zh locale and add alongside)

- [ ] **Step 1: Add the new keys to the English locale block**, immediately after the existing `onboarding_step_finish_desc` line:

```javascript
    // --- 3-step redesign ---
    onboarding_step_login_title: 'Sign in',
    onboarding_step_login_desc: 'Sign in to your Neowow account.',
    onboarding_step_plan_title: 'Plan',
    onboarding_step_plan_desc: 'Try or buy a CodingPlan.',
    onboarding_step_persona_title: 'Persona',
    onboarding_step_persona_desc: 'Pick a persona, then launch.',
    onboarding_login_heading: 'Sign in to your Neowow account',
    onboarding_login_sub: 'Signing in unlocks:',
    onboarding_login_feat_coding: 'CodingPlan — run agents with Claude / GPT models',
    onboarding_login_feat_media: 'Image / video generation (uses credits)',
    onboarding_login_feat_market: 'Skill market — get & share skills',
    onboarding_login_btn: 'Sign in to Neowow',
    onboarding_login_apikey: 'Use my own API key (advanced)',
    onboarding_login_required: 'Sign in or configure an API key to continue.',
    onboarding_login_waiting: 'Waiting for sign-in to complete in your browser…',
    onboarding_login_done: 'Signed in.',
    onboarding_plan_heading: 'Open a CodingPlan',
    onboarding_plan_sub: 'Try it free, upgrade later anytime.',
    onboarding_plan_trial: 'Free trial',
    onboarding_plan_trial_btn: 'Start trial',
    onboarding_plan_buy: 'Buy',
    onboarding_plan_unreachable: "Couldn't load plans — you can buy later in-app. Continue with the trial.",
    onboarding_persona_heading: 'Choose a persona',
    onboarding_persona_sub: 'Sets your agent’s identity, tone and style (changeable later).',
    onboarding_persona_custom: '✏️ Custom (blank)',
    onboarding_persona_all: 'See all presets',
    onboarding_persona_skip: 'Skip — use the default persona',
    onboarding_launch_btn: '🚀 Launch NeoMuse',
```

- [ ] **Step 2: Mirror the same keys into the Chinese (zh) locale block** with these values:

```javascript
    onboarding_step_login_title: '登录',
    onboarding_step_login_desc: '登录你的 Neowow 账号。',
    onboarding_step_plan_title: '套餐',
    onboarding_step_plan_desc: '试用或购买 CodingPlan。',
    onboarding_step_persona_title: '人格',
    onboarding_step_persona_desc: '选一个人格，然后启动。',
    onboarding_login_heading: '登录 Neowow 账号',
    onboarding_login_sub: '登录后即可解锁：',
    onboarding_login_feat_coding: 'CodingPlan — 用 Claude / GPT 模型跑智能体',
    onboarding_login_feat_media: '图片 / 视频生成（消耗积分）',
    onboarding_login_feat_market: 'Skill 市场 — 获取与分享技能',
    onboarding_login_btn: '登录 Neowow 账号',
    onboarding_login_apikey: '用自己的 API Key（高级）',
    onboarding_login_required: '需要登录或配置 API Key 才能继续。',
    onboarding_login_waiting: '请在浏览器里完成登录…',
    onboarding_login_done: '已登录。',
    onboarding_plan_heading: '开通 CodingPlan',
    onboarding_plan_sub: '先免费试用，满意再升级。',
    onboarding_plan_trial: '免费试用',
    onboarding_plan_trial_btn: '开始试用',
    onboarding_plan_buy: '购买',
    onboarding_plan_unreachable: '套餐加载失败 — 可稍后在应用内购买，先用试用继续。',
    onboarding_persona_heading: '选择一个人格',
    onboarding_persona_sub: '决定智能体的身份、语气与风格（启动后随时可改）。',
    onboarding_persona_custom: '✏️ 自定义（空白）',
    onboarding_persona_all: '查看全部预设',
    onboarding_persona_skip: '跳过，用默认人格',
    onboarding_launch_btn: '🚀 启动 NeoMuse',
```

- [ ] **Step 3: Verify JS still parses**

Run: `node --check webui/static/i18n.js`
Expected: no output (exit 0).

- [ ] **Step 4: Commit**

```bash
git add webui/static/i18n.js
git commit -m "i18n(onboarding): add 3-step redesign keys (en + zh)"
```

---

## Task 2: Steps array + step metadata (collapse 5 → 3)

**Files:**
- Modify: `webui/static/onboarding.js:1` (ONBOARDING object) and `:120-128` (`_onboardingStepMeta`)

- [ ] **Step 1: Change the steps array + add new form fields** — replace the `steps:[...]` and `form:{...}` portions of the `ONBOARDING` const on line 1:

```javascript
const ONBOARDING={status:null,step:0,steps:['login','plan','persona'],form:{provider:'neowow-coding-plan',workspace:'',model:'',password:'',apiKey:'',baseUrl:'',loginMethod:'',persona:'',personaContent:''},active:false,probe:{status:'idle',error:null,detail:'',models:null,probedKey:''},presets:null};
```

- [ ] **Step 2: Replace `_onboardingStepMeta()` (lines 120-128) with the 3-step version:**

```javascript
function _onboardingStepMeta(key){
  return ({
    login:{title:t('onboarding_step_login_title'),desc:t('onboarding_step_login_desc')},
    plan:{title:t('onboarding_step_plan_title'),desc:t('onboarding_step_plan_desc')},
    persona:{title:t('onboarding_step_persona_title'),desc:t('onboarding_step_persona_desc')}
  })[key];
}
```

- [ ] **Step 3: Verify parse**

Run: `node --check webui/static/onboarding.js`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add webui/static/onboarding.js
git commit -m "onboarding: steps array login/plan/persona + step meta"
```

---

## Task 3: Step 1 (login) renderer + gating

**Files:**
- Modify: `webui/static/onboarding.js` — `_renderOnboardingBody()` (235-386): replace the `if(key==='system')`, `if(key==='setup')`, `if(key==='workspace')`, `if(key==='password')`, and finish blocks with three new blocks (`login`, `plan`, `persona`). This task does `login`.

- [ ] **Step 1: In `_renderOnboardingBody()`, replace the `system`/`setup`/`workspace`/`password`/finish bodies with a `login` block** (keep the `const body=$('onboardingBody'); if(!body||!ONBOARDING.status)return; const key=ONBOARDING.steps[ONBOARDING.step];` header):

```javascript
  if(key==='login'){
    const ns=ONBOARDING.status.neowow||{};
    const signedIn=!!ns.hasJwt;
    const m=ONBOARDING.form.loginMethod;
    _setOnboardingNotice(signedIn?t('onboarding_login_done'):t('onboarding_login_required'), signedIn?'success':'info');
    body.innerHTML=`
      <div class="onboarding-welcome">${t('onboarding_title')}</div>
      <h3 class="onboarding-h">${t('onboarding_login_heading')}</h3>
      <p class="onboarding-sub">${t('onboarding_login_sub')}</p>
      <div class="onboarding-feat"><span class="of-ic">🧩</span><span>${t('onboarding_login_feat_coding')}</span></div>
      <div class="onboarding-feat"><span class="of-ic">🎨</span><span>${t('onboarding_login_feat_media')}</span></div>
      <div class="onboarding-feat"><span class="of-ic">🛒</span><span>${t('onboarding_login_feat_market')}</span></div>
      <button class="onboarding-cta ${signedIn?'is-done':''}" id="onboardingLoginBtn" onclick="startOnboardingLogin()" ${signedIn?'disabled':''}>${signedIn?'✓ '+t('onboarding_login_done'):t('onboarding_login_btn')}</button>
      <button class="onboarding-alt" id="onboardingApiKeyToggle" onclick="toggleOnboardingApiKey()">${t('onboarding_login_apikey')}</button>
      <div id="onboardingApiKeyForm" style="display:${m==='apikey'?'block':'none'}">${_renderOnboardingApiKeyForm()}</div>
      <p class="onboarding-foot">${t('onboarding_login_required')}</p>`;
    return;
  }
```

- [ ] **Step 2: Add the helper renderers + login trigger + api-key toggle** near the other onboarding functions (after `_renderOnboardingProviderOAuthField`, ~line 227). `_renderOnboardingApiKeyForm` reuses the existing provider-select + key inputs the old `setup` step built (provider dropdown + `onboardingApiKeyInput` + `onboardingBaseUrlInput`):

```javascript
function _renderOnboardingApiKeyForm(){
  const providers=_getOnboardingSetupProviders();
  const sel=ONBOARDING.form.provider;
  const opts=providers.map(p=>`<option value="${p.id}" ${p.id===sel?'selected':''}>${p.label||p.id}</option>`).join('');
  return `<div class="onboarding-field"><label>Provider</label>
      <select id="onboardingProviderSelect">${opts}</select></div>
    <div class="onboarding-field"><label>API Key</label>
      <input id="onboardingApiKeyInput" type="password" autocomplete="off" placeholder="sk-..."></div>
    <div class="onboarding-field"><label>Base URL (optional)</label>
      <input id="onboardingBaseUrlInput" type="text" placeholder="https://..."></div>`;
}
function toggleOnboardingApiKey(){
  ONBOARDING.form.loginMethod = ONBOARDING.form.loginMethod==='apikey' ? '' : 'apikey';
  _renderOnboardingBody();
}
async function startOnboardingLogin(){
  try{
    const returnUrl=location.origin+'/api/neowow/oauth-callback';
    const r=await api('/api/neowow/oauth/launch',{method:'POST',body:JSON.stringify({return_url:returnUrl})});
    if(r&&r.url&&!r.ok){ window.open(r.url,'_blank'); }
    _setOnboardingNotice(t('onboarding_login_waiting'),'info');
    _pollOnboardingLogin();
  }catch(e){ _setOnboardingNotice(e.message||String(e),'warn'); }
}
let _onbLoginTimer=null;
async function _pollOnboardingLogin(){
  try{
    const s=await api('/api/neowow/status');
    if(s&&s.hasJwt){
      ONBOARDING.form.loginMethod='neowow';
      ONBOARDING.status.neowow=s;
      if(_onbLoginTimer){clearTimeout(_onbLoginTimer);_onbLoginTimer=null;}
      _renderOnboardingBody();
      return;
    }
  }catch(e){}
  _onbLoginTimer=setTimeout(_pollOnboardingLogin,3000);
}
```

- [ ] **Step 3: Gate "Next" on step 1** — in `nextOnboardingStep()` (518-563), replace the per-step validation blocks. For the `login` step add at the top of the try:

```javascript
    const curKey=ONBOARDING.steps[ONBOARDING.step];
    if(curKey==='login'){
      const signedIn=!!((ONBOARDING.status.neowow||{}).hasJwt);
      if(ONBOARDING.form.loginMethod==='apikey'){
        ONBOARDING.form.provider=(($('onboardingProviderSelect')||{}).value||ONBOARDING.form.provider||'').trim();
        ONBOARDING.form.apiKey=(($('onboardingApiKeyInput')||{}).value||'').trim();
        ONBOARDING.form.baseUrl=(($('onboardingBaseUrlInput')||{}).value||'').trim();
        if(!ONBOARDING.form.apiKey) throw new Error(t('onboarding_login_required'));
        // api-key path skips the plan step
        ONBOARDING.step=ONBOARDING.steps.indexOf('persona');
        _renderOnboardingSteps(); _renderOnboardingBody(); return;
      }
      if(!signedIn) throw new Error(t('onboarding_login_required'));
    }
```

(Delete the old `setup`/`workspace`/`password` validation blocks; keep the `if(ONBOARDING.step===ONBOARDING.steps.length-1){await _finishOnboarding();return;}` tail and the `ONBOARDING.step++` advance.)

- [ ] **Step 4: Verify parse**

Run: `node --check webui/static/onboarding.js`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add webui/static/onboarding.js
git commit -m "onboarding: step 1 login (neowow OAuth or api-key) + gating"
```

---

## Task 4: Step 2 (plan) renderer

**Files:**
- Modify: `webui/static/onboarding.js` — add the `plan` block in `_renderOnboardingBody()`.

- [ ] **Step 1: Add the `plan` block** (models come from `ONBOARDING.status.setup.models` — populated by `_fetch_neowow_plan_models`; render the trial card always, plus any plan cards the status exposes):

```javascript
  if(key==='plan'){
    const ns=ONBOARDING.status.neowow||{};
    const plans=(ONBOARDING.status.setup&&ONBOARDING.status.setup.plans)||[];
    _setOnboardingNotice('', 'info');
    const acct=ns.hasJwt?`<div class="onboarding-acct">${ns.points!=null?('积分 '+ns.points):'已登录'}</div>`:'';
    const buyCards=plans.map(p=>`<div class="onboarding-plan"><div class="op-name">${p.name||''}</div><div class="op-price">${p.price||''}</div><button class="onboarding-plan-btn" onclick="window.open('${(p.buy_url||'').replace(/'/g,'')}','_blank')">${t('onboarding_plan_buy')}</button></div>`).join('');
    body.innerHTML=`${acct}
      <h3 class="onboarding-h">${t('onboarding_plan_heading')}</h3>
      <p class="onboarding-sub">${t('onboarding_plan_sub')}</p>
      <div class="onboarding-plans">
        <div class="onboarding-plan is-hot"><div class="op-badge">${t('onboarding_plan_trial')}</div><div class="op-price">¥0</div><button class="onboarding-plan-btn solid" onclick="nextOnboardingStep()">${t('onboarding_plan_trial_btn')}</button></div>
        ${buyCards||''}
      </div>
      ${plans.length?'':('<p class="onboarding-foot">'+t('onboarding_plan_unreachable')+'</p>')}`;
    return;
  }
```

- [ ] **Step 2: No extra gating for plan** — "Continue" / "Start trial" both just advance (the existing `ONBOARDING.step++` tail handles it). Confirm `nextOnboardingStep()` has no leftover `plan` validation.

- [ ] **Step 3: Verify parse**

Run: `node --check webui/static/onboarding.js`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add webui/static/onboarding.js
git commit -m "onboarding: step 2 plan (trial default + dashboard plans)"
```

> Note: `ONBOARDING.status.setup.plans` is best-effort. If the backend status doesn't yet expose a `plans` array, only the trial card renders (graceful) — Task 8 optionally enriches status. Models/trial allowance stay dashboard-driven; do NOT hardcode prices.

---

## Task 5: Step 3 (persona) renderer + selection

**Files:**
- Modify: `webui/static/onboarding.js` — add the `persona` block + selection handler + lazy preset load.

- [ ] **Step 1: Add the `persona` block:**

```javascript
  if(key==='persona'){
    _setOnboardingNotice('', 'info');
    if(ONBOARDING.presets===null){ _loadOnboardingPresets(); }
    const presets=ONBOARDING.presets||[];
    const shown=presets.slice(0,5);
    const cards=shown.map(p=>`<div class="onboarding-persona ${ONBOARDING.form.persona===p.id?'sel':''}" onclick="selectOnboardingPersona('${p.id.replace(/'/g,'')}')"><div class="op-pname">${p.name}</div><div class="op-prole">${(p.summary||'').slice(0,12)}</div></div>`).join('');
    body.innerHTML=`
      <h3 class="onboarding-h">${t('onboarding_persona_heading')}</h3>
      <p class="onboarding-sub">${t('onboarding_persona_sub')}</p>
      <div class="onboarding-personas">
        ${cards}
        <div class="onboarding-persona custom ${ONBOARDING.form.persona==='__custom__'?'sel':''}" onclick="selectOnboardingPersona('__custom__')">${t('onboarding_persona_custom')}</div>
      </div>
      <div class="onboarding-foot">${presets.length>5?('+ '+(presets.length-5)+' · '+t('onboarding_persona_all')):''}</div>
      <button class="onboarding-cta" onclick="nextOnboardingStep()">${t('onboarding_launch_btn')}</button>
      <button class="onboarding-alt" onclick="selectOnboardingPersona('');nextOnboardingStep()">${t('onboarding_persona_skip')}</button>`;
    return;
  }
```

- [ ] **Step 2: Add the preset loader + selector:**

```javascript
async function _loadOnboardingPresets(){
  try{
    const r=await api('/api/personas/presets');
    ONBOARDING.presets=Array.isArray(r)?r:(r&&r.presets)||[];
  }catch(e){ ONBOARDING.presets=[]; }
  if(ONBOARDING.steps[ONBOARDING.step]==='persona') _renderOnboardingBody();
}
function selectOnboardingPersona(id){
  ONBOARDING.form.persona=id;
  if(id&&id!=='__custom__'){
    const p=(ONBOARDING.presets||[]).find(x=>x.id===id);
    ONBOARDING.form.personaContent=p?p.content:'';
  }else{
    ONBOARDING.form.personaContent='';
  }
  _renderOnboardingBody();
}
```

- [ ] **Step 3: Verify parse**

Run: `node --check webui/static/onboarding.js`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add webui/static/onboarding.js
git commit -m "onboarding: step 3 persona grid + selection"
```

---

## Task 6: Finish — apply persona, silent defaults, complete

**Files:**
- Modify: `webui/static/onboarding.js` — `_finishOnboarding()` (490-504) and `_saveOnboardingDefaults()` (469-488).

- [ ] **Step 1: Replace `_saveOnboardingDefaults()` with a non-blocking silent-defaults writer** (no workspace/model/password user input anymore — pick safe defaults):

```javascript
async function _saveOnboardingDefaults(){
  // Workspace: keep an existing default if present; else use the first known
  // workspace; else let the backend default stand. Never block first run on it.
  const st=ONBOARDING.status||{};
  let workspace=(st.settings&&st.settings.default_workspace)||'';
  if(!workspace){
    const choices=_getOnboardingWorkspaceChoices();
    workspace=(choices[0]&&choices[0].path)||'';
  }
  const body={};
  if(workspace) body.default_workspace=workspace;
  if(Object.keys(body).length){
    try{ await api('/api/settings',{method:'POST',body:JSON.stringify(body)}); }catch(e){}
  }
}
```

- [ ] **Step 2: Replace `_finishOnboarding()`** so it saves the api-key path provider (if used), writes the persona SOUL, then completes:

```javascript
async function _finishOnboarding(){
  // API-key path: persist the provider config the user entered on step 1.
  if(ONBOARDING.form.loginMethod==='apikey'){
    await _saveOnboardingProviderSetup();
  }
  await _saveOnboardingDefaults();
  // Apply the chosen preset persona to ~/.hermes/SOUL.md (skip when none/blank).
  const pc=(ONBOARDING.form.personaContent||'').trim();
  if(ONBOARDING.form.persona && ONBOARDING.form.persona!=='__custom__' && pc){
    try{ await api('/api/memory/write',{method:'POST',body:JSON.stringify({section:'soul',content:pc})}); }catch(e){}
  }
  const done=await api('/api/onboarding/complete',{method:'POST',body:'{}'});
  ONBOARDING.status=done;
  ONBOARDING.active=false;
  $('onboardingOverlay').style.display='none';
  showToast(t('onboarding_complete'));
  await loadWorkspaceList();
  if(typeof renderSessionList==='function') await renderSessionList();
  if(!S.session && typeof newSession==='function'){
    await newSession(true);
    await renderSessionList();
  }
}
```

- [ ] **Step 3: Verify parse**

Run: `node --check webui/static/onboarding.js`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add webui/static/onboarding.js
git commit -m "onboarding: finish writes persona SOUL + silent defaults + complete"
```

---

## Task 7: boot.js form init for new fields

**Files:**
- Modify: `webui/static/boot.js` `loadOnboardingWizard()` (417-438)

- [ ] **Step 1: Update the form initialization** to seed the new fields and the neowow status (replace the `ONBOARDING.form.*` assignment block):

```javascript
    const current=((status.setup||{}).current)||{};
    ONBOARDING.form.provider=current.provider||'neowow-coding-plan';
    ONBOARDING.form.workspace=(status.workspaces&&status.workspaces.last)||status.settings.default_workspace||'';
    ONBOARDING.form.model=status.settings.default_model||current.model||'';
    ONBOARDING.form.password='';
    ONBOARDING.form.apiKey='';
    ONBOARDING.form.baseUrl=current.base_url||'';
    ONBOARDING.form.loginMethod=((status.neowow||{}).hasJwt)?'neowow':'';
    ONBOARDING.form.persona='';
    ONBOARDING.form.personaContent='';
    ONBOARDING.presets=null;
```

- [ ] **Step 2: Verify parse**

Run: `node --check webui/static/boot.js`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add webui/static/boot.js
git commit -m "onboarding: boot.js seeds login/persona form fields"
```

---

## Task 8: Backend — expose neowow login + plans in onboarding status

**Files:**
- Modify: `webui/api/onboarding.py` `get_onboarding_status()` (1108-1318)
- Test: `webui/tests/test_onboarding_3step.py` (new)

- [ ] **Step 1: Write the failing test** (`webui/tests/test_onboarding_3step.py`):

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

def test_status_exposes_neowow_block(monkeypatch):
    import api.onboarding as ob
    monkeypatch.setattr(ob, "_safe_neowow_status", lambda: {"hasJwt": True, "points": 1200})
    st = ob.get_onboarding_status()
    assert "neowow" in st
    assert st["neowow"]["hasJwt"] is True
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd webui && python -m pytest tests/test_onboarding_3step.py -v`
Expected: FAIL (`KeyError: 'neowow'` or AttributeError on `_safe_neowow_status`).

- [ ] **Step 3: Add `_safe_neowow_status()` + include it in the status dict.** Near the top helpers of `onboarding.py`:

```python
def _safe_neowow_status() -> dict:
    """Best-effort neowow login status for the wizard. Never raises."""
    try:
        from api.neowow import get_status
        s = get_status() or {}
        return {"hasJwt": bool(s.get("hasJwt")), "points": s.get("points")}
    except Exception:
        return {"hasJwt": False, "points": None}
```

Then in the `get_onboarding_status()` return dict, add the key (alongside `system`, `setup`, ...):

```python
        "neowow": _safe_neowow_status(),
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd webui && python -m pytest tests/test_onboarding_3step.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add webui/api/onboarding.py webui/tests/test_onboarding_3step.py
git commit -m "onboarding(api): expose neowow login status to the wizard"
```

> The `setup.plans` array (Task 4) is optional polish — if you have dashboard plan/price data available via an existing call, shape it into `status['setup']['plans'] = [{name,price,buy_url}]` here behind the same `try/except`. If not available, leave it out; the trial-only card renders gracefully.

---

## Task 9: CSS for plan cards + persona grid + step content

**Files:**
- Modify: `webui/static/style.css` (append near the existing `.onboarding-*` rules, after line ~949)

- [ ] **Step 1: Append the new classes** (match the existing onboarding visual language; keep it minimal):

```css
.onboarding-welcome{font-size:12px;opacity:.6;margin-bottom:2px}
.onboarding-h{margin:0 0 4px}
.onboarding-sub{font-size:13px;opacity:.7;margin:0 0 16px}
.onboarding-feat{display:flex;gap:10px;align-items:center;margin:9px 0;font-size:13px}
.onboarding-feat .of-ic{width:26px;height:26px;border-radius:7px;background:rgba(79,124,255,.18);display:flex;align-items:center;justify-content:center}
.onboarding-cta{display:flex;width:100%;justify-content:center;align-items:center;height:42px;margin-top:16px;border:none;border-radius:9px;background:var(--accent,#4f7cff);color:#fff;font-weight:600;font-size:14px;cursor:pointer}
.onboarding-cta.is-done{background:rgba(63,178,127,.7)}
.onboarding-alt{display:flex;width:100%;justify-content:center;align-items:center;height:36px;margin-top:10px;border:1px solid var(--border,#3a3a4a);border-radius:9px;background:transparent;color:var(--text);font-size:13px;cursor:pointer}
.onboarding-foot{text-align:center;font-size:11px;opacity:.55;margin-top:10px}
.onboarding-acct{float:right;font-size:11px;opacity:.75;border:1px solid var(--border,#3a3a4a);border-radius:20px;padding:3px 10px}
.onboarding-plans{display:flex;gap:12px}
.onboarding-plan{flex:1;border:1px solid var(--border,#3a3a4a);border-radius:10px;padding:14px;text-align:center}
.onboarding-plan.is-hot{border-color:var(--accent,#4f7cff);background:rgba(79,124,255,.08)}
.onboarding-plan .op-price{font-size:20px;font-weight:700;margin:6px 0}
.onboarding-plan-btn{width:100%;height:30px;border-radius:7px;border:1px solid var(--border,#3a3a4a);background:transparent;color:var(--text);font-size:12px;cursor:pointer}
.onboarding-plan-btn.solid{background:var(--accent,#4f7cff);color:#fff;border:none}
.onboarding-personas{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.onboarding-persona{border:1px solid var(--border,#3a3a4a);border-radius:9px;padding:10px;font-size:12px;cursor:pointer}
.onboarding-persona.sel{border-color:var(--accent,#4f7cff);background:rgba(79,124,255,.10)}
.onboarding-persona.custom{border-style:dashed;display:flex;align-items:center;justify-content:center;opacity:.85}
.onboarding-persona .op-pname{font-weight:700}
.onboarding-persona .op-prole{font-size:10px;opacity:.6}
```

- [ ] **Step 2: Commit**

```bash
git add webui/static/style.css
git commit -m "onboarding(css): plan cards + persona grid styles"
```

---

## Task 10: Update static tests + full suite

**Files:**
- Modify: `webui/tests/test_onboarding_static.py`

- [ ] **Step 1: Update `test_onboarding_js_exposes_bootstrap_hooks`** to assert the new structure (replace the body):

```python
def test_onboarding_js_exposes_bootstrap_hooks():
    js = read("static/onboarding.js")
    assert "steps:['login','plan','persona']" in js.replace(" ", "")
    assert "async function loadOnboardingWizard()" in read("static/boot.js")
    assert "async function nextOnboardingStep()" in js
    assert "function startOnboardingLogin()" in js
    assert "function selectOnboardingPersona(" in js
    assert "/api/neowow/oauth/launch" in js
    assert "/api/personas/presets" in js
    assert "/api/memory/write" in js
    assert "/api/onboarding/complete" in js
```

- [ ] **Step 2: Run the onboarding static tests**

Run: `cd webui && python -m pytest tests/test_onboarding_static.py tests/test_onboarding_3step.py -v`
Expected: PASS (update any other assertion in that file that referenced the old 5-step names like `onboarding_step_system_title` — point them at the new keys or remove).

- [ ] **Step 3: Run the full webui suite**

Run: `cd webui && python -m pytest . -q`
Expected: all pass (fix any test still asserting the removed `system`/`workspace`/`password` steps — e.g. `test_onboarding_mvp.py`, `test_onboarding_overlay_js.py` — by updating them to the 3-step flow).

- [ ] **Step 4: Commit**

```bash
git add webui/tests/
git commit -m "onboarding(test): assert 3-step flow"
```

---

## Task 11: Manual smoke + PR

- [ ] **Step 1: Launch the app locally and walk the wizard** (fresh state):

```bash
HERMES_WEBUI_SKIP_ONBOARDING=0 .build_venv/bin/python -c "import webui.server" 2>/dev/null || true
# Run the desktop app or the webui server per repo's run instructions, with a clean ~/.hermes,
# and confirm: stepper shows ①登录 ②套餐 ③人格; login gating works; api-key path skips plan;
# persona selection + 启动 writes ~/.hermes/SOUL.md and enters the app.
```

Expected: 3-step wizard renders; finishing enters the app with the chosen persona applied.

- [ ] **Step 2: Push branch + open PR**

```bash
git push -u origin feat/onboarding-3step
gh pr create --title "feat(onboarding): 3-step redesign (login → plan → persona)" --body "Implements docs/superpowers/specs/2026-06-05-onboarding-3step-redesign-design.md. Collapses the 5-step wizard to 3 steps; persona applied via /api/memory/write; workspace/model/password dropped (sensible defaults on finish)."
```

- [ ] **Step 3: After CI green, hold for the combined NeoMuse + onboarding release** (per the agreed release plan — do NOT tag separately).
