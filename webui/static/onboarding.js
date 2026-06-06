const ONBOARDING={status:null,step:0,steps:['login','plan','persona'],form:{provider:'neowow-coding-plan',workspace:'',model:'',password:'',apiKey:'',baseUrl:'',loginMethod:'',persona:'',personaContent:''},active:false,probe:{status:'idle',error:null,detail:'',models:null,probedKey:''},presets:null};

// ── Onboarding base-URL probe (#1499) ───────────────────────────────────────
// Probes <base_url>/models so the wizard can validate the configured endpoint
// before persisting AND populate the model dropdown from the live catalog.
// Probe state lives on ONBOARDING.probe; the dropdown render and the
// nextOnboardingStep gate both consult it.

let _onboardingProbeTimer=null;

function _onboardingProbeKey(provider,baseUrl,apiKey){
  return `${provider||''}|${(baseUrl||'').trim().replace(/\/+$/,'')}|${apiKey||''}`;
}

function _setOnboardingProbeState(patch){
  ONBOARDING.probe={...ONBOARDING.probe,...patch};
  // Re-render body so probe status / model dropdown reflect new state.
  _renderOnboardingBody();
}

async function _runOnboardingProbe({force=false}={}){
  const provider=ONBOARDING.form.provider;
  const cat=_getOnboardingSetupProvider(provider);
  if(!cat||!cat.requires_base_url){
    _setOnboardingProbeState({status:'idle',error:null,detail:'',models:null,probedKey:''});
    return ONBOARDING.probe;
  }
  const baseUrl=(ONBOARDING.form.baseUrl||'').trim();
  if(!baseUrl){
    _setOnboardingProbeState({status:'idle',error:null,detail:'',models:null,probedKey:''});
    return ONBOARDING.probe;
  }
  const apiKey=(ONBOARDING.form.apiKey||'').trim();
  const key=_onboardingProbeKey(provider,baseUrl,apiKey);
  if(!force&&ONBOARDING.probe.probedKey===key&&ONBOARDING.probe.status!=='probing'){
    return ONBOARDING.probe;
  }
  _setOnboardingProbeState({status:'probing',error:null,detail:'',probedKey:key});
  try{
    const res=await api('/api/onboarding/probe',{method:'POST',body:JSON.stringify({provider,base_url:baseUrl,api_key:apiKey||undefined})});
    if(res&&res.ok){
      _setOnboardingProbeState({status:'ok',error:null,detail:'',models:Array.isArray(res.models)?res.models:[],probedKey:key});
      // If the user hasn't picked a model yet (or their pick is no longer in
      // the list), default to the first probed model so Continue isn't blocked
      // on an empty selection.
      const stillPresent=ONBOARDING.form.model&&(res.models||[]).some(m=>m.id===ONBOARDING.form.model);
      if(!stillPresent&&(res.models||[]).length>0){
        ONBOARDING.form.model=res.models[0].id;
        _renderOnboardingBody();
      }
    }else{
      const err=(res&&res.error)||'unreachable';
      const detail=(res&&res.detail)||'';
      _setOnboardingProbeState({status:'error',error:err,detail,models:null,probedKey:key});
    }
  }catch(e){
    _setOnboardingProbeState({status:'error',error:'unreachable',detail:(e&&e.message)||String(e),models:null,probedKey:key});
  }
  return ONBOARDING.probe;
}

function _scheduleOnboardingProbe(){
  if(_onboardingProbeTimer)clearTimeout(_onboardingProbeTimer);
  _onboardingProbeTimer=setTimeout(()=>{_runOnboardingProbe();},400);
}

function _onboardingProbeMessage(probe){
  if(!probe||probe.status==='idle')return '';
  if(probe.status==='probing')return t('onboarding_probe_probing')||'Testing connection…';
  if(probe.status==='ok'){
    const n=(probe.models||[]).length;
    const tmpl=t('onboarding_probe_ok')||'Connected. {n} model(s) available.';
    return tmpl.replace('{n}',String(n));
  }
  // status === 'error'
  const errKey='onboarding_probe_error_'+probe.error;
  const localized=t(errKey);
  // i18n.js's `t()` returns the key itself when missing — fall back to a generic message.
  const heading=(localized&&localized!==errKey)?localized:(t('onboarding_probe_error_generic')||'Could not reach the configured base URL.');
  const detail=probe.detail?` (${probe.detail})`:'';
  return heading+detail;
}

function _getOnboardingSetupProviders(){
  return (((ONBOARDING.status||{}).setup||{}).providers)||[];
}

function _getOnboardingSetupProvider(id){
  return _getOnboardingSetupProviders().find(p=>p.id===id)||null;
}

function _getOnboardingSetupCategories(){
  return (((ONBOARDING.status||{}).setup||{}).categories)||[];
}

/** Render the provider <select> with <optgroup> per category. */
function _renderProviderSelectOptions(selectedId){
  const providers=_getOnboardingSetupProviders();
  const categories=_getOnboardingSetupCategories();
  const provMap={};
  providers.forEach(p=>{provMap[p.id]=p;});
  if(!categories.length){
    // Fallback: flat list when no categories are available.
    return providers.map(p=>`<option value="${esc(p.id)}">${esc(p.label)}${p.quick?' — '+esc(t('onboarding_quick_setup_badge')):''}</option>`).join('');
  }
  return categories.map(cat=>{
    const opts=cat.providers.map(pid=>{
      const p=provMap[pid];
      if(!p)return '';
      return `<option value="${esc(p.id)}"${p.id===selectedId?' selected':''}>${esc(p.label)}${p.quick?' — '+esc(t('onboarding_quick_setup_badge')):''}</option>`;
    }).join('');
    return `<optgroup label="${esc(t('provider_category_'+cat.id)||cat.label)}">${opts}</optgroup>`;
  }).join('');
}

function _getOnboardingCurrentSetup(){
  return (((ONBOARDING.status||{}).setup||{}).current)||{};
}

function _onboardingStepMeta(key){
  return ({
    login:{title:t('onboarding_step_login_title'),desc:t('onboarding_step_login_desc')},
    plan:{title:t('onboarding_step_plan_title'),desc:t('onboarding_step_plan_desc')},
    persona:{title:t('onboarding_step_persona_title'),desc:t('onboarding_step_persona_desc')}
  })[key];
}

function _renderOnboardingSteps(){
  const wrap=$('onboardingSteps');
  if(!wrap)return;
  wrap.innerHTML='';
  ONBOARDING.steps.forEach((key,idx)=>{
    const meta=_onboardingStepMeta(key);
    const item=document.createElement('div');
    item.className='onboarding-step'+(idx===ONBOARDING.step?' active':idx<ONBOARDING.step?' done':'');
    item.innerHTML=`<div class="onboarding-step-index">${idx+1}</div><div><div class="onboarding-step-title">${meta.title}</div><div class="onboarding-step-desc">${meta.desc}</div></div>`;
    wrap.appendChild(item);
  });
}

function _setOnboardingNotice(msg,kind='info'){
  const el=$('onboardingNotice');
  if(!el)return;
  if(!msg){el.style.display='none';el.textContent='';el.className='onboarding-status';return;}
  el.style.display='block';
  el.className='onboarding-status '+kind;
  el.textContent=msg;
}

function _getOnboardingWorkspaceChoices(){
  const items=((ONBOARDING.status||{}).workspaces||{}).items||[];
  return items.length?items:[{name:'Home',path:ONBOARDING.form.workspace||''}];
}

function _getOnboardingProviderModelChoices(){
  const provider=_getOnboardingSetupProvider(ONBOARDING.form.provider);
  // Probe-discovered models (#1499) take precedence over the static catalog
  // for providers with requires_base_url=True.  The catalog ships an empty
  // list for self-hosted providers (lmstudio, ollama, custom) — without the
  // probe the user had nothing to pick from.
  if(provider&&provider.requires_base_url&&ONBOARDING.probe&&ONBOARDING.probe.status==='ok'&&Array.isArray(ONBOARDING.probe.models)&&ONBOARDING.probe.models.length){
    return ONBOARDING.probe.models;
  }
  return provider?(provider.models||[]):[];
}

function _renderOnboardingBaseUrlField(showBaseUrl){
  // Renders the base_url input PLUS the probe status banner / Test button
  // when the active provider has requires_base_url=True (#1499).  Returns
  // the empty string when the active provider does not require a base URL,
  // so the existing call sites can continue to template-interpolate this in
  // place of the previous inline `<label …>` snippet.
  if(!showBaseUrl)return '';
  const probe=ONBOARDING.probe||{status:'idle'};
  const msg=_onboardingProbeMessage(probe);
  let banner='';
  if(msg){
    const cls={ok:'onboarding-probe-ok',probing:'onboarding-probe-probing',error:'onboarding-probe-error'}[probe.status]||'';
    banner=`<p class="onboarding-copy onboarding-probe-banner ${cls}">${esc(msg)}</p>`;
  }
  const testBtnLabel=t('onboarding_probe_test_button')||'Test connection';
  const testBtnDisabled=(probe.status==='probing')?'disabled':'';
  return `<label class="onboarding-field"><span>${t('onboarding_base_url_label')}</span><input id="onboardingBaseUrlInput" value="${esc(ONBOARDING.form.baseUrl||'')}" placeholder="${t('onboarding_base_url_placeholder')}" oninput="ONBOARDING.form.baseUrl=this.value;_scheduleOnboardingProbe()" onblur="_runOnboardingProbe()"></label><div class="onboarding-probe-row"><button type="button" class="onboarding-probe-btn" ${testBtnDisabled} onclick="_runOnboardingProbe({force:true})">${esc(testBtnLabel)}</button></div>${banner}`;
}

function _renderOnboardingApiKeyField(){
  // Renders the API-key input.  For providers flagged `key_optional` in the
  // setup catalog (lmstudio, ollama, custom — typically self-hosted servers
  // that run keyless by default), the field shows an "(optional)" hint and
  // empty input is accepted on Continue.  Pre-#1499-third-sub-bug-fix the
  // wizard required a non-empty string here even for keyless installs, which
  // forced users to type random gibberish to clear onboarding.
  const provider=_getOnboardingSetupProvider(ONBOARDING.form.provider);
  const keyOptional=!!(provider&&provider.key_optional);
  const labelKey=keyOptional?'onboarding_api_key_label_optional':'onboarding_api_key_label';
  const placeholderKey=keyOptional?'onboarding_api_key_placeholder_optional':'onboarding_api_key_placeholder';
  const helpHtml=keyOptional?`<p class="onboarding-copy onboarding-api-key-help">${esc(t('onboarding_api_key_help_keyless')||'')}</p>`:'';
  return `<label class="onboarding-field" id="onboardingApiKeyField"><span>${t(labelKey)}</span><input id="onboardingApiKeyInput" type="password" value="${esc(ONBOARDING.form.apiKey||'')}" placeholder="${t(placeholderKey)}" oninput="ONBOARDING.form.apiKey=this.value" onblur="_runOnboardingProbe()"></label>${helpHtml}`;
}

function _getOnboardingSelectedModel(){
  return ONBOARDING.form.model||'';
}

function _renderOnboardingModelField(){
  const choices=_getOnboardingProviderModelChoices();
  if(ONBOARDING.form.provider==='custom'){
    return `<label class="onboarding-field"><span>${t('onboarding_model_label')}</span><input id="onboardingModelInput" value="${esc(_getOnboardingSelectedModel())}" placeholder="${t('onboarding_custom_model_placeholder')}" oninput="ONBOARDING.form.model=this.value"></label><p class="onboarding-copy">${t('onboarding_custom_model_help')}</p>`;
  }
  const options=choices.map(m=>`<option value="${esc(m.id)}">${esc(m.label)}</option>`).join('');
  return `<label class="onboarding-field"><span>${t('onboarding_model_label')}</span><select id="onboardingModelSelect" onchange="ONBOARDING.form.model=this.value">${options}</select></label><p class="onboarding-copy">${t('onboarding_workspace_help')}</p>`;
}

function _renderOnboardingProviderOAuthField(provider){
  if(!provider||provider.oauth_provider!=='anthropic')return '';
  return `<div class="onboarding-oauth-card onboarding-oauth-pending" style="margin-top:12px">
    <div class="onboarding-oauth-icon">🔑</div>
    <div style="flex:1">
      <strong>Use Claude Code OAuth instead</strong>
      <p style="margin-top:6px;color:var(--muted);font-size:13px"><strong>Claude Code subscription credentials are not the same as an Anthropic API key.</strong> Use this path only when you want Hermes to use Claude Code credentials already available on the server, or start a short polling flow while you complete <code>claude setup-token</code> on the host.</p>
      <div style="margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap"><button class="sm-btn" id="anthropicOAuthBtn" onclick="startAnthropicOAuth()" type="button">Login with Claude Code</button></div>
      <div id="anthropicOAuthFlow" style="display:none;margin-top:12px"></div>
    </div>
  </div>`;
}

function _providerStatusLabel(system){
  if(system.chat_ready) return t('onboarding_check_provider_ready');
  if(system.provider_configured) return t('onboarding_check_provider_partial');
  return t('onboarding_check_provider_pending');
}

function _renderOnboardingApiKeyForm(){
  const providers=_getOnboardingSetupProviders();
  const sel=ONBOARDING.form.provider;
  const opts=providers.map(p=>`<option value="${p.id}" ${p.id===sel?'selected':''}>${p.label||p.id}</option>`).join('');
  return `<div class="onboarding-field"><label>Provider</label>
      <select id="onboardingProviderSelect">${opts}</select></div>
    <div class="onboarding-field"><label>API Key</label>
      <input id="onboardingApiKeyInput" type="password" autocomplete="off" placeholder="sk-..."></div>
    <div class="onboarding-field"><label>Base URL (optional)</label>
      <input id="onboardingBaseUrlInput" type="text" placeholder="https://..."></div>`;
}
function toggleOnboardingApiKey(){
  ONBOARDING.form.loginMethod = ONBOARDING.form.loginMethod==='apikey' ? '' : 'apikey';
  _renderOnboardingBody();
}
async function startOnboardingLogin(){
  try{
    const returnUrl=location.origin+'/api/neowow/oauth-callback';
    const r=await api('/api/neowow/oauth/launch',{method:'POST',body:JSON.stringify({return_url:returnUrl})});
    if(r&&r.url&&!r.ok){ window.open(r.url,'_blank'); }
    _setOnboardingNotice(t('onboarding_login_waiting'),'info');
    _pollOnboardingLogin();
  }catch(e){ _setOnboardingNotice(e.message||String(e),'warn'); }
}
let _onbLoginTimer=null;
async function _pollOnboardingLogin(){
  try{
    const s=await api('/api/neowow/status');
    if(s&&s.hasJwt){
      ONBOARDING.form.loginMethod='neowow';
      ONBOARDING.status.neowow=s;
      if(_onbLoginTimer){clearTimeout(_onbLoginTimer);_onbLoginTimer=null;}
      _renderOnboardingBody();
      return;
    }
  }catch(e){}
  _onbLoginTimer=setTimeout(_pollOnboardingLogin,3000);
}

async function _loadOnboardingPresets(){
  try{
    const r=await api('/api/personas/presets');
    ONBOARDING.presets=Array.isArray(r)?r:(r&&r.presets)||[];
  }catch(e){ ONBOARDING.presets=[]; }
  if(ONBOARDING.steps[ONBOARDING.step]==='persona') _renderOnboardingBody();
}
function selectOnboardingPersona(id){
  ONBOARDING.form.persona=id;
  if(id&&id!=='__custom__'){
    const p=(ONBOARDING.presets||[]).find(x=>x.id===id);
    ONBOARDING.form.personaContent=p?p.content:'';
  }else{
    ONBOARDING.form.personaContent='';
  }
  _renderOnboardingBody();
}

function _renderOnboardingBody(){
  const body=$('onboardingBody');
  if(!body||!ONBOARDING.status)return;
  const key=ONBOARDING.steps[ONBOARDING.step];
  const system=ONBOARDING.status.system||{};
  const settings=ONBOARDING.status.settings||{};
  const setup=ONBOARDING.status.setup||{};
  const nextBtn=$('onboardingNextBtn');
  const backBtn=$('onboardingBackBtn');
  if(backBtn) backBtn.style.display=ONBOARDING.step>0?'':'none';
  if(nextBtn) nextBtn.textContent=key==='persona'?t('onboarding_open'):t('onboarding_continue');

  if(key==='login'){
    const ns=ONBOARDING.status.neowow||{};
    const signedIn=!!ns.hasJwt;
    const m=ONBOARDING.form.loginMethod;
    _setOnboardingNotice(signedIn?t('onboarding_login_done'):t('onboarding_login_required'), signedIn?'success':'info');
    body.innerHTML=`
      <div class="onboarding-welcome">${t('onboarding_title')}</div>
      <h3 class="onboarding-h">${t('onboarding_login_heading')}</h3>
      <p class="onboarding-sub">${t('onboarding_login_sub')}</p>
      <div class="onboarding-feat"><span class="of-ic">🧩</span><span>${t('onboarding_login_feat_coding')}</span></div>
      <div class="onboarding-feat"><span class="of-ic">🎨</span><span>${t('onboarding_login_feat_media')}</span></div>
      <div class="onboarding-feat"><span class="of-ic">🛒</span><span>${t('onboarding_login_feat_market')}</span></div>
      <button class="onboarding-cta ${signedIn?'is-done':''}" id="onboardingLoginBtn" onclick="startOnboardingLogin()" ${signedIn?'disabled':''}>${signedIn?'✓ '+t('onboarding_login_done'):t('onboarding_login_btn')}</button>
      <button class="onboarding-alt" id="onboardingApiKeyToggle" onclick="toggleOnboardingApiKey()">${t('onboarding_login_apikey')}</button>
      <div id="onboardingApiKeyForm" style="display:${m==='apikey'?'block':'none'}">${_renderOnboardingApiKeyForm()}</div>
      <p class="onboarding-foot">${t('onboarding_login_required')}</p>`;
    return;
  }

  if(key==='plan'){
    const ns=ONBOARDING.status.neowow||{};
    const plans=(ONBOARDING.status.setup&&ONBOARDING.status.setup.plans)||[];
    _setOnboardingNotice('', 'info');
    const acct=ns.hasJwt?`<div class="onboarding-acct">${ns.points!=null?('积分 '+ns.points):'已登录'}</div>`:'';
    const buyCards=plans.map(p=>`<div class="onboarding-plan"><div class="op-name">${p.name||''}</div><div class="op-price">${p.price||''}</div><button class="onboarding-plan-btn" onclick="window.open('${(p.buy_url||'').replace(/'/g,'')}','_blank')">${t('onboarding_plan_buy')}</button></div>`).join('');
    body.innerHTML=`${acct}
      <h3 class="onboarding-h">${t('onboarding_plan_heading')}</h3>
      <p class="onboarding-sub">${t('onboarding_plan_sub')}</p>
      <div class="onboarding-plans">
        <div class="onboarding-plan is-hot"><div class="op-badge">${t('onboarding_plan_trial')}</div><div class="op-price">¥0</div><button class="onboarding-plan-btn solid" onclick="nextOnboardingStep()">${t('onboarding_plan_trial_btn')}</button></div>
        ${buyCards||''}
      </div>
      ${plans.length?'':('<p class="onboarding-foot">'+t('onboarding_plan_unreachable')+'</p>')}`;
    return;
  }

  if(key==='persona'){
    _setOnboardingNotice('', 'info');
    if(ONBOARDING.presets===null){ _loadOnboardingPresets(); }
    const presets=ONBOARDING.presets||[];
    const shown=presets.slice(0,5);
    const cards=shown.map(p=>`<div class="onboarding-persona ${ONBOARDING.form.persona===p.id?'sel':''}" onclick="selectOnboardingPersona('${p.id.replace(/'/g,'')}')"><div class="op-pname">${p.name}</div><div class="op-prole">${(p.summary||'').slice(0,12)}</div></div>`).join('');
    body.innerHTML=`
      <h3 class="onboarding-h">${t('onboarding_persona_heading')}</h3>
      <p class="onboarding-sub">${t('onboarding_persona_sub')}</p>
      <div class="onboarding-personas">
        ${cards}
        <div class="onboarding-persona custom ${ONBOARDING.form.persona==='__custom__'?'sel':''}" onclick="selectOnboardingPersona('__custom__')">${t('onboarding_persona_custom')}</div>
      </div>
      <div class="onboarding-foot">${presets.length>5?('+ '+(presets.length-5)+' · '+t('onboarding_persona_all')):''}</div>
      <button class="onboarding-cta" onclick="nextOnboardingStep()">${t('onboarding_launch_btn')}</button>
      <button class="onboarding-alt" onclick="selectOnboardingPersona('');nextOnboardingStep()">${t('onboarding_persona_skip')}</button>`;
    return;
  }
}

function _getOnboardingPasswordSummaryKey(settings){
  const hasExistingPassword=!!(settings&&settings.password_enabled);
  const hasNewPassword=!!((ONBOARDING.form.password||'').trim());
  if(hasNewPassword) return hasExistingPassword?'onboarding_password_will_replace':'onboarding_password_will_enable';
  return hasExistingPassword?'onboarding_password_keep_existing':'onboarding_password_remains_disabled';
}

function syncOnboardingWorkspaceSelect(value){
  ONBOARDING.form.workspace=value;
  const input=$('onboardingWorkspaceInput');
  if(input) input.value=value;
}

function syncOnboardingProvider(value){
  const provider=_getOnboardingSetupProvider(value);
  ONBOARDING.form.provider=value;
  if(provider){
    if(!ONBOARDING.form.model || !_getOnboardingProviderModelChoices().some(m=>m.id===ONBOARDING.form.model) || value==='custom'){
      ONBOARDING.form.model=provider.default_model||'';
    }
    if(provider.requires_base_url){
      ONBOARDING.form.baseUrl=ONBOARDING.form.baseUrl||provider.default_base_url||'';
    }else{
      ONBOARDING.form.baseUrl=provider.default_base_url||'';
    }
  }
  _renderOnboardingBody();
}

async function loadOnboardingWizard(){
  try{
    const status=await api('/api/onboarding/status');
    ONBOARDING.status=status;
    const current=((status.setup||{}).current)||{};
    ONBOARDING.form.provider=current.provider||'openrouter';
    ONBOARDING.form.workspace=(status.workspaces&&status.workspaces.last)||status.settings.default_workspace||'';
    ONBOARDING.form.model=status.settings.default_model||current.model||'';
    ONBOARDING.form.password='';
    ONBOARDING.form.apiKey='';
    ONBOARDING.form.baseUrl=current.base_url||'';
    ONBOARDING.active=!status.completed;
    if(!ONBOARDING.active) return false;
    $('onboardingOverlay').style.display='flex';
    _renderOnboardingSteps();
    _renderOnboardingBody();
    return true;
  }catch(e){
    console.warn('onboarding status failed',e);
    return false;
  }
}

function prevOnboardingStep(){
  if(ONBOARDING.step===0)return;
  ONBOARDING.step--;
  _renderOnboardingSteps();
  _renderOnboardingBody();
}

async function _saveOnboardingProviderSetup(){
  const provider=(ONBOARDING.form.provider||'').trim();
  const model=(ONBOARDING.form.model||'').trim();
  const apiKey=(ONBOARDING.form.apiKey||'').trim();
  const baseUrl=(ONBOARDING.form.baseUrl||'').trim();
  const current=_getOnboardingCurrentSetup();
  const isUnchanged=current.provider===provider&&((current.model||'')===model)&&((current.base_url||'')===baseUrl);
  // Skip the POST when nothing changed.  We also skip when the provider is
  // unsupported/OAuth-based and already working — chat_ready may be false for
  // providers not in the quick-setup list (e.g. minimax-cn) even though they are
  // fully configured.  Posting in that case would either be a no-op (the server
  // just marks complete for unsupported providers) or could silently overwrite
  // config.yaml if the user accidentally changed the provider dropdown.
  const currentIsOauth=!!(ONBOARDING.status&&ONBOARDING.status.setup&&ONBOARDING.status.setup.current_is_oauth);
  if(isUnchanged && !apiKey && ((ONBOARDING.status.system||{}).chat_ready || currentIsOauth)) return;
  const body={provider,model};
  if(apiKey) body.api_key=apiKey;
  if(baseUrl) body.base_url=baseUrl;
  const status=await api('/api/onboarding/setup',{method:'POST',body:JSON.stringify(body)});
  ONBOARDING.status=status;
}

async function _saveOnboardingDefaults(){
  // Workspace: keep an existing default if present; else use the first known
  // workspace; else let the backend default stand. Never block first run on it.
  const st=ONBOARDING.status||{};
  let workspace=(st.settings&&st.settings.default_workspace)||'';
  if(!workspace){
    const choices=_getOnboardingWorkspaceChoices();
    workspace=(choices[0]&&choices[0].path)||'';
  }
  const body={};
  if(workspace) body.default_workspace=workspace;
  if(Object.keys(body).length){
    try{ await api('/api/settings',{method:'POST',body:JSON.stringify(body)}); }catch(e){}
  }
}

async function _finishOnboarding(){
  // API-key path: persist the provider config the user entered on step 1.
  if(ONBOARDING.form.loginMethod==='apikey'){
    await _saveOnboardingProviderSetup();
  }
  await _saveOnboardingDefaults();
  // Apply the chosen preset persona to ~/.hermes/SOUL.md (skip when none/blank).
  const pc=(ONBOARDING.form.personaContent||'').trim();
  if(ONBOARDING.form.persona && ONBOARDING.form.persona!=='__custom__' && pc){
    try{ await api('/api/memory/write',{method:'POST',body:JSON.stringify({section:'soul',content:pc})}); }catch(e){}
  }
  const done=await api('/api/onboarding/complete',{method:'POST',body:'{}'});
  ONBOARDING.status=done;
  ONBOARDING.active=false;
  $('onboardingOverlay').style.display='none';
  showToast(t('onboarding_complete'));
  await loadWorkspaceList();
  if(typeof renderSessionList==='function') await renderSessionList();
  if(!S.session && typeof newSession==='function'){
    await newSession(true);
    await renderSessionList();
  }
}

async function skipOnboarding(){
  try{
    // Mark onboarding completed server-side without changing any config
    await api('/api/onboarding/complete',{method:'POST',body:'{}'});
    ONBOARDING.active=false;
    $('onboardingOverlay').style.display='none';
    showToast(t('onboarding_skipped')||'Setup skipped');
  }catch(e){
    _setOnboardingNotice((e.message||String(e)),'warn');
  }
}

async function nextOnboardingStep(){
  try{
    const curKey=ONBOARDING.steps[ONBOARDING.step];
    if(curKey==='login'){
      const signedIn=!!((ONBOARDING.status.neowow||{}).hasJwt);
      if(ONBOARDING.form.loginMethod==='apikey'){
        ONBOARDING.form.provider=(($('onboardingProviderSelect')||{}).value||ONBOARDING.form.provider||'').trim();
        ONBOARDING.form.apiKey=(($('onboardingApiKeyInput')||{}).value||'').trim();
        ONBOARDING.form.baseUrl=(($('onboardingBaseUrlInput')||{}).value||'').trim();
        if(!ONBOARDING.form.apiKey) throw new Error(t('onboarding_login_required'));
        // api-key path skips the plan step
        ONBOARDING.step=ONBOARDING.steps.indexOf('persona');
        _renderOnboardingSteps(); _renderOnboardingBody(); return;
      }
      if(!signedIn) throw new Error(t('onboarding_login_required'));
    }
    if(ONBOARDING.step===ONBOARDING.steps.length-1){
      await _finishOnboarding();
      return;
    }
    ONBOARDING.step++;
    _renderOnboardingSteps();
    _renderOnboardingBody();
  }catch(e){
    _setOnboardingNotice(e.message||String(e),'warn');
  }
}

/* ── Codex OAuth device-code flow ── */
let _codexOAuthPollTimer=null;
let _codexOAuthFlowId=null;

function _clearCodexOAuthPoll(){
  if(_codexOAuthPollTimer){clearTimeout(_codexOAuthPollTimer);_codexOAuthPollTimer=null;}
}

function _setCodexOAuthButton(enabled){
  const btn=$('codexOAuthBtn');
  if(btn){btn.disabled=!enabled;btn.textContent=enabled?t('oauth_login_codex'):'...';}
}

async function copyCodexOAuthCode(code){
  try{
    await navigator.clipboard.writeText(code||'');
    showToast('Code copied');
  }catch(e){
    showToast(code||'');
  }
}

async function cancelCodexOAuth(){
  const flowDiv=$('codexOAuthFlow');
  const flowId=_codexOAuthFlowId;
  _clearCodexOAuthPoll();
  _codexOAuthFlowId=null;
  if(flowId){
    try{await api('/api/onboarding/oauth/cancel',{method:'POST',body:JSON.stringify({flow_id:flowId})});}catch(e){}
  }
  _setCodexOAuthButton(true);
  if(flowDiv){
    flowDiv.innerHTML=`<div class="onboarding-oauth-card"><div class="onboarding-oauth-icon">⏹</div><div><strong>OAuth login cancelled</strong><p style="margin-top:6px;color:var(--muted);font-size:13px">Start again whenever you're ready.</p></div></div>`;
  }
}

function _renderCodexOAuthTerminal(status,message){
  const flowDiv=$('codexOAuthFlow');
  if(!flowDiv)return;
  const ok=status==='success';
  const icon=ok?'✅':status==='expired'?'⌛':status==='cancelled'?'⏹':'❌';
  const title=ok?t('oauth_codex_success'):(status==='expired'?t('oauth_codex_expired'):(status==='cancelled'?'OAuth login cancelled':t('oauth_codex_error')));
  flowDiv.innerHTML=`
    <div class="onboarding-oauth-card ${ok?'onboarding-oauth-ready':''}" ${ok?'':'style="border-color:var(--error,#e55)"'}>
      <div class="onboarding-oauth-icon">${icon}</div>
      <div><strong>${title}</strong><p style="margin-top:6px;color:var(--muted);font-size:13px">${esc(message||'')}</p></div>
    </div>`;
}

async function _pollCodexOAuth(){
  const flowId=_codexOAuthFlowId;
  if(!flowId)return;
  try{
    const resp=await api('/api/onboarding/oauth/poll?flow_id='+encodeURIComponent(flowId));
    const status=(resp&&resp.status)||'error';
    if(status==='pending'){
      _codexOAuthPollTimer=setTimeout(_pollCodexOAuth,3000);
      return;
    }
    _clearCodexOAuthPoll();
    _codexOAuthFlowId=null;
    _setCodexOAuthButton(true);
    if(status==='success'){
      _renderCodexOAuthTerminal('success','Credentials saved to the Hermes credential pool. Refreshing provider status…');
      showToast(t('oauth_codex_success'));
      try{await loadOnboardingWizard();}catch(e){}
    }else if(status==='expired'){
      _renderCodexOAuthTerminal('expired','The code expired. Start a new login flow to try again.');
    }else if(status==='cancelled'){
      _renderCodexOAuthTerminal('cancelled','The login flow was cancelled.');
    }else{
      _renderCodexOAuthTerminal('error',(resp&&resp.error)||'OAuth login failed. Please try again.');
    }
  }catch(e){
    _clearCodexOAuthPoll();
    _codexOAuthFlowId=null;
    _setCodexOAuthButton(true);
    _renderCodexOAuthTerminal('error',(e&&e.message)||String(e));
  }
}

async function startCodexOAuth(){
  const flowDiv=$('codexOAuthFlow');
  if(!flowDiv)return;
  _clearCodexOAuthPoll();
  _codexOAuthFlowId=null;
  _setCodexOAuthButton(false);
  flowDiv.style.display='block';
  flowDiv.innerHTML=`<div class="onboarding-oauth-card onboarding-oauth-pending"><div class="onboarding-oauth-icon">⏳</div><div><strong>${t('oauth_codex_polling')}</strong><p>Starting device-code flow…</p></div></div>`;
  try{
    const resp=await api('/api/onboarding/oauth/start',{method:'POST',body:JSON.stringify({provider:'openai-codex'})});
    if(resp.error) throw new Error(resp.error);
    const{flow_id,user_code,verification_uri}=resp;
    if(!flow_id||!user_code||!verification_uri) throw new Error('Invalid OAuth response');
    _codexOAuthFlowId=flow_id;
    flowDiv.innerHTML=`
      <div class="onboarding-oauth-card onboarding-oauth-pending">
        <div class="onboarding-oauth-icon">📋</div>
        <div style="flex:1">
          <strong>${t('oauth_codex_step1')}</strong>
          <p><a href="${esc(verification_uri)}" target="_blank" rel="noopener" style="color:var(--accent);word-break:break-all">${esc(verification_uri)}</a></p>
          <p style="margin-top:8px"><strong>${t('oauth_codex_step2')}</strong></p>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:4px">
            <code style="display:inline-block;font-size:18px;letter-spacing:0.1em;background:rgba(255,255,255,.08);padding:6px 14px;border-radius:8px;user-select:all">${esc(user_code)}</code>
            <button class="sm-btn" type="button" onclick="copyCodexOAuthCode('${esc(user_code)}')">Copy code</button>
            <button class="sm-btn" type="button" onclick="cancelCodexOAuth()">Cancel</button>
          </div>
          <p style="margin-top:8px;color:var(--muted);font-size:13px">${t('oauth_codex_polling')}</p>
        </div>
      </div>`;
    _codexOAuthPollTimer=setTimeout(_pollCodexOAuth,Math.max(1000,Number(resp.poll_interval_seconds||3)*1000));
  }catch(e){
    _clearCodexOAuthPoll();
    _codexOAuthFlowId=null;
    _renderCodexOAuthTerminal('error',(e&&e.message)||String(e));
    _setCodexOAuthButton(true);
  }
}

/* ── Anthropic / Claude Code credential-link flow ── */
let _anthropicOAuthPollTimer=null;
let _anthropicOAuthFlowId=null;

function _clearAnthropicOAuthPoll(){
  if(_anthropicOAuthPollTimer){clearTimeout(_anthropicOAuthPollTimer);_anthropicOAuthPollTimer=null;}
}

function _setAnthropicOAuthButton(enabled){
  const btn=$('anthropicOAuthBtn');
  if(btn){btn.disabled=!enabled;btn.textContent=enabled?'Login with Claude Code':'...';}
}

async function cancelAnthropicOAuth(){
  const flowDiv=$('anthropicOAuthFlow');
  const flowId=_anthropicOAuthFlowId;
  _clearAnthropicOAuthPoll();
  _anthropicOAuthFlowId=null;
  if(flowId){
    try{await api('/api/onboarding/oauth/cancel',{method:'POST',body:JSON.stringify({flow_id:flowId,provider:'anthropic'})});}catch(e){}
  }
  _setAnthropicOAuthButton(true);
  if(flowDiv){
    flowDiv.innerHTML=`<div class="onboarding-oauth-card"><div class="onboarding-oauth-icon">⏹</div><div><strong>Claude Code OAuth cancelled</strong><p style="margin-top:6px;color:var(--muted);font-size:13px">Start again whenever you're ready.</p></div></div>`;
  }
}

function _renderAnthropicOAuthTerminal(status,message){
  const flowDiv=$('anthropicOAuthFlow');
  if(!flowDiv)return;
  const ok=status==='success';
  const icon=ok?'✅':status==='expired'?'⌛':status==='cancelled'?'⏹':'❌';
  const title=ok?'Claude Code OAuth linked':(status==='expired'?'Claude Code polling expired':(status==='cancelled'?'Claude Code OAuth cancelled':'Claude Code OAuth failed'));
  flowDiv.style.display='block';
  flowDiv.innerHTML=`
    <div class="onboarding-oauth-card ${ok?'onboarding-oauth-ready':''}" ${ok?'':'style="border-color:var(--error,#e55)"'}>
      <div class="onboarding-oauth-icon">${icon}</div>
      <div><strong>${title}</strong><p style="margin-top:6px;color:var(--muted);font-size:13px">${esc(message||'')}</p></div>
    </div>`;
}

async function _pollAnthropicOAuth(){
  const flowId=_anthropicOAuthFlowId;
  if(!flowId)return;
  try{
    const resp=await api('/api/onboarding/oauth/poll?flow_id='+encodeURIComponent(flowId));
    const status=(resp&&resp.status)||'error';
    if(status==='pending'){
      _anthropicOAuthPollTimer=setTimeout(_pollAnthropicOAuth,3000);
      return;
    }
    _clearAnthropicOAuthPoll();
    _anthropicOAuthFlowId=null;
    _setAnthropicOAuthButton(true);
    if(status==='success'){
      _renderAnthropicOAuthTerminal('success','Hermes is now linked to Claude Code credentials. Refreshing provider status…');
      showToast('Claude Code OAuth linked');
      try{await loadOnboardingWizard();}catch(e){}
    }else if(status==='expired'){
      _renderAnthropicOAuthTerminal('expired','Claude Code credentials were not detected before this flow expired. Start a new flow to try again.');
    }else if(status==='cancelled'){
      _renderAnthropicOAuthTerminal('cancelled','The login flow was cancelled.');
    }else{
      _renderAnthropicOAuthTerminal('error',(resp&&resp.error)||'Claude Code OAuth linking failed. Please try again.');
    }
  }catch(e){
    _clearAnthropicOAuthPoll();
    _anthropicOAuthFlowId=null;
    _setAnthropicOAuthButton(true);
    _renderAnthropicOAuthTerminal('error',(e&&e.message)||String(e));
  }
}

async function startAnthropicOAuth(){
  const flowDiv=$('anthropicOAuthFlow');
  if(!flowDiv)return;
  _clearAnthropicOAuthPoll();
  _anthropicOAuthFlowId=null;
  _setAnthropicOAuthButton(false);
  flowDiv.style.display='block';
  flowDiv.innerHTML=`<div class="onboarding-oauth-card onboarding-oauth-pending"><div class="onboarding-oauth-icon">⏳</div><div><strong>Checking Claude Code credentials…</strong><p>Hermes is checking for existing Claude Code OAuth credentials on this server.</p></div></div>`;
  try{
    const resp=await api('/api/onboarding/oauth/start',{method:'POST',body:JSON.stringify({provider:'anthropic'})});
    if(resp.error) throw new Error(resp.error);
    const{flow_id,status,action_required}=resp;
    if(!flow_id) throw new Error('Invalid OAuth response');
    _anthropicOAuthFlowId=flow_id;
    if(status==='success'){
      _clearAnthropicOAuthPoll();
      _anthropicOAuthFlowId=null;
      _setAnthropicOAuthButton(true);
      _renderAnthropicOAuthTerminal('success','Hermes is now linked to Claude Code credentials. Refreshing provider status…');
      showToast('Claude Code OAuth linked');
      try{await loadOnboardingWizard();}catch(e){}
      return;
    }
    flowDiv.innerHTML=`
      <div class="onboarding-oauth-card onboarding-oauth-pending">
        <div class="onboarding-oauth-icon">🖥️</div>
        <div style="flex:1">
          <strong>Complete Claude Code login on this host</strong>
          <p style="margin-top:6px">${esc(action_required||"Run 'claude setup-token' on the server, then return here. Hermes will detect the credential automatically.")}</p>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px">
            <code style="display:inline-block;background:rgba(255,255,255,.08);padding:6px 10px;border-radius:8px;user-select:all">claude setup-token</code>
            <button class="sm-btn" type="button" onclick="cancelAnthropicOAuth()">Cancel</button>
          </div>
          <p style="margin-top:8px;color:var(--muted);font-size:13px">Waiting for Claude Code credentials...</p>
        </div>
      </div>`;
    _anthropicOAuthPollTimer=setTimeout(_pollAnthropicOAuth,Math.max(1000,Number(resp.poll_interval_seconds||3)*1000));
  }catch(e){
    _clearAnthropicOAuthPoll();
    _anthropicOAuthFlowId=null;
    _renderAnthropicOAuthTerminal('error',(e&&e.message)||String(e));
    _setAnthropicOAuthButton(true);
  }
}
