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


@pytest.mark.skipif(not shutil.which("node"), reason="Node required for JS interaction tests")
@pytest.mark.parametrize("operation", ["status", "save", "clear"])
def test_profile_switch_clears_workspace_and_discards_old_connection_responses(operation):
    static = Path(__file__).resolve().parents[1] / "static"
    script = r"""
const fs=require('fs'), assert=require('assert/strict');
const elements=new Map();
function element(id){
  if(!elements.has(id)) elements.set(id,{value:'',checked:false,hidden:false,dataset:{},
    disabled:false,textContent:'',options:[],children:[],attrs:{},
    setAttribute(k,v){this.attrs[k]=v},querySelectorAll(){return []},focus(){},
    replaceChildren(...items){this.children=items;this.options=items},
    add(item){this.options.push(item)},dispatchEvent(){},append(item){this.children.push(item)}});
  return elements.get(id);
}
global.document={getElementById:element,createElement:()=>({append(){},addEventListener(){}})};
global.Option=function(text,value){this.text=text;this.value=value};
global.window={};global.S={activeProfile:'a'};
const ready={installed:true,runtimeReady:true,connected:true,bindingAvailable:true};
global.fetch=async url=>({ok:true,json:async()=>url.includes('/canvas/projects')
  ? {projects:[{sessionId:'123',title:'Profile A画布'}],folders:[],active:{sessionId:'123',title:'Profile A画布'},hasMore:false}
  : ready});
new Function(fs.readFileSync(process.argv[2],'utf8'))();
const source=fs.readFileSync(process.argv[1],'utf8');
new Function('$',source.slice(source.indexOf('  let canvasState ='),source.indexOf('  async function loadSkillsStatus()')))(element);
(async()=>{
  await window.neowowCanvasRefresh();
  await window.neowowCanvasLoadProjects();
  element('neowowCanvasResults').children.push({textContent:'Profile A结果'});
  element('neowowCanvasPrompt').value='Profile A创作草稿';
  const requests=[];
  global.fetch=(url,opts)=>new Promise(resolve=>requests.push({url,opts,resolve}));
  let pending;
  if(process.argv[3]==='save'){
    element('neowowCanvasKey').value='dummy-a-secret';
    pending=window.neowowCanvasSave({preventDefault(){}});
  }else if(process.argv[3]==='clear') pending=window.neowowCanvasClear();
  else pending=window.neowowCanvasRefresh();
  assert.equal(requests.length,1);
  S.activeProfile='b';window.neowowCanvasProfileChanged();
  assert.equal(requests.length,2);
  assert.equal(element('neowowCanvasWorkspace').hidden,true);
  assert.equal(element('neowowCanvasResults').children.length,0);
  assert.equal(element('neowowCanvasPrompt').value,'');
  assert.equal(element('neowowCanvasKey').value,'');
  // Returning to A still must not accept the first A request's response.
  S.activeProfile='a';window.neowowCanvasProfileChanged();
  assert.equal(requests.length,3);
  const connected=process.argv[3]!=='clear';
  requests[0].resolve({ok:true,json:async()=>({...ready,connected})});
  await pending;
  assert.equal(element('neowowCanvasBadge').textContent,'检查中');
  assert.equal(element('neowowCanvasWorkspace').hidden,true);
  assert.equal(element('neowowCanvasRefreshBtn').disabled,true);
  requests[2].resolve({ok:true,json:async()=>({...ready,connected:false})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(element('neowowCanvasBadge').textContent,'等待连接');
  assert.equal(element('neowowCanvasSaveBtn').disabled,false);
  requests[1].resolve({ok:true,json:async()=>ready});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(element('neowowCanvasBadge').textContent,'等待连接');
  assert.equal(element('neowowCanvasWorkspace').hidden,true);
  assert.equal(element('neowowCanvasResults').children.length,0);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    result = subprocess.run([shutil.which("node"), "-e", script,
                             str(static / "neowow.js"), str(static / "neowow-canvas.js"), operation],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    # The actual cookie-switch response must trigger the reset before any awaits.
    panels = (static / "panels.js").read_text()
    assignment = panels.index("S.activeProfile = data.active || name;")
    assert "neowowCanvasProfileChanged()" in panels[assignment:assignment + 220]
