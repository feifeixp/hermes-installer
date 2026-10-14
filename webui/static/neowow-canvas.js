// Canvas workbench: CLI-backed selection/results, native chat owns creation.
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  let active = null, folder = '', page = 1, busy = false, epoch = 0;
  let profile = '', connected = false;
  let connectionBusy = false;
  const blobUrls = [];
  const profileNow = () => typeof S !== 'undefined' ? S.activeProfile || 'default' : 'default';

  function feedback(message, error = false) {
    const el = $('neowowCanvasWorkStatus');
    if (!el) return;
    el.textContent = message;
    el.dataset.tone = error ? 'error' : 'neutral';
  }
  function clearResults() {
    blobUrls.splice(0).forEach(url => URL.revokeObjectURL(url));
    $('neowowCanvasResults').replaceChildren();
    $('neowowCanvasExport').hidden = true;
    $('neowowCanvasResultsSummary').textContent = '选择画布后，可以提取已完成的创作结果。';
  }
  function controls() {
    const root = $('neowowCanvasWorkspace');
    if (!root) return;
    const disabled = busy || connectionBusy;
    root.setAttribute('aria-busy', String(disabled));
    root.querySelectorAll('button,input,select,textarea').forEach(el => { el.disabled = disabled; });
    $('neowowCanvasStartBtn').disabled = disabled || !active;
    $('neowowCanvasResultsRefreshBtn').disabled = disabled || !active;
    $('neowowCanvasPrevPage').disabled = disabled || page <= 1;
    $('neowowCanvasNextPage').disabled = disabled || $('neowowCanvasNextPage').dataset.hasMore !== 'true';
  }
  function setActive(value) {
    active = value;
    clearResults();
    $('neowowCanvasClosed').checked = false;
    $('neowowCanvasActiveProject').textContent = active ? '当前画布：' + active.title : '先载入并选择一张画布。';
    controls();
  }
  window.neowowCanvasResetWorkspace = function () {
    if (!$('neowowCanvasWorkspace')) return;
    epoch++;
    busy = false; active = null; folder = ''; page = 1;
    clearResults();
    $('neowowCanvasProjects').hidden = true;
    $('neowowCanvasPrompt').value = '';
    $('neowowCanvasProjectTitle').value = '';
    $('neowowCanvasFolder').replaceChildren(new Option('根目录', ''));
    $('neowowCanvasProject').replaceChildren(new Option('选择画布…', ''));
    setActive(null);
    feedback('');
  };
  window.neowowCanvasWorkspaceConnected = function (value) {
    const root = $('neowowCanvasWorkspace');
    if (!root) return;
    if (!value || profile !== profileNow()) window.neowowCanvasResetWorkspace();
    connected = !!value;
    profile = profileNow();
    root.hidden = !connected;
  };
  window.neowowCanvasWorkspaceBusy = function (value) {
    connectionBusy = !!value;
    controls();
  };
  async function request(path, body) {
    const response = await fetch('/api/neowow/canvas/' + path, {
      cache: 'no-store', ...(body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '画布操作未完成，请稍后重试。');
    return data;
  }
  async function action(message, fn) {
    if (busy || connectionBusy || !connected) return;
    busy = true; controls(); feedback(message);
    const token = epoch, selectedProfile = profileNow();
    const current = () => token === epoch && selectedProfile === profileNow() && connected;
    try { await fn(current); }
    catch (e) { if (current()) feedback(e.message || '画布操作失败，请重试。', true); }
    finally { if (current()) { busy = false; controls(); } }
  }
  function chooseOption(select, value, label) {
    if (!Array.from(select.options).some(option => option.value === value)) select.add(new Option(label, value));
    select.value = value;
  }
  window.neowowCanvasLoadProjects = async function () {
    await action('正在载入画布…', async current => {
      const data = await request('projects?page=' + page + '&folder=' + encodeURIComponent(folder));
      if (!current()) return;
      const select = $('neowowCanvasProject');
      select.replaceChildren(new Option('选择画布…', ''));
      data.projects.forEach(p => select.add(new Option(p.title, p.sessionId)));
      if (!folder) {
        const folders = $('neowowCanvasFolder');
        folders.replaceChildren(new Option('根目录', ''));
        data.folders.forEach(f => folders.add(new Option(f.name, f.folderId)));
      }
      const changed = (active && active.sessionId) !== (data.active && data.active.sessionId);
      if (changed) setActive(data.active);
      if (active) chooseOption(select, active.sessionId, active.title + '（当前）');
      $('neowowCanvasProjects').hidden = false;
      $('neowowCanvasNextPage').dataset.hasMore = String(!!data.hasMore);
      $('neowowCanvasProjectPage').textContent = '第 ' + page + ' 页';
      feedback(data.projects.length ? '选择目标画布，或为这次创作新建画布。' : '这里还没有画布，可以在下方新建。');
    });
  };
  window.neowowCanvasChangeFolder = function () {
    if (busy || connectionBusy) return;
    folder = $('neowowCanvasFolder').value; page = 1;
    return window.neowowCanvasLoadProjects();
  };
  window.neowowCanvasProjectPage = function (direction) {
    if (busy || connectionBusy) return;
    page = Math.max(1, page + direction);
    return window.neowowCanvasLoadProjects();
  };
  window.neowowCanvasSelectProject = async function () {
    if (busy || connectionBusy) return;
    const projectId = $('neowowCanvasProject').value;
    if (!projectId) { setActive(null); return; }
    setActive(null);
    await action('正在连接所选画布…', async current => {
      const data = await request('select', { projectId });
      if (!current()) return;
      setActive(data.active);
      feedback('画布已选定。填写创作要求，或先提取已有结果。');
    });
    if (!active) $('neowowCanvasProject').value = '';
  };
  window.neowowCanvasCreateProject = async function (event) {
    event.preventDefault();
    const title = $('neowowCanvasProjectTitle').value.trim();
    const canvasClosed = $('neowowCanvasClosed').checked;
    if (!title || !canvasClosed) { feedback('请填写名称，并确认已关闭目标画布网页。', true); return; }
    await action('正在新建画布，请勿重复提交…', async current => {
      const data = await request('create', { title, canvasClosed });
      if (!current()) return;
      setActive(data.active);
      chooseOption($('neowowCanvasProject'), active.sessionId, active.title);
      $('neowowCanvasProjectTitle').value = '';
      feedback('新画布已创建并选定。');
    });
  };
  function chatAvailable() {
    if (typeof S === 'undefined' || typeof send !== 'function' || !$('msg')) throw new Error('请在完整聊天工作区中发起创作。');
    if (S.busy || (typeof _sendInProgress !== 'undefined' && _sendInProgress)) throw new Error('当前聊天正在处理任务，请等待完成后发起创作。');
    if ($('msg').value.trim() || (S.pendingFiles || []).length) throw new Error('聊天输入框已有草稿或附件，请先发送或保存。');
    if (S.session && (S.session.read_only || S.session.is_read_only)) throw new Error('当前聊天为只读，请新建聊天后创作。');
    if (S.session && (S.session.profile || 'default') !== profileNow()) throw new Error('当前聊天使用其他 Profile，请在当前 Profile 新建聊天后创作。');
    if (typeof isCompressionUiRunning === 'function' && isCompressionUiRunning()) throw new Error('聊天正在整理上下文，请稍后再发起创作。');
  }
  window.neowowCanvasStartCreation = async function (event) {
    event.preventDefault();
    if (!active) { feedback('请先选择目标画布。', true); return; }
    const prompt = $('neowowCanvasPrompt').value.trim();
    const canvasClosed = $('neowowCanvasClosed').checked;
    if (!prompt || !canvasClosed) { feedback('请填写创作要求，并确认目标画布网页已关闭。', true); return; }
    await action('正在准备创作任务…', async current => {
      chatAvailable();
      const data = await request('brief', { prompt, canvasClosed, projectId: active.sessionId, kind: $('neowowCanvasKind').value });
      if (!current()) return;
      chatAvailable();
      if (typeof switchPanel !== 'function' || await switchPanel('chat') === false) throw new Error('请先完成当前设置，再切换到聊天创作。');
      if (!current()) return;
      chatAvailable();
      $('msg').value = data.message;
      $('msg').dispatchEvent(new Event('input', { bubbles: true }));
      if (typeof autoResize === 'function') autoResize();
      // Native send owns the visible user turn, model context and SSE stream.
      Promise.resolve(send()).catch(() => {
        if (typeof showToast === 'function') showToast('创作任务未发送成功，请在聊天中重试。', 3000, 'error');
      });
      $('neowowCanvasPrompt').value = '';
      feedback('任务已交给当前聊天。生成前请确认方案与积分，完成后回来提取结果。');
    });
  };
  function node(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }
  function renderResults(data) {
    clearResults();
    const grid = $('neowowCanvasResults');
    for (const item of data.results) {
      const card = node('article', 'canvas-result');
      card.append(node('h4', '', item.label));
      const link = node('a', 'canvas-button', item.kind === 'text' ? '下载文本' : '打开原文件 ↗');
      if (item.kind === 'text') {
        card.append(node('pre', 'canvas-result-text', item.text));
        const url = URL.createObjectURL(new Blob([item.text], { type: 'text/plain;charset=utf-8' }));
        blobUrls.push(url); link.href = url; link.download = 'canvas-' + item.nodeKey + '.txt';
      } else {
        let url;
        try { url = new URL(item.url); } catch (_) { continue; }
        if (url.protocol !== 'https:' || url.username || url.password) continue;
        const media = node(item.kind === 'image' ? 'img' : item.kind === 'video' ? 'video' : 'audio');
        media.src = url.href;
        media.addEventListener('error', () => {
          media.hidden = true;
          card.append(node('p', 'canvas-results-empty', '预览暂时无法加载，请打开原文件查看。'));
        }, { once: true });
        if (item.kind === 'image') { media.alt = item.label; media.loading = 'lazy'; media.referrerPolicy = 'no-referrer'; }
        else { media.controls = true; media.preload = 'none'; }
        card.append(media);
        link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
      }
      card.append(link); grid.append(card);
    }
    const counts = data.counts;
    $('neowowCanvasResultsSummary').textContent = `已完成 ${counts.success} 个节点 · 生成中 ${counts.pending} · 失败 ${counts.failed}`;
    if (!data.results.length) grid.append(node('p', 'canvas-results-empty', counts.pending ? '任务还在生成，稍后点击刷新提取。' : '当前画布还没有可提取的最终结果。'));
    const exporter = $('neowowCanvasExport');
    exporter.href = '/api/neowow/canvas/export?project=' + encodeURIComponent(active.sessionId);
    exporter.hidden = false;
    feedback(data.warning || '已提取最终结果。参考素材和生成中的旧结果不会作为成品展示。');
  }
  window.neowowCanvasRefreshResults = async function () {
    if (!active) return;
    const projectId = active.sessionId;
    await action('正在提取结果并检查任务进度…', async current => {
      const data = await request('results?project=' + encodeURIComponent(projectId));
      if (current() && active && active.sessionId === projectId) renderResults(data);
    });
  };
})();
