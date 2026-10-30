/* Regressions for Settings, Error Log and cohort DOM. Canonical code, fake API, no network. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
let checks = 0;
const ok = (condition, label) => { assert.ok(condition, label); checks++; };
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const index = fs.readFileSync('index.html','utf8');
const app = fs.readFileSync('nbx-app.js','utf8');
function fragment(source, start, end) {
  const a=source.indexOf(start), b=source.indexOf(end,a);
  assert.ok(a>=0 && b>a, 'source boundaries exist');
  return source.slice(a,b);
}
function domFor(html) { return new JSDOM(html, {url:'https://fixture.invalid/'}); }
function evaluate(code, dom, extra={}) {
  const doc=dom.window.document;
  const ctx={document:doc,window:dom.window,byId:id=>doc.getElementById(id),$:id=>doc.getElementById(id),
    escText:esc,esc,t:k=>k,inFa:k=>k,fmtNum:v=>String(v ?? '—'),PC:v=>String(v),LANG:'en',
    srcLine:esc,weightRows:()=>'',animateBars:()=>{},refreshIcons:()=>{},...extra};
  vm.createContext(ctx);vm.runInContext(code,ctx);return ctx;
}
for (const file of ['index.html','nobitex-mobile.html']) {
  const dom=domFor(fs.readFileSync(file,'utf8')),doc=dom.window.document;
  const ids=[...doc.querySelectorAll('[id]')].map(n=>n.id);
  ok(new Set(ids).size===ids.length,`${file}: all IDs unique`);
  ok(doc.getElementById('siCohorts').tagName==='TBODY',`${file}: summary cohort target is table body`);
  ok(doc.getElementById('siDriftCohorts').tagName==='DIV',`${file}: drift target separate`);
  ok(doc.querySelectorAll('#setSecrets').length===1,`${file}: one secret container`);
  dom.window.close();
}
const settingsIds=['setReadonly','setHash','setTaWeights','setComposite','setRiskModel','setThresholds','setRiskLimits','setProviders','setFlags','setEnvFile','setSecrets','page-settings'];
const dom=domFor(settingsIds.map(id=>`<div id="${id}"></div>`).join(''));
const ctx=evaluate(fragment(index,'function renderSettings(d){','async function loadSettings(){'),dom,{lastSettings:null});
const base={read_only:true,config_hash:'fixture',weights:{},thresholds:{},risk_limits:{},flags:[],secrets:[],providers:{fixture:{ok:true,kind:'fixture'}}};
ctx.renderSettings(base);
ok(dom.window.document.getElementById('setProviders').textContent.includes('set.up'),'successful health shown');
ctx.renderSettings({error:'unavailable'});
ok(dom.window.document.getElementById('setHash').textContent==='—','failure clears prior hash');
ok(!dom.window.document.getElementById('setProviders').textContent.includes('set.up'),'failure clears green provider state');
ctx.renderSettings(base);
ok(dom.window.document.getElementById('setReadonly').textContent==='set.readonly','recovery resets banner');
const markup='<b>fixture only</b>';
const weightContext=evaluate(fragment(index,'function weightRows(values){','function srcLine(src){'),dom,{L:v=>v.en,setWLabel:{}});
dom.window.document.getElementById('setTaWeights').innerHTML=weightContext.weightRows({[markup]:0.5});
ok(!dom.window.document.getElementById('setTaWeights').querySelector('b'),'weight labels stay escaped literal text');
ctx.renderSettings({...base,providers:{[markup]:{ok:false,error:markup,kind:markup}},flags:[{name:markup,value:markup,meaning:markup,unit:markup}],env:{exists:true,loaded:true,path:markup,error:markup},secrets:[{name:markup,present:true,hint:markup,purpose:markup,scope:markup,where:markup}]});
for (const id of ['setProviders','setFlags','setEnvFile','setSecrets']) {
  ok(!dom.window.document.getElementById(id).querySelector('b'),`${id}: server markup stays literal text`);
}
dom.window.close();
const errDom=domFor('<table><tbody id="errBody"></tbody></table>'+['errTotal','errErrors','errDay','errLast','errBadge','errStatus'].map(id=>`<div id="${id}"></div>`).join(''));
const errs=evaluate(fragment(index,'function renderErrors(payload){','/* Every control below'),errDom,{errRows:[],ERR_STYLE:{},errAgo:()=> 'ago',errWhen:()=> 'when'});
const doc=errDom.window.document;
for (const value of [{error:'unavailable',errors:[],counts:{}},{},null,{errors:[],counts:{}}]) {
  errs.renderErrors(value);
  ok(doc.getElementById('errTotal').textContent==='—','unavailable/malformed counts stay unknown');
  ok(!doc.getElementById('errBody').textContent.includes('No errors'),'unavailable is not green empty success');
}
const counts={total:2,error:1,critical:0,last_24h:2,last_ts:1,sources:{[markup]:2}};
errs.renderErrors({errors:[],counts});
ok(doc.getElementById('errBody').textContent.includes('global log is not empty'),'filtered empty distinguished');
errs.renderErrors({errors:[{ts:1,level:'warn',source:markup,message:markup}],counts});
ok(!doc.getElementById('errStatus').querySelector('b'),'error source summary escaped');
errDom.window.close();
async function installation() {
  const dom=domFor('<div id="setInstall"></div><div id="setInstallBody"></div>');
  const posts=[];
  const details={NBX_JOURNAL_DB:{effective:'/fixture/runtime/journal.db',managed:null,default:'/fixture/default/journal.db',source:'process_environment'},NBX_GATE_PREFETCH:{effective:'10',managed:null,default:'10',source:'runtime/default'}};
  const auth={me:async()=>({is_owner:true}),call:async(path,payload,method)=>{
    if (method==='GET' && path==='/install/settings') return {ok:true,data:{values:{},fields:details,allowed:Object.keys(details)}};
    if (path==='/install/wizard/status') return {ok:false};
    posts.push({path,payload});return {ok:true,data:{}};
  }};
  dom.window.confirm=()=>true;
  const ctx=evaluate(fragment(app,'  async function renderInstall() {','  async function renderOperations() {'),dom,{AUTH:auth});
  await ctx.renderInstall();
  const doc=dom.window.document;
  ok(doc.querySelector('[data-install="NBX_JOURNAL_DB"]').value==='/fixture/runtime/journal.db','editor starts with effective path, no varlib fallback');
  ok(doc.getElementById('setInstallBody').textContent.includes('process_environment'),'editor shows provenance');
  await doc.getElementById('installSave').onclick();
  ok(posts.length===0,'unchanged form makes no save request');
  doc.querySelector('[data-install="NBX_GATE_PREFETCH"]').value='20';
  await doc.getElementById('installSave').onclick();
  ok(posts.length===1 && Object.keys(posts[0].payload).join()==='NBX_GATE_PREFETCH','only edited field submitted');
  await doc.getElementById('installSave').onclick();
  ok(posts.length===1,'saved baseline prevents duplicate unchanged save');
  doc.querySelector('[data-install="NBX_JOURNAL_DB"]').value='/fixture/new/journal.db';
  dom.window.confirm=()=>false;
  await doc.getElementById('installSave').onclick();
  ok(posts.length===1,'path migration confirmation can cancel save');
  dom.window.confirm=()=>true;
  await doc.getElementById('installSave').onclick();
  ok(posts.length===2 && Object.keys(posts[1].payload).join()==='NBX_JOURNAL_DB','confirmed path draft alone submitted');
  dom.window.close();
}
async function operations() {
  const dom=domFor('<div id="setOperations"></div><div id="setOperationsBody"></div>');
  const auth={me:async()=>({is_owner:true}),call:async(path)=>({ok:true,data:path==='/operations'?{archives:[],helper_available:false,restore_available:false}:path==='/live/status'?{placement_enabled:false}:{ready:false,samples:0,days:0}})};
  const ctx=evaluate(fragment(app,'  async function renderOperations() {','  function bindLogout() {'),dom,{AUTH:auth,crypto:{}});
  await ctx.renderOperations();
  ok(dom.window.document.getElementById('opsRestore').disabled,'online restore disabled');
  ok(dom.window.document.getElementById('livePlace').disabled,'live remains disabled for unready fixture');
  dom.window.close();
}
(async()=>{
  await installation();await operations();
  console.log(`Review phase 1: ${checks} assertions passed; fake DOM/API only.`);
})().catch(error=>{console.error(error);process.exitCode=1;});
