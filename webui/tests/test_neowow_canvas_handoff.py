"""Native chat handoff must preserve drafts, profile scope and request ownership."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="Node required for JS interaction checks")
def test_native_canvas_handoff_preserves_chat_and_discards_stale_results():
    source = Path(__file__).resolve().parents[1] / "static/neowow-canvas.js"
    script = r"""
const fs=require('fs'), assert=require('assert/strict');
const elements=new Map();
function element(id){
  if(!elements.has(id)) elements.set(id,{value:'',checked:false,hidden:false,dataset:{},
    textContent:'',options:[],children:[],setAttribute(){},querySelectorAll(){return []},
    replaceChildren(...items){this.children=items;this.options=items},
    add(item){this.options.push(item)},dispatchEvent(){},append(item){this.children.push(item)}});
  return elements.get(id);
}
global.document={getElementById:element,createElement:()=>({append(){},addEventListener(){}})};
global.Option=function(text,value){this.text=text;this.value=value};
global.window={};
global.S={activeProfile:'default',session:{profile:'default'},busy:false,pendingFiles:[]};
global.switchPanel=async()=>true;
const sent=[];let briefs=0;
global.send=()=>{sent.push(element('msg').value);element('msg').value='';S.busy=true};
global.fetch=async(url,opts)=>({ok:true,json:async()=>{
  if(url.includes('projects'))return {projects:[{sessionId:'123',title:'作品'}],folders:[],active:{sessionId:'123',title:'作品'},hasMore:false};
  if(url.includes('brief')){briefs++;return {message:'目标画布 123，先确认积分。'}}
  return {results:[],counts:{success:0,pending:0,failed:0},warning:''};
}});
new Function(fs.readFileSync(process.argv[1],'utf8'))();
(async()=>{
  window.neowowCanvasWorkspaceConnected(true);
  await window.neowowCanvasLoadProjects();
  element('neowowCanvasClosed').checked=true;
  element('neowowCanvasPrompt').value='制作产品短片';
  element('neowowCanvasKind').value='video';
  element('msg').value='尚未发送的草稿';
  await window.neowowCanvasStartCreation({preventDefault(){}});
  assert.equal(element('msg').value,'尚未发送的草稿');
  assert.equal(briefs,0);assert.equal(sent.length,0);
  element('msg').value='';S.session.profile='other';
  await window.neowowCanvasStartCreation({preventDefault(){}});
  assert.equal(briefs,0);assert.match(element('neowowCanvasWorkStatus').textContent,/其他 Profile/);
  S.session.profile='default';
  await window.neowowCanvasStartCreation({preventDefault(){}});
  assert.deepEqual(sent,['目标画布 123，先确认积分。']);
  assert.equal(element('neowowCanvasPrompt').value,'');
  element('neowowCanvasPrompt').value='不要发送两次';
  await window.neowowCanvasStartCreation({preventDefault(){}});
  assert.equal(sent.length,1);assert.equal(briefs,1);
  S.busy=false;
  let finish;
  global.fetch=()=>new Promise(resolve=>{finish=resolve});
  const pending=window.neowowCanvasRefreshResults();
  window.neowowCanvasWorkspaceConnected(false);
  finish({ok:true,json:async()=>({results:[{kind:'text',text:'旧账号结果'}],counts:{success:1,pending:0,failed:0}})});
  await pending;
  assert.equal(element('neowowCanvasWorkspace').hidden,true);
  assert.equal(element('neowowCanvasResults').children.length,0);
  assert.equal(element('neowowCanvasStartBtn').disabled,true);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    result = subprocess.run([shutil.which("node"), "-e", script, str(source)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
