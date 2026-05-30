// ── Messaging channels panel ──────────────────────────────────────────────
// Renders 3 channel cards (WeChat QR / Feishu / WeCom). Reached via the
// 消息渠道 rail tab. Calls /api/messaging/* routes.
// Spec: docs/superpowers/specs/2026-05-30-messaging-channels-design.md
(function () {
  let _weixinPoll = null;

  function $(id) { return document.getElementById(id); }
  function tx(key, vars) {
    let s = (typeof t === 'function' ? (t(key) || key) : key);
    if (vars) for (const k in vars) s = s.split('{' + k + '}').join(vars[k]);
    return s;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function badge(state) {
    const map = {
      connected: 'messaging_status_connected',
      connecting: 'messaging_status_connecting',
      error: 'messaging_status_error',
      unconfigured: 'messaging_status_unconfigured',
    };
    return `<span class="messaging-badge ${state}">${esc(tx(map[state] || 'messaging_status_unconfigured'))}</span>`;
  }

  async function fetchStatus() {
    try {
      const r = await fetch('/api/messaging/channels');
      if (!r.ok) return null;
      return await r.json();
    } catch (e) { return null; }
  }

  // ── WeChat QR ──
  function stopWeixinPoll() {
    if (_weixinPoll) { clearInterval(_weixinPoll); _weixinPoll = null; }
  }

  function renderQr(container, imgUrl) {
    container.innerHTML = '';
    try {
      const qr = (window.qrcode || qrcode)(0, 'M');
      qr.addData(imgUrl);
      qr.make();
      container.innerHTML = qr.createSvgTag({ cellSize: 5, margin: 4 });
    } catch (e) {
      const a = document.createElement('a');
      a.href = imgUrl; a.target = '_blank'; a.rel = 'noopener';
      a.textContent = imgUrl;
      container.appendChild(a);
    }
  }

  async function startWeixinQr(box) {
    stopWeixinPoll();
    box.innerHTML = `<div class="messaging-qr-hint">${esc(tx('messaging_status_connecting'))}</div>`;
    let started;
    try {
      const r = await fetch('/api/messaging/weixin/qr/start', { method: 'POST' });
      if (!r.ok) throw new Error('start failed');
      started = await r.json();
    } catch (e) {
      box.innerHTML = `<div class="messaging-qr-hint">${esc(tx('messaging_weixin_qr_failed'))}</div>`;
      return;
    }
    const token = started.qrcode_token;
    const imgUrl = started.qrcode_img_url || started.qrcode_token;
    box.innerHTML = `<div class="messaging-qr-box"><div class="qr-svg"></div>
      <div class="messaging-qr-hint">${esc(tx('messaging_weixin_scan_hint'))}</div></div>`;
    renderQr(box.querySelector('.qr-svg'), imgUrl);

    _weixinPoll = setInterval(async () => {
      let st;
      try {
        const r = await fetch('/api/messaging/weixin/qr/status?token=' + encodeURIComponent(token));
        st = await r.json();
      } catch (e) { return; }
      const status = st.status;
      if (status === 'scaned' || status === 'scaned_but_redirect') {
        const hint = box.querySelector('.messaging-qr-hint');
        if (hint) hint.textContent = tx('messaging_weixin_scaned');
      } else if (status === 'expired') {
        stopWeixinPoll();
        box.innerHTML = `<div class="messaging-qr-hint">${esc(tx('messaging_weixin_expired'))}</div>
          <button class="messaging-btn" id="weixinRegenBtn">${esc(tx('messaging_btn_regenerate_qr'))}</button>`;
        const b = $('weixinRegenBtn');
        if (b) b.onclick = () => startWeixinQr(box);
      } else if (status === 'confirmed') {
        stopWeixinPoll();
        messagingLoad();  // re-render whole panel; weixin now connected
      } else if (status === 'invalid_token' || status === 'error') {
        stopWeixinPoll();
        box.innerHTML = `<div class="messaging-qr-hint">${esc(tx('messaging_weixin_qr_failed'))}</div>
          <button class="messaging-btn" id="weixinRegenBtn">${esc(tx('messaging_btn_regenerate_qr'))}</button>`;
        const b = $('weixinRegenBtn');
        if (b) b.onclick = () => startWeixinQr(box);
      }
    }, 2000);
  }

  function weixinCard(s) {
    const connected = s.connected;
    const state = connected ? 'connected' : 'unconfigured';
    let body;
    if (connected) {
      body = `<div class="messaging-card-body">
        ${esc(tx('messaging_weixin_connected', { account: s.account_id || '' }))}
        <div class="messaging-actions">
          <button class="messaging-btn" id="weixinDisconnectBtn">${esc(tx('messaging_btn_disconnect'))}</button>
        </div></div>`;
    } else {
      body = `<div class="messaging-card-body">
        <div class="messaging-actions"><button class="messaging-btn primary" id="weixinConnectBtn">${esc(tx('messaging_btn_connect'))}</button></div>
        <div id="weixinQrArea"></div></div>`;
    }
    return `<div class="messaging-card">
      <div class="messaging-card-head"><span class="messaging-card-icon">💬</span>
        <span class="messaging-card-name">${esc(tx('messaging_weixin_name'))}</span>${badge(state)}</div>
      ${body}</div>`;
  }

  function feishuCard(s) {
    const state = s.connected ? 'connected' : 'unconfigured';
    const secretPlaceholder = s.has_secret ? tx('messaging_secret_keep') : '';
    return `<div class="messaging-card">
      <div class="messaging-card-head"><span class="messaging-card-icon">🐦</span>
        <span class="messaging-card-name">${esc(tx('messaging_feishu_name'))}</span>${badge(state)}</div>
      <div class="messaging-card-body">
        <div class="messaging-form-row"><label>${esc(tx('messaging_feishu_app_id'))}</label>
          <input id="feishuAppId" value="" placeholder="cli_..."></div>
        <div class="messaging-form-row"><label>${esc(tx('messaging_feishu_app_secret'))}</label>
          <input id="feishuAppSecret" type="password" placeholder="${esc(secretPlaceholder)}"></div>
        <div class="messaging-actions">
          <button class="messaging-btn primary" id="feishuSaveBtn">${esc(tx('messaging_btn_save_connect'))}</button>
          ${s.connected ? `<button class="messaging-btn" id="feishuDisconnectBtn">${esc(tx('messaging_btn_disconnect'))}</button>` : ''}
        </div>
        <details class="messaging-teaching"><summary>${esc(tx('messaging_teaching_toggle'))}</summary>
          <ol>
            <li>open.feishu.cn 开发者后台 →「创建企业自建应用」</li>
            <li>「凭证与基础信息」复制 App ID + App Secret 填到上面</li>
            <li>「权限管理」开启 im:message + im:message:send_as_bot</li>
            <li>「事件与回调」订阅方式选「长连接」，订阅 im.message.receive_v1</li>
            <li>「版本管理与发布」创建版本 → 申请发布（管理员审批）</li>
            <li>回这里点「保存并连接」，飞书里拉机器人进群 / 私聊 @它</li>
          </ol></details>
      </div></div>`;
  }

  function wecomCard(s) {
    const state = s.connected ? 'connected' : 'unconfigured';
    const secretPlaceholder = s.has_secret ? tx('messaging_secret_keep') : '';
    return `<div class="messaging-card">
      <div class="messaging-card-head"><span class="messaging-card-icon">🏢</span>
        <span class="messaging-card-name">${esc(tx('messaging_wecom_name'))}</span>${badge(state)}</div>
      <div class="messaging-card-body">
        <div class="messaging-form-row"><label>${esc(tx('messaging_wecom_bot_id'))}</label>
          <input id="wecomBotId" placeholder="bot_..."></div>
        <div class="messaging-form-row"><label>${esc(tx('messaging_wecom_secret'))}</label>
          <input id="wecomSecret" type="password" placeholder="${esc(secretPlaceholder)}"></div>
        <div class="messaging-actions">
          <button class="messaging-btn primary" id="wecomSaveBtn">${esc(tx('messaging_btn_save_connect'))}</button>
          ${s.connected ? `<button class="messaging-btn" id="wecomDisconnectBtn">${esc(tx('messaging_btn_disconnect'))}</button>` : ''}
        </div>
        <details class="messaging-teaching"><summary>${esc(tx('messaging_teaching_toggle'))}</summary>
          <ol>
            <li>work.weixin.qq.com 管理后台 →「应用管理」创建智能机器人 / 自建应用</li>
            <li>复制 Bot ID + Secret 填到上面</li>
            <li>接收消息选 websocket 长连接模式</li>
            <li>回这里点「保存并连接」，企业微信里 @机器人 即可对话</li>
          </ol></details>
      </div></div>`;
  }

  async function postJson(url, payload) {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    let data = null;
    try { data = await r.json(); } catch (e) { /* */ }
    return { ok: r.ok, data };
  }

  function wireActions() {
    const wc = $('weixinConnectBtn');
    if (wc) wc.onclick = () => startWeixinQr($('weixinQrArea'));
    const wd = $('weixinDisconnectBtn');
    if (wd) wd.onclick = async () => { await postJson('/api/messaging/weixin/disconnect'); messagingLoad(); };

    const fs = $('feishuSaveBtn');
    if (fs) fs.onclick = async () => {
      const res = await postJson('/api/messaging/feishu/config', {
        app_id: ($('feishuAppId') || {}).value || '',
        app_secret: ($('feishuAppSecret') || {}).value || '',
      });
      if (!res.ok && res.data && res.data.error) { alert(res.data.error); return; }
      if (typeof showToast === 'function') showToast(tx('messaging_saved_restart_hint'));
      messagingLoad();
    };
    const fd = $('feishuDisconnectBtn');
    if (fd) fd.onclick = async () => { await postJson('/api/messaging/feishu/disconnect'); messagingLoad(); };

    const ws = $('wecomSaveBtn');
    if (ws) ws.onclick = async () => {
      const res = await postJson('/api/messaging/wecom/config', {
        bot_id: ($('wecomBotId') || {}).value || '',
        secret: ($('wecomSecret') || {}).value || '',
      });
      if (!res.ok && res.data && res.data.error) { alert(res.data.error); return; }
      if (typeof showToast === 'function') showToast(tx('messaging_saved_restart_hint'));
      messagingLoad();
    };
    const wcd = $('wecomDisconnectBtn');
    if (wcd) wcd.onclick = async () => { await postJson('/api/messaging/wecom/disconnect'); messagingLoad(); };
  }

  // Global entry — called by panels.js lazy-load hook on tab switch.
  window.messagingLoad = async function messagingLoad() {
    stopWeixinPoll();
    const cards = $('messagingCards');
    if (!cards) return;
    const s = await fetchStatus();
    if (!s) {
      cards.innerHTML = `<div class="messaging-loading">${esc(tx('messaging_status_error'))}</div>`;
      return;
    }
    cards.innerHTML = weixinCard(s.weixin) + feishuCard(s.feishu) + wecomCard(s.wecom);
    if (typeof applyI18n === 'function') applyI18n();
    wireActions();
  };
})();
