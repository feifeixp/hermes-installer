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

    // Cloud-config card piggybacks on the same panel-open event so it
    // renders together with the token state — keeps panel-load to one
    // /api/neowow/* round-trip pair instead of a network waterfall.
    void loadCloudStatus();
    // Skills card too — disk-only read, free.
    void loadSkillsStatus();
  }

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
