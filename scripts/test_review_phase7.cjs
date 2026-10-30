/* Backtest Lab: real engines/client/renderers; synthetic data/fetch only.
 * No network, account, orders, provider, OS helper or deployment. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
let checks=0;const eq=(a,b,m)=>{assert.equal(a,b,m);checks++;};const ok=(v,m)=>{assert.ok(v,m);checks++;};
const near=(a,b,e=0.011)=>ok(Math.abs(a-b)<e,`${a} != ${b}`);
const turn=()=>new Promise(r=>setImmediate(r));
const app=fs.readFileSync('nbx-app.js','utf8').replace("  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);\n  else start();",'');
const rows=n=>Array.from({length:n},(_,i)=>({t:1700000000+i*3600,o:100,h:100,l:100,c:100,v:1}));
(async()=>{
for(const file of ['index.html','nobitex-mobile.html'])for(const lang of ['fa','en']){
 const html=fs.readFileSync(file,'utf8').replace(/<script id="nbx-inline-nbx-app-js">[\s\S]*?<\/script>/,'');
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const dom=new JSDOM(html,{url:'https://dashboard.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,
  beforeParse(w){w.setInterval=()=>0;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.fetch=()=>{throw Error('Real network forbidden');};}});
 const w=dom.window,d=w.document;
 try{
  if(d.readyState!=='complete')await new Promise(r=>w.addEventListener('load',r,{once:true}));await turn();
  w.eval(fs.readFileSync('nbx-core.js','utf8'));w.NBX.core=w.NBX.createCore();const api=w.NBX.core.api;api.standaloneMobile=false;api.getExecMode=async()=>({ok:true,default:'semi',symbols:{}});
  w.eval(app);w.eval(`LANG='${lang}';PREFS.numFmt='en';applyStaticI18n();`);await turn();
  d.getElementById('page-backtest').classList.remove('hidden');
  // No scan result is not evidence of an exchange outage or no server history.
  w.eval('BT.lastEnvelope=null;BT.lastReport=null;window.__btRan=false;');
  w.live.unavailable=true;w.live.ranked=[];w.drawBt();
  const text=d.getElementById('btChart').textContent;
  ok(text.includes(lang==='fa'?'این نشست':'this session'));ok(!/unreachable|در دسترس نیست/.test(text));
  ok(d.getElementById('btGateMsg').textContent.includes(lang==='fa'?'بررسی نشده':'not been checked'));
  ok(!d.getElementById('btGate').className.includes('emerald'));
  for(const id of ['btRunScope','btGridScope','btMultiScope'])ok(d.getElementById(id).textContent.trim().length>70,id);
  ok(d.getElementById('btGridScope').textContent.includes('holdout'));
  eq(d.getElementById('btTf').closest('label').querySelector('span').textContent,lang==='fa'?'تایم‌فریم':'Timeframe');
  ok(d.getElementById('btMultiScope').textContent.includes('400')||d.getElementById('btMultiScope').textContent.includes('۴۰۰'));
  // Actual browser engine: cash costs, PnL, terminal point and MDD reconcile.
  const engine=w.NBX.Backtest,runWindow=engine.runWindow;
  for(const slip of [0,6,35])for(const n of [62,66,450]){
   const data=rows(n),signals=[];signals[61]={direction:'LONG',score:80,risk:{atr:1.5}};
   const cfg={initial_equity:50000,risk_pct:0.5,atr_stop_k:2,slippage_bps:slip,_signals:signals};
   const result=runWindow.call(engine,data,'XUSDT','1h',cfg),t=result.sample_trades[0];
   eq(result.trades,1);eq(t.reason,'eod');near(t.entry,100*(1+slip/10000),1e-8);near(t.exit,100*(1-slip/10000),1e-8);
   const qty=250/3,pnl=qty*(t.exit*(1-0.002)-t.entry*(1+0.002));
   near(t.pnl,pnl);near(result.final_equity,50000+pnl);eq(result.final_equity,result.equity_curve.at(-1)[1]);
   eq(result.max_drawdown,+((50000-result.final_equity)/50000*100).toFixed(1));
   eq(Date.parse(t.entry_t),data[61].t*1000);eq(Date.parse(t.exit_t),data.at(-1).t*1000);
   eq(result.equity_curve.length,n-60,'internal curve is never truncated before chained MDD');
  }
  // Real walk-forward aggregation over stubbed window results isolates chaining
  // from strategy, including zero endpoints and a peak outside display tail.
  function fakeWindow(end,points){return {trades:1,wins:0,gross_profit:0,gross_loss:100-end,expectancy_r:-1,
   initial_equity:100,final_equity:end,max_drawdown:10,window_start:'',window_end:'',equity_curve:points,sample_trades:[]};}
  let index=0;const fixtures=[fakeWindow(90,[[1,120],[2,90]]),fakeWindow(90,[[3,100],[4,90]])];
  engine.runWindow=()=>fixtures[index++];
  let wf=engine.runWalkForward(rows(100),'XUSDT','1h',{initial_equity:100,train_bars:60,eval_bars:20});
  eq(wf.aggregate.windows,2);eq(wf.aggregate.final_equity,81);eq(wf.aggregate.equity_curve.at(-1)[1],81);eq(wf.aggregate.max_drawdown,32.5);
  ok(wf.gate.state!=='PASS','browser cannot grant promotion');ok(Number.isFinite(Date.parse(wf.generated_at)));
  index=0;fixtures.splice(0,2,fakeWindow(0,[[1,0]]),fakeWindow(150,[[2,150]]));
  wf=engine.runWalkForward(rows(100),'XUSDT','1h',{initial_equity:100,train_bars:60,eval_bars:20});
  eq(wf.aggregate.final_equity,0);eq(wf.aggregate.max_drawdown,100);eq(wf.aggregate.equity_curve.at(-1)[1],0);
  index=0;fixtures.splice(0,2,fakeWindow(100,Array.from({length:500},(_,i)=>[i,i===0?200:100])),fakeWindow(100,[[501,100]]));
  wf=engine.runWalkForward(rows(100),'XUSDT','1h',{initial_equity:100,train_bars:60,eval_bars:20});
  eq(wf.aggregate.equity_curve.length,400);eq(wf.aggregate.max_drawdown,50);engine.runWindow=runWindow;
  // All three server computations: one POST, unchanged auth headers, no silent
  // local substitution after authorization, transport, JSON or shape failures.
  const auth=api._authHeaders.bind(api);api._authHeaders=()=>({...auth(),'X-CSRF-Token':'fixture-csrf'});api.postError=async()=>({ok:true});
  let localCalls=0;const getRows=api._getCandlesForBacktest;api._getCandlesForBacktest=()=>{localCalls++;return rows(200);};
  const calls=[()=>api.runBacktest('BTCIRT','1h',{bars:777}),()=>api.runGridSearch('BTCIRT','1h',undefined,777),()=>api.runGridMulti(['BTCIRT'],'1h',undefined,400)];
  for(const call of calls)for(const scenario of ['401','403','503','network','json','empty','malformed']){
   let requests=0;w.fetch=async(u,o)=>{requests++;eq(o.method,'POST');eq(o.headers['X-CSRF-Token'],'fixture-csrf');
    if(scenario==='network')throw Error('offline');
    return {ok:!/^\d+$/.test(scenario),status:/^\d+$/.test(scenario)?+scenario:200,json:async()=>{if(scenario==='json')throw Error('invalid JSON');return scenario==='malformed'?{ok:true}:{ };}};
   };
   const result=await call();eq(result.ok,false);eq(result.unavailable,true);eq(result.error,'backend_result_unavailable');eq(localCalls,0);eq(requests,1);
  }
  const valid=[{ok:true,report:{aggregate:{},generated_at:'2026-10-29T08:00:00Z'}},{ok:true,heatmap:{values:[]},best:{}},{ok:true,results:[]}];
  for(let i=0;i<calls.length;i++){
   let body=null;w.fetch=async(u,o)=>{body=JSON.parse(o.body);return {ok:true,status:200,json:async()=>valid[i]};};
   eq(await calls[i](),valid[i]);eq(body.tf,'1h');if(i===1)eq(body.bars,777,'Grid bars actually reach the supported server field');
   w.fetch=async()=>({ok:true,status:200,json:async()=>({ok:false,error:'refused'})});eq((await calls[i]()).error,'refused');eq(localCalls,0);
  }
  // Explicit standalone still works, is named and does not call the backend.
  api.standaloneMobile=true;w.fetch=()=>{throw Error('Standalone must not call backend');};
  const localRun=await calls[0]();eq(localRun.ok,true);eq(localRun.standalone,true);eq(localRun.report.engine,'browser-experimental');eq(localRun.report.gate.state,'SYNTHETIC_DATA');
  const realGrid=engine.gridSearch;engine.gridSearch=()=>({ok:true,best:{},heatmap:{values:[]}});
  eq((await calls[1]()).standalone,true);eq((await calls[2]()).standalone,true);engine.gridSearch=realGrid;api._getCandlesForBacktest=getRows;
  // Actual UI report renderer; stub chart library only (not result calculation).
  let notifications=0;w.toast=()=>{notifications++;};w.LightweightCharts={createChart:()=>({addAreaSeries:()=>({setData:()=>{}}),timeScale:()=>({fitContent:()=>{}})})};
  const report={symbol:'BTCIRT',tf:'1h',config_hash:'fixture',run_id:'distinct-run',generated_at:'2026-10-29T08:00:00Z',train_bars:240,eval_bars:120,
   gate:{state:'PASS',message:'checks passed',checks:{},failed:[]},aggregate:{windows:3,trades:40,profit_factor:1.4,max_drawdown:5,expectancy_r:0.2,equity_curve:[[1700000000000,49990]]},
   details:[{sample_trades:[{entry_t:'2026-10-28T01:00:00Z',exit_t:'2026-10-28T02:00:00Z',entry:100,exit:101,pnl:1,r:0.1,reason:'eod'}]}]};
  const response={ok:true,report,freshness:{stale:true,age_bars:8,reason:'old'}};
  w.btRender(response);eq(notifications,1);const summary=d.getElementById('btSummary').textContent;
  ok(!d.getElementById('btFreshChip').classList.contains('hidden'));ok(d.getElementById('btFreshChip').title.includes('8'));
  ok(d.getElementById('btHash').title.includes('distinct-run'));ok(d.getElementById('btHash').title.includes(lang==='fa'?'نه شناسه':'not a complete'));
  ok(d.getElementById('btGateMsg').textContent.includes(lang==='fa'?'تضمین سود':'profit guarantee'));
  const cells=d.querySelector('#btTradesBody tr').cells;ok(cells[0].textContent!==cells[1].textContent);eq(cells[0].textContent,new w.Date(report.details[0].sample_trades[0].entry_t).toLocaleString(w.dateLocale()));
  w.drawBt();eq(notifications,1,'view-only redraw never announces a new run');eq(d.getElementById('btSummary').textContent,summary,'redraw preserves generation time');ok(!d.getElementById('btFreshChip').classList.contains('hidden'));
  w.btRender({ok:true,report});ok(d.getElementById('btFreshChip').textContent.includes(lang==='fa'?'نامشخص':'unknown'));
  w.btRender({ok:true,report,freshness:{stale:false}});ok(d.getElementById('btFreshChip').classList.contains('hidden'));
  w.btRender({ok:true,report,standalone:true});ok(d.getElementById('btSummary').textContent.includes(lang==='fa'?'مرورگر آزمایشی':'experimental browser'));
  w.btRender({ok:true,report:{...report,generated_at:'not-a-date',details:[{sample_trades:[{entry:100,reason:'<img src=x onerror=alert(1)>',pnl:1}]}]}});
  ok(d.getElementById('btSummary').textContent.includes(lang==='fa'?'زمان تولید نامشخص':'generation time unknown'));eq(d.querySelector('#btTradesBody tr').cells[0].textContent,'—');eq(d.querySelectorAll('#btTradesBody img').length,0);
  // Expanded icon gate exposed a visual regression from phase6. Restore the
  // actual local helper, not a production workaround for a regex assertion.
  const iconRenderer=w.coinIcon;let detailCalls=0;
  api.getBackendFA=async()=>{detailCalls++;throw Error('Full scan should not refetch');};
  w.live.ranked=[{symbol:'BTCIRT',direction:'LONG',composite:80,fa_score:72,fa:{score:72,profile:'live',fresh:true,vetoes:[]}}];
  w.live.unavailable=false;w.live.timeframe='1h';w.live.lastScanAt=1234567;
  await w.NBX_APP.refreshFundamentals();
  const logo=d.querySelector('#faList [data-fa-symbol="BTCIRT"] img[data-coin="BTC"]');
  ok(logo,'Fundamentals actual row renders the bundled coin logo again');eq(logo.width,20);eq(detailCalls,0);eq(d.querySelector('#faList [data-fa-score]').textContent,'72.0');
  // Multi's three checks are not the ADR gate; missing MDD is unknown, not 0.
  w.coinIcon=()=>'';
  const best={params:{risk_pct:0.5,atr_stop_k:2},aggregate:{profit_factor:1.4,max_drawdown:5,trades:40},score:2};
  w.renderMulti({tf:'1h',results:[{symbol:'BTCIRT',best},{symbol:'ETHIRT',best:{...best,aggregate:{profit_factor:2,trades:40}}},{symbol:'XUSDT',error:'<img src=x onerror=alert(1)>'}]});
  const multi=d.getElementById('multiGridBody');ok(multi.rows[0].cells[6].textContent.includes(lang==='fa'?'مقدماتی':'preliminary'));ok(multi.rows[1].cells[6].textContent.includes(lang==='fa'?'نامشخص':'unknown'));eq(multi.querySelectorAll('img').length,0);
  const grid={symbol:'BTCIRT',tf:'1h',combo_count:1,heatmap:{x:[0.5],y:[2],values:[[{profit_factor:1.4,trades:8}]]},best};
  w.renderGrid(grid);ok(d.getElementById('gridMeta').textContent.includes('1h'));ok(d.getElementById('gridMeta').textContent.includes(lang==='fa'?'پاسخ سرور':'server response'));
  w.renderGrid({...grid,standalone:true});ok(d.getElementById('gridMeta').textContent.includes(lang==='fa'?'مرورگر آزمایشی':'experimental browser'));
  w.renderGrid({...grid,heatmap:{values:[]}});eq(d.getElementById('gridBest').textContent,'');ok(d.getElementById('gridHeat').textContent.includes(lang==='fa'?'نتیجه‌ای':'No result'));
  w.coinIcon=iconRenderer;
  // Actual measurement/evidence handlers, not a mirror of the clamp predicate.
  for(const factor of [1,0.85,1.25,1.1]){
   api.get=async()=>({ok:true,symbol:'BTCIRT',tf:'1h',scales:{entry:factor},thresholds:{base:{},recommended:{}},frequency:{},volatility:{},freshness:{stale:false}});
   await w.adMeasureThresholds();
   const clamped=d.getElementById('adState').textContent.includes(w.eval("t('ad.clamped')"));
   eq(clamped,factor===0.85||factor===1.25);ok(d.getElementById('adState').textContent.includes(w.eval("t('ad.notApplied')")));
  }
  api.get=async()=>({ok:true,baseline:{profit_factor:1},adaptive:{profit_factor:1.1},delta:{},windows:{count:3,train_bars:100,eval_bars:50},verdict:'favours-adaptive'});
  await w.adEvidenceRun();
  ok(d.getElementById('adEvWhy').textContent.includes(lang==='fa'?'فرمول متفاوت':'different formula'));
  ok(d.getElementById('adEvWhy').textContent.includes(lang==='fa'?'کل تاریخچه':'full history'));
  ok(d.getElementById('adEvWhy').textContent.includes(lang==='fa'?'شاهد مستقل OOS نیست':'not independent OOS evidence'));
  const other=lang==='fa'?'en':'fa';w.eval(`LANG='${other}';applyStaticI18n();`);w.drawBt();
  ok(d.getElementById('multiGridBody').rows[0].cells[6].textContent.includes(other==='fa'?'مقدماتی':'preliminary'),'language redraw reuses Multi result without compute');
  w.eval(`LANG='${lang}';applyStaticI18n();`);w.drawBt();
  ok(!errors.some(x=>/ReferenceError|SyntaxError|Uncaught/.test(x)),errors.join('\n'));
 }finally{dom.window.close();}
}
console.log(`Phase 7: ${checks} checks PASS (canonical/mobile × FA/EN; offline engines/API/renderers)`);
})().catch(e=>{console.error(e);process.exit(1);});
