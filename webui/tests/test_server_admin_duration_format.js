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

// Stub browser globals referenced at top level of server-admin.js
// (e.g. `window.addEventListener('beforeunload', ...)`). Function
// bodies that touch document/fetch don't execute during eval, so we
// only need stubs for what runs at load time.
global.window = { addEventListener: function() {} };

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
