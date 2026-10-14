"""Observable canvas connection states and duplicate-submit regression checks."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="Node required for JS interaction tests")
def test_canvas_connection_interactions():
    source = Path(__file__).resolve().parents[1] / "static/neowow.js"
    script = r"""
const fs = require('fs');
const assert = require('assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const code = source.slice(source.indexOf('  let canvasState ='), source.indexOf('  async function loadSkillsStatus()'));
const elements = new Map();
let focused = '';
const $ = id => {
  if (!elements.has(id)) elements.set(id, {value:'', dataset:{}, hidden:false,
    disabled:false, textContent:'', attrs:{}, setAttribute(k,v){this.attrs[k]=v},
    focus(){if (!this.disabled) focused=id}});
  return elements.get(id);
};
global.window = {};
const ready = {installed:true,runtimeReady:true,connected:false,bindingAvailable:true};
global.fetch = async()=>({ok:true,json:async()=>ready});
new Function('$', code)($);
(async()=>{
  await window.neowowCanvasRefresh();
  assert.equal($('neowowCanvasSetup').hidden,false);
  assert.equal($('neowowCanvasSaveBtn').disabled,false);
  // A repeated click while verification is pending must never write twice.
  let resolveSave;
  let requests = 0;
  global.fetch = ()=>{requests++;return new Promise(resolve=>{resolveSave=resolve})};
  $('neowowCanvasKey').value = 'dummy-form-secret';
  const pending = window.neowowCanvasSave({preventDefault(){}});
  assert.equal($('neowowCanvasKey').value,'');
  assert.equal($('neowowCanvasSaveBtn').disabled,true);
  assert.equal($('neowowCanvasSaveBtn').textContent,'正在验证…');
  await window.neowowCanvasSave({preventDefault(){}});
  await window.neowowCanvasRefresh();
  assert.equal(requests,1);
  resolveSave({ok:true,json:async()=>({...ready,connected:true})});
  await pending;
  assert.equal($('neowowCanvasSetup').hidden,true);
  assert.equal($('neowowCanvasLinked').hidden,false);
  assert.equal($('neowowCanvasBadge').textContent,'已保存连接');
  assert.equal($('neowowCanvasStatus').dataset.tone,'success');
  assert.equal(focused,'neowowCanvasEditBtn');
  // Failed replacement keeps the connected account and lets the user retry.
  window.neowowCanvasEditKey();
  assert.equal($('neowowCanvasSetup').hidden,false);
  assert.equal(focused,'neowowCanvasKey');
  $('neowowCanvasKey').value = 'dummy-replacement';
  global.fetch = async()=>({ok:false,json:async()=>({error:'模拟验证失败，请重试。'})});
  await window.neowowCanvasSave({preventDefault(){}});
  assert.equal($('neowowCanvasStatus').dataset.tone,'error');
  assert.equal($('neowowCanvasBadge').textContent,'已保存连接');
  assert.equal($('neowowCanvasSaveBtn').disabled,false);
  assert.equal($('neowowCanvasKey').value,'');
  window.neowowCanvasEditKey();
  assert.equal($('neowowCanvasSetup').hidden,true);
  // Unsupported shared instances show guidance and never enable saving.
  global.fetch = async()=>({ok:true,json:async()=>({...ready,bindingAvailable:false})});
  await window.neowowCanvasRefresh();
  assert.equal($('neowowCanvasSetup').hidden,true);
  assert.equal($('neowowCanvasLinked').hidden,true);
  assert.equal($('neowowCanvasSaveBtn').disabled,true);
  assert.match($('neowowCanvasStatus').textContent,/专属 Agent/);
  // Network failure does not silently restore an actionable connection form.
  global.fetch = async()=>{throw new Error('offline')};
  await window.neowowCanvasRefresh();
  assert.equal($('neowowCanvasStatus').dataset.tone,'error');
  assert.equal($('neowowCanvasRefreshBtn').disabled,false);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    result = subprocess.run([shutil.which("node"), "-e", script, str(source)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
