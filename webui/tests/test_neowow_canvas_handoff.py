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
    add(item){this.options.push(item)},dispatchEvent(){},append(item){this.children.push(item)},focus(){}});
  return elements.get(id);
}
global.document={getElementById:element,createElement:()=>({children:[],listeners:{},append(item){this.children.push(item)},addEventListener(name,fn){this.listeners[name]=fn}})};
global.Option=function(text,value){this.text=text;this.value=value};
global.window={};
global.S={activeProfile:'default',session:{profile:'default'},busy:false,pendingFiles:[]};
global.switchPanel=async()=>true;
const sent=[], payloads=[];let briefs=0;
global.send=()=>{sent.push(element('msg').value);element('msg').value='';S.busy=true};
global.fetch=async(url,opts)=>({ok:true,json:async()=>{
  if(url.includes('projects'))return {projects:[{sessionId:'123',title:'作品'}],folders:[],active:{sessionId:'123',title:'作品'},hasMore:false};
  if(url.includes('brief')){briefs++;payloads.push(JSON.parse(opts.body));return {message:'目标画布 123，先确认积分。'}}
  return {results:[{kind:'text',label:'剧本逐字稿 · 初稿',nodeKey:'text1',text:'甲：你终于来了。\n乙：我一直都在。'}],counts:{success:1,pending:0,failed:0},warning:''};
}});
new Function(fs.readFileSync(process.argv[1],'utf8'))();
(async()=>{
  window.neowowCanvasWorkspaceConnected(true);
  await window.neowowCanvasLoadProjects();
  element('neowowCanvasClosed').checked=true;
  element('neowowCanvasPrompt').value='制作产品短片';
  element('neowowCanvasKind').value='video';
  element('neowowCanvasDuration').value='60';element('neowowCanvasAspect').value='16:9';
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
  assert.equal(element('neowowCanvasPrompt').value,'制作产品短片');
  assert.equal(payloads[0].stage,'script');
  element('neowowCanvasPrompt').value='不要发送两次';
  await window.neowowCanvasStartCreation({preventDefault(){}});
  assert.equal(sent.length,1);assert.equal(briefs,1);
  S.busy=false;
  await window.neowowCanvasPrepareClips();
  assert.equal(briefs,1); // No approved script: cannot even prepare production.
  await window.neowowCanvasRefreshResults();
  const scriptButton=element('neowowCanvasResults').children[0].children[2].children[0];
  scriptButton.listeners.click();
  assert.equal(element('neowowCanvasScript').value,'甲：你终于来了。\n乙：我一直都在。');
  assert.equal(element('neowowCanvasScriptConfirmed').checked,false);
  element('neowowCanvasScriptConfirmed').checked=true;window.neowowCanvasScriptApprovalChanged();
  assert.equal(element('neowowCanvasClipsBtn').disabled,false);
  element('neowowCanvasScript').value+='\n旁白：雨停了。';window.neowowCanvasScriptChanged();
  assert.equal(element('neowowCanvasScriptConfirmed').checked,false);
  assert.equal(element('neowowCanvasClipsBtn').disabled,true);
  await window.neowowCanvasPrepareClips();assert.equal(briefs,1);
  element('neowowCanvasScriptConfirmed').checked=true;
  await window.neowowCanvasPrepareClips();
  assert.equal(payloads[1].stage,'clips');assert.equal(payloads[1].scriptConfirmed,true);
  assert.match(payloads[1].script,/旁白：雨停了/);assert.equal(payloads[1].duration,60);
  S.busy=false;
  element('neowowCanvasRevision').value='让对白更口语化';
  window.neowowCanvasScriptChanged();
  assert.equal(element('neowowCanvasScriptConfirmed').checked,false);
  element('neowowCanvasScriptConfirmed').checked=true;
  await window.neowowCanvasPrepareClips();assert.equal(briefs,2);
  await window.neowowCanvasReviseScript();
  assert.equal(payloads[2].stage,'revise');assert.equal(payloads[2].revision,'让对白更口语化');
  assert.equal(element('neowowCanvasScriptConfirmed').checked,false);
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
  assert.equal(element('neowowCanvasScript').value,'');
  assert.equal(element('neowowCanvasScriptConfirmed').checked,false);
  scriptButton.listeners.click(); // A detached old-account result cannot restore the script.
  assert.equal(element('neowowCanvasScript').value,'');
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    result = subprocess.run([shutil.which("node"), "-e", script, str(source)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
