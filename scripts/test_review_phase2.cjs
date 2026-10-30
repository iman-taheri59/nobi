/* Offline execution of the real client + renderers. No server, secrets or account. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM, VirtualConsole} = require('jsdom');
let checks = 0;
function eq(actual, expected, message) { assert.equal(actual, expected, message); checks++; }
function ok(value, message) { assert.ok(value, message); checks++; }
const app = fs.readFileSync('nbx-app.js', 'utf8')
  .replace('  window.NBX_APP = {', '  window.__phase2 = {paintPaperState,refreshPaperState};\n  window.NBX_APP = {')
  .replace("  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);\n  else start();", '');
const core = fs.readFileSync('nbx-core.js', 'utf8');
const head = require('node:child_process').execFileSync('git', ['show','HEAD:index.html'], {encoding:'utf8'});
const strip = x => String(x).replace(/[\u2066-\u2069\u200e\u200f\u061c]/g, '');
function deferred() { let resolve; const promise=new Promise(r=>{resolve=r;}); return {promise,resolve}; }
(async()=>{
for (const file of ['index.html','nobitex-mobile.html']) {
  const errors=[];const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
  const html=fs.readFileSync(file,'utf8').replace(/<script id="nbx-inline-nbx-app-js">[\s\S]*?<\/script>/,'');
  const dom=new JSDOM(html, {url:'https://dashboard.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,
    beforeParse(w){w.setInterval=()=>0;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};
      w.fetch=()=>{throw new Error('Unstubbed network forbidden');};}});
  const w=dom.window,d=w.document;
  if(file==='nobitex-mobile.html') {
    const raw=new JSDOM(fs.readFileSync(file,'utf8'));
    eq(raw.window.document.getElementById('nbx-inline-nbx-core-js').textContent.trim(),core.trim(),'standalone core exact parity');
    eq(raw.window.document.getElementById('nbx-inline-nbx-app-js').textContent.trim(),fs.readFileSync('nbx-app.js','utf8').trim(),'standalone app exact parity');
    raw.window.close();
  }

  eq(errors.length,0,'real inline dashboard boots offline');
  // The excluded pages are byte-identical DOM (including scripts' i18n repaint) to HEAD.
  const ref=new JSDOM(head);
  for(const id of ['page-users','page-account']) {
    // Compare raw parse, not language-mutated live DOM.
    const raw=new JSDOM(fs.readFileSync(file,'utf8'));
    eq(raw.window.document.getElementById(id).outerHTML,ref.window.document.getElementById(id).outerHTML,id+' unchanged');
    raw.window.close();
  }
  ref.window.close();
  w.eval(core);w.NBX.core=w.NBX.createCore();const api=w.NBX.core.api;
  api.standaloneMobile=false;
  const originalMetrics=w.NBX.Paper.getMetrics;
  w.NBX.Paper.getMetrics=()=>{throw new Error('Local metrics must never be read in server mode');};
  for(const failure of ['network','401','503','malformed']) {
    api.get=async()=>{if(failure==='malformed')return {};throw new Error(failure);};
    const pos=await api.getPaperPositions(),met=await api.getPaperMetrics(),risk=await api.getBackendRiskState();
    eq(pos.unavailable,true,failure+' positions unavailable');eq(pos.positions,null,'not empty success');
    eq(met.metrics,null,'not local equity');eq(met.curve.length,0,'not local curve');eq(risk,null,'not default account risk');
  }
  api.get=async path=>path.endsWith('/positions')?{positions:[],metrics:{equity:1100,start_equity:1000}}:path.endsWith('/metrics')?{metrics:{equity:1100,start_equity:1000},curve:[]}:{equity:1100,openHeat:0};
  eq((await api.getPaperPositions()).source,'server');eq((await api.getPaperMetrics()).source,'server');eq((await api.getBackendRiskState()).source,'server');
  w.NBX.Paper.getMetrics=()=>({equity:777,start_equity:500,trades:0});
  api.standaloneMobile=true;
  eq((await api.getPaperPositions()).source,'browser-local');eq((await api.getPaperMetrics()).metrics.equity,777);eq((await api.getBackendRiskState()).source,'browser-local');
  w.NBX.Paper.getMetrics=originalMetrics;api.standaloneMobile=false;
  for(const malformed of [{positions:[{}]}, {positions:[{symbol:'BTCUSDT',entry_price:0,state:'OPEN'}]}]) {
    api.get=async()=>malformed;eq((await api.getPaperPositions()).unavailable,true,'malformed positions rejected');
  }
  for(const equity of [null,undefined,false,'',NaN]) {
    api.get=async()=>({metrics:{equity},equity,openHeat:0});
    eq((await api.getPaperMetrics()).metrics,null,'invalid metric presence is not validity');
    eq(await api.getBackendRiskState(),null,'invalid risk equity rejected');
  }

  w.eval(app);w.eval("LANG='en';PREFS.numFmt='en';applyStaticI18n();");
  const risk={source:'server',equity:1100,openHeat:0,caps:{maxHeatPct:13,maxConcurrent:20,maxSingleAssetPct:25,minCashPct:10,riskPct:.75,minRR:1.5}};
  const pos={source:'server',positions:Array.from({length:12},(_,i)=>({symbol:'BTCUSDT',state:'OPEN',entry_price:100,last_price:110,stop_pct:2,side:'buy',qty:i+1})),metrics:{equity:1100,start_equity:1000,cash:1000,trades:2,profit_factor:1.25}};
  const met={source:'server',metrics:pos.metrics,curve:[]};
  w.__phase2.paintPaperState(pos,met,risk);
  eq(w.eval('positions.length'),8,'display limit only');eq(w.eval('positions[0].stop_pct'),2,'mapper retains initial stop');
  ok(strip(d.querySelector('#posBody tr td:nth-child(6)').textContent).includes('5.00R'),'actual 2% stop, not 3.8% default');
  ok(d.querySelector('[data-i18n="ov.kpi3cap"]').textContent.includes('13.00'),'actual heat cap, not 6% literal');
  ok(w.eval('JSON.stringify(caps[0].val)').includes('12'),'all positions counted, not 8-row slice');
  eq(d.getElementById('kPf').textContent,'1.25');ok(d.querySelector('[data-i18n="ov.kpi4sub"]').textContent.includes('partial exits'));
  ok(d.getElementById('equityChart').textContent.includes('Equity curve unavailable'),'successful empty curve not an exchange outage');
  for(const stop of [null,undefined,0,-1,false,'',Infinity]) {
    const row={...pos.positions[0],stop_pct:stop};w.__phase2.paintPaperState({...pos,positions:[row]},met,risk);
    eq(d.querySelector('#posBody tr td:nth-child(6)').textContent,'—','invalid initial stop gives unknown R');
  }
  w.__phase2.paintPaperState({...pos,positions:[{...pos.positions[0],side:'sell'}]},met,risk);
  ok(d.querySelector('#posBody tr td:nth-child(6)').textContent.includes('-5.00R'),'short direction honored');
  w.__phase2.paintPaperState(pos,{source:'browser-local',metrics:{equity:50000,trades:100,profit_factor:99}},risk);
  ok(strip(d.getElementById('kEquity').textContent).includes('1,100.00'),'local metrics cannot override valid server positions metrics');
  eq(d.getElementById('kPf').textContent,'1.25');
  w.__phase2.paintPaperState({unavailable:true,positions:null},{unavailable:true,metrics:null},null);
  eq(d.getElementById('kEquity').textContent,'—','kEquity unknown');
  eq(d.getElementById('kToday').textContent,'Performance data not recorded','kToday unknown');
  eq(d.getElementById('kRisk').textContent,'—','kRisk unknown');
  eq(d.getElementById('kPf').textContent,'Profit factor not recorded','kPf unknown');
  ok(d.getElementById('posBody').textContent.includes('unknown'),'unknown is not no positions');
  ok(d.getElementById('paperStateNotice').textContent.includes('unknown does not mean zero'));
  ok(!d.getElementById('kToday').className.includes('emerald'),'unknown PnL not green');
  eq(d.querySelector('#page-overview .bar-fill[data-w]').dataset.w,'0','old heat animation cannot reappear');
  eq(d.getElementById('paperRiskNotice').dataset.state,'unknown');
  eq(d.getElementById('paperDeskNotice').dataset.state,'unknown');
  w.__phase2.paintPaperState({...pos,positions:[]},met,risk);
  ok(d.getElementById('posBody').textContent.includes('No open positions'),'genuine empty success distinct');
  w.__phase2.paintPaperState(pos,{...met,metrics:{...pos.metrics,profit_factor:0,profit_factor_defined:false}},risk);
  eq(d.getElementById('kPf').textContent,'Profit factor not recorded','break-even PF undefined, not zero success');
  ok(d.querySelector('[data-i18n="ov.kpi4sub"]').textContent.includes('break-even'));
  w.__phase2.paintPaperState(pos,{...met,metrics:{...pos.metrics,profit_factor:99.99,profit_factor_is_capped:true}},risk);
  eq(d.getElementById('kPf').textContent,'≥99.99','cap is not a measured exact 99.99');
  w.__phase2.paintPaperState({...pos,positions:[{...pos.positions[0],tp1_filled:true,stop:100}]},met,risk);
  ok(d.querySelector('#posBody tr td:nth-child(6)').textContent.includes('5.00R'),'TP1 trailing stop never replaces initial risk denominator');
  api.standaloneMobile=true;
  w.__phase2.paintPaperState({source:'browser-local',positions:[],metrics:pos.metrics},{source:'browser-local',metrics:pos.metrics,curve:[]},{...risk,source:'browser-local'});
  ok(d.getElementById('paperStateNotice').textContent.includes('browser-local'),'local mode visibly labelled');api.standaloneMobile=false;
  // Actual async refresh: a late failed read cannot overwrite a newer successful one.
  const slow=deferred();let calls=0;
  api.getPaperPositions=()=>++calls===1?slow.promise:Promise.resolve(pos);
  api.getPaperMetrics=async()=>met;api.getBackendRiskState=async()=>risk;
  const old=w.__phase2.refreshPaperState();await w.__phase2.refreshPaperState();slow.resolve(null);await old;
  ok(strip(d.getElementById('kEquity').textContent).includes('1,100.00'),'latest refresh wins');
  // Detail consumes the real flat contract and tolerates an older wrapped envelope.
  for(const wrapped of [false,true]) {
    const body={signal:{id:7,symbol:'BTCUSDT',timeframe:'15m',entry:'<img src=x onerror=alert(1)>',component_scores:{ta:70}},outcomes:[{horizon_key:'H1',r_multiple:1}],reviews:[{tag:'good-signal',actor_username:'<svg onload=alert(1)>',created_at:1,note:'<img src=x>'}]};
    api.get=async()=>wrapped?{ok:true,review:body}:body;
    await w.loadSignalDetail(7);
    ok(d.getElementById('siDetail').textContent.includes('BTCUSDT'),'detail contract');
    eq(d.querySelectorAll('#siDetail img,#siDetail svg').length,0,'detail escaped');
    ok(d.getElementById('siDetail').textContent.includes('r_multiple'),'outcome preserved');
  }
  api.get=async()=>({ok:true});await w.loadSignalDetail(7);ok(d.getElementById('siDetail').textContent.includes('Invalid signal detail'));
  const detailSlow=deferred();api.get=path=>path.endsWith('/7')?detailSlow.promise:Promise.resolve({signal:{id:8,symbol:'ETHUSDT',timeframe:'1h'}});
  const older=w.loadSignalDetail(7);await w.loadSignalDetail(8);detailSlow.resolve({signal:{id:7,symbol:'BTCUSDT',timeframe:'15m'}});await older;
  ok(d.getElementById('siDetail').textContent.includes('ETHUSDT'),'late detail cannot overwrite selected signal');
  let driftState='ok';let cohortDelay=null;
  function driftGet(path) {
    if(path.includes('/latest')) {
      if(driftState==='404'){const e=new Error('not found');e.meta={status:404};return Promise.reject(e);}
      if(['network','403','503'].includes(driftState)){const e=new Error(driftState);e.meta={status:Number(driftState)||0};return Promise.reject(e);}
      return Promise.resolve({run:{id:42,status:'completed',horizon:'H2',created_at:1700000000,current_start:1699000000,current_end:1700000000,baseline_start:1698000000,baseline_end:1699000000,filters:'{}'},findings:driftState==='bad-json'?[{metric:'composite_psi',evidence:'invalid'}]:[{metric:'expectancy_r',current_value:1,severity:'ok',baseline_n:40,current_n:40}]});
    }
    if(path.includes('/schedule'))return Promise.resolve({enabled:false,interval_s:86400,cohort_mode:'global'});
    if(path.includes('/config-inventory'))return Promise.resolve({rows:[]});
    if(path.includes('/cohorts'))return cohortDelay?cohortDelay.promise:Promise.resolve({rows:[]});
    throw new Error('Unexpected fake GET '+path);
  }
  api.get=driftGet;
  await w.loadSignalDrift();eq(d.getElementById('siDrift').dataset.runId,'42');ok(d.getElementById('siDriftExport').href.endsWith('run_id=42'));
  ok(d.getElementById('siDrift').textContent.includes('H2'));ok(d.getElementById('siDrift').textContent.includes('independent of the filters above'));ok(d.getElementById('siDrift').textContent.includes('UTC'));
  for(const state of ['network','403','503','bad-json','404']) {
    driftState=state;await w.loadSignalDrift();
    eq(d.getElementById('siDrift').dataset.runId,undefined,'no stale run id');
    eq(d.getElementById('siDriftExport').hasAttribute('href'),false,'no stale export');
    eq(d.getElementById('siPsi').textContent,'');
    if(state==='404') {ok(d.getElementById('siDrift').textContent.includes('No drift analysis'));eq(d.getElementById('siSaveSchedule').disabled,false,'schedule still readable without a run');}
    else {ok(d.getElementById('siDrift').textContent.includes('unknown'),'error is unknown, not empty success');eq(d.getElementById('siSaveSchedule').disabled,true,'unknown schedule not editable');}
  }
  driftState='ok';cohortDelay=deferred();const oldDrift=w.loadSignalDrift();await new Promise(r=>setImmediate(r));
  const delay=cohortDelay;cohortDelay=null;driftState='404';await w.loadSignalDrift();delay.resolve({rows:[]});await oldDrift;
  eq(d.getElementById('siDriftExport').hasAttribute('href'),false,'late old drift cannot republish export');
  driftState='ok';await w.loadSignalDrift();eq(d.getElementById('siDriftExport').getAttribute('aria-disabled'),null,'recovery reenables current export');
  const siCalls=[];
  api.getSignalEvidence=qs=>api.get(w.NBX.config.pythonApi+'/v1/signal-intelligence/evidence?'+qs);
  api.get=async path=>{
    siCalls.push(path);
    if(path.includes('/v1/signal-intelligence/evidence?')){
      const q=new URL(path,'https://dashboard.test').searchParams,as_of=1800000000;
      return {schema:'nobitrader.signal-evidence.v1',filters:{days:Number(q.get('days')),horizon:q.get('horizon'),symbol:q.get('symbol')||null,timeframe:q.get('timeframe')||null,as_of,since:as_of-Number(q.get('days'))*86400},generated_at:as_of,
        page:{total:0,loaded:0,limit:100,has_more:false},rows:[],cohorts:{rows:[],limit:50,total:0,has_more:false},
        measurement:{basis:'modeled-ohlc-gross-r',net_account_pnl:false,independent_samples_established:false},quality:{excluded_price_contract_records:0},
        summary:{signals:0,resolved:0,sample_minimum:30,sample_sufficient:false,outcome_maturity:{pending_not_due:0,due_unresolved:0}},
        pipeline:{available:true,stages:[],reasons:[],scope:{since:as_of-Number(q.get('days'))*86400,as_of,symbol:null,timeframe:null,horizon:null,independent_ledgers:true}}};
    }
    return driftGet(path);
  };
  d.getElementById('siSymbol').value='BTCUSDT';d.getElementById('siTf').value='15m';
  await w.loadSignalIntelligence();
  ok(siCalls.some(path=>path.includes('/evidence?')&&path.includes('symbol=BTCUSDT')&&path.includes('timeframe=15m')));
  ok(d.querySelector('[data-i18n="si.scopeNote"]').textContent.includes('share day/symbol/timeframe/horizon filters'),'shared projection scope explicit');
  ok(d.querySelector('[data-i18n="si.modelNote"]').textContent.includes('not net account PnL'));
  eq(d.querySelector('#siHorizon option').value,'H1');ok(d.querySelector('#siHorizon option').textContent.includes('1h'));
  let exported,downloaded;w.URL.createObjectURL=()=> 'blob:fixture';w.URL.revokeObjectURL=()=>{};
  w.HTMLAnchorElement.prototype.click=function(){downloaded=this.download;};
  w.fetch=async(url,options)=>{exported={url,options};const meta=await api.get(url.replace('/export?','/evidence?'));meta.page.limit=10000;delete meta.rows;delete meta.summary;delete meta.cohorts;delete meta.pipeline;return {ok:true,headers:{get:()=>w.btoa(JSON.stringify(meta))},blob:async()=>new w.Blob(['fixture'])};};
  await w.exportSignalIntelligence('csv');
  ok(exported.url.includes('/v1/signal-intelligence/export?days=30&horizon=H1&as_of=1800000000&view=evidence&format=csv&symbol=BTCUSDT&timeframe=15m'),'shared filters and issuance cutoff');
  eq(downloaded,'signal-evidence-BTCUSDT-15m-H1-30d-asof-1800000000-read-1800000000-0-of-0.csv','filename declares actual filtered projection and read time');

  d.getElementById('siSignals').innerHTML='<tr><td>old success</td></tr>';d.getElementById('siCohorts').innerHTML='<tr><td>old cohort</td></tr>';
  api.get=async()=>{throw new Error('HTTP 503');};await w.loadSignalIntelligence();
  for(const id of ['siSummary','siSignals','siCohorts','siReasons','siFunnel'])eq(d.getElementById(id).textContent,'','failed refresh clears '+id);
  eq(d.getElementById('siDriftExport').hasAttribute('href'),false);ok(d.getElementById('siStatus').textContent.includes('503'));
  // Legacy secondary position loader must clear old/closed rows on empty/failure.
  api.getPaperPositions=async()=>({positions:[]});await w.updatePaperPositions();eq(w.eval('positions.length'),0);
  api.getPaperPositions=async()=>({unavailable:true,positions:null});await w.updatePaperPositions();ok(d.getElementById('posBody').textContent.includes('unknown'));
  w.eval("setLang('fa');");
  w.__phase2.paintPaperState({unavailable:true,positions:null},{unavailable:true,metrics:null},null);
  ok(d.getElementById('paperStateNotice').textContent.includes('نامشخص'));
  ok(d.getElementById('posBody').textContent.includes('نامشخص'));
  ok(d.querySelector('[data-i18n="si.modelNote"]').textContent.includes('آستانه'));
  ok(d.querySelector('[data-i18n="si.scopeNote"]').textContent.includes('فیلتر روز، نماد، تایم‌فریم و افق یکسان'));
  eq(d.documentElement.dir,'rtl');
  eq(errors.length,0,'no hidden jsdom exceptions');
  dom.window.close();console.log(file+': phase 2 offline contracts/renderers PASS');
}
console.log('Phase 2: '+checks+' checks PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
