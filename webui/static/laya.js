// Optional Laya settings. No model/runtime dependency is loaded in the browser.
(function () {
  'use strict';
  const el = id => document.getElementById(id);
  let loaded = false;
  let pending = false;
  let editVersion = 0;
  let loadVersion = 0;
  window._layaDirty = false;

  function status(id, text, error = false) {
    const node = el(id);
    node.textContent = text;
    node.dataset.error = String(error);
  }

  function modeHelp() {
    el('layaModeHelp').textContent = t('laya_' + el('layaMode').value + '_help');
  }

  function payload() {
    return {
      mode: el('layaMode').value,
      endpoint: el('layaEndpoint').value.trim(),
      model: el('layaModel').value,
      threshold: Number(el('layaThreshold').value),
      timeout_ms: Number(el('layaTimeout').value),
      api_key: el('layaKey').value,
      clear_api_key: el('layaClearKey').checked,
    };
  }

  function hydrate(settings) {
    el('layaMode').value = settings.mode;
    el('layaEndpoint').value = settings.endpoint;
    el('layaModel').value = settings.model;
    el('layaThreshold').value = settings.threshold;
    el('layaTimeout').value = settings.timeout_ms;
    el('layaKey').value = '';
    el('layaKey').placeholder = t(settings.has_api_key ? 'laya_key_saved' : 'laya_key_empty');
    el('layaClearKey').checked = false;
    el('layaClearKey').disabled = !settings.has_api_key;
    modeHelp();
  }

  function resultText(result) {
    if (result.status !== 'ok') {
      return t(({timeout: 'laya_timeout_error', unauthorized: 'laya_unauthorized', busy: 'laya_busy'})[result.status] || 'laya_unavailable');
    }
    const elapsed = Number(result.elapsed_ms || 0).toFixed(0) + ' ms';
    if (!result.workflow) return t('laya_connected') + ' · ' + elapsed;
    return t('laya_workflow_' + result.workflow) + ' · ' + (Number(result.confidence) * 100).toFixed(1) + '% · ' + elapsed
      + '\n' + t(result.accepted ? 'laya_accepted' : 'laya_fallback_result');
  }

  function renderRecent(rows) {
    const container = el('layaRecent');
    container.replaceChildren();
    if (!rows.length) { container.textContent = t('laya_empty'); return; }
    for (const row of rows) {
      const div = document.createElement('div');
      div.className = 'laya-recent-row';
      div.textContent = new Date(row.time * 1000).toLocaleTimeString() + ' · '
        + t('laya_' + row.mode) + ' · ' + resultText(row);
      container.appendChild(div);
    }
  }

  function busy(value) {
    pending = value;
    for (const id of ['layaSave', 'layaConnect', 'layaRun']) el(id).disabled = value || !loaded;
  }

  window.layaChanged = function () {
    window._layaDirty = true;
    editVersion++;
    modeHelp();
    status('layaSaveStatus', t('laya_dirty'));
    status('layaConnectionStatus', '');
    status('layaSampleStatus', '');
  };

  window.loadLayaSettings = async function (force = false) {
    if (pending || (window._layaDirty && !force)) return;
    const requestVersion = ++loadVersion;
    const edits = editVersion;
    el('layaRetry').hidden = true;
    try {
      const data = await api('/api/laya');
      if (requestVersion !== loadVersion || edits !== editVersion) return;
      hydrate(data.settings);
      renderRecent(data.recent || []);
      loaded = true;
      window._layaDirty = false;
      el('layaFields').disabled = false;
      busy(false);
      status('layaLoadStatus', '');
      status('layaSaveStatus', '');
    } catch (_) {
      if (requestVersion !== loadVersion) return;
      status('layaLoadStatus', t('laya_load_failed'), true);
      el('layaRetry').hidden = false;
      if (!loaded) { el('layaFields').disabled = true; busy(false); }
    }
  };

  window.saveLayaSettings = async function () {
    if (!loaded || pending || !el('layaForm').reportValidity()) return false;
    const settings = payload();
    busy(true);
    el('layaFields').disabled = true;
    status('layaSaveStatus', t('laya_saving'));
    try {
      const saved = await api('/api/laya', {method: 'POST', body: JSON.stringify(settings)});
      hydrate(saved);
      window._layaDirty = false;
      status('layaSaveStatus', t('laya_saved'));
      if (!_settingsDirty) {
        const bar = el('settingsUnsavedBar');
        if (bar) bar.style.display = 'none';
      }
      return true;
    } catch (_) {
      status('layaSaveStatus', t('laya_save_failed'), true);
      return false;
    } finally {
      el('layaFields').disabled = false;
      busy(false);
    }
  };

  window.testLaya = async function (kind) {
    if (!loaded || pending || !el('layaForm').reportValidity()) return;
    const text = el('layaSample').value.trim();
    if (kind === 'sample' && !text) { el('layaSample').focus(); return; }
    const target = kind === 'sample' ? 'layaSampleStatus' : 'layaConnectionStatus';
    const version = editVersion;
    busy(true);
    status(target, t('laya_testing'));
    try {
      const result = await api('/api/laya/test', {method: 'POST', body: JSON.stringify({kind, text, settings: payload()})});
      if (version === editVersion) status(target, resultText(result), result.status !== 'ok');
    } catch (_) {
      if (version === editVersion) status(target, t('laya_unavailable'), true);
    } finally { busy(false); }
  };

  window.refreshLayaRecent = async function () {
    try {
      const data = await api('/api/laya');
      renderRecent(data.recent || []);
    } catch (_) { el('layaRecent').textContent = t('laya_load_failed'); }
  };

  // Compose after the Neowow/Gateway wrappers; their closed pane lists do not
  // know about this pane. Hide all panes before delegating to either wrapper.
  const previous = window.switchSettingsSection;
  window.switchSettingsSection = function (name) {
    document.querySelectorAll('#mainSettings .settings-pane').forEach(node => node.classList.remove('active'));
    if (name !== 'laya') { previous(name); return; }
    _settingsSection = 'laya';
    _currentSettingsSection = 'laya';
    document.querySelectorAll('#settingsMenu .side-menu-item').forEach(node => {
      node.classList.toggle('active', node.dataset.settingsSection === 'laya');
    });
    el('settingsPaneLaya').classList.add('active');
    const dropdown = el('settingsSectionDropdown');
    if (dropdown) dropdown.value = 'laya';
    modeHelp();
    loadLayaSettings();
  };
})();
