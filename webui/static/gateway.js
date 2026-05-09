// ─────────────────────────────────────────────────────────────────────────────
// Gateway connection mode — settings pane + UX wiring.
//
// Adds a "连接模式" entry under Settings that lets the user pick between
// running Hermes locally (default) and connecting to a remote Hermes
// WebUI on a cloud server. State lives in ~/.hermes/webui/gateway.json
// (see webui/api/gateway_config.py).
//
// Same pattern as neowow.js: hook switchSettingsSection() to short-
// circuit 'gateway' before upstream's allow-list rejects it, then run
// our own pane activation + loader. Why an override and not a patch
// to panels.js: panels.js is subtree-pulled from nesquena/hermes-webui
// and gets rewritten on every upstream sync — overrides survive.
//
// After saving, the user is told to restart Hermes Installer. main.py
// reads gateway.json once at startup so a config change mid-session
// does NOT take effect until restart. We could file-watch + restart
// automatically but that's surprising — better to make the trade-off
// explicit ("save then restart").
// ─────────────────────────────────────────────────────────────────────────────

(function () {
  function $(id) { return document.getElementById(id); }
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // ── switchSettingsSection override ─────────────────────────────────
  //
  // Same trick as neowow.js. Section 'gateway' must short-circuit
  // upstream's allow-list before _orig() falls through to 'conversation'.
  if (typeof window.switchSettingsSection === 'function') {
    const _orig = window.switchSettingsSection;
    window.switchSettingsSection = function (name) {
      if (name === 'gateway') {
        document.querySelectorAll('#settingsMenu .side-menu-item').forEach((it) => {
          it.classList.toggle('active', it.dataset && it.dataset.settingsSection === 'gateway');
        });
        // Same pane list as neowow override — keeps "any future upstream
        // pane we don't know about" from being silently activated.
        const paneIds = [
          'settingsPaneConversation',
          'settingsPaneAppearance',
          'settingsPanePreferences',
          'settingsPaneProviders',
          'settingsPaneNeowow',
          'settingsPaneGateway',
          'settingsPaneSystem',
        ];
        paneIds.forEach((id) => {
          const pane = document.getElementById(id);
          if (pane) pane.classList.toggle('active', id === 'settingsPaneGateway');
        });
        const dd = document.getElementById('settingsSectionDropdown');
        if (dd && dd.value !== 'gateway') dd.value = 'gateway';
        loadGatewayConfig();
        return;
      }
      _orig(name);
    };
  }

  // ── Mode picker reactive show/hide ──────────────────────────────────
  document.addEventListener('change', (e) => {
    if (!e.target || e.target.name !== 'gatewayMode') return;
    const remoteFields = $('gatewayRemoteFields');
    if (remoteFields) {
      remoteFields.style.display = e.target.value === 'remote' ? '' : 'none';
    }
  });

  // ── Load current config and populate the form ───────────────────────
  async function loadGatewayConfig() {
    const modeBadge   = $('gatewayCurrentMode');
    const urlInput    = $('gatewayRemoteUrl');
    const labelInput  = $('gatewayRemoteLabel');
    const remoteFields = $('gatewayRemoteFields');
    const saveResult  = $('gatewaySaveResult');
    if (saveResult) saveResult.innerHTML = '';

    try {
      const r = await fetch('/api/gateway/config', { cache: 'no-store' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const cfg = await r.json();
      const mode = cfg.mode === 'remote' ? 'remote' : 'local';

      // Status badge — shows the user what's actually saved.
      if (modeBadge) {
        if (mode === 'remote' && cfg.url) {
          const labelHtml = cfg.label
            ? ` · <span style="color:var(--accent)">${escapeHtml(cfg.label)}</span>`
            : '';
          modeBadge.innerHTML =
            `当前模式：<strong style="color:var(--accent)">远程</strong> → ` +
            `<code style="font-size:11px">${escapeHtml(cfg.url)}</code>${labelHtml}`;
        } else {
          modeBadge.innerHTML =
            `当前模式：<strong style="color:var(--text)">本机</strong>（默认 — Hermes Agent 跑在你这台机器上）`;
        }
      }

      // Pre-fill the form fields.
      const radio = document.querySelector(`input[name="gatewayMode"][value="${mode}"]`);
      if (radio) radio.checked = true;
      if (urlInput)   urlInput.value   = cfg.url   || '';
      if (labelInput) labelInput.value = cfg.label || '';
      if (remoteFields) {
        remoteFields.style.display = mode === 'remote' ? '' : 'none';
      }
    } catch (e) {
      if (modeBadge) {
        modeBadge.innerHTML = `<span style="color:#ef4444">加载失败：${escapeHtml(e.message)}</span>`;
      }
    }
  }

  // ── Save ────────────────────────────────────────────────────────────
  async function saveGatewayConfig() {
    const result = $('gatewaySaveResult');
    if (result) result.innerHTML = '<span style="color:var(--muted)">保存中…</span>';

    const modeRadio = document.querySelector('input[name="gatewayMode"]:checked');
    const mode = modeRadio ? modeRadio.value : 'local';
    const url   = ($('gatewayRemoteUrl') || {}).value || '';
    const label = ($('gatewayRemoteLabel') || {}).value || '';

    // Client-side validation — catch obvious mistakes before the round
    // trip. Server still re-validates (parseScopes-style defense).
    if (mode === 'remote') {
      const u = url.trim();
      if (!u) {
        if (result) result.innerHTML = '<span style="color:#ef4444">远程 URL 不能为空。</span>';
        return;
      }
      if (!/^https?:\/\//.test(u)) {
        if (result) result.innerHTML = '<span style="color:#ef4444">URL 必须以 http:// 或 https:// 开头。</span>';
        return;
      }
    }

    try {
      const r = await fetch('/api/gateway/config', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ mode, url, label }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);

      // Success — render restart hint + refresh the badge.
      if (result) {
        result.innerHTML = `
          <div style="padding:10px 12px;border-radius:6px;background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.3);color:var(--text)">
            ✓ 已保存。<strong>需要重启 Hermes Installer 才能生效</strong>。
          </div>
        `;
      }
      // Reload the badge to reflect the saved state.
      loadGatewayConfig();
    } catch (e) {
      if (result) {
        result.innerHTML = `<span style="color:#ef4444">保存失败：${escapeHtml(e.message)}</span>`;
      }
    }
  }

  // ── Reset (clear gateway.json → revert to local) ────────────────────
  async function resetGatewayConfig() {
    if (!confirm('确定重置为本机模式？\n下次启动 Hermes Installer 将装/启动本地 Hermes Agent。')) return;
    const result = $('gatewaySaveResult');
    if (result) result.innerHTML = '<span style="color:var(--muted)">重置中…</span>';
    try {
      const r = await fetch('/api/gateway/config', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ clear: true }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      // Re-pick the local radio and clear remote fields locally.
      const localRadio = document.querySelector('input[name="gatewayMode"][value="local"]');
      if (localRadio) localRadio.checked = true;
      const urlInput   = $('gatewayRemoteUrl');
      const labelInput = $('gatewayRemoteLabel');
      const remoteFields = $('gatewayRemoteFields');
      if (urlInput)   urlInput.value   = '';
      if (labelInput) labelInput.value = '';
      if (remoteFields) remoteFields.style.display = 'none';
      if (result) {
        result.innerHTML = `
          <div style="padding:10px 12px;border-radius:6px;background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.3);color:var(--text)">
            ✓ 已重置为本机模式。重启 Hermes Installer 生效。
          </div>
        `;
      }
      loadGatewayConfig();
    } catch (e) {
      if (result) {
        result.innerHTML = `<span style="color:#ef4444">重置失败：${escapeHtml(e.message)}</span>`;
      }
    }
  }

  // Expose the inline-handler entry points the HTML uses.
  window.saveGatewayConfig  = saveGatewayConfig;
  window.resetGatewayConfig = resetGatewayConfig;
  window.loadGatewayConfig  = loadGatewayConfig;
})();
