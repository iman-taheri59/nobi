/* Signal Intelligence: real DOM/core, mocked GET/export only; no network/accounts. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
let checks=0;const eq=(a,b,m)=>{assert.equal(a,b,m);checks++;},ok=(v,m)=>{assert.ok(v,m);checks++;};
const later=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const clone=x=>JSON.parse(JSON.stringify(x));
function fixture(selection,{n=1,groups=1,exporting=false}={}){
 const as_of=selection.as_of||1800000000,limit=exporting?10000:100;
 const f={...selection,as_of,since:as_of-selection.days*86400};
 const rows=Array.from({length:Math.min(n,limit)},(_,i)=>({id:n-i,ts:as_of-i,symbol:selection.symbol||'BTCUSDT',timeframe:selection.timeframe||'15m',regime:'trend',review_count:0,composite:70,r_multiple:-1,mfe_pct:2,mae_pct:-1,model_status:'resolved'}));
 return {schema:'nobitrader.signal-evidence.v1',filters:f,generated_at:as_of+1,rows,
   page:{limit,total:n,loaded:rows.length,has_more:n>rows.length},quality:{excluded_price_contract_records:2},
   measurement:{basis:'modeled-ohlc-gross-r',net_account_pnl:false,independent_samples_established:false,sample_threshold_is_count_only:true},
   summary:{signals:n,resolved:n,sample_minimum:30,sample_sufficient:n>=30,voided:0,expectancy_r:-1,median_r:-1,reviews:0,outcome_maturity:{pending_not_due:0,due_unresolved:0},unresolved_eligibility:{invalid_model_inputs:0,invalid_model_outcomes:0,non_long_not_applicable:0}},
   cohorts:{total:groups,limit:50,has_more:groups>50,rows:Array.from({length:Math.min(groups,50)},(_,i)=>({symbol:selection.symbol||'BTCUSDT',timeframe:selection.timeframe||'15m',regime:'trend',config_hash:'fixture'+i,signals:1,resolved:1,expectancy_r:-1,win_rate_pct:0,sample_sufficient:false}))},
   pipeline:{available:true,scope:{since:f.since,as_of,symbol:null,timeframe:null,horizon:null,independent_ledgers:true},stages:[{id:'candidates_seen',count:259}],reasons:[{group:'market',code:'spread',count:5}]}};
}
(async()=>{
for(const file of ['index.html','nobitex-mobile.html'])for(const lang of ['fa','en']){
 const html=fs.readFileSync(file,'utf8').replace(/<script id="nbx-inline-nbx-app-js">[\s\S]*?<\/script>/,'');
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));let blocked=0;
 const dom=new JSDOM(html,{url:'https://dashboard.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){w.setInterval=()=>0;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.fetch=()=>{blocked++;throw Error('Network forbidden');};}});
 const w=dom.window,d=w.document,el=id=>d.getElementById(id),txt=id=>el(id).textContent,t=k=>w.eval(`t(${JSON.stringify(k)})`);
 try{
  if(d.readyState!=='complete')await new Promise(r=>w.addEventListener('load',r,{once:true}));await new Promise(r=>setImmediate(r));
  eq(errors.length,0,'dashboard boots');
  w.eval(fs.readFileSync('nbx-core.js','utf8'));w.NBX.core=w.NBX.createCore();const api=w.NBX.core.api;api.standaloneMobile=false;
  w.eval(`LANG='${lang}';PREFS.numFmt='en';applyStaticI18n();`);
  eq(blocked,2,'bootstrap GET attempts blocked');
  eq(el('siExportCsv').disabled,true,'export disabled until validated read');
  let dispatched=[];w.NBX.config.apiToken='fixture-token-not-real';d.cookie='nbx_csrf=fixture-csrf; path=/';
  w.fetch=async(url,options)=>{dispatched.push({url,options});return {ok:true,json:async()=>({fixture:true})};};
  eq((await api.getSignalEvidence(new w.URLSearchParams({days:30,horizon:'H2',symbol:'BTCUSDT',timeframe:'15m'}))).fixture,true);
  ok(dispatched[0].url.includes('/v1/signal-intelligence/evidence?days=30&horizon=H2&symbol=BTCUSDT&timeframe=15m'));
  eq(dispatched[0].options.headers['X-API-Token'],'fixture-token-not-real');eq(dispatched[0].options.headers['X-CSRF-Token'],'fixture-csrf');eq(dispatched[0].options.cache,'no-store');
  api.standaloneMobile=true;await assert.rejects(()=>api.getSignalEvidence('days=30'),/requires the backend/);checks++;eq(dispatched.length,1,'no local fabricated evidence');api.standaloneMobile=false;
  w.fetch=async()=>({ok:false,status:403});await assert.rejects(()=>api.getSignalEvidence('days=30'),/HTTP 403/);checks++;
  let selection={days:30,horizon:'H2',symbol:'BTCUSDT',timeframe:'15m'};
  function choose(s){selection=s;el('siDays').value=String(s.days);el('siHorizon').value=s.horizon;el('siSymbol').value=s.symbol||'';el('siTf').value=s.timeframe||'';}
  choose(selection);let model=fixture(selection,{n:120,groups:51}),requests=[];
  api.getSignalEvidence=async qs=>{requests.push(String(qs));return clone(model);};
  let driftReads=0;api.get=async()=>{driftReads++;throw Error('mock drift unavailable');};
  await w.loadSignalIntelligence();eq(requests.length,1,'one evidence read, not five inconsistent projections');
  ok(requests[0].includes('horizon=H2'));ok(requests[0].includes('symbol=BTCUSDT'));ok(requests[0].includes('timeframe=15m'));
  eq(el('siSignals').children.length,100);eq(el('siCohorts').children.length,50);ok(txt('siReadScope').includes('100/120'));ok(txt('siReadScope').includes('50/51'));ok(txt('siReadScope').includes(t('si.partial')));ok(txt('siReadScope').includes('UTC'));ok(txt('siReadScope').includes('2'));
  ok(txt('siFunnel').includes('259'),'independent global count not forced to selected 120');ok(txt('siReasons').includes('spread'));eq(el('siExportCsv').disabled,false);ok(driftReads>0,'drift is separate and does not invalidate valid evidence');
  for(const mutate of [v=>{v.schema='wrong';},v=>{v.filters.symbol='ETHUSDT';},v=>{v.filters.horizon='H1';},v=>{v.filters.timeframe='1h';},v=>{v.filters.days=7;},v=>{v.filters.since++;},v=>{v.page.has_more=false;},v=>{v.page.loaded=0;v.rows=[];},v=>{v.summary.signals=0;},v=>{v.cohorts.has_more=false;},v=>{v.pipeline.scope.symbol='BTCUSDT';},v=>{v.rows[0].id=0;},v=>{v.rows[0].r_multiple='oops';},v=>{v.rows[0].symbol='ETHUSDT';},v=>{v.measurement.net_account_pnl=true;},v=>{v.generated_at=0;}]){
   model=fixture(selection,{n:120,groups:51});mutate(model);await w.loadSignalIntelligence();
   eq(el('siExportCsv').disabled,true,'malformed response cannot enable export');ok(txt('siStatus').includes(t('si.invalidEvidence')));
   for(const id of ['siSummary','siCohorts','siSignals','siReadScope','siFunnel','siReasons'])eq(txt(id),'','no stale/partial success after invalid response');
  }
  model=fixture(selection,{n:0,groups:0});await w.loadSignalIntelligence();eq(el('siSignalsEmpty').classList.contains('hidden'),false,'real empty success');eq(el('siExportCsv').disabled,false);
  model=fixture(selection);model.pipeline={available:false,scope:model.pipeline.scope,error:'cycle-ledger-unavailable'};await w.loadSignalIntelligence();ok(txt('siFunnel').includes(t('si.pipelineUnavailable')));eq(el('siExportCsv').disabled,false);
  // Escaping remains necessary even on stored evidence with correct scope.
  const evil='<img src=x onerror="window.pwned=1">';model=fixture(selection);model.rows[0].regime=evil;model.cohorts.rows[0].regime=evil;model.pipeline.reasons[0].code=evil;
  await w.loadSignalIntelligence();eq(d.querySelectorAll('#page-intelligence img').length,0);eq(w.pwned,undefined);ok(txt('siSignals').includes(evil));
  // Input invalidates before blur/Enter/network; cycling back never revives an old generation.
  for(const id of ['siSymbol','siDays','siTf','siHorizon']){
   model=fixture(selection);await w.loadSignalIntelligence();el(id).dispatchEvent(new w.Event('input',{bubbles:true}));
   eq(el('siExportCsv').disabled,true);eq(txt('siSignals'),'');eq(txt('siReadScope'),'');ok(txt('siStatus').includes(t('si.reloadRequired')));
  }
  const old=later();api.getSignalEvidence=()=>old.promise;const pending=w.loadSignalIntelligence();
  choose({...selection,symbol:'ETHUSDT'});model=fixture(selection);api.getSignalEvidence=async()=>clone(model);await w.loadSignalIntelligence();
  old.resolve(fixture({...selection,symbol:'BTCUSDT'}));await pending;ok(txt('siSignals').includes('ETHUSDT'));ok(!txt('siSignals').includes('BTCUSDT'));
  // Programmatic selection changes (no input event) still cannot publish a late read.
  const late=later();api.getSignalEvidence=()=>late.promise;const pp=w.loadSignalIntelligence();const previous={...selection};choose({...selection,horizon:'H3'});late.resolve(fixture(previous));await pp;eq(el('siExportCsv').disabled,true);eq(txt('siSignals'),'');
  api.getSignalEvidence=async()=>clone(model=fixture(selection));await w.loadSignalIntelligence();
  const downloads=[];let blobs=0;w.URL.createObjectURL=()=>{blobs++;return 'blob:fixture';};w.URL.revokeObjectURL=()=>{};w.HTMLAnchorElement.prototype.click=function(){downloads.push(this.download);};
  const exp=fixture({...selection,as_of:1800000000},{n:10001,exporting:true});let exported;
  function csvReply(meta=exp){const m=clone(meta);delete m.rows;return {ok:true,headers:{get:()=>w.btoa(JSON.stringify(m))},blob:async()=>new w.Blob(['fixture-csv'])};}
  w.fetch=async(url,options)=>{exported={url,options};return csvReply();};
  eq(await w.exportSignalIntelligence('csv'),true);eq(downloads.length,1);ok(downloads[0].includes('H3-30d-asof-1800000000-read-1800000001-10000-of-10001-PARTIAL.csv'));ok(txt('siExportStatus').includes('10000/10001'));ok(txt('siExportStatus').includes(t('si.partial')));ok(txt('siExportStatus').includes(t('si.currentOutcomes')));
  const q=new URL(exported.url,'https://dashboard.test').searchParams;eq(q.get('view'),'evidence');eq(q.get('symbol'),selection.symbol);eq(q.get('timeframe'),selection.timeframe);eq(q.get('horizon'),'H3');eq(q.get('as_of'),'1800000000');eq(exported.options.cache,'no-store');eq(exported.options.headers['X-API-Token'],'fixture-token-not-real');
  const json=fixture({...selection,as_of:1800000000},{exporting:true});json.rows[0].r_multiple=9;w.fetch=async()=>({ok:true,json:async()=>clone(json)});
  eq(await w.exportSignalIntelligence('json'),true);ok(downloads[1].endsWith('1-of-1.json'));ok(txt('siExportStatus').includes(t('si.complete')));
  const count=downloads.length;
  for(const reply of [()=>({ok:false,status:503}),()=>({ok:true,headers:{get:()=>null}}),()=>csvReply({...exp,filters:{...exp.filters,horizon:'H1'}}),()=>csvReply({...exp,page:{...exp.page,has_more:false}}),()=>csvReply({...exp,filters:{...exp.filters,as_of:1799999999}})]){
   w.fetch=async()=>reply();eq(await w.exportSignalIntelligence('csv'),false);eq(downloads.length,count,'unverified export not downloaded');eq(el('siExportCsv').disabled,false,'failed export can retry against valid loaded evidence');
  }
  const exportSlow=later();w.fetch=()=>exportSlow.promise;const exporting=w.exportSignalIntelligence('csv');
  el('siSymbol').dispatchEvent(new w.Event('input'));exportSlow.resolve(csvReply());eq(await exporting,false);eq(downloads.length,count);eq(txt('siExportStatus'),'','late export does not change new selection notice');eq(el('siExportCsv').disabled,true);
  // A late blob/body must also be guarded, not just response headers.
  await w.loadSignalIntelligence();const body=later(),beforeBlobs=blobs;w.fetch=async()=>{const r=csvReply();r.blob=()=>body.promise;return r;};
  const pendingBody=w.exportSignalIntelligence('csv');await new Promise(r=>setImmediate(r));el('siTf').dispatchEvent(new w.Event('input'));body.resolve(new w.Blob(['old']));eq(await pendingBody,false);eq(blobs,beforeBlobs);eq(downloads.length,count);
  // Failure clears previous success and cannot be mistaken for a zero row set.
  api.getSignalEvidence=async()=>{throw Error('HTTP 401');};await w.loadSignalIntelligence();ok(txt('siStatus').includes('401'));eq(el('siSignalsEmpty').classList.contains('hidden'),true);eq(el('siExportCsv').disabled,true);
  eq(blocked,2,'no real network after mocked fixture setup');
 }finally{w.close();}
}
console.log(`Phase11 Signal Intelligence: ${checks} checks PASS (canonical/mobile × fa/en; mocked readonly evidence/export).`);
})().catch(e=>{console.error(e);process.exitCode=1;});
