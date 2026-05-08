// Hermes ↔ Neowow Studio integration — Settings panel client logic.
//
// Backed by api/neowow.py routes:
//   GET  /api/neowow/status         → { hasToken, maskedToken, lastDeploy }
//   POST /api/neowow/token  { token }              → save
//   POST /api/neowow/token  { clear: true }        → clear
//   POST /api/neowow/deploy { workerName, workspace } → publish
//
// The pane is loaded lazily on first switch — keeps the boot path lean and
// avoids hitting the dashboard before the user actually cares.

(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);

  // ── Rail avatar — primary auth entry point ──────────────────────────────
  //
  // The avatar button next to the gear icon is now THE login surface.
  // Three responsibilities:
  //   1. Visual state — gray dashed circle when logged-out, solid colored
  //      disc with the user's first character when logged-in.
  //   2. Click — start OAuth (no JWT) or open the user popover (has JWT).
  //   3. Popover — name, balance, per-type breakdown, recharge & logout.
  //
  // Refreshes on `neoSessionUpdated` (fired by /api/neowow/jwt POST + by
  // logout) and on DOMContentLoaded.  Tolerates the API being briefly
  // unavailable — the avatar just stays in its current state until the
  // next refresh.

  async function refreshRailAvatar() {
    const disc    = $('neowowAvatarDisc');
    const initial = $('neowowAvatarInitial');
    const btn     = $('neowowAvatarRail');
    if (!disc || !initial || !btn) return;

    let status = null;
    try {
      const r = await fetch('/api/neowow/status');
      if (r.ok) status = await r.json();
    } catch (_) { /* offline — keep current state */ }

    if (!status || !status.hasJwt) {
      disc.style.background = 'rgba(255,255,255,0.08)';
      disc.style.border     = '1px dashed rgba(255,255,255,0.30)';
      disc.style.color      = 'rgba(255,255,255,0.60)';
      initial.textContent   = '?';
      btn.title             = '点击登录 Neodomain';
      disc.dataset.hasJwt   = '';
      disc.dataset.nickname = '';
      return;
    }

    // Logged in — pull nickname from /api/neowow/whoami so we can
    // render the user's first character. Best-effort: '?' if fetch
    // fails.
    let nickname = '';
    try {
      const r = await fetch('/api/neowow/whoami');
      if (r.ok) {
        const j = await r.json();
        nickname = (j && (j.nickname || j.contact || j.email)) || '';
      }
    } catch (_) { /* keep nickname empty */ }

    const ch = (nickname && nickname[0]) || '✓';
    disc.style.background = 'linear-gradient(135deg, #5e60ce, #7950f2)';
    disc.style.border     = 'none';
    disc.style.color      = '#fff';
    initial.textContent   = ch.toUpperCase();
    btn.title             = nickname ? `已登录 · ${nickname}（点击查看积分）` : '已登录（点击查看积分）';
    disc.dataset.hasJwt   = '1';
    disc.dataset.nickname = nickname || '';
  }

  window.neowowAvatarClick = async function (event) {
    if (event && event.preventDefault) event.preventDefault();
    const disc    = $('neowowAvatarDisc');
    const popover = $('neowowAuthPopover');
    const body    = $('neowowAuthPopBody');
    const btn     = $('neowowAvatarRail');
    if (!disc || !popover || !body || !btn) return;

    // Logged out → straight into OAuth.  No popover.
    if (!disc.dataset.hasJwt) {
      // Use the existing OAuth start (defined further down). Fall back
      // to a direct window.open if for some reason the function isn't
      // ready yet (script-load race).
      if (typeof window.neowowStartOAuth === 'function') {
        window.neowowStartOAuth();
      } else {
        const ret = window.location.origin + '/api/neowow/oauth-callback';
        window.open('https://app.neowow.studio/api/oauth/start?return=' + encodeURIComponent(ret), '_blank');
      }
      return;
    }

    // Toggle on second click.
    if (popover.style.display === 'block') {
      popover.style.display = 'none';
      return;
    }

    // Position the popover next to the rail button. Rail is on the
    // LEFT edge of the viewport so we anchor to its right.
    const rect = btn.getBoundingClientRect();
    popover.style.left = (rect.right + 8) + 'px';
    popover.style.top  = Math.max(8, rect.top - 8) + 'px';
    body.innerHTML = '<div style="color:var(--muted)">加载积分余额…</div>';
    popover.style.display = 'block';

    try {
      const r = await fetch('/api/neowow/points');
      const d = await r.json();
      if (!r.ok) {
        // Most likely cause: JWT expired (Neodomain ~30-day TTL) or the
        // user invalidated the session elsewhere. Offer re-login + logout.
        body.innerHTML = `
          <div style="color:#e8a030;line-height:1.6">⚠️ ${escapeHtml(d.error || ('HTTP '+r.status))}</div>
          <div style="display:flex;gap:6px;margin-top:8px">
            <button class="btn-tiny" onclick="neowowStartOAuth()" style="background:linear-gradient(135deg,#5e60ce,#7950f2);color:#fff;border:none;flex:1">🔑 重新登录</button>
            <button class="btn-tiny" onclick="neowowClearJwt()" style="flex:1">退出</button>
          </div>
        `;
        return;
      }
      renderPopoverBody(body, d, disc.dataset.nickname || '');
    } catch (e) {
      body.innerHTML = `<div style="color:#ef4444">加载失败：${escapeHtml(e.message || 'unknown')}</div>`;
    }
  };

  function renderPopoverBody(el, points, nickname) {
    const total = points.totalAvailablePoints || 0;
    const m = points.membershipInfo || {};
    const initial = (nickname && nickname[0] || '?').toUpperCase();
    const memBadge = m.levelCode && m.levelCode !== 'FREE'
      ? `<span style="display:inline-block;padding:1px 8px;background:linear-gradient(135deg,#f59e0b,#ef4444);color:#fff;font-size:10px;font-weight:700;border-radius:6px;letter-spacing:0.3px;margin-left:6px">${escapeHtml(m.levelCode)}</span>`
      : '';
    const memLine = m.levelCode && m.levelCode !== 'FREE'
      ? `<div style="color:var(--muted,#94a3b8);font-size:11px;margin-top:2px">${escapeHtml(m.levelName || m.levelCode)} 会员${m.membershipTypeDesc ? ' · '+escapeHtml(m.membershipTypeDesc) : ''}${m.expireTime ? ' · 到期 '+escapeHtml(String(m.expireTime).slice(0,10)) : ''}</div>`
      : '';
    const breakdown = (points.pointsDetails || [])
      .filter(p => p.currentPoints > 0)
      .map(p => `
        <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--muted,#94a3b8);padding:2px 0">
          <span>${escapeHtml(p.pointsTypeName || ('类型 '+p.pointsType))}</span>
          <span style="font-family:ui-monospace,Menlo,monospace">${p.currentPoints.toLocaleString()}</span>
        </div>
      `).join('');

    el.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,0.06)">
        <div style="width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#5e60ce,#7950f2);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:14px;flex-shrink:0">${escapeHtml(initial)}</div>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;font-size:14px;line-height:1.3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(nickname || '已登录')}${memBadge}</div>
          ${memLine}
        </div>
      </div>
      <div style="display:flex;align-items:baseline;justify-content:space-between;padding:8px 0">
        <span style="font-size:11px;color:var(--muted,#94a3b8);text-transform:uppercase;letter-spacing:0.4px">总可用积分</span>
        <span style="font-weight:700;font-size:20px;color:#c7d2fe;font-variant-numeric:tabular-nums">💎 ${total.toLocaleString()}</span>
      </div>
      ${breakdown ? '<div style="padding:6px 0;border-top:1px dashed rgba(255,255,255,0.06)">' + breakdown + '</div>' : ''}
      <div style="display:flex;gap:6px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.06)">
        <a class="btn-tiny" href="https://app.neowow.studio/account" target="_blank" rel="noreferrer" style="text-decoration:none;flex:1;text-align:center;padding:6px 10px">💎 充值</a>
        <button class="btn-tiny" onclick="neowowClearJwt()" style="flex:1;padding:6px 10px">退出</button>
      </div>
    `;
  }

  // Close popover on outside-click + ESC.
  document.addEventListener('click', (e) => {
    const popover = $('neowowAuthPopover');
    const btn     = $('neowowAvatarRail');
    if (!popover || popover.style.display !== 'block') return;
    if (popover.contains(e.target) || (btn && btn.contains(e.target))) return;
    popover.style.display = 'none';
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const popover = $('neowowAuthPopover');
    if (popover && popover.style.display === 'block') popover.style.display = 'none';
  });

  // Refresh on session change + first paint.
  document.addEventListener('DOMContentLoaded', () => {
    void refreshRailAvatar();
    void refreshAccountBlock();
  });
  window.addEventListener('neoSessionUpdated', () => {
    void refreshRailAvatar();
    void refreshAccountBlock();
  });
  // First-render fallback if DOMContentLoaded already fired before this
  // script registered its listener (script lives inside an IIFE that
  // runs on parse).
  if (document.readyState !== 'loading') {
    void refreshRailAvatar();
    void refreshAccountBlock();
  }

  // ── Account block in Settings → Neowow Studio ─────────────────────────
  //
  // Replaces the old identity card + JWT block.  Three states:
  //   1. Logged out — single big "🔑 登录 Neodomain" button.
  //   2. Logged in — compact identity row + balance + recharge / logout.
  //   3. JWT expired or backend issue — error + re-login button.
  //
  // Mirrors the popover contents to keep the surface consistent: the
  // user can act either from the rail avatar or from this panel.

  async function refreshAccountBlock() {
    const el = $('neowowAccountBlock');
    if (!el) return;

    let status = null;
    try {
      const r = await fetch('/api/neowow/status');
      if (r.ok) status = await r.json();
    } catch (_) { /* offline */ }

    if (!status || !status.hasJwt) {
      // State 1: not logged in. Single prominent CTA.
      el.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap">
          <div style="flex:1;min-width:200px">
            <div style="font-weight:600;color:var(--text);font-size:14px;margin-bottom:4px">未登录 Neodomain</div>
            <div style="color:var(--muted);font-size:12px;line-height:1.5">登录后即可：发布应用、同步技能、使用云端配置、查看积分余额。</div>
          </div>
          <button class="btn" onclick="neowowStartOAuth()" style="padding:10px 20px;background:linear-gradient(135deg,#5e60ce,#7950f2);color:#fff;border:none;border-radius:8px;font-weight:600;cursor:pointer;flex-shrink:0">
            🔑 登录 Neodomain
          </button>
        </div>
      `;
      return;
    }

    // State 2 / 3: logged in — try to fetch points + identity. On
    // failure (likely expired JWT), surface re-login option.
    el.innerHTML = '<span style="color:var(--muted)">加载积分余额…</span>';
    let points = null;
    let nickname = '';
    try {
      const r = await fetch('/api/neowow/points');
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
      points = d;
    } catch (e) {
      el.innerHTML = `
        <div style="color:#e8a030;line-height:1.6;margin-bottom:8px">⚠️ ${escapeHtml((e && e.message) || 'unknown')}</div>
        <div style="display:flex;gap:8px">
          <button class="btn-tiny" onclick="neowowStartOAuth()" style="background:linear-gradient(135deg,#5e60ce,#7950f2);color:#fff;border:none">🔑 重新登录</button>
          <button class="btn-tiny" onclick="neowowClearJwt()">退出</button>
        </div>
      `;
      return;
    }
    try {
      const r = await fetch('/api/neowow/whoami');
      if (r.ok) {
        const j = await r.json();
        nickname = (j && (j.nickname || j.contact || j.email)) || '';
      }
    } catch (_) { /* keep '' */ }

    const total = points.totalAvailablePoints || 0;
    const m = points.membershipInfo || {};
    const initial = (nickname[0] || '✓').toUpperCase();
    const memBadge = m.levelCode && m.levelCode !== 'FREE'
      ? `<span style="display:inline-block;padding:1px 8px;background:linear-gradient(135deg,#f59e0b,#ef4444);color:#fff;font-size:10px;font-weight:700;border-radius:6px;letter-spacing:0.3px;margin-left:6px">${escapeHtml(m.levelCode)}</span>`
      : '';
    const memLine = m.levelCode && m.levelCode !== 'FREE'
      ? `<div style="color:var(--muted);font-size:11px;margin-top:2px">${escapeHtml(m.levelName || m.levelCode)} 会员${m.membershipTypeDesc ? ' · ' + escapeHtml(m.membershipTypeDesc) : ''}${m.expireTime ? ' · 到期 ' + escapeHtml(String(m.expireTime).slice(0,10)) : ''}</div>`
      : '';
    const breakdown = (points.pointsDetails || [])
      .filter(p => p.currentPoints > 0)
      .map(p => `<span style="color:var(--muted)">${escapeHtml(p.pointsTypeName)} <strong style="color:var(--text);font-variant-numeric:tabular-nums">${p.currentPoints.toLocaleString()}</strong></span>`)
      .join(' · ');

    el.innerHTML = `
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <div style="width:40px;height:40px;border-radius:50%;background:linear-gradient(135deg,#5e60ce,#7950f2);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:16px;flex-shrink:0">${escapeHtml(initial)}</div>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;color:var(--text);font-size:14px;line-height:1.3">${escapeHtml(nickname || '已登录')}${memBadge}</div>
          ${memLine}
        </div>
        <div style="text-align:right;flex-shrink:0">
          <div style="color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:0.4px">总积分</div>
          <div style="font-weight:700;font-size:18px;color:#c7d2fe;font-variant-numeric:tabular-nums">💎 ${total.toLocaleString()}</div>
        </div>
      </div>
      ${breakdown ? `<div style="margin-top:8px;padding-top:8px;border-top:1px dashed rgba(255,255,255,0.06);font-size:11px">${breakdown}</div>` : ''}
      <div style="display:flex;gap:6px;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.06)">
        <a class="btn-tiny" href="https://app.neowow.studio/account" target="_blank" rel="noreferrer" style="text-decoration:none;flex:1;text-align:center">💎 充值 / 详情</a>
        <button class="btn-tiny" onclick="neowowClearJwt()" style="flex:1">退出登录</button>
      </div>
    `;
  }

  // Hook the existing switchSettingsSection.  IMPORTANT: upstream's
  // implementation in webui/static/panels.js uses a closed allow-list:
  //
  //   const section = (name==='appearance'||name==='preferences'||
  //                    name==='providers'||name==='system') ? name
  //                                                         : 'conversation';
  //
  // so calling _orig('neowow') falls through to 'conversation' and our
  // pane never activates.  This is the canonical "subtree pulled in a
  // breaking upstream change" case our INTEGRATIONS.md playbook warned
  // about — fix it locally instead of patching panels.js (which would
  // re-break on every nightly subtree sync).
  //
  // The override below short-circuits 'neowow' before _orig sees it,
  // does the same sidebar-active + pane-active toggling upstream does
  // for whitelisted sections, then triggers our loader.  Any other
  // name still goes through _orig untouched.
  if (typeof window.switchSettingsSection === 'function') {
    const _orig = window.switchSettingsSection;
    window.switchSettingsSection = function (name) {
      if (name === 'neowow') {
        // Sidebar — mark Neowow item active, others inactive.
        document.querySelectorAll('#settingsMenu .side-menu-item').forEach((it) => {
          it.classList.toggle('active', it.dataset && it.dataset.settingsSection === 'neowow');
        });
        // Panes — show Neowow, hide the others.  We list every known
        // pane id explicitly so we don't accidentally activate a future
        // upstream pane we don't know about.
        var paneIds = [
          'settingsPaneConversation',
          'settingsPaneAppearance',
          'settingsPanePreferences',
          'settingsPaneProviders',
          'settingsPaneNeowow',
          'settingsPaneSystem',
        ];
        paneIds.forEach(function (id) {
          var pane = document.getElementById(id);
          if (pane) pane.classList.toggle('active', id === 'settingsPaneNeowow');
        });
        // Mobile dropdown sync — keep it in sync if it's there.
        var dd = document.getElementById('settingsSectionDropdown');
        if (dd && dd.value !== 'neowow') dd.value = 'neowow';
        loadNeowowStatus();
        return;
      }
      _orig(name);
    };
  }

  // ── Status ────────────────────────────────────────────────────────────
  async function loadNeowowStatus() {
    const statusEl = $('neowowTokenStatus');
    const clearBtn = $('neowowClearTokenBtn');
    const inputEl  = $('neowowTokenInput');
    const lastBlock = $('neowowLastDeployBlock');
    const lastInfo  = $('neowowLastDeployInfo');
    if (!statusEl) return;

    try {
      const r = await fetch('/api/neowow/status');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      if (d.hasToken) {
        statusEl.innerHTML = `已保存 token：<code>${escapeHtml(d.maskedToken)}</code>`;
        statusEl.style.color = 'var(--accent)';
        if (inputEl) inputEl.value = '';
        if (inputEl) inputEl.placeholder = '粘贴新 token 可覆盖';
        if (clearBtn) clearBtn.style.display = '';
      } else {
        statusEl.innerHTML = '还没保存 token。<a href="https://app.neowow.studio/account/deploy-tokens" target="_blank" rel="noreferrer" style="color:var(--accent)">前往 app.neowow.studio 生成 →</a>';
        statusEl.style.color = 'var(--muted)';
        if (clearBtn) clearBtn.style.display = 'none';
      }
      if (d.lastDeploy && d.lastDeploy.workerName) {
        const ld = d.lastDeploy;
        lastBlock.style.display = '';
        const url = ld.url ? `<a href="${escapeHtml(ld.url)}" target="_blank" rel="noreferrer" style="color:var(--accent);word-break:break-all">${escapeHtml(ld.url)}</a>` : '(URL pending)';
        const at  = ld.at ? new Date(ld.at).toLocaleString() : '';
        lastInfo.innerHTML = `
          <div><strong>${escapeHtml(ld.workerName)}</strong> · ${ld.fileCount || 0} files${at ? ` · ${escapeHtml(at)}` : ''}</div>
          <div style="margin-top:4px">${url}</div>
        `;
      } else {
        lastBlock.style.display = 'none';
      }
    } catch (e) {
      statusEl.textContent = `加载失败：${e.message}`;
      statusEl.style.color = '#ef4444';
    }

    // Pre-fill the workspace field from the current session if not set yet.
    const wsInput = $('neowowDeployWorkspace');
    if (wsInput && !wsInput.value) {
      wsInput.value = currentWorkspace() || '';
    }

    // Cloud-config card piggybacks on the same panel-open event so it
    // renders together with the token state — keeps panel-load to one
    // /api/neowow/* round-trip pair instead of a network waterfall.
    void loadCloudStatus();
    // Skills card too — disk-only read, free.
    void loadSkillsStatus();
    // Identity chip + JWT block are now driven by the rail avatar +
    // settings account block (refreshAccountBlock). The legacy
    // identity card is hidden in the new HTML; we keep loadIdentityChip
    // a no-op (it returns early when neowowIdentityCard has no
    // visible parent siblings). Account-block refresh happens
    // independently on neoSessionUpdated, so nothing to do here.
  }

  // ── Identity chip ────────────────────────────────────────────────────
  // Renders <name> + small subtitle (account type / contact / userId).
  // Populated by GET /api/neowow/whoami which proxies the dashboard's
  // /api/me/whoami with the saved deploy-token.
  async function loadIdentityChip() {
    const card = $('neowowIdentityCard');
    const nameEl = $('neowowIdentityName');
    const metaEl = $('neowowIdentityMeta');
    const avatarEl = $('neowowIdentityAvatar');
    if (!card || !nameEl || !metaEl) return;
    try {
      const r = await fetch('/api/neowow/whoami');
      if (!r.ok) {
        // 401 (no token) is expected on first load — silently hide.
        // Other errors: also hide; the rest of the panel still works.
        hideIdentityChip();
        return;
      }
      const d = await r.json();
      const name = d.displayName || d.nickname || d.userId || '匿名';
      nameEl.textContent = name;

      // Avatar: use server-supplied image when present; otherwise
      // first letter of name with the brand gradient background.
      if (d.avatar) {
        avatarEl.innerHTML = `<img src="${escapeHtml(d.avatar)}" alt="" style="width:100%;height:100%;object-fit:cover">`;
      } else {
        avatarEl.textContent = (name || '?').slice(0, 1).toUpperCase();
      }

      // Subtitle: account type · masked contact · last seen.  Filter out
      // empty values so the row stays tight.
      const parts = [];
      if (d.userType) parts.push(d.userType === 'ENTERPRISE' ? '🏢 企业账户' : '👤 个人账户');
      if (d.contact)  parts.push('📱 ' + escapeHtml(d.contact));
      else if (d.email) parts.push('✉️ ' + escapeHtml(d.email));
      // Keep userId compact — show last 8 chars only, full version is
      // in the title attribute for hover-inspect.
      if (d.userId)   parts.push('<span style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace" title="user id: ' + escapeHtml(d.userId) + '">id …' + escapeHtml(d.userId.slice(-8)) + '</span>');
      metaEl.innerHTML = parts.join(' · ');

      card.style.display = 'block';
    } catch {
      hideIdentityChip();
    }
  }

  function hideIdentityChip() {
    const card = $('neowowIdentityCard');
    if (card) card.style.display = 'none';
  }

  // ── JWT (Neodomain login) sub-block of the identity card ────────────────
  //
  // Three states the panel toggles between:
  //   1. No deploy-token → identity card hidden entirely (handled above)
  //   2. Deploy-token but no JWT → "登录 Neodomain" button to start OAuth
  //   3. Deploy-token + JWT → balance / membership / "退出 Neodomain" button
  //
  // The OAuth flow uses the dashboard's existing /api/oauth/start
  // endpoint with our localhost callback as the return URL.  Dashboard's
  // sanitizeReturnUrl already whitelists localhost (the cross-origin
  // session fragment work in commit 51361e4 added that), so no
  // dashboard-side change is needed.

  async function loadJwtBlock(statusFromCaller) {
    const block = $('neowowJwtBlock');
    if (!block) return;
    // We may already have a fresh status from loadNeowowStatus that
    // called us; otherwise refetch.
    let status = statusFromCaller;
    if (!status) {
      try {
        const r = await fetch('/api/neowow/status');
        if (!r.ok) throw new Error('status http ' + r.status);
        status = await r.json();
      } catch (e) {
        block.innerHTML = `<span style="color:#ef4444">加载授权状态失败：${escapeHtml(e.message)}</span>`;
        return;
      }
    }

    if (!status.hasJwt) {
      // State 2: prompt for OAuth.
      block.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap">
          <div style="color:var(--muted);line-height:1.6">
            尚未登录 Neodomain — 登录后这里会显示积分余额，并解锁 Hermes 直接调用图片 / 视频生成。
          </div>
          <button class="btn-tiny" onclick="neowowStartOAuth()" style="background:linear-gradient(135deg,#5e60ce,#7950f2);color:#fff;border:none;flex-shrink:0">
            🔑 登录 Neodomain
          </button>
        </div>
      `;
      return;
    }

    // State 3: have JWT — fetch balance + membership and render.
    block.innerHTML = `<span style="color:var(--muted)">加载积分余额…</span>`;
    try {
      const r = await fetch('/api/neowow/points');
      const d = await r.json();
      if (!r.ok) {
        // 502 + "JWT 已过期" type message — show with re-login prompt
        block.innerHTML = `
          <div style="color:#e8a030;line-height:1.6">⚠️ ${escapeHtml(d.error || ('HTTP '+r.status))}</div>
          <button class="btn-tiny" onclick="neowowStartOAuth()" style="background:linear-gradient(135deg,#5e60ce,#7950f2);color:#fff;border:none;margin-top:6px">
            🔑 重新登录 Neodomain
          </button>
        `;
        return;
      }
      renderBalance(block, d, status);
    } catch (e) {
      block.innerHTML = `<span style="color:#ef4444">${escapeHtml(e.message || 'unknown')}</span>`;
    }
  }

  function renderBalance(el, points, status) {
    const total = points.totalAvailablePoints || 0;
    const m     = points.membershipInfo || {};
    const memBadge = m.levelCode && m.levelCode !== 'FREE'
      ? `<span style="display:inline-block;padding:1px 6px;background:linear-gradient(135deg,#f59e0b,#ef4444);color:#fff;font-size:10px;font-weight:700;border-radius:6px;letter-spacing:0.3px;margin-left:6px">${escapeHtml(m.levelCode)}</span>`
      : '';
    // Per-type breakdown — only show types the user has nonzero of
    const details = (points.pointsDetails || []).filter(p => p.currentPoints > 0);
    const detailRows = details.map(p => {
      const expiry = p.expireTime ? ` <span style="color:var(--muted);font-size:10px">到期 ${escapeHtml(String(p.expireTime).slice(0,10))}</span>` : '';
      return `<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-top:2px">
        <span>${escapeHtml(p.pointsTypeName || ('类型 '+p.pointsType))}${expiry}</span>
        <span style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace">${p.currentPoints.toLocaleString()}</span>
      </div>`;
    }).join('');

    const memInfo = m.levelCode && m.levelCode !== 'FREE'
      ? `<div style="font-size:11px;color:var(--muted);margin-top:6px;line-height:1.5">
           ${escapeHtml(m.levelName || m.levelCode)} 会员
           ${m.membershipTypeDesc ? '· ' + escapeHtml(m.membershipTypeDesc) : ''}
           ${m.expireTime ? '· 到期 <strong>' + escapeHtml(String(m.expireTime).slice(0,10)) + '</strong>' : ''}
           ${m.dailyPointsQuota ? '· 每日赠送 ' + Number(m.dailyPointsQuota).toLocaleString() : ''}
         </div>`
      : '';

    // JWT-mask + logout link
    const masked = status && status.maskedJwt ? `· <code style="font-size:10px;color:var(--muted)">${escapeHtml(status.maskedJwt)}</code>` : '';

    el.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
        <div>
          <div style="font-weight:700;color:#c7d2fe;font-size:18px;font-variant-numeric:tabular-nums">
            💎 ${total.toLocaleString()}
            ${memBadge}
          </div>
          ${memInfo}
        </div>
        <div style="display:flex;flex-direction:column;gap:4px;align-items:flex-end">
          <a href="https://app.neowow.studio/account" target="_blank" rel="noreferrer" class="btn-tiny" style="text-decoration:none">💎 充值</a>
          <button class="btn-tiny" onclick="neowowClearJwt()" style="font-size:10px;padding:2px 8px">退出 Neodomain</button>
        </div>
      </div>
      ${detailRows ? '<div style="margin-top:8px;padding-top:8px;border-top:1px dashed rgba(255,255,255,0.06)">' + detailRows + '</div>' : ''}
      <div style="margin-top:6px;font-size:10px;color:var(--muted)">已登录 Neodomain ${masked}</div>
    `;
  }

  // ── OAuth start (button click) ───────────────────────────────────────
  // Build the dashboard URL with our localhost callback as return.
  // The dashboard's existing /api/oauth/start route handles state +
  // platform redirect + callback — same as Web does today.  After the
  // user completes login, dashboard's /api/oauth/callback redirects
  // them to <return>#neo_session=<base64>; the callback HTML at
  // /api/neowow/oauth-callback persists the JWT + tells the user to
  // close that tab.
  window.neowowStartOAuth = function () {
    const ret = window.location.origin + '/api/neowow/oauth-callback';
    const url = 'https://app.neowow.studio/api/oauth/start?return=' + encodeURIComponent(ret);
    // Open in a new tab so the user's Hermes session is preserved if
    // the OAuth flow gets interrupted.
    window.open(url, '_blank');
  };

  window.neowowClearJwt = async function () {
    if (!confirm('确认退出 Neodomain？\n积分余额信息会消失，但保存的 deploy token 不受影响。')) return;
    try {
      const r = await fetch('/api/neowow/jwt', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ clear: true }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.error || ('HTTP '+r.status));
      }
      // Close the popover + refresh visible state.
      const popover = $('neowowAuthPopover');
      if (popover) popover.style.display = 'none';
      void refreshRailAvatar();
      void loadJwtBlock();
    } catch (e) {
      alert('退出失败：' + (e.message || e));
    }
  };

  // ── Cloud config — read-only status card ─────────────────────────────
  async function loadCloudStatus() {
    const el = $('neowowCloudStatus');
    if (!el) return;
    try {
      const r = await fetch('/api/neowow/cloud-status');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      const last = d.lastSync || {};
      const cached = d.cached || null;

      if (!last.slug && !cached) {
        el.innerHTML = '<span style="color:var(--muted)">还没同步过云端配置。点下面的「同步」按钮拉取激活的配置。</span>';
        return;
      }

      const name = last.name || cached?.name || '?';
      const slug = last.slug || cached?.slug || '';
      const model = last.modelName || cached?.config?.model?.name || '?';
      const at   = last.syncedAt || cached?.synced_at || '';
      const atStr = at ? `· 上次同步 ${formatRelative(at)}` : '';
      el.innerHTML = `
        <div style="color:var(--text)">已同步：<strong>${escapeHtml(name)}</strong> <span style="color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px">/${escapeHtml(slug)}</span></div>
        <div style="color:var(--muted);margin-top:2px">🤖 ${escapeHtml(model)} ${atStr}</div>
      `;
    } catch (e) {
      el.textContent = `加载失败：${e.message}`;
      el.style.color = '#ef4444';
    }
  }

  function currentWorkspace() {
    // panels.js stores the active workspace on a few globals depending on
    // where the user was last. Try them in order; fall back to the
    // last-workspace API which is always populated server-side.
    if (typeof window._lastWorkspace === 'string' && window._lastWorkspace) {
      return window._lastWorkspace;
    }
    const lbl = document.getElementById('terminalWorkspaceLabel') ||
                document.getElementById('terminalDockWorkspaceLabel');
    if (lbl && lbl.textContent && lbl.textContent.trim()) {
      return lbl.textContent.trim();
    }
    return '';
  }

  // ── Token actions ────────────────────────────────────────────────────
  window.neowowSaveToken = async function () {
    const inputEl = $('neowowTokenInput');
    const statusEl = $('neowowTokenStatus');
    const token = (inputEl?.value || '').trim();
    if (!token) {
      statusEl.textContent = 'Token 不能为空';
      statusEl.style.color = '#ef4444';
      return;
    }
    try {
      const r = await fetch('/api/neowow/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      await loadNeowowStatus();
    } catch (e) {
      statusEl.textContent = `保存失败：${e.message}`;
      statusEl.style.color = '#ef4444';
    }
  };

  window.neowowClearToken = async function () {
    if (!confirm('清除已保存的部署 token？\n你之前用此 token 部署的应用不受影响。')) return;
    try {
      const r = await fetch('/api/neowow/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clear: true }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.error || `HTTP ${r.status}`);
      }
      await loadNeowowStatus();
    } catch (e) {
      const statusEl = $('neowowTokenStatus');
      if (statusEl) {
        statusEl.textContent = `清除失败：${e.message}`;
        statusEl.style.color = '#ef4444';
      }
    }
  };

  // ── Deploy ───────────────────────────────────────────────────────────
  window.neowowDeploy = async function () {
    const nameEl  = $('neowowDeployName');
    const wsEl    = $('neowowDeployWorkspace');
    const btn     = $('neowowDeployBtn');
    const statusEl = $('neowowDeployStatus');

    const workerName = (nameEl?.value || '').trim().toLowerCase();
    const workspace  = (wsEl?.value || '').trim();

    if (!workerName) {
      showStatus(statusEl, '请填写应用名（小写字母、数字、连字符）', 'error');
      return;
    }
    if (!workspace) {
      showStatus(statusEl, '请填写 workspace 路径', 'error');
      return;
    }

    btn.disabled = true;
    const origText = btn.textContent;
    btn.textContent = '部署中…';
    showStatus(statusEl, '正在打包 workspace 并推送到 neowow.studio…', 'progress');

    try {
      const r = await fetch('/api/neowow/deploy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workerName, workspace }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      const url = d.url || `https://${workerName}.neowow.studio`;
      showStatus(
        statusEl,
        `✅ 已发布上线！<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer" style="color:var(--accent);word-break:break-all">${escapeHtml(url)}</a>`,
        'ok',
      );
      // Refresh the "Last deploy" block.
      loadNeowowStatus();
    } catch (e) {
      showStatus(statusEl, `❌ ${e.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = origText;
    }
  };

  // ── Tiny helpers ─────────────────────────────────────────────────────
  function showStatus(el, html, kind) {
    if (!el) return;
    el.innerHTML = html;
    el.style.color = kind === 'error' ? '#ef4444'
                   : kind === 'ok'    ? '#22c55e'
                   :                    'var(--muted)';
  }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }

  // ── Cloud config — sync button ───────────────────────────────────────
  // Pulls the active cloud config and writes ~/.hermes/config.yaml.
  // Shows a clear "applied / skipped" report so users can see exactly
  // what changed locally vs. what's still in the cloud blob for visibility
  // only (tools / mcp / skills aren't auto-applied yet — by design).
  window.neowowCloudSync = async function () {
    const btn  = $('neowowCloudSyncBtn');
    const out  = $('neowowCloudResult');
    if (!btn || !out) return;
    const wasLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = '同步中…';
    out.innerHTML = '';
    try {
      const r = await fetch('/api/neowow/cloud-apply', { method: 'POST' });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);

      if (d.applied === false && d.reason === 'no_active') {
        out.innerHTML = `<span style="color:#e8a030">⚠️ ${escapeHtml(d.message || '云端没有激活的配置')}</span>` +
          (d.url ? ` <a href="${escapeHtml(d.url)}" target="_blank" rel="noreferrer" style="color:var(--accent)">前往设置 →</a>` : '');
        return;
      }

      const applied = (d.appliedFields || []).map(s => `<code>${escapeHtml(s)}</code>`).join('、') || '（无）';
      const skipped = (d.skippedFields || []).length
        ? `<div style="color:var(--muted);margin-top:4px">未写入：${d.skippedFields.map(s => escapeHtml(s)).join('；')}</div>` : '';
      out.innerHTML = `
        <div style="color:var(--accent)">✓ 已同步「${escapeHtml(d.name)}」 — 模型 <code>${escapeHtml(d.modelName || '?')}</code></div>
        <div style="color:var(--muted);margin-top:4px">已写入 <code>~/.hermes/config.yaml</code> 的字段：${applied}</div>
        ${skipped}
      `;
      // Refresh the status card so the timestamp updates.
      void loadCloudStatus();
    } catch (e) {
      out.innerHTML = `<span style="color:#ef4444">❌ 同步失败：${escapeHtml(e.message)}</span>`;
    } finally {
      btn.disabled = false;
      btn.textContent = wasLabel;
    }
  };

  // ── Cloud config — read-only list expansion ──────────────────────────
  // First click pulls + renders the list. Second click hides it.
  // Switching active is intentionally NOT exposed here: the dashboard's
  // PATCH /active endpoint is JWT-only, so users do that via web UI.
  window.neowowCloudList = async function () {
    const btn = $('neowowCloudListBtn');
    const box = $('neowowCloudListBox');
    if (!btn || !box) return;
    if (box.style.display === 'block') {
      box.style.display = 'none';
      btn.textContent = '📋 查看所有云端配置';
      return;
    }
    const wasLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = '加载中…';
    try {
      const r = await fetch('/api/neowow/cloud-configs');
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      const items = d.configs || [];
      if (!items.length) {
        box.innerHTML = '<div style="color:var(--muted);font-size:12px">云端还没有任何配置。<a href="https://app.neowow.studio/account/hermes-configs" target="_blank" rel="noreferrer" style="color:var(--accent)">前往创建 →</a></div>';
      } else {
        box.innerHTML =
          '<div style="font-size:11px;color:var(--muted);margin-bottom:8px">点 dashboard 上的 ⭐ 把某个设为「激活中」，然后回到这里点上方的「同步」按钮。</div>' +
          items.map(c => `
            <div style="padding:8px 10px;margin-bottom:6px;background:var(--bg);border:1px solid var(--border2);border-radius:6px">
              <div style="font-weight:600;font-size:13px;color:var(--text)">${escapeHtml(c.name || c.slug)}</div>
              <div style="font-size:11px;color:var(--muted);margin-top:2px">
                <span style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace">/${escapeHtml(c.slug)}</span>
                · 🤖 ${escapeHtml(c.modelName || '默认')}
                · ⚡ ${c.skillCount || 0}
                ${c.updatedAt ? `· 🕒 ${escapeHtml(formatRelative(c.updatedAt))}` : ''}
              </div>
              ${c.description ? `<div style="font-size:12px;color:var(--muted);margin-top:4px;line-height:1.5">${escapeHtml(c.description)}</div>` : ''}
            </div>
          `).join('');
      }
      box.style.display = 'block';
      btn.textContent = '📋 收起列表';
    } catch (e) {
      box.style.display = 'block';
      box.innerHTML = `<div style="color:#ef4444;font-size:12px">加载失败：${escapeHtml(e.message)}</div>`;
      btn.textContent = wasLabel;
    } finally {
      btn.disabled = false;
    }
  };

  // ─── Skills sync ────────────────────────────────────────────────────────
  //
  // Pulls the user's market subscriptions from the dashboard into
  // ~/.hermes/skills/_neowow/. Three buttons:
  //   • neowowSkillsSync — POST /api/neowow/skills/sync, shows
  //     {added, updated, removed, unchanged} summary
  //   • neowowSkillsCloudList — GET /api/neowow/skills/cloud-list,
  //     read-only preview of cloud subscriptions
  //   • neowowSkillsLocalList — GET /api/neowow/skills/local-status,
  //     shows what's currently on disk under _neowow/
  //
  // Also called on panel-open by loadNeowowStatus() to render the
  // status card without a network round-trip.

  async function loadSkillsStatus() {
    const el = $('neowowSkillsStatus');
    if (!el) return;
    try {
      const r = await fetch('/api/neowow/skills/local-status');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      const items = d.localSkills || [];
      if (!items.length) {
        el.innerHTML = '<span style="color:var(--muted)">本地还没同步过任何技能。点下面的「同步订阅的技能」按钮拉取。</span>';
        return;
      }
      // Newest synced first; show top 3 with timestamp.
      const sorted = items.slice().sort((a, b) => (b.syncedAt || '').localeCompare(a.syncedAt || ''));
      const top = sorted.slice(0, 3).map(s => {
        const at = s.syncedAt ? formatRelative(s.syncedAt) : '';
        return `<div>• <strong>${escapeHtml(s.name)}</strong> <span style="color:var(--muted);font-size:11px">v${s.version || 0} ${at ? '· '+at : ''}</span></div>`;
      }).join('');
      const more = items.length > 3 ? `<div style="color:var(--muted);font-size:11px;margin-top:2px">…还有 ${items.length - 3} 个</div>` : '';
      el.innerHTML = `
        <div style="color:var(--text)">已同步 <strong>${items.length}</strong> 个技能 · <code style="font-size:11px;color:var(--muted)">${escapeHtml(d.rootPath || '~/.hermes/skills/_neowow')}</code></div>
        <div style="margin-top:4px">${top}${more}</div>
      `;
    } catch (e) {
      el.textContent = `加载失败：${e.message}`;
      el.style.color = '#ef4444';
    }
  }

  window.neowowSkillsSync = async function () {
    const btn = $('neowowSkillsSyncBtn');
    const out = $('neowowSkillsResult');
    if (!btn || !out) return;
    const wasLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = '同步中…';
    out.innerHTML = '';
    try {
      const r = await fetch('/api/neowow/skills/sync', { method: 'POST' });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      const a = (d.added    || []).length;
      const u = (d.updated  || []).length;
      const re = (d.removed || []).length;
      const un = d.unchanged || 0;
      // Build a clean summary — list names of added/updated/removed if
      // small enough, else just count.
      const parts = [];
      parts.push(`<div style="color:var(--accent)">✓ 同步完成 · 新增 ${a} · 更新 ${u} · 删除 ${re} · 不变 ${un}</div>`);
      if (a) parts.push(`<div style="color:var(--muted);margin-top:4px">新增: ${(d.added||[]).map(x => '<strong>'+escapeHtml(x.name)+'</strong>').join('、')}</div>`);
      if (u) parts.push(`<div style="color:var(--muted);margin-top:4px">更新: ${(d.updated||[]).map(x => '<strong>'+escapeHtml(x.name)+'</strong> v'+x.fromVersion+' → v'+x.toVersion).join('、')}</div>`);
      if (re) parts.push(`<div style="color:var(--muted);margin-top:4px">删除: ${(d.removed||[]).map(x => escapeHtml(x.name)).join('、')}</div>`);
      out.innerHTML = parts.join('');
      // Refresh the status card to show the new state.
      void loadSkillsStatus();
    } catch (e) {
      out.innerHTML = `<span style="color:#ef4444">❌ 同步失败：${escapeHtml(e.message)}</span>`;
    } finally {
      btn.disabled = false;
      btn.textContent = wasLabel;
    }
  };

  window.neowowSkillsCloudList = async function () {
    const btn = $('neowowSkillsCloudBtn');
    const box = $('neowowSkillsListBox');
    if (!btn || !box) return;
    if (box.style.display === 'block' && box.dataset.mode === 'cloud') {
      box.style.display = 'none';
      btn.textContent = '☁ 查看云端订阅';
      return;
    }
    const wasLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = '加载中…';
    try {
      const r = await fetch('/api/neowow/skills/cloud-list');
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      const items = d.skills || [];
      if (!items.length) {
        box.innerHTML = '<div style="color:var(--muted)">你还没订阅任何技能。<a href="https://app.neowow.studio/market?tab=skill" target="_blank" rel="noreferrer" style="color:var(--accent)">前往技能市场 →</a></div>';
      } else {
        box.innerHTML =
          '<div style="font-size:11px;color:var(--muted);margin-bottom:8px">这是你在 dashboard 上订阅的技能列表（云端 SSOT）。本地还没拉的会通过「🔄 同步」补齐。</div>' +
          items.map(s => `
            <div style="padding:6px 8px;margin-bottom:4px;background:var(--bg);border:1px solid var(--border2);border-radius:6px">
              <div><strong>${escapeHtml(s.name || s.id)}</strong> <span style="color:var(--muted);font-size:11px">v${s.version || 1}</span></div>
              <div style="font-size:11px;color:var(--muted);margin-top:2px">
                <span style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace">${escapeHtml(s.id)}</span>
                ${s.displayName ? '· 作者 '+escapeHtml(s.displayName) : ''}
              </div>
              ${s.description ? `<div style="margin-top:4px;font-size:12px;color:var(--muted)">${escapeHtml(s.description)}</div>` : ''}
            </div>
          `).join('');
      }
      box.dataset.mode = 'cloud';
      box.style.display = 'block';
      btn.textContent = '☁ 收起';
    } catch (e) {
      box.style.display = 'block';
      box.innerHTML = `<div style="color:#ef4444">加载失败：${escapeHtml(e.message)}</div>`;
      btn.textContent = wasLabel;
    } finally {
      btn.disabled = false;
    }
  };

  window.neowowSkillsLocalList = async function () {
    const btn = $('neowowSkillsLocalBtn');
    const box = $('neowowSkillsListBox');
    if (!btn || !box) return;
    if (box.style.display === 'block' && box.dataset.mode === 'local') {
      box.style.display = 'none';
      btn.textContent = '📂 查看本地';
      return;
    }
    const wasLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = '加载中…';
    try {
      const r = await fetch('/api/neowow/skills/local-status');
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      const items = d.localSkills || [];
      if (!items.length) {
        box.innerHTML = `<div style="color:var(--muted)">本地 <code>${escapeHtml(d.rootPath || '~/.hermes/skills/_neowow')}</code> 是空的。</div>`;
      } else {
        box.innerHTML =
          `<div style="font-size:11px;color:var(--muted);margin-bottom:8px">本地路径: <code>${escapeHtml(d.rootPath || '')}</code> — Hermes-agent 启动时自动加载。</div>` +
          items.map(s => `
            <div style="padding:6px 8px;margin-bottom:4px;background:var(--bg);border:1px solid var(--border2);border-radius:6px">
              <div><strong>${escapeHtml(s.name || s.id)}</strong>${s.stale ? ' <span style="color:#e8a030;font-size:11px">⚠ 无元数据</span>' : ''}</div>
              <div style="font-size:11px;color:var(--muted);margin-top:2px">
                <span style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace">${escapeHtml(s.id)}</span>
                · v${s.version || 0}
                ${s.syncedAt ? '· '+formatRelative(s.syncedAt) : ''}
              </div>
              ${s.description ? `<div style="margin-top:4px;font-size:12px;color:var(--muted)">${escapeHtml(s.description)}</div>` : ''}
            </div>
          `).join('');
      }
      box.dataset.mode = 'local';
      box.style.display = 'block';
      btn.textContent = '📂 收起';
    } catch (e) {
      box.style.display = 'block';
      box.innerHTML = `<div style="color:#ef4444">加载失败：${escapeHtml(e.message)}</div>`;
      btn.textContent = wasLabel;
    } finally {
      btn.disabled = false;
    }
  };

  // ── Helpers ──────────────────────────────────────────────────────────
  function formatRelative(isoStr) {
    const t = Date.parse(isoStr);
    if (isNaN(t)) return isoStr;
    const sec = Math.round((Date.now() - t) / 1000);
    if (sec < 0) return '刚刚';
    if (sec < 60) return sec + ' 秒前';
    const min = Math.round(sec / 60);
    if (min < 60) return min + ' 分钟前';
    const hr = Math.round(min / 60);
    if (hr < 24)  return hr + ' 小时前';
    const day = Math.round(hr / 24);
    if (day < 30) return day + ' 天前';
    return new Date(t).toLocaleDateString();
  }

  // Light-weight: Enter key on the token field saves immediately.
  document.addEventListener('DOMContentLoaded', () => {
    const inputEl = $('neowowTokenInput');
    if (inputEl) {
      inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); window.neowowSaveToken(); }
      });
    }
  });
})();
