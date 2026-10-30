/* Error Log: real DOM/adapters, mocked network, private evidence only. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
let checks=0;const eq=(a,b,m)=>{assert.equal(a,b,m);checks++;},ok=(v,m)=>{assert.ok(v,m);checks++;};
const tick=()=>new Promise(r=>setImmediate(r));
const later=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function data(extra={}){return {errors:[{id:1,ts:1700000000,level:'warning',source:'fixture',message:'fixture row',detail:'detail',context:'context'}],n:1,
 counts:{total:259,error:21,critical:8,warning:230,last_24h:155,last_ts:1700000000,sources:{fixture:259}},filtered_count:259,truncated:true,
 storage:{backend:'sqlite',degraded:false,max_db:2000,max_memory:500,sqlite_readable:true},filters:{limit:200,level:null,q:null},generated_at:1700000000,...extra};}
const diag=status=>({overall:status,counts:{ok:0,skip:0,fail:0,error:0,[status]:1},results:[{id:'fixture',fa:'آزمون',en:'fixture check',status,ms:1,detail:'fixture only'}]});
(async()=>{
for(const file of ['index.html','nobitex-mobile.html'])for(const lang of ['fa','en']){
 const html=fs.readFileSync(file,'utf8').replace(/<script id="nbx-inline-nbx-app-js">[\s\S]*?<\/script>/,'');
 const vc=new VirtualConsole(),errors=[];vc.on('jsdomError',e=>errors.push(e.message));let blocked=0;
 const dom=new JSDOM(html,{url:'https://dashboard.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){w.setInterval=()=>0;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.fetch=()=>{blocked++;throw Error('Network forbidden');};}});
 const w=dom.window,d=w.document,txt=id=>d.getElementById(id).textContent,t=k=>w.eval(`t(${JSON.stringify(k)})`);
 try{
  if(d.readyState!=='complete')await new Promise(r=>w.addEventListener('load',r,{once:true}));await tick();
  w.eval(fs.readFileSync('nbx-core.js','utf8'));w.NBX.core=w.NBX.createCore();const api=w.NBX.core.api;api.standaloneMobile=false;
  w.eval(`LANG='${lang}';PREFS.numFmt='en';applyStaticI18n();`);
  eq(blocked,2,'bootstrap GETs blocked, not dispatched');blocked=0;
  let writes=0;for(const name of ['clearErrors','selfTestErrors','runDiagnostics','runDevSuite']){const real=api[name].bind(api);api['_real_'+name]=real;api[name]=async()=>{writes++;throw Error('Unrequested write forbidden');};}
  const realGet=api.getErrors.bind(api);
  for(const bad of [null,{},data({error:'bad'}),data({counts:{}}),data({errors:[null]}),data({errors:{}}),data({counts:{total:-1,error:0,critical:0,last_24h:0}})]){
   api.get=async()=>bad;eq((await realGet()).ok,false);
  }
  api.standaloneMobile=true;api.get=()=>{throw Error('standalone must not read');};eq((await realGet()).standalone,true);api.standaloneMobile=false;
  let url='';api.get=async u=>{url=u;return data();};eq((await realGet({level:'error',q:'% literal',source:'fixture',hours:2})).n,1);ok(url.includes('source=fixture'));ok(url.includes('hours=2'));ok(url.includes('q=%25+literal'));
  api.getErrors=async()=>data();await w.loadErrors();eq(txt('errTotal'),'259');eq(txt('errErrors'),'29');eq(txt('errDay'),'155');ok(txt('errScope').includes('1 / 259'));ok(txt('errScope').includes('sqlite'));ok(txt('errScope').includes(t('err.partial')));ok(txt('errBody').includes('fixture row'));eq(writes,0);
  // Export is the exact loaded snapshot, with captured filters and truncation, not all 259 rows.
  w.URL.createObjectURL=()=> 'blob:fixture';w.URL.revokeObjectURL=()=>{};w.HTMLAnchorElement.prototype.click=()=>{};
  d.getElementById('errExport').onclick();let exported=JSON.parse(d.getElementById('errExportText').value);
  eq(exported.scope,'loaded-rows-only');eq(exported.errors.length,1);eq(exported.counts.total,259);eq(exported.filtered_count,259);eq(exported.truncated,true);eq(exported.filters.limit,200);eq(exported.storage.backend,'sqlite');
  api.getErrors=async()=>{throw Error('HTTP failure');};await w.loadErrors();eq(txt('errTotal'),'—');eq(txt('errBadge'),'?');eq(d.getElementById('errExportText').value,'');ok(d.getElementById('errExportBox').classList.contains('hidden'));eq(d.getElementById('errExport').disabled,true);ok(!txt('errBody').includes('fixture row'));
  // Degraded storage is explicit, never silently called persistent emptiness.
  api.getErrors=async()=>data({errors:[],n:0,counts:{total:0,error:0,critical:0,last_24h:0},filtered_count:0,storage:{backend:'memory',degraded:true}});await w.loadErrors();ok(txt('errScope').includes('memory'));ok(txt('errScope').includes(t('err.degraded')));ok(txt('errBody').includes(t('err.partialEmpty')));
  const evil='<img src=x onerror="window.pwned=1">';
  api.getErrors=async()=>data({errors:[{id:-1,ts:NaN,level:'warning',source:evil,message:evil,detail:evil,context:evil}],counts:{total:1,error:0,critical:0,last_24h:1,sources:{[evil]:1}},filters:{q:evil,level:'warning'},filtered_count:1});await w.loadErrors();eq(d.querySelectorAll('#page-errors img').length,0);eq(w.pwned,undefined);ok(txt('errStatus').includes(evil));ok(txt('errScope').includes(evil));ok(txt('errBody').includes(evil));eq(w.errWhen(NaN),'—');eq(w.errWhen('bad'),'—');eq(w.errAgo(w.Date.now()/1000+480),t('err.timeUnknown'));
  // Identical refreshes coalesce; changed filters race safely. Loading invalidates export immediately.
  const old=later(),fresh=later();let calls=0;api.getErrors=()=>++calls===1?old.promise:fresh.promise;
  d.getElementById('errLevel').value='warning';const a=w.loadErrors(),same=w.loadErrors();eq(calls,1);eq(txt('errTotal'),'—');eq(d.getElementById('errExportText').value,'');
  d.getElementById('errLevel').value='error';const b=w.loadErrors();eq(calls,2);fresh.resolve(data({counts:{total:9,error:9,critical:0,last_24h:9},filters:{level:'error',q:null}}));await b;
  old.resolve(data({counts:{total:2,error:0,critical:0,last_24h:2},filters:{level:'warning',q:null}}));eq((await a).superseded,true);await same;eq(txt('errTotal'),'9');ok(txt('errScope').includes('level=error'));
  const preMutation=later();let forcedCalls=0;api.getErrors=()=>++forcedCalls===1?preMutation.promise:Promise.resolve(data({counts:{total:0,error:0,critical:0,last_24h:0},errors:[],n:0}));
  const staleRead=w.loadErrors();await w.loadErrors(true);preMutation.resolve(data());eq((await staleRead).superseded,true);eq(txt('errTotal'),'0');
  // Invalidate immediately on text input, before its debounce actually dispatches a read.
  const originalSet=w.setTimeout;let debounce;
  w.setTimeout=(fn,ms)=>{if(ms===350){debounce=fn;return 1;}return originalSet(fn,ms);};
  d.getElementById('errSearch').value='new';d.getElementById('errSearch').oninput();eq(txt('errTotal'),'—');eq(d.getElementById('errExport').disabled,true);ok(typeof debounce==='function');w.setTimeout=originalSet;
  // Self-test's legacy readable=true is not proof of SQLite persistence.
  const toasts=[];w.toast=(...args)=>toasts.push(args);api.getErrors=async()=>data();
  api.selfTestErrors=async()=>({readable:true,wrote:1,total_after:1});await d.getElementById('errSelfTest').onclick();ok(toasts.some(a=>a[0]===t('err.selfTestFail')));ok(!toasts.some(a=>a[0]===t('err.selfTestOk')));
  toasts.length=0;api.selfTestErrors=async()=>({ok:true,sqlite_verified:true,readable:true,wrote:2,total_after:2});await d.getElementById('errSelfTest').onclick();ok(toasts.some(a=>a[0]===t('err.selfTestOk')));
  // Malformed/no result diagnostic responses can never become 'all OK'.
  for(const bad of [{},{results:[]},diag('ok')&&{...diag('ok'),overall:'skip'},{...diag('ok'),counts:{ok:99,skip:0,error:0,fail:0}}]){
   w.renderDiag(bad);ok(txt('diagSummary').includes(t('diag.unavailable')));ok(!txt('diagSummary').includes(t('diag.allOk')));
  }
  w.renderDiag(diag('skip'));ok(txt('diagSummary').includes(t('diag.someSkip').replace('{n}','1')));
  w.renderDiagCatalogue([{id:'fixture',fa:'عنوان فارسی',en:'English title',needs:''}]);ok(txt('diagList').includes(lang==='fa'?'عنوان فارسی':'English title'));ok(txt('diagList').includes(t('diag.notRun')));
  const probe=later();let diagCalls=0;api.runDiagnostics=()=>{diagCalls++;return probe.promise;};
  const run=w.runDiagnostics(false);await w.runDiagnostics(true);eq(diagCalls,1);eq(d.getElementById('diagRun').disabled,true);eq(d.getElementById('diagRunMarket').disabled,true);
  toasts.length=0;probe.resolve(diag('skip'));await run;eq(d.getElementById('diagRun').disabled,false);eq(toasts.at(-1)[2],'warn');ok(!toasts.some(a=>a[2]==='ok'));
  // Actual write adapters inspect HTTP status; mock transport only, no server action.
  const transport=[];w.fetch=async (u,o)=>{transport.push([u,o]);return {ok:false,status:403,json:async()=>({ok:true,readable:true})};};
  eq((await api._real_clearErrors()).ok,false);eq((await api._real_selfTestErrors()).ok,false);eq(transport.length,2);for(const [,o] of transport)eq(o.method,'POST');
  eq(writes,0,'no unrequested operational actions');eq(blocked,0);eq(errors.length,0,errors.join('\n'));
 }finally{dom.window.close();}
}
console.log(`Phase10 Error Log: ${checks} checks PASS (canonical/mobile × fa/en; mocked reads/explicit fake controls only).`);
})().catch(e=>{console.error(e);process.exitCode=1;});
