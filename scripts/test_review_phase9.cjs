/* Real Performance renderers/adapters; fake reads only. No network/accounts/writes. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
let checks=0;const eq=(a,b,m)=>{assert.equal(a,b,m);checks++;};const ok=(v,m)=>{assert.ok(v,m);checks++;};
const tick=()=>new Promise(r=>setImmediate(r));
const later=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function payload(extra={}){return {stage_read_only:true,kpi:{hit:null,exp:null,vpf:null,ic:null,sig:1,res:0,void:0,issued:3},vpf_state:'undefined',
 horizons:[{k:'H1',n:1,hit:1,avgr:1,sources:{synthetic:1}},{k:'H2',n:0,hit:null,avgr:null,sources:{}},{k:'H3',n:0,hit:null,avgr:null,sources:{}}],
 families:[],calibration:[],signals:[{id:1,ts:1700000000,sym:'XUSDT',dir:'long',score:70,rank:1,h1:true,h2:null,h3:null,r:null,st:'inprogress',source:'synthetic',origin:'live'}],
 msi:{status:'insufficient',ratio:null,realized:{n:0,ic:null},baseline:{n:0,ic:null,source:'journal-history'}},generated_at:'2026-10-29T10:00:00Z',...extra};}
(async()=>{
for(const file of ['index.html','nobitex-mobile.html'])for(const lang of ['fa','en']){
 const html=fs.readFileSync(file,'utf8').replace(/<script id="nbx-inline-nbx-app-js">[\s\S]*?<\/script>/,'');
 const vc=new VirtualConsole(),errors=[];vc.on('jsdomError',e=>errors.push(e.message));
 let network=0;
 const dom=new JSDOM(html,{url:'https://dashboard.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,
 beforeParse(w){w.setInterval=()=>0;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.fetch=()=>{network++;throw Error('Network forbidden');};}});
 const w=dom.window,d=w.document,txt=id=>d.getElementById(id).textContent;
 try{
  if(d.readyState!=='complete')await new Promise(r=>w.addEventListener('load',r,{once:true}));await tick();
  w.eval(fs.readFileSync('nbx-core.js','utf8'));w.NBX.core=w.NBX.createCore();
  const api=w.NBX.core.api;api.standaloneMobile=false;
  w.eval(`LANG='${lang}';PREFS.numFmt='en';applyStaticI18n();`);
  const t=k=>w.eval(`t(${JSON.stringify(k)})`);
  const readState=()=>d.getElementById('perfReadStatus').dataset.state;
  let reads=0,stages=0,writes=0;
  for(const k of ['postJournalBackfill','postMsiBaseline','placeOrder'])api[k]=()=>{writes++;throw Error('Write forbidden');};
  const realSummary=api.getJournalSummary.bind(api),realStage=api.getJournalStage.bind(api);
  // Real summary adapter rejects transport errors, success-shaped empty errors and malformed payloads.
  for(const bad of [null,{}, {kpi:{}}, {error:'unavailable',...payload()},payload({kpi:{sig:-1,res:0,void:0}}),payload({horizons:null}),payload({kpi:{sig:1,res:2,void:0}})]){
   api.get=async()=>bad;const r=await realSummary({origin:'live'});eq(r.ok,false);eq(r.error,'unavailable');ok(!r.kpi);
  }
  api.get=async()=>{throw Error('HTTP 401/503');};eq((await realSummary()).ok,false);
  let seen='';api.get=async url=>{seen=url;return payload();};eq((await realSummary({origin:'backfill',days:7,tf:'15m'})).kpi.sig,1);ok(seen.includes('origin=backfill'));ok(seen.includes('days=7'));ok(seen.includes('tf=15m'));
  api.standaloneMobile=true;api.get=()=>{throw Error('Standalone must not read');};eq((await realSummary()).standalone,true);eq((await realStage({readOnlyContract:true})).standalone,true);api.standaloneMobile=false;
  for(const bad of [{ok:true,stage:'run'},{ok:true,read_only:true,evaluated:true,stage:'run'},{ok:true,read_only:true,evaluated:false,stage:'bogus'}]){
   api.get=async()=>bad;eq((await realStage({readOnlyContract:true})).ok,false);
  }
  let stageProbe=0;api.get=async()=>{stageProbe++;return {ok:true};};eq((await realStage()).ok,false);eq(stageProbe,0,'no capability means no old-server GET');
  api.get=async()=>({ok:true,read_only:true,evaluated:false,stage:null});eq((await realStage({readOnlyContract:true})).stage,null);
  api.getJournalSummary=async()=>{reads++;return payload();};api.getJournalStage=async()=>{stages++;return {ok:true,stage:'run',read_only:true,evaluated:false,recorded_at:1700000000};};
  eq(network,2,'two bootstrap GETs were blocked by the guard; nothing was dispatched');network=0;
  w.live={unavailable:true};await w.loadPerf();eq(readState(),'ready');eq(txt('pSig'),'1');eq(stages,1);
  ok(txt('hzCards').includes('synthetic: 1'),'H1 provenance remains visible without H2');
  ok(txt('famBody').includes(t('perf.familyEmpty')));ok(txt('perfBody').includes('XUSDT'));eq(d.querySelectorAll('#perfBody td')[5].textContent,'✓');
  ok(txt('msiBadge').includes(t('msi.insufficient')),'historical MSI is not hidden by current exchange outage');
  ok(txt('stageWhy').includes(t('perf.stageStored')));ok(txt('perfReadStatus').includes('2026-10-29'));
  eq(writes,0);eq(network,0);
  const beforeStage=stages;api.getJournalSummary=async()=>payload({stage_read_only:false});await w.loadPerf();eq(stages,beforeStage,'old server is not probed');ok(txt('stageWhy').includes(t('perf.stageAbsent')));
  // Failure clears every previously rendered number/row/chart; not a green zero or empty success.
  api.getJournalSummary=async()=>({error:'unavailable'});await w.loadPerf();eq(readState(),'error');
  for(const id of ['pExp','pPf','pIc','pSig','pRes','pVoid'])eq(txt(id),'—');
  eq(txt('pHit'),lang==='fa'?'آمار کارایی ثبت نشده است':'Performance data not recorded');
  ok(txt('perfBody').includes(t('perf.readError')));ok(!txt('perfBody').includes('XUSDT'));eq(d.querySelectorAll('#calChart circle').length,0);ok(!txt('stageNow').includes('run'));
  api.getJournalSummary=async()=>({standalone:true});await w.loadPerf();eq(readState(),'standalone');ok(txt('msiMsg').includes(t('perf.requiresServer')));
  api.getJournalSummary=async()=>payload({kpi:{hit:null,exp:null,vpf:null,ic:null,sig:0,res:0,void:0},signals:[]});await w.loadPerf();eq(readState(),'ready');eq(txt('pSig'),'0');ok(txt('perfBody').includes(t('perf.empty')));
  // Malicious data remains literal text. Non-LONG is N/A, never a false pending LONG.
  const evil='<img src=x onerror="window.pwned=1">';
  api.getJournalSummary=async()=>payload({signals:[{id:'1" onclick="boom()',ts:1700000000,sym:evil,dir:'neutral',score:null,rank:null,h1:false,h2:true,h3:'false',r:999,st:'not_applicable',source:evil,origin:evil},
   {id:2,ts:1700000000,sym:'EXITUSDT',dir:'exit',score:0,st:'not_applicable'},
   {id:3,ts:1700000000,sym:'VOIDUSDT',dir:'long',score:50,st:'void',void_reason:evil,h1:undefined,h2:'false',h3:false}]});
  await w.loadPerf();eq(d.querySelectorAll('#perfBody img').length,0);eq(d.querySelectorAll('#perfBody [onclick]').length,0);eq(w.pwned,undefined);
  const trs=d.querySelectorAll('#perfBody tr');ok(trs[0].textContent.includes(evil));eq(trs[0].dataset.sig,'');
  eq(trs[0].children[2].textContent,t('sg.neutral'));eq(trs[1].children[2].textContent,t('sg.exit'));
  for(const tr of [trs[0],trs[1]])for(const idx of [5,6,7])eq(tr.children[idx].textContent,'N/A');
  eq(trs[0].children[8].textContent,'—');eq(trs[2].children[5].textContent,'—');eq(trs[2].children[6].textContent,'—');eq(trs[2].children[7].textContent,'✗');eq(trs[2].children[9].querySelector('span[title]').title,evil);
  // Numeric axis is proportional, 100% stays inside; each bucket exposes n; no ideal diagonal.
  api.getJournalSummary=async()=>payload({calibration:[[90,1,3],[20,0,2],[50,0.5,7],[null,0.5,1],[70,2,1]],vpf_state:'no_losses'});
  await w.loadPerf();const circles=d.querySelectorAll('#calChart circle');eq(circles.length,3);eq(circles[0].dataset.score,'20');eq(circles[1].dataset.score,'50');eq(circles[2].dataset.score,'90');
  eq(+circles[1].getAttribute('cx')-(+circles[0].getAttribute('cx')),78);eq(+circles[2].getAttribute('cy'),30);ok(circles[2].textContent.includes('n=3'));ok(txt('calChart').includes('n=7'));eq(d.querySelectorAll('#calChart line[stroke-dasharray]').length,0);ok(txt('pPf').includes('∞'));ok(d.getElementById('pPf').title.length>10);
  api.getJournalStage=async()=>{throw Error('Stage failed independently');};await w.loadPerf();eq(readState(),'ready');eq(txt('pSig'),'1');ok(txt('stageWhy').includes(t('perf.stageAbsent')));
  // New filter starts while an old read is pending; latest response wins, including Stage.
  const old=later(),fresh=later();let calls=0;
  api.getJournalSummary=()=>++calls===1?old.promise:fresh.promise;
  d.getElementById('perfOriginSel').value='live';const a=w.loadPerf();eq(readState(),'loading');eq(txt('pSig'),'—');
  d.getElementById('perfOriginSel').value='backfill';const b=w.loadPerf();
  fresh.resolve(payload({kpi:{hit:null,exp:null,vpf:null,ic:null,sig:9,res:0,void:0}}));await b;eq(txt('pSig'),'9');
  old.resolve(payload({kpi:{hit:null,exp:null,vpf:null,ic:null,sig:2,res:0,void:0}}));await a;eq(txt('pSig'),'9');ok(txt('perfReadStatus').includes('backfill'));eq(d.getElementById('perfRefresh').disabled,false);
  const delayedStage=later();api.getJournalSummary=async()=>payload();api.getJournalStage=()=>delayedStage.promise;
  const c=w.loadPerf();await tick();api.getJournalSummary=async()=>({error:'unavailable'});await w.loadPerf();
  delayedStage.resolve({ok:true,stage:'run',read_only:true,evaluated:false});await c;eq(readState(),'error');ok(txt('stageWhy').includes(t('perf.stageAbsent')));
  // Benchmark is descriptive, escaped and latest-request only; no historical/account writes.
  const bm={ok:true,bench:evil,tf:'1h',lookback:240,periods_per_year:8760,candles_from:['cache'],summary:{beta:1,symbols:1,beating_benchmark:1},dropped:[{symbol:evil,reason:evil}],rows:[{symbol:evil,beta:1,alpha_annual_pct:2,relative_return_pct:1,tracking_error_pct:3,info_ratio:0.4,n:200}]};
  api.postBenchmark=async()=>bm;await w.loadBenchmark();ok(txt('bmBody').includes(evil));eq(d.querySelectorAll('#bmBody img').length,0);ok(txt('bmBody').includes('cache'));ok(txt('bmBody').includes('8,760'));ok(d.querySelector('[data-i18n="perf.bmLimit"]').textContent.length>100);
  for(const rows of [null,{},[null]]){api.postBenchmark=async()=>({ok:true,rows});await w.loadBenchmark();ok(txt('bmBody').includes(t('perf.readError')));}
  api.postBenchmark=async()=>({ok:false,error:evil});await w.loadBenchmark();eq(d.querySelectorAll('#bmBody img').length,0);ok(txt('bmBody').includes(evil));
  const bo=later(),bn=later();let bc=0;api.postBenchmark=()=>++bc===1?bo.promise:bn.promise;
  const ba=w.loadBenchmark(),bb=w.loadBenchmark();bn.resolve({...bm,bench:'NEWBENCH'});await bb;bo.resolve({...bm,bench:'OLDBENCH'});await ba;ok(txt('bmBody').includes('NEWBENCH'));ok(!txt('bmBody').includes('OLDBENCH'));
  eq(writes,0);eq(network,0);eq(errors.length,0,errors.join('\n'));
 }finally{dom.window.close();}
}
console.log(`Phase9 Performance: ${checks} checks PASS (canonical/mobile × fa/en; no network/writes).`);
})().catch(e=>{console.error(e);process.exitCode=1;});
