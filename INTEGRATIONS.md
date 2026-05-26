# Integrations — Subtree-Survival Playbook

This fork of `hermes-installer` carries a few integrations that don't exist
upstream. Because the `webui/` directory is pulled from
`nesquena/hermes-webui` via `git subtree pull` (automated daily by
`.github/workflows/sync-webui.yml`), our local edits to files in that
directory live in **3-way merge territory**: most of the time they survive
cleanly, but occasionally upstream and our patches will touch overlapping
lines and produce conflicts.

This document is the recovery playbook.

---

## What's at risk vs. what's safe

| Category | Files | Behaviour during subtree pull |
|---|---|---|
| **Self-contained add-ons** | `webui/api/neowow.py`<br>`webui/api/skills.py`<br>`webui/static/neowow.js` | ✅ **Always safe.** These files don't exist upstream, so subtree merge has nothing to merge. They survive every sync untouched. |
| **Patched upstream files — marker-wrapped** | `webui/api/routes.py` (GET + POST routes blocks)<br>`webui/static/index.html` (6 blocks: boot overlay / auth avatar rail / auth popover / sidebar avatar / side-menu entry / settings pane) | ⚠️ **3-way merged.** Our edits are isolated to clearly-marked `BEGIN: Neowow integration …` blocks — most upstream changes won't conflict. When they do, follow the recovery flow. |
| **Patched upstream files — unmarked (bare edits)** | `webui/api/auth.py`<br>`webui/api/config.py`<br>`webui/api/providers.py`<br>`webui/api/updates.py`<br>`webui/server.py`<br>`webui/static/boot.js`<br>`webui/static/i18n.js`<br>`webui/static/panels.js`<br>`webui/static/style.css`<br>`webui/static/workspace.js` | ⚠️⚠️ **3-way merged without markers.** These files have Neowow-distribution overrides spread inline (Marvis skin CSS, zh language defaults, neodomain auth mode, Skills 3-tab rewrite, 401-handler loginUrl awareness, etc.). Conflicts are common and require manual inspection. See `webui/AGENTS.md` "Neowow distribution overrides" for the canonical list of what must be preserved. Recovery checklist below. |

### Why some files are marker-wrapped and others aren't

The marker convention was introduced to make recovery cheap. Files in
the **unmarked** row pre-date the convention or got added in flight
under time pressure. Moving each of them to marker-wrapped (or to the
extensions/ directory under Phase 2) is the right long-term fix —
tracked informally; volunteer who's syncing next welcome to do it.

---

## Conflict marker convention

Every patch into a shared upstream file is wrapped in opening / closing
banner lines so a human resolving a conflict can see the boundary at a
glance:

### Python (`webui/api/routes.py`)

```python
# ════════════════════════════════════════════════════════════════════
# BEGIN: Neowow integration — GET routes  (custom; not from upstream)
# If a subtree-pull conflict ever clobbers this block, copy it back
# from the patch snapshot.  See INTEGRATIONS.md (repo root) for the
# full recovery playbook.  Last verified working with upstream 9986d2f.
# Companion files: webui/api/neowow.py, webui/static/neowow.js,
#                  webui/static/index.html (the settings pane).
# ════════════════════════════════════════════════════════════════════
…patch contents…
# ════════════════════════════════════════════════════════════════════
# END: Neowow integration — GET routes
# ════════════════════════════════════════════════════════════════════
```

### HTML (`webui/static/index.html`)

```html
<!-- ════════════════════════════════════════════════════════════════
     BEGIN: Neowow integration — settings pane  (custom; not from upstream)
     …
     ════════════════════════════════════════════════════════════════ -->
…patch contents…
<!-- ════════════════════════════════════════════════════════════════
     END: Neowow integration — settings pane
     ════════════════════════════════════════════════════════════════ -->
```

The exact same banner style is used for the `routes.py` POST routes block
and the `index.html` side-menu button entry.

---

## Where each block lives today

| Marker label | File | Insertion point in upstream |
|---|---|---|
| `Neowow integration — boot overlay` | `webui/static/index.html` | Inline boot stage overlay markup near the top of `<body>` |
| `Neowow integration — auth avatar rail button` | `webui/static/index.html` | Inside the icon rail, above the bot-status item |
| `Neowow integration — auth popover` | `webui/static/index.html` | Loose popover anchored to the avatar rail button |
| `Neowow integration — sidebar avatar` | `webui/static/index.html` | Inside `.sidebar-header`, mirrors `#neowowAvatarRail`; shown only in Marvis skin via CSS |
| `Neowow integration — side-menu entry` | `webui/static/index.html` | Inside the side-menu `<div>`, between the **Providers** and **System** items |
| `Neowow integration — settings pane` | `webui/static/index.html` | Right before `<div class="settings-pane" id="settingsPaneSystem">` |
| `Neowow integration — GET routes` | `webui/api/routes.py` | Right after the `/api/workspaces` GET block, before `/api/workspaces/suggest` |
| `Neowow integration — POST routes` | `webui/api/routes.py` | Inside the POST handler dispatch, right after the `/api/rollback/restore` block, before the `return False` 404 fall-through |

---

## When a subtree sync produces conflicts

The cron in `.github/workflows/sync-webui.yml` runs `git subtree pull
--prefix=webui upstream-webui master --squash` daily. If it conflicts:

### 1. The cron will fail

GitHub Actions (when manually triggered) will surface the failure on
the workflow run page. The working tree on the bot's branch will have
`<<<<<<<` / `=======` / `>>>>>>>` conflict markers. Expect conflicts
across some subset of these files (observed during the 2026-05 sync to
v0.51.137):

- `webui/api/auth.py` (auth-mode dispatch + neodomain JWT helpers)
- `webui/api/config.py` (`_SETTINGS_DEFAULTS` + `_SETTINGS_SKIN_VALUES`)
- `webui/api/providers.py` (`_is_neowow_only_mode` filter)
- `webui/api/routes.py` (marker-wrapped Neowow GET/POST blocks)
- `webui/api/updates.py` (`@{upstream}` compare-ref logic)
- `webui/server.py` (neowow JWT request-scoping import)
- `webui/static/boot.js` (Marvis skin in `_SKINS`)
- `webui/static/i18n.js` (`cmd_theme` strings, one per locale)
- `webui/static/index.html` (inline boot script + 6 marker-wrapped blocks)
- `webui/static/panels.js` (Skills 3-tab rewrite vs upstream's Skills code)
- `webui/static/style.css` (Marvis skin CSS + Skills 3-tab CSS)
- `webui/static/workspace.js` (401 neodomain `loginUrl` redirect)

### 2. Inspect the conflict locally

```bash
git checkout main
git pull origin main           # gets the failed sync attempt's branch
git fetch upstream-webui master
git subtree pull --prefix=webui upstream-webui master --squash
# resolve conflicts manually, then:
git add webui/
git commit
git push origin main
```

### 3. Resolution rules

When you see a conflict, it will look something like this:

```
<<<<<<< HEAD
    # ════════════════════════════════════════════════════════════════════
    # BEGIN: Neowow integration — GET routes  (custom; not from upstream)
    …our patch…
    # END: Neowow integration — GET routes
    # ════════════════════════════════════════════════════════════════════
=======
    # ── New upstream code that touched the same area ──
    if parsed.path == "/api/some-new-upstream-route":
        …
>>>>>>> upstream/master
```

**Rule of thumb:** keep BOTH our marked block AND the upstream addition.
Our block is self-contained — if upstream merely added new code in the
same area, the right resolution is to keep upstream's new code AND
preserve our `BEGIN`/`END`-bracketed block right next to it.

The only time you actually have to think is when upstream **renamed
or removed something we depended on** (e.g. they moved `from api.neowow
import …` to a different path, or the dispatcher signature changed).
That's a real port, not just a merge — read the upstream commit to
understand what changed.

### 4. If the block was completely clobbered

If, somehow, the `BEGIN`/`END` block is gone entirely (e.g. someone
accepted "their side" without thinking), you can re-extract the bytes
from a known-good commit:

```bash
# find the last commit where the block was healthy
git log --oneline -- webui/api/routes.py | head -20
# recover the block from that commit
git show <good-sha>:webui/api/routes.py | \
    sed -n '/BEGIN: Neowow integration — GET routes/,/END: Neowow integration — GET routes/p'
# paste it back in the current file at the correct insertion point
```

The `feat(neowow): pull + apply Hermes cloud configs` commit is a
well-known reference point — `adc70ca` (or whatever its current hash is
on `feat/neowow-cloud-config`).

---

## Known historical traps (and how we sidestepped them)

These are upstream changes that broke the integration.  Each one is
documented so a future maintainer recognizes the symptom faster than
we did.

### Trap A — `switchSettingsSection` allow-list (caught: 2026-05)

**Symptom**: clicking "Neowow Studio" in settings highlighted the
sidebar item but the right pane silently kept showing Conversation.

**Root cause**: at some point upstream `webui/static/panels.js` rewrote
`switchSettingsSection(name)` to use a closed allow-list

```js
const section = (name === 'appearance' || name === 'preferences' ||
                 name === 'providers'  || name === 'system')
                  ? name : 'conversation';
```

so `switchSettingsSection('neowow')` falls through to `'conversation'`.
The pre-existing override pattern (delegate to `_orig(name)`) inherited
the bug verbatim.

**Fix**: in `webui/static/neowow.js`, the override now short-circuits
`'neowow'` BEFORE delegating to `_orig`, doing the sidebar / pane
toggling itself by id list.  No upstream patch — keeps the trap
isolated to our self-contained file.

**Lesson**: function-level overrides that delegate to `_orig` only stay
correct if upstream's function semantics don't change.  Treat any
upstream JS function we hook as a fragile contract; intercept inputs
we care about ourselves.

### Trap B — `_profileSwitchPanelLoad` references removed function (caught: 2026-05 sync)

**Symptom**: after our Skills 3-tab rewrite removed
`loadSkills/_toggleCatCollapse/renderSkills/filterSkills/toggleSkill`
from `webui/static/panels.js`, upstream introduced a new helper
`_profileSwitchPanelLoad()` that internally calls `loadSkills()`.
The subtree merge auto-resolved cleanly (no conflict markers in that
helper), so the broken reference would have shipped silently — Skills
panel would silently fail to load after a profile switch.

**Fix**: in the same conflict resolution pass, edit `_profileSwitchPanelLoad`
to call `loadSkillsPanel()` (our 3-tab entry point) instead of `loadSkills()`.
Documented at the call site.

**Lesson**: when we delete a function and rename it, search the WHOLE
post-merge tree for callers of the old name. Subtree merge only flags
conflicts on overlapping line edits, not on broken references — those
are silent until runtime.

### Trap C — `is_auth_enabled` was renamed by upstream (caught: 2026-05 sync)

**Symptom**: upstream split our original `is_auth_enabled` into
`is_password_auth_enabled` (the old body) + a new `is_auth_enabled`
that adds passkey support. Our HEAD had its own `is_auth_enabled`
extended with neodomain mode. Naive conflict resolution (keep one side)
would have dropped either passkey OR neodomain coverage.

**Fix**: keep BOTH `is_password_auth_enabled` (upstream's new helper)
AND a three-way-merged `is_auth_enabled` that calls
`get_auth_mode() != "none" or are_passkeys_enabled()` — covering all
three modes. See `webui/api/auth.py` for the merged version.

**Lesson**: when upstream extracts our logic into a renamed helper,
the right merge is usually to ADD the new helper and MERGE the new
upstream behavior into our extended version of the original function,
not pick a side.

### Trap D — `updates.py` `_select_apply_compare_ref` superseded our @{upstream} fallback (caught: 2026-05 sync)

**Symptom**: our HEAD had an inline `@{upstream}` → `_detect_default_branch`
fallback for choosing `compare_ref` before fetch. Upstream extracted the
SAME logic into a new helper `_select_apply_compare_ref(path)` (with
release-tag handling on top) and called it AFTER fetch.

**Fix**: drop our inline computation entirely. Our logic is fully
present inside `_select_apply_compare_ref` (lines 402–407 of the new
helper) — keeping our inline version would just be dead code that
gets overwritten 7 lines later.

**Lesson**: when an upstream helper subsumes our patch, accept the
helper and delete our patch. Don't preserve dead code as a "safety
keepsake" — it makes the next merge harder.

---

## Future direction (Phase 2)

The patch-and-pray approach gets fragile as we add more integrations.
The proper fix is a small extension/plugin system:

1. Move all integration code into `extensions/<name>/` at the repo root
   (outside `webui/`, so subtree pull never touches it).
2. Add a thin `webui/api/extensions_loader.py` (one upstream patch, ever)
   that dynamically registers routes from `extensions/*/api.py`.
3. `index.html` gets one mount point (`<div id="extension-mount-point">`),
   and each extension can inject HTML / JS / CSS into it via a
   `register_html()` / `register_static()` API.

When Phase 2 lands, this file's "patched upstream files" row shrinks to
**one tiny patch per upstream file ever**, and the conflict surface
collapses to near-zero. Tracking issue: TBD.

For now, the markers + this playbook are the cheapest defensive layer.
