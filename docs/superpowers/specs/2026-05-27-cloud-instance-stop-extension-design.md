# 云端实例 Phase 1 — 关机延期机制设计

> **Scope**: 用户手动关机的时长等量延长订阅 `expiresAt`。本 spec 只覆盖 Phase 1。
> Phase 2（实例内嵌轮询 + 立即升级 + 立即推送备份）单独 spec。

**Repos affected**:
- `aliyun-supa/dashboard` — TableStore schema 字段、`/stop` & `/start` 路由、`settleStoppedExtension()` 新函数、`/status` 响应字段
- `hermes-installer` — `webui/api/neowow.py` 透传字段、`webui/static/server-admin.js` UI 显示、`webui/static/i18n.js` 6 个 key

**Decisions reached during brainstorming** (2026-05-27):
1. **延期算法**：完全等量补偿 — stopped 24h ⇒ `expiresAt` 后推 24h
2. **未 resume 不影响过期**：`expiresAt` 不冻结，到期照常进 `expired_grace` → `expired`。补偿仅在 resume 那一刻结算
3. **只手动关机算**：未来加 `auto_idle` 后不享受延期 — schema 加 `stopReason` 区分
4. **Phase 2 远程命令机制**：实例内嵌轮询（复用 heartbeat），不引入云助手 / SSH（Phase 1 不实现，只为 Phase 2 选型记录）
5. **发版节奏**：Phase 1（关机延期）独立发；Phase 2（轮询 + 2 个新按钮）一起发

---

## 1. 架构总览

### 数据流

```
[用户点关机]
  → POST /api/me/instance/stop
  → dashboard 写 InstanceRow.stoppedAt + stopReason='manual'
  → provider.stop(ECS)
  → 此时 PlanRow.expiresAt 不动
  → GET /status 此后返回 stoppedAt / stoppedMs / estimatedNewExpiresAt
    供 WebUI 显示 "如果现在启动可延长到 YYYY-MM-DD"

[用户点启动]
  → POST /api/me/instance/start
  → provider.start(ECS) 成功后才结算（先 start 后 settle）
  → 调 settleStoppedExtension(userId):
      读 InstanceRow.stoppedAt, stoppedMs = now - stoppedAt
      仅当 stopReason='manual' 且 stoppedMs > 0:
        PlanRow.expiresAt += stoppedMs              ← 原子写
        PlanRow.extendedByMs += stoppedMs           ← 本周期累计
        InstanceRow.totalManualStoppedMs += stoppedMs ← 审计累计
      CAS 清空 InstanceRow.stoppedAt + stopReason
  → 返回 {state:'spawning', extendedBy:{ms,days,hours,minutes}, newExpiresAt}

[过期检查不变]
  PlanRow.expiresAt 永远是真实截止时刻
  expiry-sweeper cron 照旧（stop 期间走到期照样进 expired_grace）
```

### 关键不变式

- `PlanRow.expiresAt` 永远**单调推后或不变**，从不回退
- 唯一会推后 `expiresAt` 的写入点：续费 + start 路由的 settle 分支
- `InstanceRow.stoppedAt` 不为空 ⇔ `state='stopped' AND stopReason='manual'`

### 已考虑的场景

| 场景 | 行为 |
|---|---|
| stopped 期间用户充值 | 续费写新 `expiresAt` + 重置 `extendedByMs=0`；下次 start 时 settle 把这段 stoppedMs 加到新周期 |
| stopped 期间过期 | `expiresAt` 走到 ⇒ 进 `expired_grace` ⇒ start 被现有 scope 拒绝 ⇒ 用户必须先续费再 start ⇒ settle 把停机时长加到新周期 |
| stopped 期间 destroyed | `destroy=true` 删除 InstanceRow，stoppedAt 跟着没了，停机时长丢弃（用户主动放弃） |
| auto-idle 未来加入 | settle 函数检查 `stopReason==='manual'`，`auto_idle` 跳过 |
| 时钟回拨 | `stoppedMs <= 0` 时静默跳过 settle，状态字段照清，日志 warn |
| 并发 double-start | TableStore CAS on stoppedAt — 第二个请求 CAS fail，返回 `extendedMs: 0` |

---

## 2. 数据 Schema 改动

TableStore 是 schemaless，零迁移成本：旧行第一次 stop 时才写入新字段。

### Table A: `InstanceRow` (dashboard `src/lib/instance-store.ts`)

新增 3 个字段：

| 字段 | 类型 | 默认 | 写入时机 | 含义 |
|---|---|---|---|---|
| `stoppedAt` | `string \| undefined` (ISO 8601) | `undefined` | manual stop 时写；start / destroy 时清 | 当前这段 stopped 的开始时刻 |
| `stopReason` | `'manual' \| undefined` | `undefined` | 同上 | 区分手动 vs 未来的 `auto_idle` |
| `totalManualStoppedMs` | `string` (ms as decimal) | `"0"` | start 时 += stoppedMs | 实例生命周期累计审计值（运维用，不显示） |

`state='stopped'` 字段不动。

未来扩展点：`stopReason` 枚举增加 `'auto_idle'` 时，settle 函数加一行 `if (stopReason !== 'manual') return { extendedMs: 0 }`。

### Table B: `PlanRow` (dashboard `src/lib/membership-store.ts`)

新增 1 个字段：

| 字段 | 类型 | 默认 | 写入时机 | 含义 |
|---|---|---|---|---|
| `extendedByMs` | `string` (ms as decimal) | `"0"` | start 时 += stoppedMs；续费时归零 | 本订阅周期内通过关机累计延长的毫秒数；UI 显示用 |

现有字段 `expiresAt` 多一个写入路径（除续费、过期 sweep 外，start 路由也可推后）；仍**单调不回退**。

### 兼容性 / 回滚

- **新行 / 旧行兼容**：新字段为 `undefined` / `"0"` 时所有逻辑无影响
- **撤回回滚**：撤回 `/start` 的 settle 分支 + 忽略 4 个新字段 → 老逻辑完全不受影响

---

## 3. 关键函数：`settleStoppedExtension()`

位置：dashboard `src/lib/instance-extension.ts` (新建)

```ts
import { getInstance, updateInstance } from './instance-store';
import { getPlan, updatePlan } from './membership-store';

export interface ExtensionResult {
  extendedMs:   number;       // 这次结算延长的毫秒数（0 = no-op）
  newExpiresAt: string | null; // PlanRow 新 expiresAt（null = no plan）
}

/** 在 /start 路由中、provider.start 成功**之后**调用。
 *  幂等：CAS on stoppedAt — 并发调用第二次返回 extendedMs=0。
 *  失败安全：settle 出错不应回滚 provider.start。 */
export async function settleStoppedExtension(userId: string): Promise<ExtensionResult> {
  const inst = await getInstance(userId);
  if (!inst || !inst.stoppedAt || inst.stopReason !== 'manual') {
    return { extendedMs: 0, newExpiresAt: null };
  }
  const stoppedMs = Date.now() - Date.parse(inst.stoppedAt);
  if (stoppedMs <= 0) {
    // 时钟回拨：清状态但不延期
    console.warn('[settle] clock skew detected, stoppedMs=%d', stoppedMs);
    await updateInstance(userId, {
      stoppedAt: undefined, stopReason: undefined,
    }, /* expectedStoppedAt */ inst.stoppedAt);
    return { extendedMs: 0, newExpiresAt: null };
  }

  // CAS-protected clear on InstanceRow
  const cleared = await updateInstance(userId, {
    stoppedAt:            undefined,
    stopReason:           undefined,
    totalManualStoppedMs: String(parseInt(inst.totalManualStoppedMs || '0', 10) + stoppedMs),
  }, /* expectedStoppedAt */ inst.stoppedAt);
  if (!cleared) {
    // CAS fail → 另一个并发 start 已经 settle 过
    return { extendedMs: 0, newExpiresAt: null };
  }

  // Update PlanRow (best-effort; instance has no plan = arrowhead user)
  const plan = await getPlan(userId);
  if (!plan) return { extendedMs: stoppedMs, newExpiresAt: null };

  const newExpiresAt = new Date(Date.parse(plan.expiresAt) + stoppedMs).toISOString();
  await updatePlan(userId, {
    expiresAt:    newExpiresAt,
    extendedByMs: String(parseInt(plan.extendedByMs || '0', 10) + stoppedMs),
  });
  return { extendedMs: stoppedMs, newExpiresAt };
}
```

**两个前置子任务** (本 spec 范围内的 dashboard 改动)：

1. **`updateInstance()` 增加 CAS 支持** — 现有签名 `updateInstance(userId, patch)`，需扩展为 `updateInstance(userId, patch, expectedStoppedAt?: string): Promise<boolean>`，返回 `true` 表示更新成功、`false` 表示 CAS 不匹配。底层用 TableStore 的 `RowExistenceExpectation.IGNORE` + `condition: { columnConditions: [{ name: 'stoppedAt', expectedValue: ... }] }`。如果 `expectedStoppedAt` 未传则维持现有非 CAS 行为（向后兼容）。
2. **续费流程归零 `extendedByMs`** — `membership-store.ts` 现有续费写入路径（grep `expiresAt:.*cycleEndMs` 找到）追加 `extendedByMs: '0'`。一行改动。

---

## 4. API 改动

### 4.1 `POST /api/me/instance/stop` (dashboard)

**改动**：`destroy=false` 分支（软关机）追加写 `stoppedAt` + `stopReason='manual'`。

```ts
// 现有代码（约 line 93）：
await provider.stop(row.instanceId);
await updateInstance(caller.userId, {
  state: 'stopped',
});

// 改为：
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
  stoppedAt,                            // 新字段：客户端立即能渲染 "已停机 0 分钟"
  note:      /* 现有 note 字面量保留 */, // 不动
});
```

`destroy=true` 分支不动。

### 4.2 `POST /api/me/instance/start` (dashboard)

**改动**：provider.start 成功之后调用 `settleStoppedExtension()`。

```ts
// 在现有 start 成功路径里追加：
const ext = await settleStoppedExtension(caller.userId);

return NextResponse.json({
  ok:           true,
  state:        'spawning',  // 现有
  extendedBy:   ext.extendedMs > 0 ? formatDuration(ext.extendedMs) : null,
  newExpiresAt: ext.newExpiresAt,
});

// 其中 formatDuration(ms) → { ms, days, hours, minutes }
```

`settleStoppedExtension()` 抛错时**记日志但不影响 start 响应**（按 brainstorm 决议：先 start 后 settle，settle 是 best-effort）。建议包一层 try/catch + sentry。

### 4.3 `GET /api/me/instance/status` (dashboard)

**改动**：响应追加 4 个字段。

```ts
return NextResponse.json({
  // ...现有所有字段
  stoppedAt:             row.stoppedAt || null,
  stoppedMs:             row.stoppedAt ? Date.now() - Date.parse(row.stoppedAt) : 0,
  estimatedNewExpiresAt: row.stoppedAt && plan
    ? new Date(Date.parse(plan.expiresAt) + (Date.now() - Date.parse(row.stoppedAt))).toISOString()
    : null,
  extendedByMs:          parseInt(plan?.extendedByMs || '0', 10),
});
```

仅当 `stoppedAt` 存在 (即手动 stopped 中) 时才返回非空 `stoppedMs` / `estimatedNewExpiresAt`。

### 4.4 `webui/api/neowow.py` (hermes-installer)

**改动**：`get_instance_status()` 透传新字段。已有 helper 是 dict passthrough，无需改 code，只需测试覆盖这几个字段。

---

## 5. WebUI 改动

### 5.1 `webui/static/server-admin.js`

#### 5.1.1 停机状态卡新增一段

`stopped` state 渲染分支里，状态行下面追加：

```
已停机：1 小时 23 分钟
启动时订阅延长至：2026-06-04 23:21 (+1h23m)
```

实现：从 status 响应读 `stoppedAt`、`estimatedNewExpiresAt`、`stoppedMs`，用 `_saFormatDuration(ms)` 格式化。

每 30 秒（用 setInterval）重新渲染这两行（不重新 fetch status），让 "已停机" 文字随时间变化。`saServerAdminUnload()` 时 clearInterval。

#### 5.1.2 启动成功 toast

`saStart()` 收到 `{extendedBy, newExpiresAt}` 后：

```
✓ 实例已启动，订阅延长 1 小时 23 分钟（至 2026-06-04 23:21）
```

如果 `extendedBy === null` (没延期 — fresh start / no plan)，toast 退化为现有的 `'✓ 实例已启动'`。

#### 5.1.3 运行时状态卡 footer

`running` state 渲染分支里，如果 `extendedByMs > 0`：

```
本周期累计通过关机延长：3.5 天
```

`extendedByMs === 0` 时不渲染这行。

#### 5.1.4 `_saFormatDuration(ms: number) → string`

新 helper。构造逻辑（伪码）：

```js
function _saFormatDuration(ms) {
  if (ms < 60_000)      return t('server_admin_duration_less_than_minute');
  if (ms < 3_600_000)   return t('server_admin_duration_minutes', { n: Math.floor(ms/60_000) });
  if (ms < 86_400_000) {
    const h = Math.floor(ms / 3_600_000);
    const m = Math.floor((ms % 3_600_000) / 60_000);
    return m === 0
      ? t('server_admin_duration_hours',   { n: h })
      : t('server_admin_duration_hours',   { n: h }) + ' ' +
        t('server_admin_duration_minutes', { n: m });
  }
  const d = Math.floor(ms / 86_400_000);
  const h = Math.floor((ms % 86_400_000) / 3_600_000);
  return h === 0
    ? t('server_admin_duration_days',  { n: d })
    : t('server_admin_duration_days',  { n: d }) + ' ' +
      t('server_admin_duration_hours', { n: h });
}
```

边界用例：`0` → `"不到 1 分钟"`；`59_999` → 同；`60_000` → `"1 分钟"`；`3_599_999` → `"59 分钟"`；`3_600_000` → `"1 小时"`；`3_660_000` → `"1 小时 1 分钟"`；`86_400_000` → `"1 天"`。

### 5.2 `webui/static/i18n.js`

en / zh 各 10 个 key（其他 locale fallback en） — 5 个 banner copy + 4 个原子时长单位 + 1 个 "< 1 分钟"：

```js
// en
server_admin_stopped_duration:          'Stopped {duration}',
server_admin_estimated_new_expiry:      'Subscription extends to {date} on start (+{duration})',
server_admin_extended_by_this_cycle:    'This cycle extended by stops: {duration}',
server_admin_start_success_extended:    '✓ Instance started — subscription extended by {duration} (to {date})',
server_admin_start_success_no_extend:   '✓ Instance started',
server_admin_duration_less_than_minute: '< 1 minute',
server_admin_duration_minutes:          '{n} min',
server_admin_duration_hours:            '{n} h',
server_admin_duration_days:             '{n} d',

// zh
server_admin_stopped_duration:          '已停机 {duration}',
server_admin_estimated_new_expiry:      '启动时订阅延长至 {date} (+{duration})',
server_admin_extended_by_this_cycle:    '本周期累计通过关机延长：{duration}',
server_admin_start_success_extended:    '✓ 实例已启动，订阅延长 {duration}（至 {date}）',
server_admin_start_success_no_extend:   '✓ 实例已启动',
server_admin_duration_less_than_minute: '不到 1 分钟',
server_admin_duration_minutes:          '{n} 分钟',
server_admin_duration_hours:            '{n} 小时',
server_admin_duration_days:             '{n} 天',
```

英文 unit 用短写（`min`/`h`/`d`）避开单复数（`1 minutes` / `1 h` 都没歧义）。中文天然无单复数。

---

## 6. 测试策略

### 6.1 Dashboard 单元测试

`src/lib/__tests__/settle-stopped-extension.test.ts`：

| 测试 | 输入 | 期望 |
|---|---|---|
| no_instance | 无 InstanceRow | `{extendedMs: 0, newExpiresAt: null}` |
| no_stoppedAt | InstanceRow 有但 stoppedAt undefined | 同上 |
| auto_idle 跳过 | stopReason='auto_idle' | 同上（forward-compatible） |
| 正常结算 | stoppedAt = now - 1h，plan 存在 | extendedMs ≈ 3_600_000，newExpiresAt 推后 1h |
| 时钟回拨 | stoppedAt > now | extendedMs=0，stoppedAt 仍被清空，warn log |
| CAS 重入 | mock `updateInstance` 第二次返回 false | extendedMs=0 (并发安全) |
| 无 plan | 实例存在但 plan 缺失 | extendedMs > 0，newExpiresAt=null |
| extendedByMs 累加 | 初始 extendedByMs='3600000' | 结算后 = 旧值 + stoppedMs |

### 6.2 Dashboard 集成测试

`src/app/api/me/instance/__tests__/stop-start-extension.test.ts`：

1. **happy path**：fake-timer → stop → wait 1h → start → 验证 PlanRow.expiresAt 推后 1h、extendedByMs += 3_600_000、InstanceRow.stoppedAt cleared
2. **no-subscription instance**：stop → start → settle 静默跳过 PlanRow，不抛错
3. **double-start race**：stop → 并发 2 个 start → CAS 保证只 1 个 settle
4. **start failure**：mock provider.start 抛错 → expiresAt 不变 → 重试时 settle 仍能生效
5. **destroy short-circuits**：stop → destroy → stoppedAt 不残留、PlanRow 不被改

### 6.3 hermes-installer 测试

`webui/tests/test_instance_status_extension_passthrough.py`：

mock dashboard `/api/me/instance/status` 返回带新字段的 JSON，验证 `/api/neowow/instance/status` 透传这 4 个字段（`stoppedAt` / `stoppedMs` / `estimatedNewExpiresAt` / `extendedByMs`）。

### 6.4 端到端验收 checklist（人工）

- [ ] dashboard.staging：stop → 等 5 min → start → `extendedByMs` ≈ 300_000，`expiresAt` 推后 ≈ 5 min
- [ ] WebUI server-admin 面板：stopped 文字每 30s 跳；启动 toast 显示正确延期时长
- [ ] expired_grace 状态下 stop → start 被现有 scope 拒绝（settle 不被调用）
- [ ] 充值流程：stop → 充值 → start → settle 正常生效（决策点 #3）

---

## 7. 不在本 spec 范围内（Phase 2 占位）

以下功能下个 brainstorm 处理，本 spec 不实现：

- 实例内嵌轮询机制（heartbeat cron 改 30s + poll-actions endpoint）
- `POST /api/me/instance/upgrade` route + UI 按钮
- `POST /api/me/instance/backup-now` route + UI 按钮
- `auto_idle` 自动关机（M3 — 现有 stop route 注释已 mark）
- Dashboard 侧"延期记录"详情页

---

## 8. 不做（YAGNI）

- 延期上限：用户问的就是"等量补偿"，不设 cap。需要时一行 `Math.min(stoppedMs, MAX)` 即可
- 单复数 i18n：`1 hours` 微小可接受，省去 i18next ICU 复杂度
- 延期审计 UI：`totalManualStoppedMs` 只为运维查问题保留，不在 WebUI 显示
- `lastExtensionAt` 时间戳：从 server logs 可追，没必要污染 schema

---

## 自审

- ✅ Placeholder：无 TBD / TODO / "implement later"
- ✅ 内部一致性：function 名 (`settleStoppedExtension`)、字段名 (`stoppedAt` / `stopReason` / `totalManualStoppedMs` / `extendedByMs`) 全文一致
- ✅ Scope：单一 phase，可独立 ship，可独立回滚
- ✅ Ambiguity：所有关键场景在 §1.4 表格明示
