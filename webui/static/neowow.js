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

  // Hook the existing switchSettingsSection to load on demand.
  if (typeof window.switchSettingsSection === 'function') {
    const _orig = window.switchSettingsSection;
    window.switchSettingsSection = function (name) {
      _orig(name);
      if (name === 'neowow') loadNeowowStatus();
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
