# 3-Step Onboarding Redesign

**Status:** Approved (design + v2 mockups accepted 2026-06-05)

**Goal:** Replace the current 5-step first-run wizard with a streamlined **3-step**
flow so a brand-new user gets from launch to a running agent fast:
**① 登录 Neowow → ② 套餐（试用 / 购买 CodingPlan）→ ③ 选人格 → 启动**.

**Layout:** A — top horizontal stepper (①②③) + a centered content card per step +
Next/Back. (Chosen over left-rail / full-screen-immersive.)

---

## Current state (what we're replacing)

- `webui/static/onboarding.js`: `ONBOARDING.steps = ['system','setup','workspace','password','finish']`;
  `_renderOnboardingBody()`, `_onboardingStepMeta()`, `_finishOnboarding()`,
  `_saveOnboardingDefaults()`.
- `webui/api/onboarding.py`: status/auto-complete logic, `apply_onboarding_setup()`.
- Gating: `config.py` `onboarding_completed` flag; `boot.js` `loadOnboardingWizard()`
  shows the wizard when not completed.
- Login: `webui/api/neowow.py` `launch_oauth()`, `get_jwt()`, `save_jwt()`, `/api/neowow/jwt`.
- Plan/models: `onboarding.py` `_fetch_neowow_plan_models()` ← dashboard `/api/me/plan`.
- Personas: `personas_presets.py` `list_persona_presets()` (16 SOUL.md) + `/api/personas/presets`;
  `panels.js` `applyPersonaPreset()` fills the Soul editor; saved to `~/.hermes/SOUL.md`.

## New flow

### Step 1 — 登录 Neowow 账号
- **Primary:** 登录 Neowow 账号 → existing OAuth (`neowow.launch_oauth` → `/api/neowow/jwt`).
  On success the JWT is saved and account context (points) becomes available.
- **Alternative (advanced):** 用自己的 API Key — reuses the existing provider + API-key
  form from the old `setup` step (`apply_onboarding_setup`).
- **No pure skip:** "Next" is disabled until EITHER Neowow login succeeded OR an API key
  is saved. (Without one, the agent cannot run.)
- Copy lists what login unlocks: CodingPlan / 图片·视频生成（消耗积分）/ Skill 市场.

### Step 2 — 套餐（CodingPlan）
- Shown only on the **Neowow-login** path. On the **API-key** path this step is
  auto-skipped (the stepper advances ①→③; user already has their own provider).
- Plan cards (试用 / 月付 / 年付) + prices + trial allowance are **fetched live from the
  dashboard** (`/api/me/plan`) — never hardcoded. Default highlight = 试用.
- Actions: 「开始试用」(default path) or 「购买」. Buying/upgrading also remains available
  in-app later, so this step never blocks: selecting 试用 continues.
- Account chip (已登录 · 积分) shown for context.

### Step 3 — 选人格 → 启动
- Grid of preset personas from `list_persona_presets()` (the 16 fixed in v1.5.9) +
  a 「✏️ 自定义（空白）」card + 「查看全部」.
- Selecting a preset → its SOUL.md content is written to `~/.hermes/SOUL.md` on launch
  (reuse the existing soul-save path — confirm exact route in planning).
- 「跳过，用默认人格」→ no SOUL override.
- 「🚀 启动 NeoMuse」→ finalize: persist persona (if chosen) → `POST /api/onboarding/complete`
  (sets `onboarding_completed`) → hide wizard → start first session (`newSession`).

## Dropped from the wizard (vs old 5-step) — decisions

- **system-check:** run silently in the background, not a user-facing step.
- **workspace + model defaults:** use sensible defaults; user changes them later in
  Settings. (Model is implied by the chosen CodingPlan anyway.)
- **optional password:** moved out of onboarding into Settings (still available, just not
  a first-run gate).

These removals are the core of "现在过于复杂 → 只需要三步".

## Backend work

- **Reuse:** neowow OAuth/jwt, `/api/me/plan`, `/api/personas/presets`,
  `apply_onboarding_setup` (api-key path), `/api/onboarding/complete`.
- **Persona apply on finish:** write the selected preset's `content` to `~/.hermes/SOUL.md`.
  Prefer an existing soul-write endpoint; add a thin one if none fits. (Confirm during planning.)
- **onboarding.py auto-complete logic:** keep the existing "already configured → skip wizard"
  guards so upgrading users aren't re-onboarded.

## Frontend work

- `onboarding.js`: `steps = ['login','plan','persona']`; new per-step render functions;
  conditional skip of `plan` on the api-key path; `_finishOnboarding` writes persona +
  completes + starts session. Remove the workspace/password/system render paths from the
  wizard (logic for defaults moves to a one-shot default-writer).
- Top stepper component (①②③ with done/active states) + Next/Back.

## i18n

- New keys for the 3 steps (titles, feature bullets, plan/persona copy, buttons).
- **en + zh required.** Other locales (the repo has ~12) fall back to en until translated.

## Edge cases

- API-key path → step 2 auto-skipped; stepper shows ① done → ③ active.
- User closes the wizard before finishing → re-shown next launch (gating unchanged).
- Dashboard `/api/me/plan` unreachable → step 2 shows a graceful "稍后在应用内购买" with
  试用 still selectable (don't hard-block first run on a network blip).
- Brand: all copy uses **NeoMuse** (rename already merged to main).

## Testing

- Frontend smoke: wizard renders exactly 3 steps; Next gating on step 1; api-key path
  skips step 2; finish writes persona + completes.
- Backend: persona-apply writes `~/.hermes/SOUL.md`; plan fetch handles unreachable;
  `/api/onboarding/complete` sets the flag.
- `pytest webui/` green.

## Out of scope

- Dashboard-side CodingPlan trial rules (separate repo); this consumes `/api/me/plan` as-is.
- Visual polish beyond the approved layout-A wireframes (can refine during build).
