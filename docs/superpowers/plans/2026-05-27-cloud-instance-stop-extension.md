# Cloud Instance Stop-Extension Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When users manually stop their cloud instance, the stopped duration is added back to `PlanRow.expiresAt` upon next start — equal-time compensation.

**Architecture:** Dashboard side records `stoppedAt + stopReason='manual'` on /stop. On /start (after `provider.start` succeeds), `settleStoppedExtension()` reads `stoppedAt`, computes `stoppedMs = now - stoppedAt`, atomically clears stop state (CAS on `stoppedAt`) and pushes `PlanRow.expiresAt += stoppedMs` + `extendedByMs += stoppedMs`. Hermes-installer WebUI consumes the new `/status` fields (`stoppedAt`/`stoppedMs`/`estimatedNewExpiresAt`/`extendedByMs`) and renders a live "已停机 X，启动时延长至 Y" line + post-start toast.

**Tech Stack:**
- **Dashboard** (`/Users/ff/aliyun-supa/dashboard`): Next.js 15 App Router, TypeScript, TableStore SDK, `node --test` with `node:assert/strict` (no vitest/jest). Tests run via `npm test`.
- **Hermes-installer** (`/Users/ff/hermes-installer`): Python 3.11 stdlib WebUI, vanilla JS frontend, pytest. Tests run via `webui/.build_venv/bin/python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-05-27-cloud-instance-stop-extension-design.md`

---

## File Structure

### Dashboard repo (`/Users/ff/aliyun-supa/dashboard`)

| File | Action | Responsibility |
|---|---|---|
| `src/lib/instance-store.ts` | Modify | Add 3 fields to `InstanceRow` (`stoppedAt`, `stopReason`, `totalManualStoppedMs`); add CAS parameter to `updateInstance()` |
| `src/lib/membership-store.ts` | Modify | Add 1 field to `PlanRow` (`extendedByMs`); plumb through `upsertPlan` PUT + `rowFromColumns` read |
| `src/lib/instance-extension.ts` | Create | `settleStoppedExtension()` with DI for testability |
| `src/app/api/me/membership/purchase/route.ts` | Modify | Pass `extendedByMs: '0'` on purchase (renewal resets the cycle counter) |
| `src/app/api/me/instance/stop/route.ts` | Modify | Write `stoppedAt` + `stopReason='manual'` on soft stop (existing route, ~9 lines added in soft-stop branch) |
| `src/app/api/me/instance/start/route.ts` | Modify | Call `settleStoppedExtension(userId)` after `provider.start` success; include `extendedBy + newExpiresAt` in response |
| `src/app/api/me/instance/status/route.ts` | Modify | Include `stoppedAt`/`stoppedMs`/`estimatedNewExpiresAt`/`extendedByMs` in response |
| `tests/instance-extension.test.mjs` | Create | Unit tests for `settleStoppedExtension()` via DI |

### Hermes-installer repo (`/Users/ff/hermes-installer`)

| File | Action | Responsibility |
|---|---|---|
| `webui/static/i18n.js` | Modify | Add 9 new keys × 2 locales (en + zh) |
| `webui/static/server-admin.js` | Modify | `_saFormatDuration()` helper, stopped-duration line, cycle-extended footer, startup toast, 30s live re-render |
| `webui/tests/test_server_admin_duration_format.js` | Create | Unit tests for `_saFormatDuration()` (Node-runnable JS) |
| `webui/tests/test_instance_status_extension_passthrough.py` | Create | Verify `/api/neowow/instance/status` passes through 4 new fields |

`webui/api/neowow.py` and `webui/api/routes.py` need NO code changes — `get_instance_status()` already does dict passthrough. Only test coverage is added.

---

## Execution Order

The dashboard PR must merge + deploy **before** the hermes-installer PR can be verified end-to-end (the WebUI reads new fields from the live dashboard).

- **Phase 1: Dashboard** (Tasks 1–8) — branch `feat/cloud-instance-stop-extension` in `aliyun-supa/dashboard`. Open PR after Task 8.
- **Phase 2: Hermes-installer** (Tasks 9–13) — branch `feat/cloud-instance-stop-extension` in `hermes-installer` (already created during brainstorming). Open PR after Task 13.
- **Phase 3: Verification** (Task 14) — staging deploy + manual e2e + production merge of both PRs.

---

## PHASE 1 — Dashboard (`/Users/ff/aliyun-supa/dashboard`)

> All Phase 1 tasks run with `cwd=/Users/ff/aliyun-supa/dashboard`. Create branch first.

### Task 0: Create dashboard feature branch

- [ ] **Step 1: Create + switch to branch**

```bash
cd /Users/ff/aliyun-supa/dashboard
git checkout main
git pull origin main
git checkout -b feat/cloud-instance-stop-extension
git status
```

Expected: `On branch feat/cloud-instance-stop-extension` + clean tree.

---

### Task 1: Extend `InstanceRow` interface — 3 new fields

**Files:**
- Modify: `src/lib/instance-store.ts`

- [ ] **Step 1: Open `src/lib/instance-store.ts` and locate the `InstanceRow` interface (line ~45)**

Use Read tool to see lines 45-63.

- [ ] **Step 2: Replace the `InstanceRow` interface to add 3 new fields**

Use Edit tool. Find:

```
export interface InstanceRow {
  userId:          string;
  provider:        string;
  region:          string;
  instanceId:      string;
  instanceType:    string;
  state:           InstanceRowState;
  publicIp?:       string;
  subdomain:       string;
  createdAt:       string;
  lastStartedAt:   string;
  lastSeenAt?:     string;
  errorMessage?:   string;
  /** Random UUID minted at spawn time, injected into cloud-init as
   *  NEOWOW_HEARTBEAT_TOKEN.  The Hermes backend sends it back on
   *  POST /api/me/instance/server-heartbeat so we can update lastSeenAt
   *  without requiring the user's JWT. */
  heartbeatToken?: string;
}
```

Replace with:

```
export interface InstanceRow {
  userId:          string;
  provider:        string;
  region:          string;
  instanceId:      string;
  instanceType:    string;
  state:           InstanceRowState;
  publicIp?:       string;
  subdomain:       string;
  createdAt:       string;
  lastStartedAt:   string;
  lastSeenAt?:     string;
  errorMessage?:   string;
  /** Random UUID minted at spawn time, injected into cloud-init as
   *  NEOWOW_HEARTBEAT_TOKEN.  The Hermes backend sends it back on
   *  POST /api/me/instance/server-heartbeat so we can update lastSeenAt
   *  without requiring the user's JWT. */
  heartbeatToken?: string;
  /** ISO 8601 — set on manual /stop (NOT on auto-idle stop). Cleared
   *  by settleStoppedExtension() on /start. Existence ⇔ user is currently
   *  in a stopped state that will be settled (extension granted) on
   *  next /start. */
  stoppedAt?:      string;
  /** Distinguishes user-initiated stop ('manual') from future auto-idle
   *  shutdown. Only 'manual' is eligible for time-extension. Forward-
   *  compatible: settleStoppedExtension checks `stopReason==='manual'`,
   *  so any future value (e.g. 'auto_idle') will be ignored automatically. */
  stopReason?:     'manual';
  /** Decimal-string ms counter — cumulative manual-stop time over the
   *  instance's lifetime. Incremented atomically in settleStoppedExtension
   *  for ops auditing. NOT shown to the user. */
  totalManualStoppedMs?: string;
}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd /Users/ff/aliyun-supa/dashboard
npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors mentioning `instance-store.ts` or `InstanceRow`. (Other unrelated `.ts` errors in the repo are OK — only fail if our edits introduced new ones.)

- [ ] **Step 4: Commit**

```bash
cd /Users/ff/aliyun-supa/dashboard
git add src/lib/instance-store.ts
git commit -m "feat(instance-store): add stoppedAt/stopReason/totalManualStoppedMs fields"
```

---

### Task 2: Add CAS support to `updateInstance()`

**Files:**
- Modify: `src/lib/instance-store.ts`

**Why this is a separate task:** CAS changes the function's return type from `Promise<void>` to `Promise<boolean>` and adds an optional third parameter. We isolate it so the diff is small and reviewable.

- [ ] **Step 1: Read existing `updateInstance` (line 163-190)**

```bash
sed -n '163,190p' /Users/ff/aliyun-supa/dashboard/src/lib/instance-store.ts
```

- [ ] **Step 2: Replace the `updateInstance` function body**

Use Edit tool. Find:

```
export async function updateInstance(
  userId: string,
  patch:  Partial<Omit<InstanceRow, 'userId' | 'createdAt'>>,
): Promise<void> {
  const tsClient = buildTSClient();
  if (!tsClient) throw new Error('TableStore not configured');

  const puts: Array<Record<string, string>> = [];
  for (const [k, v] of Object.entries(patch)) {
    if (v == null) continue;
    puts.push({ [k]: String(v) });
  }
  if (puts.length === 0) return;

  return new Promise<void>((resolve, reject) => {
    /* eslint-disable @typescript-eslint/no-explicit-any */
    tsClient.updateRow({
      tableName:                TABLE_NAME,
      // TableStore SDK requires `condition` instanceof TableStore.Condition;
      // plain object literal throws on strict bundles. makeCondition()
      // is the project-wide workaround. 0 = IGNORE (upsert).
      condition:                makeCondition(0, null) as any,
      primaryKey:               [{ workerName: rowPk(userId) }],
      updateOfAttributeColumns: [{ PUT: puts }],
    }, (err: any) => err ? reject(err) : resolve());
    /* eslint-enable @typescript-eslint/no-explicit-any */
  });
}
```

Replace with:

```
export async function updateInstance(
  userId:             string,
  patch:              Partial<Omit<InstanceRow, 'userId' | 'createdAt'>>,
  expectedStoppedAt?: string,
): Promise<boolean> {
  const tsClient = buildTSClient();
  if (!tsClient) throw new Error('TableStore not configured');

  // Build PUT (non-null patches) + DELETE (explicit-undefined patches)
  // lists. DELETE is needed so settleStoppedExtension() can clear
  // stoppedAt — the TableStore SDK distinguishes "not in patch" from
  // "set to undefined".
  const puts:    Array<Record<string, string>> = [];
  const deletes: string[]                       = [];
  for (const [k, v] of Object.entries(patch)) {
    if (v === undefined) {
      deletes.push(k);
    } else if (v !== null) {
      puts.push({ [k]: String(v) });
    }
  }
  if (puts.length === 0 && deletes.length === 0) return true;

  // CAS: when expectedStoppedAt is provided, build a column-equality
  // condition. SingleColumnCondition is the SDK shape; we construct
  // it via Object.create to dodge the strict-mode constructor bug
  // (same workaround as makeCondition for the row-level condition).
  // eslint-disable-next-line @typescript-eslint/no-require-imports, @typescript-eslint/no-explicit-any
  const TableStore = require('tablestore') as any;
  let columnCondition: unknown = null;
  if (expectedStoppedAt !== undefined) {
    const c = Object.create(TableStore.SingleColumnCondition.prototype);
    c.columnName     = 'stoppedAt';
    c.columnValue    = expectedStoppedAt;
    c.comparator     = TableStore.ComparatorType.EQUAL;
    c.passIfMissing  = false;  // missing column ≠ match
    c.latestVersionOnly = true;  // SDK field is singular
    columnCondition = c;
  }

  const updateOps: Array<Record<string, unknown>> = [];
  if (puts.length    > 0) updateOps.push({ PUT:    puts });
  if (deletes.length > 0) updateOps.push({ DELETE_ALL: deletes });

  return new Promise<boolean>((resolve, reject) => {
    /* eslint-disable @typescript-eslint/no-explicit-any */
    tsClient.updateRow({
      tableName:                TABLE_NAME,
      condition:                makeCondition(0, columnCondition) as any,
      primaryKey:               [{ workerName: rowPk(userId) }],
      updateOfAttributeColumns: updateOps,
    }, (err: any) => {
      if (err) {
        // TableStore returns OTSConditionCheckFail when CAS fails.
        // Surface as `false` rather than throwing so callers can
        // handle it as a normal "someone else got there first" event.
        const code = err.code || err.Code || '';
        if (code === 'OTSConditionCheckFail') return resolve(false);
        return reject(err);
      }
      resolve(true);
    });
    /* eslint-enable @typescript-eslint/no-explicit-any */
  });
}
```

- [ ] **Step 3: Verify all existing callers still typecheck**

```bash
cd /Users/ff/aliyun-supa/dashboard
grep -rn "updateInstance(" src --include="*.ts" | grep -v "instance-store.ts" | head -10
```

Note each line. Then:

```bash
cd /Users/ff/aliyun-supa/dashboard
npx tsc --noEmit 2>&1 | grep -E "instance-store|updateInstance" | head -10
```

Expected: empty (no errors mentioning instance-store / updateInstance). Existing callers that ignored the return value still work — `Promise<boolean>` is assignment-compatible with `Promise<void>` (the awaited boolean is just discarded).

- [ ] **Step 4: Commit**

```bash
cd /Users/ff/aliyun-supa/dashboard
git add src/lib/instance-store.ts
git commit -m "feat(instance-store): updateInstance CAS + DELETE-column support"
```

---

### Task 3: Extend `PlanRow` interface + plumb `extendedByMs`

**Files:**
- Modify: `src/lib/membership-store.ts`

- [ ] **Step 1: Read the relevant section of `membership-store.ts`**

```bash
sed -n '66,97p' /Users/ff/aliyun-supa/dashboard/src/lib/membership-store.ts
```

- [ ] **Step 2: Add `extendedByMs` to the `PlanRow` interface**

Use Edit tool. Find:

```
  /** ISO timestamp marking the end of the grace period after the
   *  subscription / trial expires. The cron sweeper sets this to
   *  `expiresAt + GRACE_HOURS` when it observes status='active' AND
   *  `expiresAt < now`. During the window (status='expired_grace'),
   *  the ECS is stopped but not deleted — the user can pay to
   *  resume. After the window, the next sweep flips to 'expired'
   *  and provider.remove()s the instance. */
  expiredGraceUntil?:     string;
}
```

Replace with:

```
  /** ISO timestamp marking the end of the grace period after the
   *  subscription / trial expires. The cron sweeper sets this to
   *  `expiresAt + GRACE_HOURS` when it observes status='active' AND
   *  `expiresAt < now`. During the window (status='expired_grace'),
   *  the ECS is stopped but not deleted — the user can pay to
   *  resume. After the window, the next sweep flips to 'expired'
   *  and provider.remove()s the instance. */
  expiredGraceUntil?:     string;
  /** Decimal-string ms counter — total time the user gained on
   *  `expiresAt` via manual stop-extension during the CURRENT
   *  subscription cycle. Reset to "0" on every renewal (purchase
   *  route). Surfaced to the WebUI as a number for the "本周期累计
   *  通过关机延长 X 天" footer. */
  extendedByMs?:          string;
}
```

- [ ] **Step 3: Plumb `extendedByMs` through `rowFromColumns()` (line ~163-190 range)**

```bash
sed -n '163,190p' /Users/ff/aliyun-supa/dashboard/src/lib/membership-store.ts
```

Use Edit. Find the final lines of `rowFromColumns()` return literal — locate `expiredGraceUntil` line in the return object. Edit to add a sibling line right after it (find unique context by reading the actual lines first since structure varies).

Specifically, use Read tool on the file to see the `return {` block of `rowFromColumns` (lines around 163-188). The return object's final property is `expiredGraceUntil: get('expiredGraceUntil') || undefined,` (verify exact text by reading). Edit to insert:

```
    extendedByMs: get('extendedByMs') || '0',
```

as a new line immediately after the `expiredGraceUntil` line, preserving 4-space indentation.

- [ ] **Step 4: Add `extendedByMs` to `upsertPlan()` PUT path**

Locate the `maybePrefixed(...)` calls in `upsertPlan()` (around line 284-298). Use Edit. Find:

```
  maybePrefixed('expiredGraceUntil',     row.expiredGraceUntil);
```

Replace with:

```
  maybePrefixed('expiredGraceUntil',     row.expiredGraceUntil);
  maybePrefixed('extendedByMs',          row.extendedByMs);
```

- [ ] **Step 5: Verify TypeScript**

```bash
cd /Users/ff/aliyun-supa/dashboard
npx tsc --noEmit 2>&1 | grep -E "membership-store|PlanRow|extendedByMs" | head -10
```

Expected: empty.

- [ ] **Step 6: Commit**

```bash
cd /Users/ff/aliyun-supa/dashboard
git add src/lib/membership-store.ts
git commit -m "feat(membership-store): add extendedByMs field to PlanRow"
```

---

### Task 4: Reset `extendedByMs` on renewal (purchase route)

**Files:**
- Modify: `src/app/api/me/membership/purchase/route.ts`

- [ ] **Step 1: Read the row construction block (lines 247-270)**

```bash
sed -n '247,270p' /Users/ff/aliyun-supa/dashboard/src/app/api/me/membership/purchase/route.ts
```

- [ ] **Step 2: Add `extendedByMs: '0'` to the row literal**

Use Edit. Find:

```
    trialUsedAt:      currentPlan?.trialUsedAt
                       || (tier.oncePerUser ? now : undefined),
  };
```

Replace with:

```
    trialUsedAt:      currentPlan?.trialUsedAt
                       || (tier.oncePerUser ? now : undefined),
    // Every purchase starts a fresh cycle — reset the cumulative
    // stop-extension counter so the WebUI footer "本周期累计延长" only
    // shows time gained AFTER this purchase. The expiry-sweeper writes
    // expired_grace / expired transitions WITHOUT touching this field.
    extendedByMs:     '0',
  };
```

- [ ] **Step 3: Verify TypeScript**

```bash
cd /Users/ff/aliyun-supa/dashboard
npx tsc --noEmit 2>&1 | grep -E "purchase|extendedByMs" | head -5
```

Expected: empty.

- [ ] **Step 4: Commit**

```bash
cd /Users/ff/aliyun-supa/dashboard
git add src/app/api/me/membership/purchase/route.ts
git commit -m "feat(purchase): reset extendedByMs on every renewal"
```

---

### Task 5: Create `settleStoppedExtension()` + unit tests

**Files:**
- Create: `src/lib/instance-extension.ts`
- Create: `tests/instance-extension.test.mjs`

**Design note:** The function uses Dependency Injection so the test suite (which doesn't have TableStore available) can pass mocks. Default deps map to real implementations for production callers.

- [ ] **Step 1: Write the failing tests first**

Create `tests/instance-extension.test.mjs` with EXACTLY:

```javascript
// Tests for settleStoppedExtension. Uses DI so no TableStore is touched.
//
// Run via:
//   node --experimental-strip-types --no-warnings --test tests/instance-extension.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { settleStoppedExtension } from '../src/lib/instance-extension.ts';

function makeDeps(overrides = {}) {
  const state = {
    instance: null,
    plan:     null,
    updates:  [],
    casFails: false,
    now:      Date.parse('2026-06-01T12:00:00Z'),
    ...overrides,
  };
  return {
    state,
    deps: {
      now: () => state.now,
      getInstance: async () => state.instance,
      updateInstance: async (userId, patch, expectedStoppedAt) => {
        state.updates.push({ patch, expectedStoppedAt });
        if (state.casFails) return false;
        if (state.instance) Object.assign(state.instance, patch);
        return true;
      },
      getPlan: async () => state.plan,
      upsertPlan: async (row) => {
        if (state.plan) Object.assign(state.plan, row);
      },
    },
  };
}

test('no instance → no-op', async () => {
  const { deps } = makeDeps({ instance: null });
  const r = await settleStoppedExtension('u1', deps);
  assert.deepEqual(r, { extendedMs: 0, newExpiresAt: null });
});

test('instance with no stoppedAt → no-op', async () => {
  const { deps } = makeDeps({
    instance: { stoppedAt: undefined, stopReason: undefined },
  });
  const r = await settleStoppedExtension('u1', deps);
  assert.deepEqual(r, { extendedMs: 0, newExpiresAt: null });
});

test('stopReason != manual → no-op (forward-compatible with auto_idle)', async () => {
  const { deps } = makeDeps({
    instance: { stoppedAt: '2026-05-31T12:00:00Z', stopReason: 'auto_idle' },
  });
  const r = await settleStoppedExtension('u1', deps);
  assert.deepEqual(r, { extendedMs: 0, newExpiresAt: null });
});

test('happy path — 1h stopped, plan present', async () => {
  const stoppedAt = '2026-06-01T11:00:00Z';   // 1h before "now"
  const { deps, state } = makeDeps({
    instance: {
      stoppedAt,
      stopReason: 'manual',
      totalManualStoppedMs: '0',
    },
    plan: {
      expiresAt:    '2026-06-30T00:00:00Z',
      extendedByMs: '0',
    },
  });
  const r = await settleStoppedExtension('u1', deps);
  assert.equal(r.extendedMs, 3_600_000);
  assert.equal(r.newExpiresAt, '2026-06-30T01:00:00.000Z');
  assert.equal(state.plan.extendedByMs, '3600000');
  assert.equal(state.plan.expiresAt,    '2026-06-30T01:00:00.000Z');
  assert.equal(state.instance.totalManualStoppedMs, '3600000');
});

test('extendedByMs accumulates across multiple stops', async () => {
  const { deps, state } = makeDeps({
    instance: {
      stoppedAt:            '2026-06-01T11:30:00Z',  // 30 min
      stopReason:           'manual',
      totalManualStoppedMs: '7200000',                // 2h prior
    },
    plan: {
      expiresAt:    '2026-06-30T00:00:00Z',
      extendedByMs: '7200000',                        // 2h gained earlier this cycle
    },
  });
  const r = await settleStoppedExtension('u1', deps);
  assert.equal(r.extendedMs, 1_800_000);              // 30 min this time
  assert.equal(state.plan.extendedByMs, '9000000');   // 2h + 30 min total
  assert.equal(state.instance.totalManualStoppedMs, '9000000');
});

test('clock skew (stoppedAt > now) → no extension, state still cleared', async () => {
  const { deps, state } = makeDeps({
    instance: {
      stoppedAt:  '2026-06-01T13:00:00Z',  // 1h in the FUTURE
      stopReason: 'manual',
    },
    plan: {
      expiresAt:    '2026-06-30T00:00:00Z',
      extendedByMs: '0',
    },
  });
  const r = await settleStoppedExtension('u1', deps);
  assert.equal(r.extendedMs, 0);
  // Plan untouched.
  assert.equal(state.plan.expiresAt,    '2026-06-30T00:00:00Z');
  assert.equal(state.plan.extendedByMs, '0');
  // State cleared (so user isn't stuck in "perpetual settlement attempt").
  const clearCall = state.updates.find(u => u.patch.stoppedAt === undefined);
  assert.ok(clearCall, 'expected an updateInstance call clearing stoppedAt');
});

test('CAS failure → returns 0 (another start raced ahead)', async () => {
  const { deps } = makeDeps({
    instance: {
      stoppedAt:  '2026-06-01T11:00:00Z',
      stopReason: 'manual',
    },
    plan: {
      expiresAt:    '2026-06-30T00:00:00Z',
      extendedByMs: '0',
    },
    casFails: true,
  });
  const r = await settleStoppedExtension('u1', deps);
  assert.equal(r.extendedMs, 0);
  assert.equal(r.newExpiresAt, null);
});

test('no plan (arrowhead user) → extendedMs counted, newExpiresAt null', async () => {
  const { deps, state } = makeDeps({
    instance: {
      stoppedAt:            '2026-06-01T11:00:00Z',
      stopReason:           'manual',
      totalManualStoppedMs: '0',
    },
    plan: null,
  });
  const r = await settleStoppedExtension('u1', deps);
  assert.equal(r.extendedMs, 3_600_000);
  assert.equal(r.newExpiresAt, null);
  assert.equal(state.instance.totalManualStoppedMs, '3600000');
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/ff/aliyun-supa/dashboard
npm test 2>&1 | tail -10
```

Expected: failure with `Cannot find module '../src/lib/instance-extension.ts'` or similar.

- [ ] **Step 3: Implement `instance-extension.ts`**

Create `src/lib/instance-extension.ts` with EXACTLY:

```typescript
// ─────────────────────────────────────────────────────────────────────────
// settleStoppedExtension — equal-time compensation for user-initiated stops.
//
// Called from /api/me/instance/start AFTER provider.start() succeeds. Reads
// the instance row, computes stoppedMs = now - stoppedAt, atomically clears
// stop state via CAS on stoppedAt, and pushes the same ms onto PlanRow's
// expiresAt + extendedByMs.
//
// Failure semantics:
//   - settle is best-effort. If it throws or returns extendedMs=0, the
//     /start route still returns success — the user gets their instance
//     back; the extension just doesn't apply (logs explain).
//   - CAS protects against concurrent /start calls double-extending.
//   - Forward-compatible: stopReason!=='manual' (e.g. future 'auto_idle')
//     short-circuits BEFORE clearing state, so we don't accidentally
//     reset an auto-idle stop's bookkeeping.
//
// Spec: docs/superpowers/specs/2026-05-27-cloud-instance-stop-extension-design.md
// ─────────────────────────────────────────────────────────────────────────

import { getInstance as realGetInstance, updateInstance as realUpdateInstance, type InstanceRow } from './instance-store';
import { getPlan as realGetPlan, upsertPlan as realUpsertPlan, type PlanRow } from './membership-store';

export interface ExtensionResult {
  /** Milliseconds added to PlanRow.expiresAt this call. 0 = no-op or CAS race. */
  extendedMs:   number;
  /** New expiresAt ISO if a plan was updated, else null. */
  newExpiresAt: string | null;
}

export interface SettleDeps {
  now:            () => number;
  getInstance:    (userId: string) => Promise<InstanceRow | null>;
  updateInstance: (
    userId:             string,
    patch:              Partial<Omit<InstanceRow, 'userId' | 'createdAt'>>,
    expectedStoppedAt?: string,
  ) => Promise<boolean>;
  getPlan:    (userId: string, planType?: 'hermes_server') => Promise<PlanRow | null>;
  upsertPlan: (row: Partial<PlanRow> & { userId: string; planType: 'hermes_server' }) => Promise<void>;
}

const DEFAULT_DEPS: SettleDeps = {
  now:            () => Date.now(),
  getInstance:    realGetInstance,
  updateInstance: realUpdateInstance,
  getPlan:        (userId) => realGetPlan(userId, 'hermes_server'),
  upsertPlan:     realUpsertPlan,
};

export async function settleStoppedExtension(
  userId: string,
  deps:   SettleDeps = DEFAULT_DEPS,
): Promise<ExtensionResult> {
  const inst = await deps.getInstance(userId);
  if (!inst || !inst.stoppedAt || inst.stopReason !== 'manual') {
    return { extendedMs: 0, newExpiresAt: null };
  }

  const stoppedMs = deps.now() - Date.parse(inst.stoppedAt);

  // Clock skew or stopped-then-immediately-started — clear state, no
  // extension. We still clear so the user isn't stuck in a perpetual
  // settlement-pending state.
  if (stoppedMs <= 0) {
    console.warn('[settle] clock skew, stoppedMs=%d userId=%s', stoppedMs, userId);
    await deps.updateInstance(userId, {
      stoppedAt:  undefined,
      stopReason: undefined,
    }, inst.stoppedAt);
    return { extendedMs: 0, newExpiresAt: null };
  }

  const newTotal = String(parseInt(inst.totalManualStoppedMs || '0', 10) + stoppedMs);

  // CAS-protected clear: if another /start raced ahead, expectedStoppedAt
  // won't match the (now-undefined) stoppedAt column, CAS fails, we
  // return 0. Otherwise we own the settlement.
  const cleared = await deps.updateInstance(userId, {
    stoppedAt:            undefined,
    stopReason:           undefined,
    totalManualStoppedMs: newTotal,
  }, inst.stoppedAt);
  if (!cleared) {
    return { extendedMs: 0, newExpiresAt: null };
  }

  // Best-effort plan update — orphan instance (no plan) still gets
  // the audit counter incremented above. Caller sees extendedMs > 0
  // but newExpiresAt null.
  const plan = await deps.getPlan(userId, 'hermes_server');
  if (!plan) {
    return { extendedMs: stoppedMs, newExpiresAt: null };
  }

  const newExpiresAt = new Date(Date.parse(plan.expiresAt) + stoppedMs).toISOString();
  const newExtendedByMs = String(parseInt(plan.extendedByMs || '0', 10) + stoppedMs);

  await deps.upsertPlan({
    userId,
    planType:     'hermes_server',
    expiresAt:    newExpiresAt,
    extendedByMs: newExtendedByMs,
  });

  return { extendedMs: stoppedMs, newExpiresAt };
}
```

- [ ] **Step 4: Run tests to confirm all pass**

```bash
cd /Users/ff/aliyun-supa/dashboard
npm test 2>&1 | tail -15
```

Expected: All 8 instance-extension tests pass + existing billing/sse tests still pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/ff/aliyun-supa/dashboard
git add src/lib/instance-extension.ts tests/instance-extension.test.mjs
git commit -m "feat(instance-extension): settleStoppedExtension with DI + 8 unit tests"
```

---

### Task 6: Wire `/stop` route to record `stoppedAt + stopReason`

**Files:**
- Modify: `src/app/api/me/instance/stop/route.ts`

- [ ] **Step 1: Read the soft-stop branch (around lines 92-110)**

```bash
sed -n '92,115p' /Users/ff/aliyun-supa/dashboard/src/app/api/me/instance/stop/route.ts
```

- [ ] **Step 2: Replace the soft-stop branch**

Use Edit. Find:

```
  // ── Soft stop (resumable) ─────────────────────────────────────────
  try {
    await provider.stop(row.instanceId);
    await updateInstance(caller.userId, {
      state: 'stopped',
    });
    return NextResponse.json({
      ok:    true,
      state: 'stopped',
      note: 'Instance powered off. /start will resume it with the ' +
            'same publicIp + state. You stop being billed for instance ' +
            'hours but still pay for disk storage.',
    });
  } catch (e) {
    return NextResponse.json({
      error: `Stop failed: ${(e as Error).message}`,
    }, { status: 502 });
  }
```

Replace with:

```
  // ── Soft stop (resumable) ─────────────────────────────────────────
  // Stamp stoppedAt + stopReason='manual' so /start can later compute
  // stoppedMs and grant equal-time extension via settleStoppedExtension.
  // See docs/superpowers/specs/2026-05-27-cloud-instance-stop-extension-design.md
  try {
    await provider.stop(row.instanceId);
    const stoppedAt = new Date().toISOString();
    await updateInstance(caller.userId, {
      state:      'stopped',
      stoppedAt,
      stopReason: 'manual',
    });
    return NextResponse.json({
      ok:        true,
      state:     'stopped',
      stoppedAt,
      note: 'Instance powered off. /start will resume it with the ' +
            'same publicIp + state, and any time spent stopped will ' +
            'extend your subscription expiry by the same amount.',
    });
  } catch (e) {
    return NextResponse.json({
      error: `Stop failed: ${(e as Error).message}`,
    }, { status: 502 });
  }
```

- [ ] **Step 3: Verify TypeScript**

```bash
cd /Users/ff/aliyun-supa/dashboard
npx tsc --noEmit 2>&1 | grep -E "instance/stop|stopReason|stoppedAt" | head -5
```

Expected: empty.

- [ ] **Step 4: Commit**

```bash
cd /Users/ff/aliyun-supa/dashboard
git add src/app/api/me/instance/stop/route.ts
git commit -m "feat(instance/stop): stamp stoppedAt + stopReason on soft stop"
```

---

### Task 7: Wire `/start` route to call `settleStoppedExtension`

**Files:**
- Modify: `src/app/api/me/instance/start/route.ts`

- [ ] **Step 1: Read the start route end-to-end to find the success path**

```bash
wc -l /Users/ff/aliyun-supa/dashboard/src/app/api/me/instance/start/route.ts
grep -n "NextResponse.json\|provider.start\|state.*spawning" /Users/ff/aliyun-supa/dashboard/src/app/api/me/instance/start/route.ts | head -10
```

- [ ] **Step 2: Read the section around the final NextResponse.json (success path)**

Use Read tool to view the file's final 40 lines, identify the success return literal. The pattern looks like:

```typescript
return NextResponse.json({ ok: true, state: 'spawning', /* ...other fields */ });
```

- [ ] **Step 3: Add import + settle call**

Use Edit. Find the file's existing imports block (top of file) and add `settleStoppedExtension` import. Insert this line in the imports (immediately after the `import { getInstance, updateInstance, ... } from '@/lib/instance-store';` line — find the exact text via Read):

```
import { settleStoppedExtension } from '@/lib/instance-extension';
```

Then locate the success-path NextResponse.json (the one that fires after `provider.start` has resolved without throwing). Wrap the return in a settle call. Use Edit. Find the exact existing success return literal (look like `return NextResponse.json({ ok: true, state: 'spawning', ... });`) — Use Read tool to capture exact text — and replace with a version that awaits `settleStoppedExtension` first and includes its result.

Concretely, given a hypothetical existing block:

```
    return NextResponse.json({
      ok:    true,
      state: 'spawning',
      /* ...other existing fields... */
    });
```

Replace with:

```
    // Best-effort extension settlement — never blocks the user from
    // getting their instance back. See settleStoppedExtension docs.
    let extendedBy: { ms: number; days: number; hours: number; minutes: number } | null = null;
    let newExpiresAt: string | null = null;
    try {
      const ext = await settleStoppedExtension(caller.userId);
      if (ext.extendedMs > 0) {
        const ms = ext.extendedMs;
        extendedBy = {
          ms,
          days:    Math.floor(ms / 86_400_000),
          hours:   Math.floor((ms % 86_400_000) / 3_600_000),
          minutes: Math.floor((ms %  3_600_000) /     60_000),
        };
        newExpiresAt = ext.newExpiresAt;
      }
    } catch (e) {
      console.error('[/instance/start] settle failed (non-fatal):', e);
    }

    return NextResponse.json({
      ok:    true,
      state: 'spawning',
      /* ...other existing fields... */
      extendedBy,
      newExpiresAt,
    });
```

**Important:** The `/* ...other existing fields... */` comment is a placeholder for whatever fields the route returns today (e.g. `publicIp`, `subdomain`, etc). Read the file's current return statement and preserve those fields verbatim. Add `extendedBy` and `newExpiresAt` as siblings.

- [ ] **Step 4: Verify TypeScript**

```bash
cd /Users/ff/aliyun-supa/dashboard
npx tsc --noEmit 2>&1 | grep -E "instance/start|settleStoppedExtension|extendedBy" | head -10
```

Expected: empty.

- [ ] **Step 5: Commit**

```bash
cd /Users/ff/aliyun-supa/dashboard
git add src/app/api/me/instance/start/route.ts
git commit -m "feat(instance/start): settle stop-extension after provider.start success"
```

---

### Task 8: Extend `/status` route response with new fields

**Files:**
- Modify: `src/app/api/me/instance/status/route.ts`

- [ ] **Step 1: Read the status route's response shape**

```bash
wc -l /Users/ff/aliyun-supa/dashboard/src/app/api/me/instance/status/route.ts
sed -n '1,200p' /Users/ff/aliyun-supa/dashboard/src/app/api/me/instance/status/route.ts
```

- [ ] **Step 2: Locate the success-path `NextResponse.json` literal**

Find the block that returns the instance status to the caller (typically includes fields like `state`, `publicIp`, `subdomain`, `lastStartedAt`). It will look approximately like:

```typescript
return NextResponse.json({
  ok:    true,
  state: row.state,
  /* ...other existing fields... */
});
```

- [ ] **Step 3: Add import for getPlan + extend the response**

Use Edit to add (in the imports block):

```
import { getPlan } from '@/lib/membership-store';
```

(If `getPlan` is already imported, skip.)

Then locate the success NextResponse.json and replace it. Given a hypothetical existing block:

```
    return NextResponse.json({
      ok:    true,
      state: row.state,
      /* ...other existing fields... */
    });
```

Replace with:

```
    // Stop-extension surface: WebUI server-admin panel reads these to
    // render "已停机 X" + "启动时延长至 Y" + "本周期累计延长 Z" copy.
    // null/0 when not in stopped-eligible state (Linux installer or
    // running instances).
    const plan          = await getPlan(caller.userId, 'hermes_server').catch(() => null);
    const stoppedAt     = (row.stoppedAt && row.stopReason === 'manual') ? row.stoppedAt : null;
    const stoppedMs     = stoppedAt ? Date.now() - Date.parse(stoppedAt)                  : 0;
    const estimatedNewExpiresAt = (stoppedAt && plan?.expiresAt)
      ? new Date(Date.parse(plan.expiresAt) + stoppedMs).toISOString()
      : null;
    const extendedByMs  = parseInt(plan?.extendedByMs || '0', 10);

    return NextResponse.json({
      ok:    true,
      state: row.state,
      /* ...other existing fields... */
      stoppedAt,
      stoppedMs,
      estimatedNewExpiresAt,
      extendedByMs,
    });
```

**Important:** preserve existing fields verbatim — the `/* ...other existing fields... */` is a placeholder. Read the current literal first.

- [ ] **Step 4: Verify TypeScript**

```bash
cd /Users/ff/aliyun-supa/dashboard
npx tsc --noEmit 2>&1 | grep -E "instance/status|stoppedAt|extendedByMs" | head -5
```

Expected: empty.

- [ ] **Step 5: Run full dashboard test suite + lint**

```bash
cd /Users/ff/aliyun-supa/dashboard
npm test 2>&1 | tail -10
```

Expected: all tests pass (existing + 8 new instance-extension tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/ff/aliyun-supa/dashboard
git add src/app/api/me/instance/status/route.ts
git commit -m "feat(instance/status): surface stoppedAt/stoppedMs/estimatedNewExpiresAt/extendedByMs"
```

- [ ] **Step 7: Push dashboard branch + open PR**

```bash
cd /Users/ff/aliyun-supa/dashboard
git push -u origin feat/cloud-instance-stop-extension
gh pr create --base main --head feat/cloud-instance-stop-extension \
  --title "✨ Cloud instance stop-extension (Phase 1)" \
  --body "$(cat <<'EOF'
Implements docs/superpowers/specs/2026-05-27-cloud-instance-stop-extension-design.md (Phase 1) — hermes-installer side will land in a separate PR after this deploys to production.

## Summary
- **`InstanceRow`** gains 3 fields: \`stoppedAt\`, \`stopReason\`, \`totalManualStoppedMs\`
- **\`updateInstance()\`** now supports CAS via optional \`expectedStoppedAt\` parameter (returns \`Promise<boolean>\`; existing void-callers still work)
- **\`PlanRow\`** gains \`extendedByMs\` — reset to "0" on every renewal (purchase route)
- **New \`settleStoppedExtension()\`** computes equal-time compensation; CAS-protected; DI-tested with 8 unit tests
- **\`/stop\`** stamps \`stoppedAt\` + \`stopReason='manual'\` on soft stop (destroy unchanged)
- **\`/start\`** calls settle after provider.start success; returns \`extendedBy\` + \`newExpiresAt\` in response
- **\`/status\`** returns \`stoppedAt\`/\`stoppedMs\`/\`estimatedNewExpiresAt\`/\`extendedByMs\`

## Tests
- 8 unit tests for \`settleStoppedExtension()\` via DI
- Existing tests still pass

## Test plan
- [ ] Deploy to staging
- [ ] Stop a test instance, wait 5 min, start — verify \`expiresAt\` advanced ~5 min and \`extendedByMs\` ≈ 300000
- [ ] Hit /status mid-stopped — verify all 4 new fields present + correct
- [ ] Purchase a fresh subscription — verify \`extendedByMs\` reset to "0"
- [ ] Expire instance into grace, then stop+start — verify scope check still blocks start (no settle attempted)

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: PR URL printed.

---

## PHASE 2 — Hermes-installer (`/Users/ff/hermes-installer`)

> Phase 2 tasks run with `cwd=/Users/ff/hermes-installer`. Branch `feat/cloud-instance-stop-extension` was created during brainstorming (commit `61540e08` has the spec).

### Task 9: Add i18n keys for banner copy + duration units

**Files:**
- Modify: `webui/static/i18n.js`

- [ ] **Step 1: Find the en `LOCALES.en` block + an anchor near server-admin keys (if any)**

```bash
grep -n "^  en: {\|^  zh: {\|server_admin_" /Users/ff/hermes-installer/webui/static/i18n.js | head -10
```

If no `server_admin_*` keys exist yet, anchor on a nearby existing key like `'Check now'` or use the i18n block opener.

- [ ] **Step 2: Add 9 keys to en locale**

Use Edit. Find an existing unique en key to anchor on — the `settings_updates_available: '{count} update(s) available',` line we know exists at line 510:

Find:

```
    settings_update_check_failed: 'Update check failed',
    installer_update_banner_title:   'Hermes Installer {version} is available',
```

Replace with:

```
    settings_update_check_failed: 'Update check failed',
    server_admin_stopped_duration:          'Stopped {duration}',
    server_admin_estimated_new_expiry:      'Subscription extends to {date} on start (+{duration})',
    server_admin_extended_by_this_cycle:    'This cycle extended by stops: {duration}',
    server_admin_start_success_extended:    '✓ Instance started — subscription extended by {duration} (to {date})',
    server_admin_start_success_no_extend:   '✓ Instance started',
    server_admin_duration_less_than_minute: '< 1 minute',
    server_admin_duration_minutes:          '{n} min',
    server_admin_duration_hours:            '{n} h',
    server_admin_duration_days:             '{n} d',
    installer_update_banner_title:   'Hermes Installer {version} is available',
```

- [ ] **Step 3: Add 9 keys to zh locale**

Use Edit. Find:

```
    settings_update_check_failed: '更新检查失败',
    installer_update_banner_title:   'Hermes Installer {version} 已发布',
```

Replace with:

```
    settings_update_check_failed: '更新检查失败',
    server_admin_stopped_duration:          '已停机 {duration}',
    server_admin_estimated_new_expiry:      '启动时订阅延长至 {date} (+{duration})',
    server_admin_extended_by_this_cycle:    '本周期累计通过关机延长：{duration}',
    server_admin_start_success_extended:    '✓ 实例已启动，订阅延长 {duration}（至 {date}）',
    server_admin_start_success_no_extend:   '✓ 实例已启动',
    server_admin_duration_less_than_minute: '不到 1 分钟',
    server_admin_duration_minutes:          '{n} 分钟',
    server_admin_duration_hours:            '{n} 小时',
    server_admin_duration_days:             '{n} 天',
    installer_update_banner_title:   'Hermes Installer {version} 已发布',
```

- [ ] **Step 4: Verify JS syntax**

```bash
node -e "
const fs = require('fs');
const content = fs.readFileSync('/Users/ff/hermes-installer/webui/static/i18n.js', 'utf-8');
try { new Function(content); console.log('i18n.js parses OK'); }
catch (e) { console.log('SYNTAX ERROR:', e.message); process.exit(1); }
"
grep -c "server_admin_stopped_duration" /Users/ff/hermes-installer/webui/static/i18n.js
```

Expected: `i18n.js parses OK` + count `2` (one per locale).

- [ ] **Step 5: Commit**

```bash
cd /Users/ff/hermes-installer
git add webui/static/i18n.js
git commit -m "feat(i18n): server-admin stop-extension keys (en + zh)"
```

---

### Task 10: `_saFormatDuration()` helper + Node-runnable tests

**Files:**
- Create: `webui/tests/test_server_admin_duration_format.js`
- Modify: `webui/static/server-admin.js` (just adds the helper; banner wiring in Task 12)

- [ ] **Step 1: Write the failing test first**

Create `webui/tests/test_server_admin_duration_format.js` with EXACTLY:

```javascript
// Tests for _saFormatDuration. Self-contained — stubs t() so we don't
// need the full i18n.js loader. Mirrors the i18n template substitution
// behavior (single {n} placeholder).
//
// Run via:
//   node webui/tests/test_server_admin_duration_format.js
// Exits non-zero on any assertion failure.

const fs = require('fs');
const path = require('path');
const assert = require('assert');

// Stub global t() before loading server-admin.js. The format function
// uses keys 'server_admin_duration_less_than_minute|minutes|hours|days'.
global.t = function(key, vars) {
  const templates = {
    server_admin_duration_less_than_minute: '<1m',
    server_admin_duration_minutes:          '{n}m',
    server_admin_duration_hours:            '{n}h',
    server_admin_duration_days:             '{n}d',
  };
  let s = templates[key] || key;
  if (vars) for (const k in vars) s = s.split('{' + k + '}').join(vars[k]);
  return s;
};

// Load server-admin.js into the current global scope (similar to a
// browser <script> tag). Capture _saFormatDuration off the global.
const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'server-admin.js'), 'utf-8');
// eslint-disable-next-line no-eval
eval(src);

const fmt = global._saFormatDuration || _saFormatDuration;
assert.ok(typeof fmt === 'function', '_saFormatDuration must be defined');

// ─── Test cases ─────────────────────────────────────────────────────────
const cases = [
  [0,            '<1m'],
  [59_999,       '<1m'],
  [60_000,       '1m'],
  [60_001,       '1m'],
  [3_599_999,    '59m'],
  [3_600_000,    '1h'],
  [3_660_000,    '1h 1m'],
  [7_320_000,    '2h 2m'],
  [86_400_000,   '1d'],
  [90_000_000,   '1d 1h'],
  [172_800_000,  '2d'],
  [172_860_000,  '2d'],          // <1h hour-component → omitted
  [176_400_000,  '2d 1h'],
];

let failures = 0;
for (const [ms, expected] of cases) {
  const actual = fmt(ms);
  if (actual !== expected) {
    console.error(`FAIL: fmt(${ms}) → ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)}`);
    failures++;
  }
}

if (failures > 0) {
  console.error(`${failures}/${cases.length} cases failed`);
  process.exit(1);
}
console.log(`✓ all ${cases.length} cases pass`);
```

- [ ] **Step 2: Run the test — expect it to fail because the function doesn't exist yet**

```bash
node /Users/ff/hermes-installer/webui/tests/test_server_admin_duration_format.js 2>&1 | tail -3
```

Expected: `AssertionError: _saFormatDuration must be defined` or similar.

- [ ] **Step 3: Add the helper to `webui/static/server-admin.js`**

Use Read tool to find a good anchor near top-of-file (e.g. right after `_saStateBadge` definition around line 56). Use Edit. Find:

```
function _saFmtTime(iso) {
```

(verify by reading the file first — this is the next helper after badge). Insert the new helper RIGHT BEFORE it. Edit by finding:

```
function _saFmtTime(iso) {
  if (!iso) return '—';
```

Replace with:

```
function _saFormatDuration(ms) {
  // Returns localized strings via t() — see server_admin_duration_*
  // keys in i18n.js. Pure: no DOM access, no time math beyond ms math.
  if (ms < 60_000)      return t('server_admin_duration_less_than_minute');
  if (ms < 3_600_000)   return t('server_admin_duration_minutes', { n: Math.floor(ms / 60_000) });
  if (ms < 86_400_000) {
    const h = Math.floor(ms / 3_600_000);
    const m = Math.floor((ms % 3_600_000) / 60_000);
    return m === 0
      ? t('server_admin_duration_hours', { n: h })
      : t('server_admin_duration_hours', { n: h }) + ' ' +
        t('server_admin_duration_minutes', { n: m });
  }
  const d = Math.floor(ms / 86_400_000);
  const h = Math.floor((ms % 86_400_000) / 3_600_000);
  return h === 0
    ? t('server_admin_duration_days',  { n: d })
    : t('server_admin_duration_days',  { n: d }) + ' ' +
      t('server_admin_duration_hours', { n: h });
}

function _saFmtTime(iso) {
  if (!iso) return '—';
```

- [ ] **Step 4: Run the test — expect all 13 cases to pass**

```bash
node /Users/ff/hermes-installer/webui/tests/test_server_admin_duration_format.js 2>&1 | tail -3
```

Expected: `✓ all 13 cases pass`

- [ ] **Step 5: Verify server-admin.js still parses as a whole**

```bash
node -e "
const fs = require('fs');
const content = fs.readFileSync('/Users/ff/hermes-installer/webui/static/server-admin.js', 'utf-8');
try { new Function(content); console.log('server-admin.js parses OK'); }
catch (e) { console.log('SYNTAX ERROR:', e.message); process.exit(1); }
"
```

Expected: `server-admin.js parses OK`

- [ ] **Step 6: Commit**

```bash
cd /Users/ff/hermes-installer
git add webui/static/server-admin.js webui/tests/test_server_admin_duration_format.js
git commit -m "feat(server-admin): _saFormatDuration helper + 13 unit tests"
```

---

### Task 11: Verify `/api/neowow/instance/status` passes through 4 new fields

**Files:**
- Create: `webui/tests/test_instance_status_extension_passthrough.py`

`webui/api/neowow.py` already does dict passthrough — no source change needed; this task adds regression coverage.

- [ ] **Step 1: Write the test**

Create `webui/tests/test_instance_status_extension_passthrough.py` with EXACTLY:

```python
"""Regression: /api/neowow/instance/status must passthrough the 4 stop-extension
fields the dashboard adds in Phase 1 (stoppedAt, stoppedMs,
estimatedNewExpiresAt, extendedByMs). If neowow.py is ever refactored to
whitelist response keys, this test breaks loudly."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from api import neowow as nw


@pytest.fixture
def fake_dashboard_response():
    """Build a urlopen-style mock returning the full Phase-1 dashboard payload."""
    payload = json.dumps({
        "ok": True,
        "state": "stopped",
        "publicIp": "1.2.3.4",
        "subdomain": "chat-u1.neowow.studio",
        # Phase 1 stop-extension fields
        "stoppedAt":             "2026-06-01T11:00:00Z",
        "stoppedMs":             3_600_000,
        "estimatedNewExpiresAt": "2026-06-30T01:00:00.000Z",
        "extendedByMs":          7_200_000,
    }).encode("utf-8")
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    resp.read.return_value = payload
    return resp


def test_get_instance_status_passes_through_extension_fields(fake_dashboard_response):
    """All 4 stop-extension fields survive the proxy roundtrip."""
    with patch.object(nw, "_authenticated_get", return_value=fake_dashboard_response.read.return_value):
        result = nw.get_instance_status("fake-jwt")
    assert result["stoppedAt"]             == "2026-06-01T11:00:00Z"
    assert result["stoppedMs"]             == 3_600_000
    assert result["estimatedNewExpiresAt"] == "2026-06-30T01:00:00.000Z"
    assert result["extendedByMs"]          == 7_200_000
    # Sanity: existing fields still come through
    assert result["state"]                  == "stopped"
    assert result["publicIp"]               == "1.2.3.4"
```

- [ ] **Step 2: Inspect actual `get_instance_status` signature + adjust mock if needed**

```bash
grep -n "def get_instance_status\|def _authenticated_get\|def _instance_get" /Users/ff/hermes-installer/webui/api/neowow.py | head -10
```

If the function uses a different helper name (e.g. `_instance_get` instead of `_authenticated_get`), update the `patch.object(nw, "...", ...)` line in the test accordingly. Read 5 lines of context around `get_instance_status` to confirm.

- [ ] **Step 3: Run the test — expect pass on first try**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_instance_status_extension_passthrough.py -v
```

Expected: 1 passed. If FAIL with `KeyError`, the existing `get_instance_status` is filtering response keys — in that case modify `neowow.py` to passthrough all dict keys (treat the dict result opaquely), commit that change as part of this task.

- [ ] **Step 4: Commit**

```bash
cd /Users/ff/hermes-installer
git add webui/tests/test_instance_status_extension_passthrough.py
git commit -m "test(neowow): verify instance/status passes through 4 extension fields"
```

---

### Task 12: Banner render — stopped duration + cycle-extended footer

**Files:**
- Modify: `webui/static/server-admin.js`

- [ ] **Step 1: Find the stopped-state render branch**

```bash
grep -n "'stopped'\|state === 'stopped'\|case 'stopped'" /Users/ff/hermes-installer/webui/static/server-admin.js | head -5
```

Note the line numbers — there should be a render function that handles state='stopped'.

- [ ] **Step 2: Find the running-state render branch (for the footer)**

```bash
grep -n "'running'\|state === 'running'\|case 'running'" /Users/ff/hermes-installer/webui/static/server-admin.js | head -5
```

- [ ] **Step 3: Read both branches to understand existing markup**

Read the function bodies. Identify where to insert the new lines.

- [ ] **Step 4: Add stopped-duration markup**

Use Edit to add (inside the stopped state render) two new lines after the existing status card content. Concrete markup to insert (adjust to fit the surrounding HTML structure):

```javascript
// Inside the stopped state render:
if (status.stoppedAt && status.stoppedMs > 0) {
  html += '<div class="server-admin-stopped-extension" data-server-admin-live="1">';
  html += '  <div>' + _saEscape(t('server_admin_stopped_duration', { duration: _saFormatDuration(status.stoppedMs) })) + '</div>';
  if (status.estimatedNewExpiresAt) {
    const extendedMs = status.stoppedMs;
    html += '  <div class="muted">' + _saEscape(t('server_admin_estimated_new_expiry', {
      date:     _saFmtTime(status.estimatedNewExpiresAt),
      duration: _saFormatDuration(extendedMs),
    })) + '</div>';
  }
  html += '</div>';
}
```

The exact way to splice this into the existing function depends on whether it builds HTML via template literals, DOM API, or innerHTML — read existing code first and match the pattern. **If `_saEscape` doesn't already exist** in server-admin.js, add it as a helper next to `_saFormatDuration`:

```javascript
function _saEscape(s) {
  return String(s).replace(/[&<>"']/g, ch => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[ch]));
}
```

- [ ] **Step 5: Add running-state footer**

Use Edit to add inside the running state render — after the main status card:

```javascript
if (status.extendedByMs > 0) {
  html += '<div class="server-admin-cycle-extended muted">';
  html += '  ' + _saEscape(t('server_admin_extended_by_this_cycle', {
    duration: _saFormatDuration(status.extendedByMs),
  }));
  html += '</div>';
}
```

- [ ] **Step 6: Verify JS still parses**

```bash
node -e "
const fs = require('fs');
const content = fs.readFileSync('/Users/ff/hermes-installer/webui/static/server-admin.js', 'utf-8');
try { new Function(content); console.log('server-admin.js parses OK'); }
catch (e) { console.log('SYNTAX ERROR:', e.message); process.exit(1); }
"
node /Users/ff/hermes-installer/webui/tests/test_server_admin_duration_format.js
```

Expected: `parses OK` + `✓ all 13 cases pass`.

- [ ] **Step 7: Commit**

```bash
cd /Users/ff/hermes-installer
git add webui/static/server-admin.js
git commit -m "feat(server-admin): stopped-duration row + cycle-extended footer"
```

---

### Task 13: Start-success toast + 30s live re-render

**Files:**
- Modify: `webui/static/server-admin.js`

- [ ] **Step 1: Find the start button handler / `saStart()` function**

```bash
grep -n "saStart\|/api/neowow/instance/start" /Users/ff/hermes-installer/webui/static/server-admin.js | head -5
```

- [ ] **Step 2: Modify the success branch of saStart() to fire the right toast**

Find the `_saToast('✓ ...')` line in saStart's success branch (or its equivalent). Use Edit. Replace:

```
_saToast('✓ 实例已启动');
```

(or whatever the current hardcoded success message is — read the file to find the exact text)

With:

```javascript
if (resp && resp.extendedBy && resp.extendedBy.ms > 0) {
  _saToast(t('server_admin_start_success_extended', {
    duration: _saFormatDuration(resp.extendedBy.ms),
    date:     _saFmtTime(resp.newExpiresAt),
  }));
} else {
  _saToast(t('server_admin_start_success_no_extend'));
}
```

- [ ] **Step 3: Add the 30s live re-render loop**

Find where `_serverAdminPollTimer` is declared / cleared (it's at module top per earlier reading). Add a NEW timer variable for the live re-render, separate from the polling timer. Insert at module top:

```javascript
let _serverAdminLiveTimer = null;
```

Find the `serverAdminLoad()` function (or wherever the panel becomes visible — `saRender()` after a fetch). Add at the END of the render function, just before it returns:

```javascript
// 30s re-render so "已停机 X 分钟" advances without re-fetching.
if (_serverAdminLiveTimer) clearInterval(_serverAdminLiveTimer);
const stoppedLiveNodes = document.querySelectorAll('[data-server-admin-live="1"]');
if (stoppedLiveNodes.length > 0 && status.stoppedAt) {
  _serverAdminLiveTimer = setInterval(() => {
    // Re-derive stoppedMs from stoppedAt (don't bump the cached value
    // — re-fetching would be wasteful; the dashboard's stoppedMs is
    // server-derived but we can advance locally between fetches).
    const ms = Date.now() - Date.parse(status.stoppedAt);
    if (ms <= 0) return;
    const newDurationStr = _saFormatDuration(ms);
    // Re-render just the live nodes' first child (the "已停机" line).
    // Cheap shortcut: re-render the whole stopped state via the same
    // function. We trade re-paint cost (negligible — a single card)
    // for code simplicity.
    saRender({ ...status, stoppedMs: ms });
  }, 30_000);
}
```

If there's a `serverAdminUnload()` or visibility-change handler, also add `clearInterval(_serverAdminLiveTimer)` there to prevent leak when the user navigates away.

```bash
grep -n "serverAdminUnload\|MAIN_VIEW_PANELS\|panel-unload" /Users/ff/hermes-installer/webui/static/server-admin.js /Users/ff/hermes-installer/webui/static/panels.js | head -10
```

If `serverAdminUnload()` doesn't exist, create it at the bottom of server-admin.js:

```javascript
function serverAdminUnload() {
  if (_serverAdminPollTimer) { clearInterval(_serverAdminPollTimer); _serverAdminPollTimer = null; }
  if (_serverAdminLiveTimer) { clearInterval(_serverAdminLiveTimer); _serverAdminLiveTimer = null; }
}
```

And add a hook from panels.js if there's a pattern for it — if not, document this as a known limitation in a comment and skip the unload wiring for this task.

- [ ] **Step 4: Verify JS syntax + duration tests still pass**

```bash
node -e "
const fs = require('fs');
const content = fs.readFileSync('/Users/ff/hermes-installer/webui/static/server-admin.js', 'utf-8');
try { new Function(content); console.log('server-admin.js parses OK'); }
catch (e) { console.log('SYNTAX ERROR:', e.message); process.exit(1); }
"
node /Users/ff/hermes-installer/webui/tests/test_server_admin_duration_format.js
```

Expected: both pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/ff/hermes-installer
git add webui/static/server-admin.js
git commit -m "feat(server-admin): start-success toast + 30s live re-render of stopped duration"
```

---

## PHASE 3 — Verification + Release

### Task 14: Full suite, smoke, push hermes-installer PR, e2e on staging

**Files:** (none modified — verification only)

- [ ] **Step 1: Run hermes-installer full test suite to confirm no regression**

```bash
cd /Users/ff/hermes-installer/webui && \
  /Users/ff/hermes-installer/.build_venv/bin/python -m pytest \
  tests/test_installer_update.py \
  tests/test_installer_update_integration.py \
  tests/test_instance_status_extension_passthrough.py \
  --timeout=15 2>&1 | tail -10
```

Expected: all pass (29 from prior Phase 1+2 work + 1 new = 30).

```bash
node /Users/ff/hermes-installer/webui/tests/test_server_admin_duration_format.js
```

Expected: `✓ all 13 cases pass`.

- [ ] **Step 2: Push hermes-installer branch + open PR**

```bash
cd /Users/ff/hermes-installer
git push -u origin feat/cloud-instance-stop-extension
gh pr create --base main --head feat/cloud-instance-stop-extension \
  --title "✨ Cloud instance stop-extension UI (Phase 1)" \
  --body "$(cat <<'EOF'
WebUI side of docs/superpowers/specs/2026-05-27-cloud-instance-stop-extension-design.md. Requires the dashboard PR to deploy first.

## Summary
- 9 new i18n keys × 2 locales (en + zh) for stopped duration / extension copy + 4 atomic duration units
- \`_saFormatDuration(ms)\` helper + 13 unit tests covering boundary cases
- Test for \`/api/neowow/instance/status\` passthrough of 4 new dashboard fields
- Stopped state shows "已停机 X" + "启动时延长至 Y (+Z)" — refreshes every 30s without re-fetching
- Running state shows "本周期累计通过关机延长：N 天" footer when extendedByMs > 0
- /start success toast shows "✓ 实例已启动，订阅延长 X（至 Y）" when an extension was applied

## Tests
- 13 JS unit tests for duration formatter
- 1 Python integration test for status passthrough
- All existing webui tests still pass

## Test plan (post dashboard deploy)
- [ ] Open server panel while running → no extension footer (extendedByMs=0)
- [ ] Stop instance → wait 1 min → verify "已停机 1 分钟" + estimated date appears
- [ ] Wait another 30s → verify "已停机 1 分钟" auto-advances without re-fetch
- [ ] Click start → verify toast shows correct extension duration
- [ ] Refresh page after start → verify "本周期累计通过关机延长：X" footer appears
- [ ] Test zh + en locale switching

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: PR URL printed.

- [ ] **Step 3: End-to-end staging checklist (after dashboard PR merges to staging)**

Manual checks — perform on staging deploy, then on prod:

- [ ] Stop a test instance, wait 5 min, start — verify response includes `extendedBy.ms ≈ 300000` + `newExpiresAt` ~5 min later
- [ ] Hit /api/me/instance/status while stopped — confirm all 4 new fields populated
- [ ] Open WebUI → server panel → confirm "已停机 X 分钟" line + start toast both render correctly
- [ ] Stop → 2 min wait → confirm displayed minutes advance via 30s setInterval
- [ ] Purchase a fresh subscription → confirm extendedByMs resets to 0 in /status
- [ ] Try to start a destroyed instance → confirm scope check / 404 still works (no settle invoked)

- [ ] **Step 4: Merge order**

1. Dashboard PR merges first → deploy to production
2. Verify staging e2e passes
3. Hermes-installer PR merges second
4. New installer release picks up the WebUI changes (next v1.5.x tag, follow PR #6's release flow)

---

## Self-Review Notes

**Spec coverage:**

| Spec section | Task(s) |
|---|---|
| §1 架构总览 (架构 + 不变式) | Task 5 (settleStoppedExtension), Task 6 (/stop), Task 7 (/start), Task 8 (/status) |
| §2 Schema (InstanceRow 3 fields) | Task 1 |
| §2 Schema (PlanRow extendedByMs) | Task 3, Task 4 (renewal reset) |
| §3 settleStoppedExtension() function | Task 5 |
| §3 updateInstance CAS prerequisite | Task 2 |
| §4.1 /stop route | Task 6 |
| §4.2 /start route | Task 7 |
| §4.3 /status route | Task 8 |
| §4.4 neowow.py passthrough | Task 11 |
| §5.1 server-admin.js renders | Task 12, Task 13 |
| §5.1.4 _saFormatDuration | Task 10 |
| §5.2 i18n keys | Task 9 |
| §6.1 Dashboard unit tests | Task 5 |
| §6.2 Dashboard integration tests | Task 14 (manual on staging — TableStore stateful tests aren't unit-testable per existing repo precedent) |
| §6.3 hermes-installer tests | Tasks 10, 11 |
| §6.4 E2E checklist | Task 14 |

**Notes for the implementing engineer:**
- **Two repos**: `cd` carefully. Dashboard tasks 0-8 in `/Users/ff/aliyun-supa/dashboard`; hermes-installer tasks 9-14 in `/Users/ff/hermes-installer`. Each repo has its own feature branch.
- **Dashboard tests use `node --test`**, not vitest/jest. Imports use `.ts` extension. See `tests/billing.test.mjs` for the convention.
- **Hermes-installer venv pytest** at `/Users/ff/hermes-installer/.build_venv/bin/python -m pytest`, not system pytest.
- **TypeScript compile checks may surface unrelated pre-existing errors** in the dashboard repo (it's a large codebase). Only fail the step if the errors mention files we edited.
- **The /start and /status routes have existing fields not enumerated here** — the plan's snippets use `/* ...other existing fields... */` placeholders. Read the actual files first and preserve those fields verbatim.
- **`_saEscape` may already exist** in server-admin.js or another file — search before adding. Don't duplicate.
- **CAS error code `OTSConditionCheckFail`** is the TableStore SDK's standard error code for condition-check failures — verified against tablestore@5.x sources. If staging shows a different code in the error logs, adjust the comparison in Task 2 accordingly.
