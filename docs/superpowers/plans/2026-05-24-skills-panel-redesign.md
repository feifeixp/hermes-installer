# Skills Panel Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current sidebar-list skills panel with a 3-tab main-area UI (技能列表 / 技能市场 / 我的技能) with neowow.studio market integration and subscribe/unsubscribe flow.

**Architecture:** Single panel (`#panelSkills` / `#mainSkills`) stays; `#mainSkills` gets a full HTML/JS rewrite — tab bar + content panes + detail overlay. Six new backend routes proxy the neowow public API and handle subscribe/unsubscribe + local sync. Skills button moves from main nav into the Settings sub-menu.

**Tech Stack:** Vanilla JS (panels.js), Python (skills.py / routes.py), HTML/CSS (index.html / style.css). No new dependencies.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `webui/static/index.html` | Modify | Hide skills nav buttons; add to Settings sub-menu; rewrite `#panelSkills` + `#mainSkills` HTML |
| `webui/static/panels.js` | Modify | Replace `loadSkills()` system with new tab-based functions; add market/mine/subscribe/detail/toggle JS |
| `webui/static/style.css` | Modify | Add tab bar, market card, detail layout, toggle switch, login prompt CSS |
| `webui/api/skills.py` | Modify | Add 6 new functions: `get_market_skills`, `get_market_skill_detail`, `get_mine_skills`, `subscribe_skill`, `unsubscribe_skill`, `toggle_local_skill` |
| `webui/api/routes.py` | Modify | Wire 6 new GET/POST routes for the above functions |

---

## Task 1: Hide skills from main nav + add to Settings sub-menu (index.html)

**Files:**
- Modify: `webui/static/index.html:163` (rail skills button)
- Modify: `webui/static/index.html:217` (sidebar-nav skills button)
- Modify: `webui/static/index.html:402–406` (Settings sub-menu, after Providers)

- [ ] **Step 1: Hide the rail skills button (line 163)**

In `webui/static/index.html`, find the rail button with `data-panel="skills"` and add `style="display:none"`:

```html
<!-- BEFORE (line 163): -->
<button class="rail-btn nav-tab has-tooltip" data-panel="skills" onclick="switchPanel('skills',{fromRailClick:true})" data-tooltip="Skills" data-i18n-title="tab_skills" aria-label="Skills"><svg ...></svg></button>

<!-- AFTER: -->
<button class="rail-btn nav-tab has-tooltip" data-panel="skills" onclick="switchPanel('skills',{fromRailClick:true})" data-tooltip="Skills" data-i18n-title="tab_skills" aria-label="Skills" style="display:none"><svg ...></svg></button>
```

Use Edit tool — match exact `aria-label="Skills"` on the rail button (there's also a sidebar button; they're on different lines).

- [ ] **Step 2: Hide the sidebar-nav skills button (line 217)**

Find the `<button class="nav-tab ... data-panel="skills"` that has `<span class="nav-tab-label">技能</span>` and add `style="display:none"`:

```html
<!-- BEFORE: -->
<button class="nav-tab has-tooltip has-tooltip--bottom" data-panel="skills" data-label="Skills" onclick="switchPanel('skills',{fromRailClick:true})" data-tooltip="Skills" data-i18n-title="tab_skills"><svg ...></svg><span class="nav-tab-label">技能</span></button>

<!-- AFTER: -->
<button class="nav-tab has-tooltip has-tooltip--bottom" data-panel="skills" data-label="Skills" onclick="switchPanel('skills',{fromRailClick:true})" data-tooltip="Skills" data-i18n-title="tab_skills" style="display:none"><svg ...></svg><span class="nav-tab-label">技能</span></button>
```

- [ ] **Step 3: Add 技能 to the Settings sub-menu**

In `webui/static/index.html`, after the closing `</button>` of the Providers entry (the button with `data-settings-section="providers"`), insert:

```html
        <!-- BEGIN: Skills panel entry -->
        <button type="button" class="side-menu-item" onclick="switchPanel('skills',{fromRailClick:true})">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
          <span>技能</span>
        </button>
        <!-- END: Skills panel entry -->
```

Insert it between the closing `</button>` of Providers and the `<!-- BEGIN: Neowow integration` comment.

- [ ] **Step 4: Verify nav changes**

Open `http://localhost:8642` in browser. Confirm:
- Left rail has no stacked-layers icon (skills hidden)
- Sidebar-nav top section has no 技能 button
- Settings sidebar menu shows: Conversation / Appearance / Preferences / Providers / **技能** / Neowow Studio / 连接模式 / ...
- Clicking 技能 in Settings menu switches to the skills panel

- [ ] **Step 5: Commit**

```bash
cd /Users/ff/hermes-installer
git add webui/static/index.html
git commit -m "feat: move skills button from nav rail to Settings sub-menu"
```

---

## Task 2: Rewrite `#panelSkills` + `#mainSkills` HTML (index.html)

**Files:**
- Modify: `webui/static/index.html:283–291` (`#panelSkills` block)
- Modify: `webui/static/index.html:806–822` (`#mainSkills` block)

- [ ] **Step 1: Replace `#panelSkills` sidebar HTML**

Find this block (lines 283–291):
```html
    <div class="panel-view" id="panelSkills">
      <div class="panel-head">
        <span data-i18n="tab_skills">Skills</span>
        <div class="panel-head-actions">
          <button class="panel-head-btn has-tooltip has-tooltip--bottom" onclick="openSkillCreate()" data-tooltip="New skill" data-i18n-title="new_skill" aria-label="New skill"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></button>
        </div>
      </div>
      <div class="skills-search sidebar-search"><svg class="sidebar-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg><input id="skillsSearch" placeholder="Search skills..." data-i18n-placeholder="search_skills" oninput="filterSkills()"></div>
      <div class="skills-list" id="skillsList"><div style="padding:12px;color:var(--muted);font-size:12px" data-i18n="loading">Loading...</div></div>
    </div>
```

Replace with:
```html
    <div class="panel-view" id="panelSkills">
      <div class="panel-head">
        <span>技能</span>
      </div>
      <div style="padding:12px 14px;font-size:12px;color:var(--muted);line-height:1.6">
        在技能市场订阅技能，或管理本地已安装的技能。
      </div>
    </div>
```

- [ ] **Step 2: Replace `#mainSkills` main-view HTML**

Find this block (lines 806–822):
```html
    <div id="mainSkills" class="main-view">
      <div class="main-view-header">
        <div class="main-view-title" id="skillDetailTitle"></div>
        <div class="main-view-actions">
          <button id="btnEditSkillDetail" ...></button>
          <button id="btnDeleteSkillDetail" ...></button>
          <button id="btnCancelSkillDetail" ...></button>
          <button id="btnSaveSkillDetail" ...></button>
        </div>
      </div>
      <div class="main-view-body" id="skillDetailBody" style="display:none"></div>
      <div class="main-view-empty" id="skillDetailEmpty">
        ...
      </div>
    </div>
```

Replace with:
```html
    <div id="mainSkills" class="main-view">
      <!-- Tab bar -->
      <div class="skills-tabs" id="skillsTabBar">
        <button class="skills-tab-btn active" data-tab="list" onclick="skillsSwitchTab('list')">技能列表</button>
        <button class="skills-tab-btn" data-tab="market" onclick="skillsSwitchTab('market')">技能市场</button>
        <button class="skills-tab-btn" data-tab="mine" onclick="skillsSwitchTab('mine')">我的技能</button>
      </div>
      <!-- Tab content panes -->
      <div class="skills-tab-content" id="skillsTabContent">
        <div class="skills-tab-pane" id="skillsPaneList">
          <div id="skillsListBody"><div class="skills-loading">加载中...</div></div>
        </div>
        <div class="skills-tab-pane" id="skillsPaneMarket" style="display:none">
          <div id="skillsMarketBody"><div class="skills-loading">加载中...</div></div>
        </div>
        <div class="skills-tab-pane" id="skillsPaneMine" style="display:none">
          <div id="skillsMineBody"><div class="skills-loading">加载中...</div></div>
        </div>
      </div>
      <!-- Detail overlay (hides tabs) -->
      <div class="skills-detail-view" id="skillsDetailView" style="display:none">
        <div class="skills-detail-back">
          <button onclick="skillsCloseDetail()" class="skills-back-btn">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="15 18 9 12 15 6"/></svg>
            返回
          </button>
        </div>
        <div class="skills-detail-layout">
          <div class="skills-detail-main">
            <div class="skills-detail-header">
              <div class="skills-detail-title-row">
                <div class="skills-detail-icon" id="skillsDetailIcon">🧩</div>
                <div style="flex:1;min-width:0">
                  <div class="skills-detail-name" id="skillsDetailName"></div>
                  <div class="skills-detail-meta" id="skillsDetailMeta"></div>
                </div>
                <button class="skills-subscribe-btn" id="skillsDetailSubscribeBtn" onclick="skillsToggleSubscribe()">订阅</button>
              </div>
              <div class="skills-detail-desc" id="skillsDetailDesc"></div>
              <div class="skills-detail-tags" id="skillsDetailTags"></div>
            </div>
            <div class="skills-detail-body" id="skillsDetailBody"></div>
          </div>
          <div class="skills-detail-sidebar">
            <div class="skills-sidebar-title">技能信息</div>
            <div id="skillsDetailSidebarInfo"></div>
            <button class="skills-sidebar-subscribe-btn" id="skillsSidebarSubscribeBtn" onclick="skillsToggleSubscribe()">订阅</button>
          </div>
        </div>
      </div>
    </div>
```

- [ ] **Step 3: Verify HTML is syntactically valid**

```bash
python3 -c "
from html.parser import HTMLParser
class V(HTMLParser): pass
V().feed(open('/Users/ff/hermes-installer/webui/static/index.html').read())
print('HTML parse OK')
"
```
Expected: `HTML parse OK` (no exception)

- [ ] **Step 4: Commit**

```bash
cd /Users/ff/hermes-installer
git add webui/static/index.html
git commit -m "feat: rewrite #panelSkills + #mainSkills HTML for 3-tab skills UI"
```

---

## Task 3: Add CSS for the new skills UI (style.css)

**Files:**
- Modify: `webui/static/style.css` (append to end of file)

- [ ] **Step 1: Append skills CSS to style.css**

Add the following block to the end of `webui/static/style.css`:

```css
/* ── Skills panel redesign (3-tab UI) ────────────────────────────────── */
#mainSkills { flex-direction: column; overflow: hidden; }

/* Tab bar */
.skills-tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
  background: var(--bg-panel);
  flex-shrink: 0;
  padding: 0 12px;
}
.skills-tab-btn {
  background: none;
  border: none;
  padding: 10px 16px 8px;
  font-size: 13px;
  color: var(--muted);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color .15s, border-color .15s;
  font-family: inherit;
}
.skills-tab-btn:hover { color: var(--fg); }
.skills-tab-btn.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
  font-weight: 500;
}

/* Tab content */
.skills-tab-content { flex: 1; overflow-y: auto; min-height: 0; }
.skills-tab-pane { padding: 12px 14px; }

/* List tab — local skills with toggle */
.skills-list-item {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  border-radius: 7px;
  cursor: pointer;
  gap: 10px;
  margin-bottom: 4px;
  transition: background .12s;
}
.skills-list-item:hover { background: var(--hover); }
.skills-list-item-info { flex: 1; min-width: 0; }
.skills-list-item-name { font-size: 13px; font-weight: 500; }
.skills-list-item-desc {
  font-size: 12px;
  color: var(--muted);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Toggle switch */
.skill-toggle-wrap { position: relative; display: flex; align-items: center; flex-shrink: 0; }
.skill-toggle { position: absolute; opacity: 0; width: 0; height: 0; }
.skill-toggle-track {
  display: inline-block;
  width: 32px; height: 18px;
  border-radius: 9px;
  background: var(--border2, #555);
  transition: background .2s;
  cursor: pointer;
  position: relative;
}
.skill-toggle-track::after {
  content: '';
  position: absolute;
  left: 2px; top: 2px;
  width: 14px; height: 14px;
  border-radius: 50%;
  background: #fff;
  transition: left .2s;
}
.skill-toggle:checked + .skill-toggle-track { background: var(--accent); }
.skill-toggle:checked + .skill-toggle-track::after { left: 16px; }

/* Market grid */
.skills-market-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px;
  padding: 2px 0;
}
.skills-market-card {
  background: var(--bg-input, var(--bg-panel));
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  cursor: pointer;
  transition: border-color .15s, box-shadow .15s;
}
.skills-market-card:hover {
  border-color: var(--accent);
  box-shadow: 0 2px 8px rgba(0,0,0,.08);
}
.skills-market-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.skills-market-card-name { font-size: 13px; font-weight: 600; }
.skills-market-card-badge {
  font-size: 10px;
  color: var(--muted);
  background: var(--hover);
  padding: 2px 6px;
  border-radius: 4px;
}
.skills-market-card-desc {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.5;
  margin-bottom: 8px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.skills-market-card-footer {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
.skills-market-card-count { font-size: 11px; color: var(--muted); margin-right: 4px; }
.skills-tag {
  font-size: 10px;
  background: var(--hover);
  color: var(--muted);
  padding: 2px 6px;
  border-radius: 10px;
}

/* Detail view */
.skills-detail-view {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.skills-detail-back {
  padding: 10px 14px 6px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.skills-back-btn {
  background: none;
  border: none;
  color: var(--accent);
  cursor: pointer;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 0;
  font-family: inherit;
}
.skills-back-btn:hover { text-decoration: underline; }
.skills-detail-layout {
  flex: 1;
  display: flex;
  overflow: hidden;
  min-height: 0;
}
.skills-detail-main {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
  min-width: 0;
}
.skills-detail-sidebar {
  width: 220px;
  flex-shrink: 0;
  border-left: 1px solid var(--border);
  overflow-y: auto;
  padding: 16px 14px;
}
.skills-detail-header { margin-bottom: 16px; }
.skills-detail-title-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 10px;
}
.skills-detail-icon { font-size: 32px; line-height: 1; flex-shrink: 0; }
.skills-detail-name { font-size: 20px; font-weight: 700; margin-bottom: 4px; }
.skills-detail-meta { font-size: 12px; color: var(--muted); }
.skills-detail-desc { font-size: 13px; line-height: 1.6; margin-bottom: 10px; }
.skills-detail-tags { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 8px; }
.skills-detail-body { font-size: 14px; line-height: 1.7; }

/* Subscribe buttons */
.skills-subscribe-btn {
  padding: 6px 16px;
  border-radius: 6px;
  border: 1px solid var(--accent);
  background: var(--accent);
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  font-weight: 500;
  white-space: nowrap;
  flex-shrink: 0;
  transition: opacity .15s;
  font-family: inherit;
}
.skills-subscribe-btn.subscribed { background: transparent; color: var(--accent); }
.skills-subscribe-btn:hover { opacity: .85; }

.skills-sidebar-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: .04em;
  margin-bottom: 10px;
}
.skills-sidebar-row {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 12px;
}
.skills-sidebar-key { color: var(--muted); }
.skills-sidebar-val { font-weight: 500; }
.skills-sidebar-subscribe-btn {
  width: 100%;
  padding: 8px;
  border-radius: 6px;
  border: 1px solid var(--accent);
  background: var(--accent);
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  font-weight: 500;
  margin-top: 12px;
  transition: opacity .15s;
  font-family: inherit;
}
.skills-sidebar-subscribe-btn.subscribed { background: transparent; color: var(--accent); }
.skills-sidebar-subscribe-btn:hover { opacity: .85; }

/* Status states */
.skills-loading { padding: 20px; text-align: center; color: var(--muted); font-size: 13px; }
.skills-empty { padding: 30px 20px; text-align: center; color: var(--muted); font-size: 13px; line-height: 1.6; }
.skills-error { padding: 16px; color: #e55; font-size: 13px; }
.skills-gated { padding: 20px; text-align: center; color: var(--muted); font-size: 13px; font-style: italic; }
.skills-synced-badge { font-size: 11px; color: #4caf50; font-weight: 500; }
.skills-unsynced-badge { font-size: 11px; color: var(--muted); }
.skills-mine-status { flex-shrink: 0; }

/* Login prompt (mine tab when not logged in) */
.skills-login-prompt {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px 20px;
  text-align: center;
  gap: 8px;
}
.skills-login-icon { font-size: 40px; }
.skills-login-title { font-size: 15px; font-weight: 600; }
.skills-login-desc { font-size: 13px; color: var(--muted); margin-bottom: 8px; }

/* Login modal (subscribe while not logged in) */
.skills-login-modal {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,.4);
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
}
.skills-login-modal-box {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 20px 24px;
  max-width: 300px;
  width: 90%;
}
/* ── End skills panel redesign ─────────────────────────────────────────── */
```

- [ ] **Step 2: Verify styles load**

Reload the WebUI and navigate to the skills panel (via Settings sub-menu). The tab bar (技能列表 / 技能市场 / 我的技能) should be visible at the top of the main area.

- [ ] **Step 3: Commit**

```bash
cd /Users/ff/hermes-installer
git add webui/static/style.css
git commit -m "feat: add CSS for skills 3-tab panel UI"
```

---

## Task 4: Backend — new skills functions (skills.py)

**Files:**
- Modify: `webui/api/skills.py` (append before `__all__`)

- [ ] **Step 1: Add the 6 new backend functions**

In `webui/api/skills.py`, find the line `__all__ = (` (around line 562) and insert the following block immediately before it:

```python
# ── New Skills Market / Subscribe API ────────────────────────────────────────

def _get_auth_header(handler=None) -> str:
    """Return 'Bearer <token>' from JWT (cookie > file) or deploy token.

    Raises ValueError when no credentials are saved.
    """
    state = _read_state()
    file_jwt = (state.get("jwt") or "").strip()
    token    = (state.get("token") or "").strip()

    cookie_jwt = ""
    if handler is not None:
        try:
            from api.neowow import _is_neodomain_mode
            if _is_neodomain_mode():
                from api.auth import parse_neo_cookie
                cookie_jwt = (parse_neo_cookie(handler) or "").strip()
        except Exception:
            pass

    auth = cookie_jwt or file_jwt or token
    if not auth:
        raise ValueError(
            "No auth token. Please log in to neowow.studio first "
            "(Settings → Neowow Studio → 登录)."
        )
    return f"Bearer {auth}"


def get_market_skills() -> dict[str, Any]:
    """GET /api/public/skills — public, no auth required."""
    url = f"{_NEOWOW_BASE}/api/public/skills"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Hermes/neowow-skills-market"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
            err = json.loads(body).get("error") or body
        except Exception:
            err = body or str(e)
        raise RuntimeError(f"market API error ({e.code}): {err}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach neowow.studio: {e.reason}")


def get_market_skill_detail(skill_id: str) -> dict[str, Any]:
    """GET /api/public/skills/{id} — public, no auth required."""
    if not _is_valid_skill_id(skill_id):
        raise ValueError(f"Invalid skill id: {skill_id!r}")
    url = f"{_NEOWOW_BASE}/api/public/skills/{skill_id}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Hermes/neowow-skills-market"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
            err = json.loads(body).get("error") or body
        except Exception:
            err = body or str(e)
        raise RuntimeError(f"market API error ({e.code}): {err}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach neowow.studio: {e.reason}")


def get_mine_skills(handler=None) -> list[dict[str, Any]]:
    """GET /api/me/skills — authenticated, returns user's subscriptions."""
    auth = _get_auth_header(handler)  # raises ValueError if no token
    req = urllib.request.Request(
        _CLOUD_SKILLS_URL,
        headers={
            "Authorization": auth,
            "User-Agent": "Hermes/neowow-skills-mine",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
            err = json.loads(body).get("error") or body
        except Exception:
            err = body or str(e)
        raise RuntimeError(f"neowow API error ({e.code}): {err}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach neowow.studio: {e.reason}")

    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, list):
        raise RuntimeError("Unexpected response shape (no `skills` array)")

    nd = _neowow_dir()
    local_ids: set[str] = (
        {c.name for c in nd.iterdir() if c.is_dir() and _is_valid_skill_id(c.name)}
        if nd.exists() else set()
    )
    dismissed = read_dismissed()

    out: list[dict[str, Any]] = []
    for s in skills:
        sid = str(s.get("id") or "")
        out.append({
            "id":             sid,
            "name":           str(s.get("name") or ""),
            "description":    str(s.get("description") or ""),
            "version":        int(s.get("version") or 1),
            "displayName":    str(s.get("displayName") or ""),
            "tags":           s.get("tags") if isinstance(s.get("tags"), list) else [],
            "isDefault":      bool(s.get("isDefault")),
            "subscriberCount": int(s.get("subscriberCount") or 0),
            "updatedAt":      int(s.get("updatedAt") or 0),
            "isLocal":        sid in local_ids,
            "isDismissed":    sid in dismissed,
        })
    return out


def _refresh_skills_prompt() -> None:
    """Rebuild _skills_prompt.txt from currently installed local skills."""
    root = _neowow_dir()
    if not root.exists():
        return
    installed: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and _is_valid_skill_id(child.name):
            m = _read_local_meta(child)
            if m:
                installed.append(m)
    skills_prompt = _build_skills_prompt(installed)
    try:
        (root / _SKILLS_PROMPT_FILE).write_text(skills_prompt, encoding="utf-8")
    except Exception as e:
        logger.warning("[skills] could not write %s: %s", _SKILLS_PROMPT_FILE, e)


def _inject_skill_to_active_personality(skill_name: str, skill_desc: str) -> None:
    """Append a skill notice to the currently active personality in config.yaml."""
    try:
        import yaml  # type: ignore[import-not-found]
        from api.profiles import get_active_hermes_home  # type: ignore[import-not-found]
        config_path = get_active_hermes_home() / "hermes-agent" / "config.yaml"
    except Exception:
        return
    if not config_path.exists():
        return
    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return

    display = cfg.get("display") or {}
    active_personality = (display.get("personality") or "").strip()
    if not active_personality:
        return

    agent = cfg.get("agent") or {}
    personalities = agent.get("personalities") or {}
    if active_personality not in personalities:
        return

    current_text = str(personalities[active_personality] or "")
    marker = f"[已订阅技能：{skill_name}]"
    if marker in current_text:
        return  # Already injected

    suffix = f" — {skill_desc}" if skill_desc else ""
    new_text = current_text.rstrip() + f"\n\n{marker}{suffix}"
    personalities[active_personality] = new_text
    agent["personalities"] = personalities
    cfg["agent"] = agent

    try:
        config_path.write_text(
            yaml.dump(cfg, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("[skills] could not update personality: %s", e)


def _remove_skill_from_active_personality(skill_name: str) -> None:
    """Remove a skill notice from the currently active personality in config.yaml."""
    try:
        import yaml  # type: ignore[import-not-found]
        from api.profiles import get_active_hermes_home  # type: ignore[import-not-found]
        config_path = get_active_hermes_home() / "hermes-agent" / "config.yaml"
    except Exception:
        return
    if not config_path.exists():
        return
    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return

    display = cfg.get("display") or {}
    active_personality = (display.get("personality") or "").strip()
    if not active_personality:
        return

    agent = cfg.get("agent") or {}
    personalities = agent.get("personalities") or {}
    if active_personality not in personalities:
        return

    current_text = str(personalities[active_personality] or "")
    import re as _re
    pattern = rf"\n\n\[已订阅技能：{_re.escape(skill_name)}\][^\n]*"
    new_text = _re.sub(pattern, "", current_text).rstrip()
    if new_text == current_text.rstrip():
        return  # Nothing changed

    personalities[active_personality] = new_text
    agent["personalities"] = personalities
    cfg["agent"] = agent
    try:
        config_path.write_text(
            yaml.dump(cfg, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("[skills] could not update personality: %s", e)


def subscribe_skill(skill_id: str, handler=None) -> dict[str, Any]:
    """Subscribe to a market skill: POST to neowow API, write locally, rebuild prompt."""
    if not _is_valid_skill_id(skill_id):
        raise ValueError(f"Invalid skill id: {skill_id!r}")

    auth = _get_auth_header(handler)

    # 1. Subscribe on neowow.studio
    subscribe_url = f"{_NEOWOW_BASE}/api/me/skills/{skill_id}/subscribe"
    req = urllib.request.Request(
        subscribe_url,
        data=b"{}",
        headers={
            "Authorization":  auth,
            "Content-Type":   "application/json",
            "User-Agent":     "Hermes/neowow-skills-subscribe",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            json.loads(resp.read().decode("utf-8"))  # consume response
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
            err = json.loads(body).get("error") or body
        except Exception:
            err = body or str(e)
        raise RuntimeError(f"subscribe error ({e.code}): {err}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach neowow.studio: {e.reason}")

    # 2. Fetch skill detail (content may now be accessible after subscribing)
    detail_url = f"{_NEOWOW_BASE}/api/public/skills/{skill_id}"
    req2 = urllib.request.Request(
        detail_url,
        headers={"Authorization": auth, "User-Agent": "Hermes/neowow-skills"},
        method="GET",
    )
    skill_data: dict[str, Any] = {}
    try:
        with urllib.request.urlopen(req2, timeout=30) as resp2:
            skill_data = json.loads(resp2.read().decode("utf-8"))
    except Exception as e:
        logger.warning("[skills] could not fetch detail after subscribe: %s", e)

    # 3. Write locally
    if skill_data and skill_data.get("id"):
        try:
            _write_skill(skill_data)
        except Exception as e:
            logger.warning("[skills] could not write skill %s: %s", skill_id, e)

    # 4. Rebuild system prompt
    _refresh_skills_prompt()
    rebuild_skills_system_prompt()

    # 5. Inject into active personality
    _inject_skill_to_active_personality(
        str(skill_data.get("name") or skill_id),
        str(skill_data.get("description") or ""),
    )

    return {
        "ok":   True,
        "id":   skill_id,
        "name": str(skill_data.get("name") or skill_id),
    }


def unsubscribe_skill(skill_id: str, handler=None) -> dict[str, Any]:
    """Unsubscribe from a skill: POST to neowow API, remove locally, rebuild prompt."""
    if not _is_valid_skill_id(skill_id):
        raise ValueError(f"Invalid skill id: {skill_id!r}")

    auth = _get_auth_header(handler)

    # Read name before deleting local copy
    meta = _read_local_meta(_neowow_dir() / skill_id) or {}
    name = str(meta.get("name") or skill_id)

    # 1. Unsubscribe on neowow.studio
    unsub_url = f"{_NEOWOW_BASE}/api/me/skills/{skill_id}/unsubscribe"
    req = urllib.request.Request(
        unsub_url,
        data=b"{}",
        headers={
            "Authorization": auth,
            "Content-Type":  "application/json",
            "User-Agent":    "Hermes/neowow-skills-unsubscribe",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
            err = json.loads(body).get("error") or body
        except Exception:
            err = body or str(e)
        raise RuntimeError(f"unsubscribe error ({e.code}): {err}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach neowow.studio: {e.reason}")

    # 2. Remove locally
    _delete_local(skill_id)

    # 3. Rebuild
    _refresh_skills_prompt()
    rebuild_skills_system_prompt()

    # 4. Remove from active personality
    _remove_skill_from_active_personality(name)

    return {"ok": True, "id": skill_id}


def toggle_local_skill(name: str, enabled: bool) -> dict[str, Any]:
    """Enable or disable a local skill by updating config.yaml skills.disabled list."""
    try:
        import yaml  # type: ignore[import-not-found]
        from api.profiles import get_active_hermes_home  # type: ignore[import-not-found]
        config_path = get_active_hermes_home() / "hermes-agent" / "config.yaml"
    except Exception as e:
        raise RuntimeError(f"Cannot locate hermes config.yaml: {e}")

    if not config_path.exists():
        raise RuntimeError(f"config.yaml not found at {config_path}")

    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise RuntimeError(f"Cannot parse config.yaml: {e}")

    skills_cfg = cfg.setdefault("skills", {})
    disabled_set: set[str] = set(skills_cfg.get("disabled", []))

    if enabled:
        disabled_set.discard(name)
    else:
        disabled_set.add(name)

    skills_cfg["disabled"] = sorted(disabled_set)
    cfg["skills"] = skills_cfg

    try:
        config_path.write_text(
            yaml.dump(cfg, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
    except Exception as e:
        raise RuntimeError(f"Cannot write config.yaml: {e}")

    rebuild_skills_system_prompt()
    return {"ok": True, "name": name, "enabled": enabled}

```

- [ ] **Step 2: Update `__all__` to export new names**

Find the `__all__` tuple in `webui/api/skills.py` and add the new exports:

```python
# BEFORE:
__all__ = (
    "list_cloud_skills",
    "sync_all_skills",
    "sync_subscribed_skills",
    "get_local_status",
    "dismiss_skill",
    "restore_skill",
    "read_dismissed",
    "save_base_prompt",
    "rebuild_skills_system_prompt",
)

# AFTER:
__all__ = (
    "list_cloud_skills",
    "sync_all_skills",
    "sync_subscribed_skills",
    "get_local_status",
    "dismiss_skill",
    "restore_skill",
    "read_dismissed",
    "save_base_prompt",
    "rebuild_skills_system_prompt",
    # New market/subscribe API
    "get_market_skills",
    "get_market_skill_detail",
    "get_mine_skills",
    "subscribe_skill",
    "unsubscribe_skill",
    "toggle_local_skill",
)
```

- [ ] **Step 3: Verify Python syntax**

```bash
cd /Users/ff/hermes-installer
python3 -c "import sys; sys.path.insert(0,'webui'); import api.skills; print('skills.py OK')"
```
Expected: `skills.py OK`

- [ ] **Step 4: Commit**

```bash
cd /Users/ff/hermes-installer
git add webui/api/skills.py
git commit -m "feat: add market/mine/subscribe/unsubscribe/toggle functions to skills.py"
```

---

## Task 5: Backend — new routes (routes.py)

**Files:**
- Modify: `webui/api/routes.py` (two insertion points: GET section and POST section)

- [ ] **Step 1: Add GET routes**

In `webui/api/routes.py`, find the existing `if parsed.path == "/api/neowow/skills/cloud-list":` block (around line 3998). Insert the following **after** that block closes (after its `except Exception` handler returns):

```python
    # Public market listing — no auth. Proxies GET /api/public/skills.
    if parsed.path == "/api/skills/market":
        from api.skills import get_market_skills
        try:
            return j(handler, get_market_skills())
        except RuntimeError as e:
            return bad(handler, str(e), status=502)
        except Exception as e:
            logger.exception("skills/market failed")
            return bad(handler, str(e), status=500)

    # Public market detail — no auth. Proxies GET /api/public/skills/{id}.
    if parsed.path.startswith("/api/skills/market/"):
        skill_id = parsed.path[len("/api/skills/market/"):]
        from api.skills import get_market_skill_detail
        try:
            return j(handler, get_market_skill_detail(skill_id))
        except ValueError as e:
            return bad(handler, str(e))
        except RuntimeError as e:
            return bad(handler, str(e), status=502)
        except Exception as e:
            logger.exception("skills/market/{id} failed")
            return bad(handler, str(e), status=500)

    # Authenticated subscriptions list — returns skills from /api/me/skills.
    # Returns 403 (not 401) when no token so the frontend can show a prompt
    # rather than triggering the 401 login redirect.
    if parsed.path == "/api/skills/mine":
        from api.skills import get_mine_skills
        try:
            return j(handler, {"skills": get_mine_skills(handler)})
        except ValueError as e:
            # No token — 403 so frontend shows login prompt, not redirect
            return bad(handler, str(e), status=403)
        except RuntimeError as e:
            return bad(handler, str(e), status=502)
        except Exception as e:
            logger.exception("skills/mine failed")
            return bad(handler, str(e), status=500)
```

**How to find the insertion point:** Search for the string `"neowow skills/cloud-list failed"` — insert after the `return bad(handler, str(e), status=500)` that follows it, before the next `if parsed.path` block.

- [ ] **Step 2: Add POST routes**

Find the existing `if parsed.path == "/api/neowow/skills/sync":` block (around line 6005). Insert the following **before** it:

```python
    # Subscribe to a market skill: calls neowow API, writes locally, rebuilds prompt.
    # Body: { "id": "skill-abc123" }
    if parsed.path == "/api/skills/subscribe":
        skill_id = (body or {}).get("id", "")
        if not skill_id:
            return bad(handler, "id is required")
        from api.skills import subscribe_skill
        try:
            return j(handler, subscribe_skill(skill_id, handler))
        except ValueError as e:
            return bad(handler, str(e), status=400)
        except RuntimeError as e:
            return bad(handler, str(e), status=502)
        except Exception as e:
            logger.exception("skills/subscribe failed")
            return bad(handler, str(e), status=500)

    # Unsubscribe from a skill: calls neowow API, removes locally, rebuilds prompt.
    # Body: { "id": "skill-abc123" }
    if parsed.path == "/api/skills/unsubscribe":
        skill_id = (body or {}).get("id", "")
        if not skill_id:
            return bad(handler, "id is required")
        from api.skills import unsubscribe_skill
        try:
            return j(handler, unsubscribe_skill(skill_id, handler))
        except ValueError as e:
            return bad(handler, str(e), status=400)
        except RuntimeError as e:
            return bad(handler, str(e), status=502)
        except Exception as e:
            logger.exception("skills/unsubscribe failed")
            return bad(handler, str(e), status=500)

    # Enable/disable a local skill. Updates config.yaml skills.disabled list.
    # Body: { "name": "my-skill", "enabled": true }
    if parsed.path == "/api/skills/toggle":
        name    = (body or {}).get("name", "")
        enabled = bool((body or {}).get("enabled", True))
        if not name:
            return bad(handler, "name is required")
        from api.skills import toggle_local_skill
        try:
            return j(handler, toggle_local_skill(name, enabled))
        except RuntimeError as e:
            return bad(handler, str(e), status=500)
        except Exception as e:
            logger.exception("skills/toggle failed")
            return bad(handler, str(e), status=500)

```

- [ ] **Step 3: Verify Python syntax**

```bash
cd /Users/ff/hermes-installer
python3 -c "import ast; ast.parse(open('webui/api/routes.py').read()); print('routes.py syntax OK')"
```
Expected: `routes.py syntax OK`

- [ ] **Step 4: Smoke-test the market endpoint**

```bash
# Start the webui server first (if not already running):
# python3 webui/server.py &

curl -s http://localhost:8642/api/skills/market | python3 -m json.tool | head -20
```
Expected: JSON with `{"skills": [...], "total": N}` or similar.

- [ ] **Step 5: Commit**

```bash
cd /Users/ff/hermes-installer
git add webui/api/routes.py
git commit -m "feat: add skills market/mine/subscribe/unsubscribe/toggle routes"
```

---

## Task 6: Frontend JS — skills panel rewrite (panels.js)

**Files:**
- Modify: `webui/static/panels.js`

This task replaces the entire skills panel JS. There are two changes:
1. Replace the `await loadSkills()` call (around line 239) with `await loadSkillsPanel()`
2. Replace the `// ── Skills panel ──` block (starting at line 3133) with the new implementation

- [ ] **Step 1: Update the switchPanel hook (line ~239)**

Find this line in `panels.js`:
```javascript
  if (nextPanel === 'skills') await loadSkills();
```

Replace with:
```javascript
  if (nextPanel === 'skills') await loadSkillsPanel();
```

- [ ] **Step 2: Replace the entire skills panel JS section**

Find this comment block in `panels.js` (around line 3133):
```javascript
// ── Skills panel ──
async function loadSkills() {
```

Everything from that comment down to the end of `saveSkillForm` (search for the last function that starts with `skill` — `saveSkillForm`, `cancelSkillForm`, `deleteCurrentSkill`). Replace from `// ── Skills panel ──` through the closing `}` of the last skill function before the next `// ──` section comment.

**The exact replacement block** — find the section boundary by locating:
- Start: `// ── Skills panel ──` 
- End: the closing `}` of `deleteCurrentSkill` function (which is the last skill function before the next section header comment like `// ── Workspaces panel ──` or `// ── Profiles`)

Replace that entire range with:

```javascript
// ── Skills panel (3-tab redesign) ──────────────────────────────────────

// Keep _stripYamlFrontmatter here — was in the old skills section, still needed.
function _stripYamlFrontmatter(content) {
  if (!content) return { frontmatter: null, body: '' };
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(content);
  if (!m) return { frontmatter: null, body: content };
  return { frontmatter: m[1], body: content.slice(m[0].length) };
}

let _skillsState = {
  activeTab:    'list',   // 'list' | 'market' | 'mine'
  detailSkill:  null,     // null = show tabs; object = show detail
  detailSource: null,     // 'list' | 'market' | 'mine'
  listLoaded:   false,
  marketLoaded: false,
  mineLoaded:   false,
  localData:    null,     // [{name, description, category, disabled, ...}]
  marketData:   null,     // [{id, name, displayName, description, ...}]
  mineData:     null,     // [{id, name, isLocal, ...}]
};

async function loadSkillsPanel() {
  // Reset detail view when re-entering panel
  if (_skillsState.detailSkill) {
    _skillsState.detailSkill = null;
  }
  _skillsRenderView();
  if (!_skillsState.listLoaded) await _skillsLoadList();
}

function skillsSwitchTab(tab) {
  _skillsState.activeTab   = tab;
  _skillsState.detailSkill = null;
  document.querySelectorAll('.skills-tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  _skillsRenderView();
  if (tab === 'list'   && !_skillsState.listLoaded)   _skillsLoadList();
  if (tab === 'market' && !_skillsState.marketLoaded) _skillsLoadMarket();
  if (tab === 'mine'   && !_skillsState.mineLoaded)   _skillsLoadMine();
}

function _skillsRenderView() {
  const detailView = $('skillsDetailView');
  const tabContent = $('skillsTabContent');
  const tabBar     = $('skillsTabBar');
  if (!detailView || !tabContent || !tabBar) return;

  if (_skillsState.detailSkill) {
    detailView.style.display = '';
    tabContent.style.display = 'none';
    tabBar.style.display     = 'none';
  } else {
    detailView.style.display = 'none';
    tabContent.style.display = '';
    tabBar.style.display     = '';
    const tab = _skillsState.activeTab;
    const paneMap = { list: 'skillsPaneList', market: 'skillsPaneMarket', mine: 'skillsPaneMine' };
    Object.entries(paneMap).forEach(([t, id]) => {
      const el = $(id);
      if (el) el.style.display = t === tab ? '' : 'none';
    });
  }
}

// ── Tab: 技能列表 ────────────────────────────────────────────────────────
async function _skillsLoadList() {
  const box = $('skillsListBody');
  if (!box) return;
  box.innerHTML = '<div class="skills-loading">加载中...</div>';
  try {
    const data = await api('/api/skills');
    _skillsState.localData  = data.skills || [];
    _skillsState.listLoaded = true;
    _skillsRenderList();
  } catch(e) {
    box.innerHTML = `<div class="skills-error">加载失败: ${esc(e.message)}</div>`;
  }
}

function _skillsRenderList() {
  const skills = _skillsState.localData || [];
  const box = $('skillsListBody');
  if (!box) return;
  if (!skills.length) {
    box.innerHTML = '<div class="skills-empty">暂无本地技能<br><small>在技能市场订阅技能后会出现在这里</small></div>';
    return;
  }
  box.innerHTML = skills.map(s => `
    <div class="skills-list-item" onclick="_skillsOpenFromList(${JSON.stringify(s)})">
      <div class="skills-list-item-info">
        <div class="skills-list-item-name">${esc(s.name || '')}</div>
        <div class="skills-list-item-desc">${esc(s.description || '')}</div>
      </div>
      <label class="skill-toggle-wrap" onclick="event.stopPropagation()" title="${s.disabled ? '已禁用' : '已启用'}">
        <input type="checkbox" class="skill-toggle" data-skill-name="${esc(s.name || '')}" ${s.disabled ? '' : 'checked'}
          onchange="skillsToggleLocal(this.dataset.skillName, this.checked)">
        <span class="skill-toggle-track"></span>
      </label>
    </div>
  `).join('');
}

async function skillsToggleLocal(name, enabled) {
  try {
    await api('/api/skills/toggle', {
      method: 'POST',
      body: JSON.stringify({ name, enabled }),
    });
    const skill = (_skillsState.localData || []).find(s => s.name === name);
    if (skill) skill.disabled = !enabled;
  } catch(e) {
    setStatus('切换失败: ' + e.message);
    _skillsState.listLoaded = false;
    await _skillsLoadList();
  }
}

function _skillsOpenFromList(skill) {
  _skillsOpenDetail(skill, 'list');
  // Fetch content from local API
  api(`/api/skills/content?name=${encodeURIComponent(skill.name)}`)
    .then(data => {
      if (!_skillsState.detailSkill || _skillsState.detailSkill.name !== skill.name) return;
      _skillsState.detailSkill = { ..._skillsState.detailSkill, content: data.content || '' };
      _skillsRenderDetail();
    })
    .catch(() => {}); // non-fatal; show without content
}

// ── Tab: 技能市场 ────────────────────────────────────────────────────────
async function _skillsLoadMarket() {
  const box = $('skillsMarketBody');
  if (!box) return;
  box.innerHTML = '<div class="skills-loading">加载市场技能...</div>';
  try {
    const data = await api('/api/skills/market');
    _skillsState.marketData   = data.skills || [];
    _skillsState.marketLoaded = true;
    _skillsRenderMarket();
  } catch(e) {
    box.innerHTML = `<div class="skills-error">加载失败: ${esc(e.message)}</div>`;
  }
}

function _skillsRenderMarket() {
  const skills = _skillsState.marketData || [];
  const box = $('skillsMarketBody');
  if (!box) return;
  if (!skills.length) {
    box.innerHTML = '<div class="skills-empty">暂无市场技能</div>';
    return;
  }
  box.innerHTML = `<div class="skills-market-grid">${skills.map(s => `
    <div class="skills-market-card" onclick="_skillsOpenFromMarket('${esc(s.id || '')}')">
      <div class="skills-market-card-header">
        <div class="skills-market-card-name">${esc(s.displayName || s.name || '')}</div>
        <div class="skills-market-card-badge">v${esc(String(s.version || 1))}</div>
      </div>
      <div class="skills-market-card-desc">${esc(s.description || '')}</div>
      <div class="skills-market-card-footer">
        <span class="skills-market-card-count">⭐ ${s.subscriberCount || 0}</span>
        ${(s.tags || []).slice(0, 3).map(t => `<span class="skills-tag">${esc(t)}</span>`).join('')}
      </div>
    </div>
  `).join('')}</div>`;
}

async function _skillsOpenFromMarket(skillId) {
  // Show stub immediately with cached data
  const cached = (_skillsState.marketData || []).find(s => s.id === skillId);
  _skillsOpenDetail(cached || { id: skillId, name: skillId, _loading: true }, 'market');
  // Then fetch full detail in background
  try {
    const full = await api(`/api/skills/market/${encodeURIComponent(skillId)}`);
    if (!_skillsState.detailSkill || _skillsState.detailSkill.id !== skillId) return;
    _skillsState.detailSkill = { ..._skillsState.detailSkill, ...full };
    _skillsRenderDetail();
  } catch(e) {
    const body = $('skillsDetailBody');
    if (body && _skillsState.detailSkill && _skillsState.detailSkill.id === skillId) {
      body.innerHTML = `<div class="skills-error">加载失败: ${esc(e.message)}</div>`;
    }
  }
}

// ── Tab: 我的技能 ────────────────────────────────────────────────────────
async function _skillsLoadMine() {
  const box = $('skillsMineBody');
  if (!box) return;
  box.innerHTML = '<div class="skills-loading">加载中...</div>';
  try {
    const data = await api('/api/skills/mine');
    _skillsState.mineData   = data.skills || [];
    _skillsState.mineLoaded = true;
    _skillsRenderMine();
  } catch(e) {
    // 403 = not logged in (backend returns 403, not 401, to avoid redirect).
    // api() attaches err.status from the HTTP response, so we check that.
    if (e.status === 403 || (e.message && e.message.toLowerCase().includes('token'))) {
      box.innerHTML = `
        <div class="skills-login-prompt">
          <div class="skills-login-icon">🔐</div>
          <div class="skills-login-title">请先登录 neowow.studio</div>
          <div class="skills-login-desc">登录后即可查看和管理您订阅的技能</div>
          <button class="sm-btn" onclick="neowowAvatarClick(event)">登录 / 注册</button>
        </div>`;
    } else {
      box.innerHTML = `<div class="skills-error">加载失败: ${esc(e.message)}</div>`;
    }
  }
}

function _skillsRenderMine() {
  const skills = _skillsState.mineData || [];
  const box = $('skillsMineBody');
  if (!box) return;
  if (!skills.length) {
    box.innerHTML = `
      <div class="skills-empty">
        还没有订阅任何技能<br>
        <small style="margin-top:4px;display:block">去「<a href="#" onclick="skillsSwitchTab('market');return false" style="color:var(--accent)">技能市场</a>」浏览并订阅</small>
      </div>`;
    return;
  }
  box.innerHTML = skills.map(s => `
    <div class="skills-list-item" onclick="_skillsOpenFromMarket('${esc(s.id || '')}')">
      <div class="skills-list-item-info">
        <div class="skills-list-item-name">${esc(s.displayName || s.name || '')}</div>
        <div class="skills-list-item-desc">${esc(s.description || '')}</div>
      </div>
      <div class="skills-mine-status">
        ${s.isLocal
          ? '<span class="skills-synced-badge">✓ 已同步</span>'
          : '<span class="skills-unsynced-badge">未同步</span>'}
      </div>
    </div>
  `).join('');
}

// ── Detail view ─────────────────────────────────────────────────────────
function _skillsOpenDetail(skill, source) {
  _skillsState.detailSkill  = skill;
  _skillsState.detailSource = source;
  _skillsRenderView();
  _skillsRenderDetail();
}

function skillsCloseDetail() {
  _skillsState.detailSkill  = null;
  _skillsState.detailSource = null;
  _skillsRenderView();
}

function _skillsRenderDetail() {
  const skill = _skillsState.detailSkill;
  if (!skill) return;

  const nameEl    = $('skillsDetailName');
  const metaEl    = $('skillsDetailMeta');
  const descEl    = $('skillsDetailDesc');
  const tagsEl    = $('skillsDetailTags');
  const bodyEl    = $('skillsDetailBody');
  const sidebarEl = $('skillsDetailSidebarInfo');

  if (nameEl) nameEl.textContent = skill.displayName || skill.name || skill.id || '';

  if (metaEl) {
    const parts = [];
    if (skill.author)           parts.push(`作者：${esc(skill.author)}`);
    else if (skill.displayName && skill.displayName !== (skill.name || ''))
                                parts.push(`作者：${esc(skill.displayName)}`);
    if (skill.subscriberCount != null) parts.push(`${skill.subscriberCount} 人订阅`);
    metaEl.innerHTML = parts.join(' · ');
  }

  if (descEl) descEl.textContent = skill.description || '';

  if (tagsEl) {
    const tags = Array.isArray(skill.tags) ? skill.tags : [];
    tagsEl.innerHTML = tags.map(t => `<span class="skills-tag">${esc(t)}</span>`).join('');
  }

  if (bodyEl) {
    if (skill._loading) {
      bodyEl.innerHTML = '<div class="skills-loading">加载内容...</div>';
    } else if (skill._contentGated) {
      bodyEl.innerHTML = '<div class="skills-gated">订阅此技能后可查看完整内容</div>';
    } else {
      const content = skill.content || '';
      if (content) {
        const { body: mdBody } = _stripYamlFrontmatter(content);
        bodyEl.innerHTML = `<div class="main-view-content skill-detail-content">${renderMd(mdBody || content)}</div>`;
      } else {
        bodyEl.innerHTML = '<div class="skills-empty">暂无内容</div>';
      }
    }
  }

  if (sidebarEl) {
    const rows = [
      ['版本', `v${skill.version || 1}`],
      ['订阅数', skill.subscriberCount != null ? String(skill.subscriberCount) : null],
      ['类型', skill.isDefault ? '官方默认' : (skill.id ? '市场技能' : '本地技能')],
    ].filter(([, v]) => v != null);
    sidebarEl.innerHTML = rows.map(([k, v]) =>
      `<div class="skills-sidebar-row"><span class="skills-sidebar-key">${esc(k)}</span><span class="skills-sidebar-val">${esc(String(v))}</span></div>`
    ).join('');
  }

  _skillsUpdateSubscribeBtns();
}

function _skillsIsSubscribed(skill) {
  if (!skill || !skill.id) return false;
  return (_skillsState.mineData || []).some(s => s.id === skill.id);
}

function _skillsUpdateSubscribeBtns() {
  const skill  = _skillsState.detailSkill;
  const source = _skillsState.detailSource;
  const detailBtn  = $('skillsDetailSubscribeBtn');
  const sidebarBtn = $('skillsSidebarSubscribeBtn');

  // Hide subscribe buttons for local-only skills (no id = can't subscribe)
  const hide = source === 'list' || !skill?.id;
  [detailBtn, sidebarBtn].forEach(btn => {
    if (!btn) return;
    btn.style.display = hide ? 'none' : '';
    if (!hide) {
      const subscribed = _skillsIsSubscribed(skill);
      btn.textContent = subscribed ? '已订阅' : '订阅';
      btn.className   = btn.id === 'skillsDetailSubscribeBtn'
        ? `skills-subscribe-btn${subscribed ? ' subscribed' : ''}`
        : `skills-sidebar-subscribe-btn${subscribed ? ' subscribed' : ''}`;
    }
  });
}

async function skillsToggleSubscribe() {
  const skill = _skillsState.detailSkill;
  if (!skill || !skill.id) return;

  if (_skillsIsSubscribed(skill)) {
    // Unsubscribe
    try {
      await api('/api/skills/unsubscribe', {
        method: 'POST',
        body: JSON.stringify({ id: skill.id }),
      });
      _skillsState.mineData   = (_skillsState.mineData || []).filter(s => s.id !== skill.id);
      _skillsState.mineLoaded = false;
      _skillsState.listLoaded = false;
      setStatus('已取消订阅');
      _skillsUpdateSubscribeBtns();
    } catch(e) {
      _skillsHandleAuthError(e);
    }
  } else {
    // Subscribe
    try {
      await api('/api/skills/subscribe', {
        method: 'POST',
        body: JSON.stringify({ id: skill.id }),
      });
      if (!_skillsState.mineData) _skillsState.mineData = [];
      if (!_skillsState.mineData.some(s => s.id === skill.id)) {
        _skillsState.mineData.push({ ...skill, isLocal: true });
      }
      _skillsState.mineLoaded = false;
      _skillsState.listLoaded = false;
      setStatus('订阅成功！技能已同步到本地。');
      _skillsUpdateSubscribeBtns();
    } catch(e) {
      _skillsHandleAuthError(e);
    }
  }
}

function _skillsHandleAuthError(e) {
  const msg = (e && e.message) || '';
  // api() attaches err.status from HTTP response; 403 = no auth token
  if (e.status === 403 || msg.toLowerCase().includes('token')) {
    // Show modal login prompt
    const modal = document.createElement('div');
    modal.className = 'skills-login-modal';
    modal.innerHTML = `
      <div class="skills-login-modal-box">
        <div class="skills-login-title" style="margin-bottom:8px">需要登录</div>
        <div class="skills-login-desc">订阅技能需要先登录 neowow.studio 账号。</div>
        <div style="display:flex;gap:8px;margin-top:14px">
          <button class="sm-btn" onclick="neowowAvatarClick(event);this.closest('.skills-login-modal').remove()">登录 / 注册</button>
          <button class="sm-btn secondary" onclick="this.closest('.skills-login-modal').remove()">取消</button>
        </div>
      </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  } else {
    setStatus('操作失败: ' + msg);
  }
}
```

- [ ] **Step 3: Remove stale references to old skill functions**

Search panels.js for any remaining calls to the old functions that were replaced. Run:

```bash
grep -n "openSkillCreate\|editCurrentSkill\|deleteCurrentSkill\|cancelSkillForm\|saveSkillForm\|renderSkills\b\|filterSkills\b\|loadSkills\b" /Users/ff/hermes-installer/webui/static/panels.js
```

For each match found that references the OLD functions (not the new `_skills*` functions), either:
- Remove the reference if it's a dead call
- Replace with a comment `// removed — skills panel redesigned`

The most likely stale callers are any inline `onclick` in index.html (e.g. `openSkillCreate()` on the old "New skill" button — already removed in Task 2). If any remain in panels.js itself (e.g. stale function definitions), remove the function bodies.

- [ ] **Step 4: Verify no JS syntax errors**

```bash
node --input-type=module < /Users/ff/hermes-installer/webui/static/panels.js 2>&1 | head -20
```
Expected: Silent or only `SyntaxError` from browser-only globals (DOM stuff) — NOT a syntax error in the new skills functions. A cleaner check:

```bash
node -e "require('fs').readFileSync('/Users/ff/hermes-installer/webui/static/panels.js','utf8')" 2>&1 | head -5
```
Expected: no output (file loads as string without error).

- [ ] **Step 5: Functional smoke test**

1. Open WebUI at `http://localhost:8642`
2. Navigate to Settings → 技能
3. **技能列表 tab**: should show local skills from `~/.hermes/skills/` with toggles
4. **技能市场 tab**: click it → cards load from neowow.studio public API
5. Click a market card → detail view opens with name, description, subscribe button
6. **返回 button**: returns to tab view

- [ ] **Step 6: Commit**

```bash
cd /Users/ff/hermes-installer
git add webui/static/panels.js
git commit -m "feat: rewrite skills panel JS with 3-tab UI, market browse, and subscribe flow"
```

---

## Task 7: End-to-end verification

- [ ] **Step 1: Full flow test — local skills toggle**

1. Open skills panel → 技能列表 tab
2. Find a skill with `disabled: false` (toggle should be ON)
3. Toggle it OFF → panel shows toggle in OFF state
4. Open `~/.hermes/hermes-agent/config.yaml` → confirm `skills.disabled: [skill-name]`
5. Toggle it back ON → confirm it's removed from `skills.disabled`

- [ ] **Step 2: Full flow test — market browse + detail**

1. Click 技能市场 tab → cards appear
2. Click a card → detail view slides in with skill metadata
3. Right sidebar shows: 技能信息 (版本 / 订阅数 / 类型)
4. 返回 button → back to market grid

- [ ] **Step 3: Full flow test — subscribe (requires login)**

If logged in to neowow.studio (JWT in neowow.json or neoToken cookie):
1. Click a market skill → detail view
2. Click 订阅 → status bar shows "订阅成功！技能已同步到本地。"
3. Navigate to 技能列表 tab → new skill appears in list
4. Check `~/.hermes/skills/_neowow/<id>/SKILL.md` exists
5. Check `agent.system_prompt` in config.yaml includes the new skill

If NOT logged in:
1. Click 订阅 → modal appears: "需要登录 / 订阅技能需要先登录..."
2. Login button visible; Cancel button closes modal

- [ ] **Step 4: Full flow test — 我的技能 tab (not logged in)**

1. Click 我的技能 tab while logged out
2. Login prompt appears with "请先登录 neowow.studio" message and 登录/注册 button

- [ ] **Step 5: Final commit with git tag**

```bash
cd /Users/ff/hermes-installer
git add -A
git status   # confirm only expected files
git commit -m "feat: skills panel redesign complete — 3-tab UI with market browse and subscribe"
```
