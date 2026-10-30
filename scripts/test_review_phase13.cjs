/* global console, process */
/* Offline audit renderer tests. No network or real credentials. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let checks = 0;
function check(value, message) { assert.ok(value, message); checks++; }
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; }
const html = fs.readFileSync('index.html', 'utf8');
const code = html.slice(html.indexOf('let auEvents=null'), html.indexOf('const _perfExportCsv'));
async function run() {
  const els = {auBody: {}, auCount: {}, auTg: {dataset:{}}, auScope: {value:'control', addEventListener() {}}, auRefresh: {addEventListener() {}}};
  const document = {documentElement:{dataset:{canMutate:'1'}}};
  const context = {document, window:{}, byId:id=>els[id], t:k=>k, escText:s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'), fmtWhen:x=>x, fmtNum:x=>String(x), Promise, Number, Array, JSON, String};
  context.window.NBX = {core:{api:{}}}; context.NBX = context.window.NBX;
  vm.createContext(context); vm.runInContext(code, context);
  const api = context.NBX.core.api;
  api.getAudit = async () => ({ok:true, scope:'control', total:1, events:[{ts:'now',action:'<img>',actor:'<script>',client:'<client>',result:'ok',detail:'<payload>'}]});
  api.getNotifyStatus = async () => ({ok:true, configured:true});
  await context.loadAudit();
  check(els.auBody.innerHTML.includes('&lt;script&gt;') && !els.auBody.innerHTML.includes('<script>'), 'actor escaped');
  check(els.auBody.innerHTML.includes('&lt;payload&gt;') && els.auBody.innerHTML.includes('&lt;client&gt;'), 'detail and client escaped');
  check(els.auCount.textContent==='1 / 1', 'loaded and total');
  check(els.auTg.dataset.state==='au.tgOn', 'configured is not delivery');
  document.documentElement.dataset.canMutate='0'; context.window.renderAuditServer();
  check(!els.auBody.innerHTML.includes('script') && els.auCount.textContent==='—' && els.auTg.dataset.state==='au.tgUnknown', 'permission loss clears data on repaint');
  document.documentElement.dataset.canMutate='1'; context.window.renderAuditServer();
  check(!els.auBody.innerHTML.includes('script'), 'permission restoration cannot republish data');
  api.getAudit=async()=>({ok:false,unavailable:true}); api.getNotifyStatus=async()=>({ok:false,configured:null});
  await context.loadAudit(); check(els.auBody.innerHTML.includes('au.error') && els.auTg.dataset.state==='au.tgUnknown', 'failure differs from empty and not configured');
  api.getAudit=async()=>({ok:true,scope:'control',total:0,events:[]}); api.getNotifyStatus=async()=>({configured:false});
  await context.loadAudit(); check(els.auBody.innerHTML.includes('au.empty') && els.auTg.dataset.state==='au.tgOff', 'successful empty and unconfigured');
  api.getAudit=async()=>({ok:false,standalone:true}); api.getNotifyStatus=async()=>({ok:false,standalone:true});
  await context.loadAudit(); check(els.auBody.innerHTML.includes('au.standalone') && els.auTg.dataset.state==='au.tgStandalone', 'standalone distinct');
  const pending=deferred(); api.getAudit=()=>pending.promise; api.getNotifyStatus=async()=>({configured:true});
  const first=context.loadAudit(); await Promise.resolve(); context.window.invalidateAuditServer();
  pending.resolve({scope:'control',total:1,events:[{action:'secret'}]}); await first;
  check(!els.auBody.innerHTML.includes('secret'), 'late reply after auth change rejected');
  const old=deferred(); api.getAudit=()=>old.promise; const older=context.loadAudit(); await Promise.resolve();
  els.auScope.value='preview'; api.getAudit=async()=>({scope:'preview',total:0,events:[]}); await context.loadAudit();
  old.resolve({scope:'control',total:1,events:[{action:'old'}]}); await older;
  check(els.auBody.innerHTML.includes('au.empty')&&!els.auBody.innerHTML.includes('old'), 'old scope response rejected');
  api.getAudit=async()=>({scope:'all',total:1,events:[{action:'wrong'}]}); await context.loadAudit();
  check(els.auBody.innerHTML.includes('au.error')&&!els.auBody.innerHTML.includes('wrong'), 'scope mismatch rejected');
  console.log(`phase13 UI: ${checks} checks passed`);
}
run().catch(e=>{console.error(e);process.exitCode=1;});
