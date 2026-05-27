// ─────────────────────────────────────────────────────────────────────────
// Server-admin panel (cloud instance management).
//
// Reached via the "服务器" sidebar tab. Calls the four /api/neowow/instance/*
// proxy routes (defined in webui/api/routes.py) which forward to
// /api/me/instance/* on app.neowow.studio using the locally-stored JWT.
//
// State machine the page renders:
//
//   ┌────────────────────────────────────────────────────────────┐
//   │  not_logged_in   → "请先登录 Neowow"                      │
//   │  no_instance     → "未开通云端实例,前往一键开通"           │
//   │  running         → status card + 重启 / 关机 / 备份 / 危险 │
//   │  stopped         → status card + 启动 / 备份 / 危险         │
//   │  spawning/init   → "实例正在创建" + 轮询                   │
//   │  error           → 显示错误 + 重试                          │
//   └────────────────────────────────────────────────────────────┘
//
// Restart is implemented client-side as stop → poll-stopped → start,
// rather than introducing a new dashboard endpoint. That keeps the
// dashboard's API surface narrower and shows the user a visible
// progress sequence ("停机中..." → "启动中...") instead of a single
// opaque 60 s spinner.
// ─────────────────────────────────────────────────────────────────────────

let _serverAdminPollTimer = null;
let _serverAdminBusy      = false;

function _saToast(msg, isError = false) {
  const el = document.getElementById('serverAdminToast');
  if (!el) return;
  el.textContent = msg;
  el.style.borderColor = isError ? '#ef4444' : 'var(--border2)';
  el.style.color = isError ? '#fca5a5' : 'var(--text)';
  el.style.display = 'block';
  // Long-enough for the user to read but not so long it sticks around
  // through subsequent actions. Errors stay 2× longer.
  setTimeout(() => { el.style.display = 'none'; }, isError ? 7000 : 3500);
}

function _saStateBadge(state) {
  const map = {
    running:    { color: '#22c55e', text: '运行中' },
    stopped:    { color: '#94a3b8', text: '已停机' },
    spawning:   { color: '#3b82f6', text: '创建中…' },
    cloud_init: { color: '#3b82f6', text: '初始化中…' },
    error:      { color: '#ef4444', text: '错误' },
    none:       { color: '#64748b', text: '未开通' },
  };
  const s = map[state] || { color: '#64748b', text: state || '未知' };
  return `<span style="display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:6px;background:${s.color}22;color:${s.color};font-size:12px;font-weight:600">
    <span style="width:6px;height:6px;border-radius:50%;background:${s.color}"></span>
    ${s.text}
  </span>`;
}

function _saFmtTime(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false });
  } catch { return String(iso); }
}

// ─── Status render ─────────────────────────────────────────────────────
async function serverAdminLoad() {
  // Render skeleton first so a slow network doesn't leave an empty page.
  const card = document.getElementById('serverAdminStatusCard');
  if (!card) return;
  card.innerHTML = '<div class="server-admin-loading">加载实例状态中…</div>';
  document.getElementById('serverAdminActions').style.display = 'none';

  let resp;
  try {
    resp = await fetch('/api/neowow/instance/status', { cache: 'no-store' });
  } catch (e) {
    _saRenderError('网络错误:' + e.message);
    return;
  }
  if (!resp.ok) {
    let msg = '';
    try { msg = (await resp.json()).error || ''; } catch {}
    if (resp.status === 400 && /未登录/.test(msg)) {
      _saRenderNotLoggedIn();
      return;
    }
    _saRenderError(msg || ('HTTP ' + resp.status));
    return;
  }
  const data = await resp.json();
  _saRenderStatus(data);
}

function _saRenderNotLoggedIn() {
  document.getElementById('serverAdminStatusCard').innerHTML = `
    <div style="text-align:center;padding:30px 16px">
      <div style="font-size:36px;margin-bottom:10px">🔑</div>
      <div style="font-weight:600;margin-bottom:6px">请先登录 Neowow Studio</div>
      <div style="font-size:13px;color:var(--muted);margin-bottom:14px">
        点左侧栏底部的头像图标完成 OAuth 授权,然后回到这里。
      </div>
    </div>`;
}

function _saRenderError(msg) {
  document.getElementById('serverAdminStatusCard').innerHTML = `
    <div style="padding:18px;color:#fca5a5">
      <div style="font-weight:600;margin-bottom:6px">❌ 加载失败</div>
      <div style="font-size:13px;line-height:1.5">${escapeHtml(msg)}</div>
      <button class="server-admin-action" style="margin-top:14px;max-width:160px" onclick="serverAdminLoad()">
        <span class="server-admin-action-icon">↻</span>
        <span class="server-admin-action-body"><span class="server-admin-action-label">重试</span></span>
      </button>
    </div>`;
}

function _saRenderStatus(s) {
  const card = document.getElementById('serverAdminStatusCard');
  const state = s.state || 'none';

  // No instance yet — show onboarding path rather than empty card.
  if (state === 'none' || !s.hasInstance) {
    card.innerHTML = `
      <div style="text-align:center;padding:30px 16px">
        <div style="font-size:36px;margin-bottom:10px">☁️</div>
        <div style="font-weight:600;margin-bottom:6px">尚未开通云端实例</div>
        <div style="font-size:13px;color:var(--muted);margin-bottom:14px;line-height:1.6">
          云端实例是你专属的 Hermes 服务器,关机后会话也不丢失,跨设备访问聊天历史云端同步。
        </div>
        <a href="https://app.neowow.studio/agent" target="_blank" rel="noreferrer" class="server-admin-action" style="display:inline-flex;max-width:200px">
          <span class="server-admin-action-icon">☁</span>
          <span class="server-admin-action-body"><span class="server-admin-action-label">一键开通</span></span>
        </a>
      </div>`;
    return;
  }

  // Normal state card — info table + reactive button visibility.
  card.innerHTML = `
    <div style="display:flex;align-items:flex-start;gap:18px;flex-wrap:wrap">
      <div style="flex:1;min-width:260px">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
          <span style="font-weight:600;font-size:15px">实例状态</span>
          ${_saStateBadge(state)}
        </div>
        <table class="server-admin-info-table">
          <tr><td>访问地址</td><td>${s.url ? `<a href="${s.url}" target="_blank" rel="noreferrer" style="color:var(--accent)">${escapeHtml(s.url)} ↗</a>` : '<span style="color:var(--muted)">—</span>'}</td></tr>
          <tr><td>实例 ID</td><td><code style="font-size:11px">${escapeHtml(s.instanceId || '—')}</code></td></tr>
          <tr><td>区域</td><td>${escapeHtml(s.region || '—')}</td></tr>
          <tr><td>云提供商</td><td>${escapeHtml(s.provider || '—')}</td></tr>
          <tr><td>创建时间</td><td>${_saFmtTime(s.createdAt)}</td></tr>
          <tr><td>最后心跳</td><td>${_saFmtTime(s.lastHeartbeatAt)}</td></tr>
        </table>
      </div>
    </div>`;

  // Show actions panel + flip start/stop button visibility based on state.
  document.getElementById('serverAdminActions').style.display = 'block';
  const isRunning = state === 'running';
  const isStopped = state === 'stopped';
  document.getElementById('serverAdminBtnStart').style.display   = isStopped ? '' : 'none';
  document.getElementById('serverAdminBtnStop').style.display    = isRunning ? '' : 'none';
  document.getElementById('serverAdminBtnRestart').style.display = isRunning ? '' : 'none';

  // Poll while in transient states so the panel updates without a manual refresh.
  if (state === 'spawning' || state === 'cloud_init') {
    _serverAdminPollTimer = setTimeout(serverAdminLoad, 4000);
  }
}

// ─── Actions ───────────────────────────────────────────────────────────
async function _saPost(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : '{}',
  });
  const json = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(json.error || ('HTTP ' + r.status));
  return json;
}

async function serverAdminStop(silent) {
  if (_serverAdminBusy) return;
  if (!silent && !confirm('确定关机吗?会话数据保留,可随时启动。')) return;
  _serverAdminBusy = true;
  if (!silent) _saToast('正在关机…');
  try {
    await _saPost('/api/neowow/instance/stop', { destroy: false });
    if (!silent) _saToast('关机指令已发送');
    await serverAdminLoad();
  } catch (e) {
    _saToast('关机失败:' + e.message, true);
  } finally {
    _serverAdminBusy = false;
  }
}

async function serverAdminStart() {
  if (_serverAdminBusy) return;
  _serverAdminBusy = true;
  _saToast('正在启动…');
  try {
    await _saPost('/api/neowow/instance/start');
    _saToast('启动指令已发送');
    await serverAdminLoad();
  } catch (e) {
    _saToast('启动失败:' + e.message, true);
  } finally {
    _serverAdminBusy = false;
  }
}

async function serverAdminRestart() {
  if (_serverAdminBusy) return;
  if (!confirm('重启会先关机再启动,约 60 秒不可用。继续?')) return;
  _serverAdminBusy = true;
  _saToast('步骤 1/2:正在关机…');
  try {
    await _saPost('/api/neowow/instance/stop', { destroy: false });
    // Wait for state=stopped before starting. Poll up to 30× 2s = 60s.
    let stopped = false;
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 2000));
      const r = await fetch('/api/neowow/instance/status', { cache: 'no-store' });
      if (r.ok) {
        const s = await r.json();
        if (s.state === 'stopped' || !s.hasInstance) { stopped = true; break; }
      }
    }
    if (!stopped) {
      _saToast('停机超时,跳过等待直接启动', true);
    }
    _saToast('步骤 2/2:正在启动…');
    await _saPost('/api/neowow/instance/start');
    _saToast('重启完成');
    await serverAdminLoad();
  } catch (e) {
    _saToast('重启失败:' + e.message, true);
  } finally {
    _serverAdminBusy = false;
  }
}

async function serverAdminDownloadBackup() {
  if (_serverAdminBusy) return;
  _serverAdminBusy = true;
  _saToast('生成下载链接…');
  try {
    const r = await fetch('/api/neowow/instance/backup', { cache: 'no-store' });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      if (/no_backup/.test(j.error || '')) {
        _saToast('暂无备份(OSS sync 还没跑过一次)', true);
      } else {
        _saToast('获取备份链接失败:' + (j.error || r.status), true);
      }
      return;
    }
    const data = await r.json();
    if (!data.url) {
      _saToast('返回数据没有 URL', true);
      return;
    }
    // Trigger the download directly. We don't try to fetch+save through
    // this browser tab because the OSS URL is presigned and the user
    // browser hitting it directly avoids streaming the (potentially
    // large) zip through the dashboard.
    const a = document.createElement('a');
    a.href = data.url;
    a.download = data.filename || 'hermes-backup.zip';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    _saToast('开始下载: ' + (data.filename || 'hermes-backup.zip'));
  } catch (e) {
    _saToast('下载失败:' + e.message, true);
  } finally {
    _serverAdminBusy = false;
  }
}

async function serverAdminDestroy() {
  if (_serverAdminBusy) return;
  // Two-step confirm — this is destroying user data permanently.
  if (!confirm('永久删除实例?磁盘 + 会话历史一并清除,无法恢复。\n\n建议先点上面的「下载备份」保存一份。')) return;
  const phrase = prompt('为了防止误操作,请输入「DELETE」(全大写)以确认:');
  if (phrase !== 'DELETE') {
    _saToast('确认词不正确,已取消');
    return;
  }
  _serverAdminBusy = true;
  _saToast('正在销毁实例…');
  try {
    await _saPost('/api/neowow/instance/stop', { destroy: true });
    _saToast('实例已删除');
    await serverAdminLoad();
  } catch (e) {
    _saToast('删除失败:' + e.message, true);
  } finally {
    _serverAdminBusy = false;
  }
}

// Small helper so the inline HTML can use escapeHtml without pulling
// in the full ui.js module. Matches the style of escapeHtml used
// elsewhere in static/.
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// Cancel any pending poll when the user navigates away from the panel.
window.addEventListener('beforeunload', () => {
  if (_serverAdminPollTimer) clearTimeout(_serverAdminPollTimer);
});
