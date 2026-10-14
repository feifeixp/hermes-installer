"""CDN loading and login/readiness deadline regressions."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_cdn_resources_do_not_block_local_login_scripts():
    import re
    html = (ROOT / "static/index.html").read_text()
    external_scripts = re.findall(r'<script[^>]+src="https://cdn[^>]+>', html)
    assert len(external_scripts) == 5
    assert all('type="text/plain"' in tag and 'data-optional-group=' in tag for tag in external_scripts)
    styles = re.findall(r'<link[^>]+href="https://cdn[^>]+>', html)
    assert len(styles) == 3
    assert all('media="print"' in tag and 'onload=' in tag for tag in styles)
    assert "script.async = true" in html
    assert "script.integrity = source.integrity" in html


@pytest.mark.skipif(not shutil.which("node"), reason="Node required")
def test_gate_deadlines_release_retry_and_preserve_authorization():
    script = r"""
const fs=require('fs'),assert=require('assert/strict'),vm=require('vm');
const src=fs.readFileSync(process.argv[1],'utf8');
const code=src.slice(src.indexOf('  async function fetchGateJSON'),src.indexOf('  window.neowowActivateCodingPlanFromGate'));
let timers=new Map(),next=0,calls=[],shown=[],hidden=[];
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{dataset:{},textContent:''});return elements.get(id)};
const window={neowowShowLoginRequired:o=>shown.push(o||{}),neowowHideBootOverlay:o=>hidden.push(o)};
const context={window,document:{getElementById:$},$,AbortController,
  _bootOverlayHidden:false,_bootOverlayResolving:false,_setWorkspaceInert(){},
  setTimeout(fn,ms){const id=++next;timers.set(id,{fn,ms});return id},clearTimeout(id){timers.delete(id)},
  Date:{now:()=>1000},fetch:()=>{},populateModelDropdown:async()=>{}};
// Avoid the cosmetic 600ms timer while preserving gate request timers.
context.Date.now=(()=>{let n=0;return()=>1000+(++n)*1000})();
vm.createContext(context);vm.runInContext(code,context);
const tick=async()=>{for(let i=0;i<12;i++)await Promise.resolve()};
const expire=async(ms)=>{for(const [id,t] of timers)if(t.ms===ms){timers.delete(id);t.fn()}await tick()};
const response=data=>({ok:true,json:async()=>data});
(async()=>{
 // Even a fetch/body that ignores abort must release the in-flight flag.
 let signal;
 context.fetch=async(url,opts)=>{calls.push(url);signal=opts.signal;return {ok:true,json:()=>new Promise(()=>{})}};
 const stalled=window.neowowResolveBootOverlay();await tick();await expire(7000);await stalled;
 assert.equal(signal.aborted,true);assert.equal(hidden.length,0);assert.equal(shown.at(-1).retry,true);
 context.fetch=async()=>response({hasJwt:false,neowowOnly:true});
 await window.neowowResolveBootOverlay();assert.deepEqual(shown.at(-1),{});
 // Readiness can legitimately outlast HTML's 8s startup watchdog.
 let ready,readinessSignal;
 context.fetch=async(url,opts)=>{
  if(url.includes('onboarding')){readinessSignal=opts.signal;return new Promise(r=>{ready=r})}
  return response({hasJwt:true,neowowOnly:true});
 };
 const pending=window.neowowResolveBootOverlay();await tick();
 assert.equal($('neowowBootOverlay').dataset.statusResolved,'1');
 assert.match($('neowowBootTitle').textContent,/Coding Plan/);
 assert.equal(hidden.length,0);await expire(20000);await pending;
 assert.equal(readinessSignal.aborted,true);assert.match(shown.at(-1).title,/Coding Plan/);assert.equal(shown.at(-1).retry,true);
 // Retrying remains available; a string false never unlocks the workspace.
 context.fetch=async url=>response(url.includes('onboarding')?{chat_ready:'false'}:{hasJwt:true,neowowOnly:true});
 await window.neowowResolveBootOverlay();assert.equal(hidden.length,0);assert.equal(shown.at(-1).activate,true);
 context.fetch=async url=>response(url.includes('onboarding')?{chat_ready:true}:{hasJwt:true,neowowOnly:true});
 await window.neowowResolveBootOverlay();assert.equal(hidden.length,1);assert.equal(hidden[0].success,true);
 let modelEntered=false;
 context.populateModelDropdown=()=>{modelEntered=true;return new Promise(()=>{})};
 const stuckModels=window.neowowResolveBootOverlay();
 for(let i=0;i<50&&!modelEntered;i++)await tick();
 assert.equal(modelEntered,true);await expire(20000);await stuckModels;
 assert.equal(hidden.length,1);assert.match(shown.at(-1).title,/模型列表/);assert.equal(shown.at(-1).retry,true);
 context.populateModelDropdown=async()=>{};
 // Invalid login shape fails closed instead of treating managed mode as false.
 context.fetch=async()=>response({hasJwt:true});
 await window.neowowResolveBootOverlay();assert.equal(hidden.length,1);assert.equal(shown.at(-1).retry,true);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run([shutil.which("node"), "-e", script, str(ROOT / "static/neowow.js")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_desktop_bundle_stamps_version_before_collecting_static_assets(tmp_path):
    import ast
    source = (ROOT.parent / "hermes_installer.spec").read_text()
    nodes = ast.parse(source).body
    start = next(i for i, node in enumerate(nodes) if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_webui_root" for t in node.targets))
    fake_root = tmp_path / "webui"
    (fake_root / "api").mkdir(parents=True)
    stamp = ast.Module(body=[nodes[start + 1]], type_ignores=[])
    namespace = {"_webui_root": fake_root, "VERSION": "1.5.34"}
    exec(compile(stamp, "<spec-version-stamp>", "exec"), namespace)
    assert (fake_root / "api/_version.py").read_text() == "__version__ = 'v1.5.34'\n"
    assert source.index('(_webui_root / "api" / "_version.py")') < source.index('_webui_datas:')


@pytest.mark.skipif(not shutil.which("node"), reason="Node required")
def test_optional_library_loader_keeps_dependency_order_and_integrity():
    import re
    html = (ROOT / "static/index.html").read_text()
    code = re.search(r"\(function loadOptionalLibraries\(\) \{.*?\}\)\(\);", html, re.S).group()
    script = r"""
const assert=require('assert/strict'),vm=require('vm');
const sources=[['prism','core'],['prism','autoload'],['terminal','xterm'],['terminal','fit'],['terminal','links']].map(([group,src])=>({dataset:{optionalGroup:group},src,integrity:'pinned-'+src,crossOrigin:'anonymous'}));
const loaded=[],warns=[];let highlighted=0;
const c={window:{Prism:{highlightAll(){highlighted++}}},console:{warn:m=>warns.push(m)},
 document:{querySelectorAll:()=>sources,createElement:()=>({}),head:{appendChild:s=>loaded.push(s)}}};
vm.createContext(c);vm.runInContext(process.argv[1],c);
const tick=async()=>{for(let i=0;i<10;i++)await Promise.resolve()};
(async()=>{
 await tick();assert.deepEqual(loaded.map(s=>s.src),['core','xterm']);
 assert.equal(loaded[0].integrity,'pinned-core');assert.equal(loaded[0].async,true);
 loaded[0].onload();loaded[1].onload();await tick();
 assert.deepEqual(loaded.map(s=>s.src),['core','xterm','autoload','fit']);
 loaded[2].onload();loaded[3].onerror();await tick();
 assert.equal(highlighted,1);assert.equal(warns.length,1);assert.match(warns[0],/terminal/);
 assert.equal(loaded.some(s=>s.src==='links'),false);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run([shutil.which("node"), "-e", script, code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
