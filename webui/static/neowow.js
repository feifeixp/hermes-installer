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
    const svg     = $('neowowAvatarSvg');
    const btn     = $('neowowAvatarRail');
    if (!disc || !initial || !svg || !btn) return;

    let status = null;
    try {
      const r = await fetch('/api/neowow/status', { cache: 'no-store' });
      if (r.ok) status = await r.json();
    } catch (_) { /* offline — keep current state */ }

    if (!status || !status.hasJwt) {
      // Logged-out: show person SVG (inherits stroke=currentColor from
      // rail-btn so it matches every other rail icon's theme color),
      // hide the colored disc.
      svg.style.display    = '';
      disc.style.display   = 'none';
      btn.title            = '点击登录 Neodomain';
      btn.dataset.hasJwt   = '';
      btn.dataset.nickname = '';
      // Also reset sidebar avatar (Marvis skin)
      const svg2b  = $('neowowAvatarSvg2');
      const disc2b = $('neowowAvatarDisc2');
      const btn2b  = $('neowowAvatarSidebar');
      if (btn2b) {
        if (svg2b)  svg2b.style.display  = '';
        if (disc2b) disc2b.style.display = 'none';
        btn2b.title          = '点击登录 Neodomain';
        btn2b.dataset.hasJwt = '';
        btn2b.dataset.nickname = '';
      }
      return;
    }

    // Logged-in: pull nickname for the disc letter.  Best-effort: '✓'
    // if /api/neowow/whoami isn't available (offline / token issue).
    let nickname = '';
    try {
      const r = await fetch('/api/neowow/whoami', { cache: 'no-store' });
      if (r.ok) {
        const j = await r.json();
        nickname = (j && (j.nickname || j.contact || j.email)) || '';
      }
    } catch (_) { /* keep nickname empty */ }

    const ch = (nickname && nickname[0]) || '✓';
    svg.style.display    = 'none';
    disc.style.display   = 'inline-flex';
    initial.textContent  = ch.toUpperCase();
    btn.title            = nickname ? `已登录 · ${nickname}（点击查看积分）` : '已登录（点击查看积分）';
    btn.dataset.hasJwt   = '1';
    btn.dataset.nickname = nickname || '';

    // Mirror visual state to the Marvis sidebar avatar button.
    const svg2     = $('neowowAvatarSvg2');
    const disc2    = $('neowowAvatarDisc2');
    const initial2 = $('neowowAvatarInitial2');
    const btn2     = $('neowowAvatarSidebar');
    if (btn2) {
      if (svg2)     { svg2.style.display  = svg.style.display; }
      if (disc2)    { disc2.style.display = disc.style.display; }
      if (initial2) { initial2.textContent = ch.toUpperCase(); }
      btn2.title            = btn.title;
      btn2.dataset.hasJwt   = '1';
      btn2.dataset.nickname = nickname || '';
    }
  }

  // ── Boot overlay control ──────────────────────────────────────────────
  //
  // The overlay element is injected directly in index.html (right after
  // <body>) so it covers content the moment the page paints — before this
  // script even loads. We're responsible for HIDING it once we know what
  // state the user is in.
  //
  // States we resolve:
  //   • Logged-in (cookie JWT validates, /api/neowow/status hasJwt=true)
  //     → swap spinner → green checkmark + "登录成功" → fade out
  //   • Logged-out / no cookie
  //     → quickly fade out (no success animation — user sees the rail
  //       avatar in its "click to login" state, which IS the expected UX
  //       on desktop Hermes / fresh cloud session)
  //   • Network error / API unreachable
  //     → fade out anyway (don't trap user); console.warn for debugging
  //
  // Sequencing relative to refreshRailAvatar(): we run BOTH calls in
  // parallel. The boot overlay shows for at least 600ms even on
  // logged-in users so the success animation reads as deliberate (not
  // a "flash and gone"). The avatar refresh happens in the background
  // so it's ready when the overlay clears.
  let _bootOverlayHidden = false;
  async function neowowResolveBootOverlay() {
    if (_bootOverlayHidden) return;
    const overlay = document.getElementById('neowowBootOverlay');
    if (!overlay) return; // already cleaned up

    // Run status check + minimum-display-time in parallel.
    const minDisplayMs = 600;
    const t0 = Date.now();

    let hasJwt = false;
    let nickname = '';
    let networkOk = true;
    try {
      const r = await fetch('/api/neowow/status', { cache: 'no-store' });
      if (r.ok) {
        const j = await r.json();
        hasJwt = !!(j && j.hasJwt);
      }
    } catch (_) {
      networkOk = false;
    }

    // If logged-in, try fetching nickname for the success message.
    if (hasJwt) {
      try {
        const r = await fetch('/api/neowow/whoami', { cache: 'no-store' });
        if (r.ok) {
          const j = await r.json();
          nickname = (j && (j.nickname || j.contact || j.email)) || '';
        }
      } catch (_) { /* fall through with empty nickname */ }
    }

    // Pad to minimum display time
    const elapsed = Date.now() - t0;
    if (elapsed < minDisplayMs) {
      await new Promise(r => setTimeout(r, minDisplayMs - elapsed));
    }

    neowowHideBootOverlay({ success: hasJwt, networkOk, nickname });
  }

  /**
   * Hide the boot overlay. Exposed as window.neowowHideBootOverlay so
   * the inline timeout-fallback script in index.html can also call it.
   *
   * @param {Object} opts
   * @param {boolean} opts.success  — true → green checkmark animation,
   *                                  false → straight fade-out
   * @param {boolean} opts.networkOk — false → log a console.warn
   * @param {string}  opts.nickname  — used in success title if present
   * @param {string}  opts.reason   — used by the safety-timeout caller
   */
  window.neowowHideBootOverlay = function (opts) {
    if (_bootOverlayHidden) return;
    _bootOverlayHidden = true;
    opts = opts || {};
    const overlay = document.getElementById('neowowBootOverlay');
    if (!overlay) {
      document.body.classList.remove('neo-boot-pending');
      return;
    }
    const spinner = document.getElementById('neowowBootSpinner');
    const success = document.getElementById('neowowBootSuccess');
    const title   = document.getElementById('neowowBootTitle');
    const hint    = document.getElementById('neowowBootHint');

    if (opts.reason === 'timeout') {
      console.warn('[neowow-boot] safety timeout fired — overlay hidden without status resolution');
    }
    if (!opts.networkOk) {
      console.warn('[neowow-boot] /api/neowow/status unreachable; proceeding without login confirmation');
    }

    if (opts.success) {
      // Swap spinner → success animation, then fade
      if (spinner) spinner.style.display = 'none';
      if (success) success.style.display = 'flex';
      if (title) {
        title.textContent = opts.nickname
          ? `欢迎回来，${opts.nickname}`
          : '登录成功';
      }
      if (hint) hint.textContent = '即将进入对话…';
      // Linger for the checkmark animation, then fade
      setTimeout(function () {
        overlay.style.opacity = '0';
        setTimeout(function () {
          overlay.remove();
          document.body.classList.remove('neo-boot-pending');
        }, 460);
      }, 700);
    } else {
      // No success animation — straight fade-out
      overlay.style.opacity = '0';
      setTimeout(function () {
        overlay.remove();
        document.body.classList.remove('neo-boot-pending');
      }, 460);
    }
  };

  window.neowowAvatarClick = async function (event) {
    if (event && event.preventDefault) event.preventDefault();
    if (event && event.stopPropagation) event.stopPropagation();
    const popover = $('neowowAuthPopover');
    const body    = $('neowowAuthPopBody');
    // The rail button is always the source of truth for auth state (kept
    // current by refreshRailAvatar).  For POSITIONING we use whichever
    // button was actually clicked — rail in normal skins, sidebar btn in
    // Marvis where the rail is visually hidden.
    const railBtn     = $('neowowAvatarRail');
    const clickedBtn  = (event && event.currentTarget instanceof Element
                         && event.currentTarget.id !== 'neowowAvatarRail')
                        ? event.currentTarget : railBtn;
    const btn = railBtn; // alias kept for downstream code that reads .dataset
    if (!popover || !body || !railBtn) return;

    // Logged-in state is stamped on the BUTTON's dataset by
    // refreshRailAvatar (the disc is display:none when logged out, so
    // its dataset isn't reliable).  Read from the rail button instead.
    const hasJwt = btn.dataset.hasJwt === '1';
    const nickname = btn.dataset.nickname || '';

    // Logged out → straight into OAuth.  No popover.
    if (!hasJwt) {
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
    //
    // Vertical anchor: the avatar sits at the BOTTOM of the rail, so
    // anchoring from `top` (popover.style.top) made the popover grow
    // downward off-screen as content (membership + balance breakdown +
    // buttons) accumulated. We pin from `bottom` instead — the popover's
    // bottom edge sits near the button's bottom edge, with a min margin
    // from the viewport bottom — and the popover grows UPWARD.
    //
    // Math:
    //   `popover.style.bottom = X` puts the popover's bottom X px from
    //   viewport bottom. We want X = window.innerHeight - rect.bottom
    //   (popover bottom = button bottom). If the button is so close to
    //   the viewport bottom that this would leave < `margin` px of
    //   breathing room, clamp to `margin`.
    // Use whichever button was actually clicked for positioning (handles
    // the Marvis skin where the sidebar button is shown instead of the rail).
    const rect   = clickedBtn.getBoundingClientRect();
    const margin = 12;
    popover.style.left   = (rect.right + 8) + 'px';
    popover.style.top    = 'auto';
    popover.style.bottom = Math.max(margin, window.innerHeight - rect.bottom) + 'px';
    body.innerHTML = '<div style="color:var(--muted)">加载积分余额…</div>';
    popover.style.display = 'block';

    try {
      // Fetch points + coding-plan in parallel. The Coding Plan call
      // (Phase β /api/neowow/coding-plan) is best-effort — if it errors
      // we still render the points panel, just without the plan banner.
      const [pointsRes, planRes] = await Promise.allSettled([
        fetch('/api/neowow/points',       { cache: 'no-store' }).then(r => r.json().then(d => ({ ok: r.ok, status: r.status, d }))),
        fetch('/api/neowow/coding-plan',  { cache: 'no-store' }).then(r => r.json().then(d => ({ ok: r.ok, status: r.status, d }))),
      ]);
      const p = pointsRes.status === 'fulfilled' ? pointsRes.value : null;
      const cp = planRes.status === 'fulfilled' && planRes.value.ok ? planRes.value.d : null;

      // Phase β.13: structured revocation signal. EITHER endpoint may
      // detect a revoked / expired JWT (the Python helper auto-cleared
      // it) and respond with `requireRelogin: true`. We treat both
      // identically — render a focused "登录已失效，重新登录" card
      // instead of the generic ⚠️ blob the user might miss.
      const pointsRevoked = p && !p.ok && p.d && p.d.requireRelogin === true;
      const planRevoked   = planRes.status === 'fulfilled'
                          && !planRes.value.ok
                          && planRes.value.d
                          && planRes.value.d.requireRelogin === true;
      if (pointsRevoked || planRevoked) {
        const msg = (p?.d?.error || planRes.value?.d?.error
                  || '登录凭据已失效，已自动清除本地状态。请点下方按钮重新登录。');
        body.innerHTML = `
          <div style="padding:14px 0;text-align:center">
            <div style="font-size:24px;margin-bottom:8px">🔒</div>
            <div style="font-weight:600;font-size:14px;margin-bottom:4px">登录已失效</div>
            <div style="color:var(--muted,#94a3b8);font-size:12px;line-height:1.6;margin-bottom:14px">${escapeHtml(msg)}</div>
            <button class="btn-tiny" onclick="neowowStartOAuth()" style="background:linear-gradient(135deg,#5e60ce,#7950f2);color:#fff;border:none;width:100%;padding:8px 12px;font-weight:600">🔑 重新登录 Neowow</button>
          </div>
        `;
        return;
      }

      if (!p || !p.ok) {
        const d = p?.d || {};
        body.innerHTML = `
          <div style="color:#e8a030;line-height:1.6">⚠️ ${escapeHtml(d.error || ('HTTP '+(p?.status ?? '???')))}</div>
          <div style="display:flex;gap:6px;margin-top:8px">
            <button class="btn-tiny" onclick="neowowStartOAuth()" style="background:linear-gradient(135deg,#5e60ce,#7950f2);color:#fff;border:none;flex:1">🔑 重新登录</button>
            <button class="btn-tiny" onclick="neowowClearJwt()" style="flex:1">退出</button>
          </div>
        `;
        return;
      }
      renderPopoverBody(body, p.d, nickname, cp);
    } catch (e) {
      body.innerHTML = `<div style="color:#ef4444">加载失败：${escapeHtml(e.message || 'unknown')}</div>`;
    }
  };

  function renderPopoverBody(el, points, nickname, codingPlan) {
    const total = points.totalAvailablePoints || 0;
    const m = points.membershipInfo || {};
    const initial = (nickname && nickname[0] || '?').toUpperCase();
    // Phase β: Coding Plan banner (above points, since it's what gates
    // chat). Omitted entirely when the user isn't subscribed yet AND
    // hasn't been issued a trial row — keeps the popover tidy.
    let codingPlanBlock = '';
    if (codingPlan && (codingPlan.planId || codingPlan.creditsLimit)) {
      const pct = codingPlan.creditsLimit > 0
        ? Math.min(100, Math.max(0, (codingPlan.creditsUsed / codingPlan.creditsLimit) * 100))
        : 0;
      const remaining = (codingPlan.creditsRemaining ?? 0).toFixed(
        codingPlan.creditsRemaining >= 100 ? 0 : 1,
      );
      const limit = (codingPlan.creditsLimit ?? 0).toLocaleString();
      const planChipBg = codingPlan.planId === 'max'   ? 'linear-gradient(135deg,#f59e0b,#ef4444)'
                       : codingPlan.planId === 'pro'   ? 'linear-gradient(135deg,#8b5cf6,#7c3aed)'
                       : codingPlan.planId === 'basic' ? 'linear-gradient(135deg,#3b82f6,#2563eb)'
                       :                                 'linear-gradient(135deg,#94a3b8,#64748b)';
      // Bar color tracks utilization — gives a visual cue to upgrade
      // before the next call hits limit-mode.
      const barColor = pct >= 100 ? 'linear-gradient(90deg,#ef4444,#dc2626)'
                     : pct >= 80  ? 'linear-gradient(90deg,#f59e0b,#d97706)'
                     :              'linear-gradient(90deg,#10b981,#059669)';
      const rateLimitedFlag = codingPlan.rateLimited
        ? `<span style="margin-left:auto;padding:1px 6px;background:rgba(239,68,68,0.18);color:#fca5a5;font-size:10px;font-weight:600;border-radius:4px">限流中</span>`
        : '';
      codingPlanBlock = `
        <div style="padding:8px 0 10px;border-bottom:1px solid rgba(255,255,255,0.06)">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
            <span style="padding:1px 8px;background:${planChipBg};color:#fff;font-size:10px;font-weight:700;border-radius:5px;letter-spacing:0.3px">${escapeHtml(codingPlan.planName || codingPlan.planId || 'Trial')} Plan</span>
            <span style="font-size:11px;color:var(--muted,#94a3b8)">Coding 套餐</span>
            ${rateLimitedFlag}
          </div>
          <div style="display:flex;align-items:baseline;gap:6px;font-variant-numeric:tabular-nums">
            <span style="font-size:18px;font-weight:700;color:#c7d2fe">${remaining}</span>
            <span style="font-size:11px;color:var(--muted,#94a3b8)">/ ${limit} credits 剩余</span>
          </div>
          <div style="height:5px;border-radius:3px;background:rgba(255,255,255,0.08);overflow:hidden;margin-top:4px">
            <div style="width:${pct}%;height:100%;background:${barColor};transition:width 0.4s ease"></div>
          </div>
        </div>
      `;
    }
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
      ${codingPlanBlock}
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
    const popover   = $('neowowAuthPopover');
    const btn       = $('neowowAvatarRail');
    const sidebarBtn = $('neowowAvatarSidebar');
    if (!popover || popover.style.display !== 'block') return;
    if (popover.contains(e.target)) return;
    if (btn && btn.contains(e.target)) return;
    if (sidebarBtn && sidebarBtn.contains(e.target)) return;
    popover.style.display = 'none';
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const popover = $('neowowAuthPopover');
    if (popover && popover.style.display === 'block') popover.style.display = 'none';
  });

  // Refresh on session change + first paint.
  // Boot overlay sequencing: neowowResolveBootOverlay() runs alongside
  // the avatar/account refresh — it does its own /api/neowow/status call
  // (independent of refreshRailAvatar's) and enforces a minimum display
  // time so the success animation actually reads. The two avatar/status
  // calls hitting the same endpoint twice is wasteful but trivially
  // cheap (~5ms server-side) and keeps the overlay's lifecycle
  // self-contained.
  document.addEventListener('DOMContentLoaded', () => {
    void refreshRailAvatar();
    void refreshAccountBlock();
    void neowowResolveBootOverlay();
  });
  window.addEventListener('neoSessionUpdated', () => {
    void refreshRailAvatar();
    void refreshAccountBlock();
    // NOTE: don't re-trigger the boot overlay on session updates —
    // those happen mid-session (login from popover, JWT refresh, etc.)
    // and the user is already past boot at that point.
  });
  // First-render fallback if DOMContentLoaded already fired before this
  // script registered its listener (script lives inside an IIFE that
  // runs on parse).
  if (document.readyState !== 'loading') {
    void refreshRailAvatar();
    void refreshAccountBlock();
    void neowowResolveBootOverlay();
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
      const r = await fetch('/api/neowow/status', { cache: 'no-store' });
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
      const r = await fetch('/api/neowow/points', { cache: 'no-store' });
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
      const r = await fetch('/api/neowow/whoami', { cache: 'no-store' });
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

  // ── Token scope badges ────────────────────────────────────────────────
  //
  // Call /api/neowow/whoami (which proxies dashboard /api/me/whoami with
  // the saved deploy token) and render the granted scopes as compact
  // pills next to the masked token. Mirrors what the dashboard's
  // /account/deploy-tokens page shows so the user sees the same answer
  // in both places.
  //
  // Failures are silent — scope display is a nice-to-have and we
  // shouldn't paint over the actual token-status line if whoami is
  // unreachable (offline, expired, etc.).

  /** Map a scope id to a short display label. Mirrors SCOPE_LABELS in
   *  dashboard's lib/scopes.ts; trimmed for the popover-tight space. */
  const SCOPE_LABELS = {
    'deploy':           '部署',
    'skills:read':      '查看技能',
    'skills:subscribe': '订阅技能',
    'skills:publish':   '发布技能',
    'configs:read':     '读配置',
    'configs:write':    '改配置',
  };

  async function renderTokenScopeBadges() {
    const slot = document.getElementById('neowowTokenScopes');
    if (!slot) return;
    try {
      const r = await fetch('/api/neowow/whoami', { cache: 'no-store' });
      if (!r.ok) return;       // status line already rendered; just skip badges
      const d = await r.json();
      const scopes = Array.isArray(d.scopes) ? d.scopes : [];
      if (scopes.length === 0) {
        slot.innerHTML = '<span style="font-size:10px;padding:1px 6px;border-radius:999px;background:rgba(255,255,255,0.04);color:var(--muted);border:1px dashed rgba(255,255,255,0.12)">无权限（已禁用）</span>';
        return;
      }
      if (scopes.includes('*')) {
        slot.innerHTML = '<span title="此 token 创建于权限分级之前；可在 dashboard 编辑收紧" style="font-size:10px;padding:1px 7px;border-radius:999px;background:rgba(245,158,11,0.12);color:#f59e0b;border:1px solid rgba(245,158,11,0.3)">完全访问（旧）</span>';
        return;
      }
      slot.innerHTML = scopes
        .map(s => `<span style="font-size:10px;padding:1px 7px;border-radius:999px;background:rgba(124,58,237,0.12);color:#a78bfa;border:1px solid rgba(124,58,237,0.25)">${escapeHtml(SCOPE_LABELS[s] || s)}</span>`)
        .join('');
    } catch {
      // Silent — badges are decorative.
    }
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
      const r = await fetch('/api/neowow/status', { cache: 'no-store' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      if (d.hasToken) {
        statusEl.innerHTML = `已保存 token：<code>${escapeHtml(d.maskedToken)}</code><span id="neowowTokenScopes" style="margin-left:8px;display:inline-flex;flex-wrap:wrap;gap:4px;vertical-align:middle"></span>`;
        statusEl.style.color = 'var(--accent)';
        if (inputEl) inputEl.value = '';
        if (inputEl) inputEl.placeholder = '粘贴新 token 可覆盖';
        if (clearBtn) clearBtn.style.display = '';
        // Fire scope fetch in parallel — purely cosmetic, OK if it fails.
        // We pass through the dashboard's whoami via /api/neowow/whoami,
        // which authenticates with the saved deploy token and returns
        // the scope set we stamped at mint time.
        void renderTokenScopeBadges();
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
      const r = await fetch('/api/neowow/whoami', { cache: 'no-store' });
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
        const r = await fetch('/api/neowow/status', { cache: 'no-store' });
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
      const r = await fetch('/api/neowow/points', { cache: 'no-store' });
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
  //
  // Hermes WebUI runs inside a pywebview window — `window.open()` is
  // silently blocked there.  Call out to the Python backend, which
  // uses webbrowser.open() to spawn the OS's default browser (same
  // module the installer's first-run flow uses, so it's known good
  // on every supported platform).
  //
  // After the launcher opens the browser, we start POLLING for the
  // JWT.  When the user finishes OAuth on the platform, the dashboard
  // callback redirects them to localhost:<port>/api/neowow/oauth-
  // callback, which writes the JWT via POST /api/neowow/jwt.  Once
  // /api/neowow/status returns `hasJwt: true`, we stop polling and
  // refresh the avatar.  Total round-trip is a few seconds.
  window.neowowStartOAuth = async function () {
    const ret = window.location.origin + '/api/neowow/oauth-callback';
    try {
      const r = await fetch('/api/neowow/oauth/launch', { cache: 'no-store',
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ returnUrl: ret }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));

      // Browser launched (or attempted) — show "等待登录中" + start
      // polling.  The avatar disc UI doesn't change yet; the inline
      // popover / account block does.
      showOAuthWaiting(d.url);
      startOAuthPolling();
    } catch (e) {
      // Fallback: surface the URL so the user can copy-paste into
      // their browser manually.  This usually means webbrowser.open()
      // returned False (no registered browser) or the launcher route
      // 500'd somehow.
      showOAuthFallback(ret, (e && e.message) || String(e));
    }
  };

  // Poll /api/neowow/status until JWT shows up (or we hit the cap).
  // Tight cadence (1 s) so a fast user typing in the browser sees the
  // avatar update within ~1 s of finishing.
  let _oauthPollHandle = null;
  function startOAuthPolling() {
    stopOAuthPolling();
    const start = Date.now();
    const MAX_MS = 5 * 60 * 1000;  // 5 min — OAuth flows that take
                                   // longer than this are dead anyway
    _oauthPollHandle = setInterval(async () => {
      if (Date.now() - start > MAX_MS) {
        stopOAuthPolling();
        return;
      }
      try {
        const r = await fetch('/api/neowow/status', { cache: 'no-store' });
        if (!r.ok) return;
        const d = await r.json();
        try { console.log('[neowow] poll status:', d); } catch (_) {}
        if (d.hasJwt) {
          stopOAuthPolling();
          try { console.log('[neowow] poll detected JWT — refreshing'); } catch (_) {}
          // Fire the same event the inline JWT-save uses — every
          // listener (rail avatar, account block, settings panes)
          // refreshes itself.
          try { window.dispatchEvent(new Event('neoSessionUpdated')); } catch (_) {}
        }
      } catch (e) { try { console.warn('[neowow] poll failed:', e); } catch (_) {} }
    }, 1000);
  }
  function stopOAuthPolling() {
    if (_oauthPollHandle != null) {
      clearInterval(_oauthPollHandle);
      _oauthPollHandle = null;
    }
  }

  // Show "waiting for OAuth" inline in the account block + popover so
  // the user has feedback that something's happening.  Fires for
  // ~5 minutes; cleared on success.
  function showOAuthWaiting(authUrl) {
    const waiting = `
      <div style="display:flex;align-items:center;gap:10px;padding:10px;background:rgba(94,96,206,0.10);border:1px solid rgba(94,96,206,0.35);border-radius:8px;font-size:12px;color:var(--accent,#5e60ce)">
        <div style="width:16px;height:16px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;animation:spin 0.8s linear infinite;flex-shrink:0"></div>
        <div style="flex:1;line-height:1.5">
          <div style="font-weight:600">浏览器已打开，等待 OAuth 完成…</div>
          <div style="color:var(--muted);font-size:11px;margin-top:2px">没自动打开？<a href="${escapeHtml(authUrl)}" target="_blank" rel="noreferrer" style="color:var(--accent)">点这里手动打开 →</a></div>
        </div>
      </div>
    `;
    const accountBlock = $('neowowAccountBlock');
    if (accountBlock) accountBlock.innerHTML = waiting;
    const popoverBody = $('neowowAuthPopBody');
    if (popoverBody) popoverBody.innerHTML = waiting;
    // Inject the spinner @keyframes once.
    ensureSpinKeyframes();
  }

  function showOAuthFallback(returnUrl, errMsg) {
    const authUrl = 'https://app.neowow.studio/api/oauth/start?return=' + encodeURIComponent(returnUrl);
    const html = `
      <div style="padding:10px;background:rgba(245,158,11,0.10);border:1px solid rgba(245,158,11,0.35);border-radius:8px;font-size:12px;line-height:1.6">
        <div style="font-weight:600;color:#e8a030">⚠️ 无法自动打开浏览器：${escapeHtml(errMsg)}</div>
        <div style="margin-top:6px;color:var(--muted)">在浏览器里手动打开下方链接完成登录：</div>
        <a href="${escapeHtml(authUrl)}" target="_blank" rel="noreferrer" style="display:block;margin-top:6px;font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--accent);word-break:break-all">${escapeHtml(authUrl)}</a>
        <button class="btn-tiny" onclick="window.neowowStartOAuth()" style="margin-top:8px">重试</button>
      </div>
    `;
    const accountBlock = $('neowowAccountBlock');
    if (accountBlock) accountBlock.innerHTML = html;
    const popoverBody = $('neowowAuthPopBody');
    if (popoverBody) popoverBody.innerHTML = html;
    // Even in fallback, start polling — user might paste the URL
    // and complete OAuth, in which case we'd still detect it.
    startOAuthPolling();
  }

  // Inject `@keyframes spin` ONCE so the showOAuthWaiting spinner has
  // an animation.  Idempotent — every subsequent call is a no-op.
  function ensureSpinKeyframes() {
    if (document.getElementById('neowowSpinKf')) return;
    const style = document.createElement('style');
    style.id = 'neowowSpinKf';
    style.textContent = '@keyframes spin{to{transform:rotate(360deg)}}';
    document.head.appendChild(style);
  }

  // When the user comes back to the Hermes window from the browser
  // tab, refresh state immediately — don't wait for the next poll
  // tick.  This makes the OAuth completion feel instant (< 100 ms
  // instead of up to 2 s).
  //
  // Pywebview's webview surface doesn't always fire `focus` reliably
  // (the embedded webkit/edge may have its own event quirks), so we
  // listen on three orthogonal triggers — at least one fires every
  // time the user comes back from another window.
  function refreshAll(reason) {
    // Aid debugging from DevTools console — comment in/out as needed.
    try { console.log('[neowow] refresh:', reason); } catch (_) {}
    void refreshRailAvatar();
    void refreshAccountBlock();
  }
  window.addEventListener('focus',          () => refreshAll('focus'));
  window.addEventListener('pageshow',       () => refreshAll('pageshow'));
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshAll('visibility');
  });

  // Defense-in-depth: low-frequency background poll. Catches the case
  // where ALL the focus/visibility events somehow miss (rare, but
  // pywebview embedded-webview implementations vary). Cheap — single
  // status fetch every 10 s, only triggers a re-render when state
  // changed.
  let _lastKnownHasJwt = null;
  setInterval(async () => {
    try {
      const r = await fetch('/api/neowow/status', { cache: 'no-store' });
      if (!r.ok) return;
      const d = await r.json();
      const has = !!d.hasJwt;
      if (_lastKnownHasJwt !== null && _lastKnownHasJwt !== has) {
        try { console.log('[neowow] background poll detected JWT change:', _lastKnownHasJwt, '→', has); } catch (_) {}
        refreshAll('background-poll');
      }
      _lastKnownHasJwt = has;
    } catch (_) { /* offline — try again next tick */ }
  }, 10_000);

  window.neowowClearJwt = async function () {
    if (!confirm('确认退出 Neodomain？\n积分余额信息会消失，但保存的 deploy token 不受影响。')) return;
    try {
      const r = await fetch('/api/neowow/jwt', { cache: 'no-store',
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
      const r = await fetch('/api/neowow/cloud-status', { cache: 'no-store' });
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
      const r = await fetch('/api/neowow/token', { cache: 'no-store',
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
      const r = await fetch('/api/neowow/token', { cache: 'no-store',
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
      const r = await fetch('/api/neowow/deploy', { cache: 'no-store',
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
      const r = await fetch('/api/neowow/cloud-apply', { cache: 'no-store', method: 'POST' });
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

  // ── Cloud config — push LOCAL → cloud (modal flow) ───────────────────
  //
  // Inverse of `neowowCloudSync`: takes the local ~/.hermes/config.yaml
  // and saves it as a row in the dashboard's hermes-configs table.
  //
  // Slug strategy: user-provided, ASCII-slug regex enforced. If the slug
  // already exists on the dashboard, the server PUTs (update) instead of
  // POST (create) — surfaces as `mode: 'updated'` in the response.
  //
  // What's NOT pushed: API keys from .env. Cloud config schema doesn't
  // include them, by design. Each machine pulling this config must
  // configure its own .env. The modal explicitly warns about this so
  // users don't expect "magic everywhere".
  window.neowowCloudPushOpen = function () {
    const modal = $('neowowCloudPushModal');
    if (!modal) return;
    modal.style.display = 'block';
    // Pre-fill slug with a sensible default derived from the hostname
    // (Mac shows up as e.g. "MacBook-Pro-FF" → "macbook-pro-ff" — clean
    // enough). Fallback to "default" when navigator can't tell us.
    const slugInput = $('neowowCloudPushSlug');
    if (slugInput && !slugInput.value) {
      const guess = (navigator.userAgent.match(/Mac OS X/) ? 'my-mac'
                   : navigator.userAgent.match(/Windows/) ? 'my-pc'
                   : navigator.userAgent.match(/Linux/) ? 'my-linux'
                   : 'default');
      slugInput.value = guess;
    }
    if (slugInput) slugInput.focus();
  };

  window.neowowCloudPushClose = function () {
    const modal = $('neowowCloudPushModal');
    if (!modal) return;
    modal.style.display = 'none';
  };

  window.neowowCloudPushSubmit = async function () {
    const slug = (($('neowowCloudPushSlug') || {}).value || '').trim().toLowerCase();
    const name = (($('neowowCloudPushName') || {}).value || '').trim();
    const desc = (($('neowowCloudPushDesc') || {}).value || '').trim();
    const out  = $('neowowCloudResult');
    const btn  = $('neowowCloudPushSubmit');

    if (!slug) {
      if (out) out.innerHTML = '<span style="color:#ef4444">❌ slug 不能为空</span>';
      return;
    }
    // Mirror the server-side regex so the user sees the constraint
    // error before the round-trip.
    if (!/^[a-z0-9][a-z0-9-]{0,30}$/.test(slug)) {
      if (out) out.innerHTML = '<span style="color:#ef4444">❌ slug 格式不对：1-31 字符，小写字母数字和连字符，首字符不能是连字符</span>';
      return;
    }

    if (btn) { btn.disabled = true; btn.textContent = '推送中…'; }
    if (out) out.innerHTML = '<span style="color:var(--muted)">推送中…</span>';

    try {
      const r = await fetch('/api/neowow/cloud-push', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        cache:   'no-store',
        body:    JSON.stringify({ slug, name, description: desc }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);

      // Success — close modal, show result with link to view in dashboard.
      window.neowowCloudPushClose();
      const verb = d.mode === 'updated' ? '已更新' : '已创建';
      if (out) {
        out.innerHTML = `
          <div style="color:var(--accent)">
            ✓ ${escapeHtml(verb)}云端配置 「${escapeHtml(d.name)}」
            <span style="color:var(--muted)">（slug: <code>${escapeHtml(d.slug)}</code>， 模型: <code>${escapeHtml(d.modelName)}</code>）</span>
          </div>
          <div style="margin-top:6px;color:var(--muted);font-size:11px">
            其他 Hermes 实例可去
            <a href="${escapeHtml(d.url)}" target="_blank" rel="noreferrer" style="color:var(--accent)">app.neowow.studio/account/hermes-configs</a>
            把它设为 active，然后用「🔄 同步激活的云端配置」按钮拉下来。
            <strong style="color:#e8a030">⚠️ API key 没有上传，需要每台机器自己配 .env。</strong>
          </div>
        `;
      }
      // Bust the cached cloud-list so next "查看所有云端配置" reflects the change.
      const listBtn = $('neowowCloudListBtn');
      const listBox = $('neowowCloudListBox');
      if (listBox && listBox.style.display === 'block') {
        listBox.style.display = 'none';
        if (listBtn) listBtn.textContent = '📋 查看所有云端配置';
      }
    } catch (e) {
      if (out) out.innerHTML = `<span style="color:#ef4444">❌ 推送失败：${escapeHtml(e.message)}</span>`;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '推送'; }
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
      const r = await fetch('/api/neowow/cloud-configs', { cache: 'no-store' });
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
      const r = await fetch('/api/neowow/skills/local-status', { cache: 'no-store' });
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
      const r = await fetch('/api/neowow/skills/sync', { cache: 'no-store', method: 'POST' });
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
      const r = await fetch('/api/neowow/skills/cloud-list', { cache: 'no-store' });
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
      const r = await fetch('/api/neowow/skills/local-status', { cache: 'no-store' });
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

  // ── Neowow-managed update notice ────────────────────────────────────
  //
  // When HERMES_NEOWOW_ONLY=1 the native git self-update is disabled.
  // Instead we poll /api/neowow/update-notice (which proxies the
  // dashboard's /api/public/update-notice). When the admin publishes a
  // new version notice the user sees a Neowow-branded banner.
  //
  // We also hide the upstream "Check for updates" toggle in Settings
  // because it would just say "disabled" and confuse users.

  let _neowowOnly = false;   // set after first status fetch

  async function _neowowCheckUpdateNotice() {
    if (!_neowowOnly) return;
    try {
      const r = await fetch('/api/neowow/update-notice', { cache: 'no-store' });
      if (!r.ok) return;
      const data = await r.json();
      if (!data.available) { _hideNeowowUpdateBanner(); return; }
      _showNeowowUpdateBanner(data);
    } catch (_) { /* ignore — offline or server not ready */ }
  }

  function _showNeowowUpdateBanner(data) {
    let banner = $('neowowUpdateBanner');
    if (!banner) {
      // Insert above the existing upstream update-banner so it sits at
      // the very top of the chat column.
      banner = document.createElement('div');
      banner.id = 'neowowUpdateBanner';
      banner.style.cssText = [
        'display:none',
        'position:relative',
        'align-items:center',
        'gap:12px',
        'padding:10px 16px',
        'background:linear-gradient(90deg,rgba(94,96,206,0.18) 0%,rgba(94,96,206,0.10) 100%)',
        'border-bottom:1px solid rgba(94,96,206,0.35)',
        'font-size:13px',
        'color:var(--text)',
        'z-index:100',
      ].join(';');
      const upstream = $('updateBanner');
      if (upstream && upstream.parentNode) {
        upstream.parentNode.insertBefore(banner, upstream);
      } else {
        // Fallback: prepend to body
        document.body.prepend(banner);
      }
    }

    const versionLabel = data.version ? `v${data.version}` : '新版本';
    const msg          = data.message ? ` — ${data.message}` : '';
    const url          = data.downloadUrl || 'https://app.neowow.studio/agent';

    banner.innerHTML = `
      <span style="font-size:18px;flex-shrink:0">🚀</span>
      <span style="flex:1;min-width:0">
        <strong style="color:var(--accent,#5e60ce)">Neowow Hermes ${versionLabel}</strong>
        已发布${msg}。请<a href="${url}" target="_blank" rel="noopener"
          style="color:var(--accent,#5e60ce);text-decoration:underline">前往下载页</a>更新。
      </span>
      <button onclick="document.getElementById('neowowUpdateBanner').style.display='none';
                       sessionStorage.setItem('neowow-update-dismissed','${data.version||''}');"
        style="flex-shrink:0;padding:4px 10px;border-radius:5px;border:1px solid rgba(94,96,206,0.4);
               background:transparent;color:var(--text);cursor:pointer;font-size:12px">稍后</button>
    `;
    // Respect "稍后" across page refreshes for the same version
    const dismissed = sessionStorage.getItem('neowow-update-dismissed');
    if (dismissed && dismissed === String(data.version || '')) return;
    banner.style.display = 'flex';
  }

  function _hideNeowowUpdateBanner() {
    const b = $('neowowUpdateBanner');
    if (b) b.style.display = 'none';
  }

  function _applyNeowowOnlyUI(isNeowowOnly) {
    // Hide the native "Check for updates" toggle row when NEOWOW_ONLY
    // because native updates are disabled — showing the toggle just
    // confuses users ("why can't I turn this on?").
    const checkUpdatesField = (() => {
      const cb = $('settingsCheckUpdates');
      return cb ? cb.closest('.settings-field') : null;
    })();
    const checkUpdatesBlock = $('checkUpdatesBlock');
    if (isNeowowOnly) {
      if (checkUpdatesField) checkUpdatesField.style.display = 'none';
      if (checkUpdatesBlock) checkUpdatesBlock.style.display = 'none';
      // Also suppress the upstream update banner entirely — it would
      // never show (updates/check returns disabled) but hide the DOM
      // node so its CSS transition can't accidentally flash.
      const upstreamBanner = $('updateBanner');
      if (upstreamBanner) upstreamBanner.style.display = 'none';
    }
  }

  // Hook into the existing refreshRailAvatar flow — it already calls
  // /api/neowow/status on DOMContentLoaded and on neoSessionUpdated.
  // We piggyback on that fetch to read `neowowOnly` without a second
  // round-trip.
  const _origRefreshRailAvatar = window.refreshRailAvatar;
  async function _patchedRefreshRailAvatar() {
    // Run the original first
    if (typeof _origRefreshRailAvatar === 'function') {
      await _origRefreshRailAvatar();
    }
    // Read status again (cached by the browser — no extra network hit
    // because it was just fetched above).
    try {
      const r = await fetch('/api/neowow/status', { cache: 'no-store' });
      if (r.ok) {
        const s = await r.json();
        _neowowOnly = !!s.neowowOnly;
        _applyNeowowOnlyUI(_neowowOnly);
        await _neowowCheckUpdateNotice();
      }
    } catch (_) { /* ignore */ }
  }

  document.addEventListener('DOMContentLoaded', async () => {
    // Delay slightly so the main boot flow finishes first
    setTimeout(_patchedRefreshRailAvatar, 800);
    // Re-check every 30 minutes in long-running tabs
    setInterval(_neowowCheckUpdateNotice, 30 * 60 * 1000);
  });

  // Expose for testing / manual trigger in devtools
  window.neowowCheckUpdateNotice = _neowowCheckUpdateNotice;

  // ── OSS Backup ────────────────────────────────────────────────────────────

  async function _neowowLoadBackupStatus() {
    const section = $('neowowBackupSection');
    const statusEl = $('neowowBackupStatus');
    if (!section || !statusEl) return;
    try {
      const r = await fetch('/api/neowow/backup/status', { cache: 'no-store' });
      const d = await r.json();
      if (!d.available) {
        // Not a cloud instance — hide the whole section
        section.style.display = 'none';
        return;
      }
      section.style.display = 'block';
      if (d.lastPushTs) {
        // Format timestamp for display
        let ts = d.lastPushTs;
        try {
          const dt = new Date(ts);
          if (!isNaN(dt.getTime())) {
            ts = dt.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai',
              month: 'numeric', day: 'numeric',
              hour: '2-digit', minute: '2-digit' });
          }
        } catch (_) { /* use raw string */ }
        statusEl.innerHTML = `上次同步：<strong>${escapeHtml(ts)}</strong>`;
      } else {
        statusEl.textContent = '暂无同步记录';
      }
    } catch (e) {
      // Silently hide — may be on a local instance without OSS
      if (section) section.style.display = 'none';
    }
  }

  window.neowowBackupPush = async function () {
    const btn = $('neowowBackupPushBtn');
    const out = $('neowowBackupResult');
    if (!btn || !out) return;
    const wasLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = '备份中…';
    out.innerHTML = '';
    try {
      const r = await fetch('/api/neowow/backup/push', { method: 'POST', cache: 'no-store' });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      out.innerHTML = `<span style="color:var(--accent)">✓ ${escapeHtml(d.message || '备份成功')} · ${d.duration_ms || 0} ms</span>`;
      // Refresh status line
      void _neowowLoadBackupStatus();
    } catch (e) {
      out.innerHTML = `<span style="color:#ef4444">❌ 备份失败：${escapeHtml(e.message)}</span>`;
    } finally {
      btn.disabled = false;
      btn.textContent = wasLabel;
    }
  };

  // Load backup status when the neowow settings pane opens.
  // Piggyback on the existing DOMContentLoaded listener by calling it
  // alongside the other init calls.
  document.addEventListener('DOMContentLoaded', () => {
    void _neowowLoadBackupStatus();
  });

})();
