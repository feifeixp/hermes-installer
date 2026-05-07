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
| **Patched upstream files** | `webui/api/routes.py`<br>`webui/static/index.html` | ⚠️ **3-way merged.** Our edits are isolated to clearly-marked blocks (see below) — most upstream changes won't conflict. When they do, follow the recovery flow. |

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
| `Neowow integration — side-menu entry` | `webui/static/index.html` | Inside the side-menu `<div>`, between the **Providers** and **System** items |
| `Neowow integration — settings pane` | `webui/static/index.html` | Right before `<div class="settings-pane" id="settingsPaneSystem">` |
| `Neowow integration — GET routes` | `webui/api/routes.py` | Right after the `/api/workspaces` GET block, before `/api/workspaces/suggest` |
| `Neowow integration — POST routes` | `webui/api/routes.py` | Inside the POST handler dispatch, right after the `/api/rollback/restore` block, before the `return False` 404 fall-through |

---

## When a subtree sync produces conflicts

The cron in `.github/workflows/sync-webui.yml` runs `git subtree pull
--prefix=webui upstream-webui master --squash` daily. If it conflicts:

### 1. The cron will fail

GitHub Actions will surface the failure on the workflow run page. The
working tree on the bot's branch will have `<<<<<<<` / `=======` /
`>>>>>>>` conflict markers in `webui/api/routes.py` and/or
`webui/static/index.html`.

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
