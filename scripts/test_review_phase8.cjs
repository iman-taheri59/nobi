/* Signals Explorer: real renderers and read orchestration, fake API/cache.
 * No real network/providers, credentials, orders, policy change or deployment. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
let checks=0;const eq=(a,b,m)=>{assert.equal(a,b,m);checks++;};const ok=(a,m)=>{assert.ok(a,m);checks++;};
const turn=()=>new Promise(r=>setImmediate(r));
const text=(d,id)=>d.getElementById(id).textContent;
const app=fs.readFileSync('nbx-app.js','utf8').replace('  window.NBX_APP = {','  window.__phase8={renderSignalsLive,syncSignalsHeader,signalSnapshotMeta,signalFaState,signalGateRow,prefetchSparkCandles};\n  window.NBX_APP = {').replace("  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);\n  else start();",'');
const bars=(fall=false)=>Array.from({length:12},(_,i)=>({t:1700000000+i*900,o:100,h:110,l:90,c:fall?110-i:100+i,v:1}));
const record=(symbol,extra={})=>({symbol,composite:80,score:75,fa_score:60,fa:{score:60,profile:'live',fresh:true,vetoes:[]},direction:'LONG',regime:'TREND',liquidity_score:90,components:{trend:20,momentum:10},risk:{stopPct:1},...extra});
(async()=>{
for(const file of ['index.html','nobitex-mobile.html'])for(const lang of ['fa','en']){
 const html=fs.readFileSync(file,'utf8').replace(/<script id="nbx-inline-nbx-app-js">[\s\S]*?<\/script>/,'');
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const dom=new JSDOM(html,{url:'https://dashboard.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,
  beforeParse(w){w.setInterval=()=>0;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.fetch=()=>{throw Error('Network forbidden');};}});
 const w=dom.window,d=w.document;
 try{
  if(d.readyState!=='complete')await new Promise(r=>w.addEventListener('load',r,{once:true}));await turn();
  w.eval(fs.readFileSync('nbx-core.js','utf8'));w.NBX.core=w.NBX.createCore();const api=w.NBX.core.api,eng=w.NBX.core.engine;
  api.standaloneMobile=false;api.getExecMode=async()=>({ok:true,default:'semi',symbols:{}});api.getBackendFA=()=>{throw Error('Renderer must not fetch FA');};
  w.eval(app);w.eval(`LANG='${lang}';PREFS.numFmt='en';applyStaticI18n();`);await turn();
  const fx=w.__phase8,live=w.live;d.getElementById('page-signals').classList.remove('hidden');
  let interval=120,reads=0;const originalPref=w.pref;w.pref=k=>k==='scanSec'?interval:originalPref(k);
  api.getBackendCandles=async()=>{reads++;return {ok:true,rows:[],unavailable:true};};
  const list=[record('BTCIRT'),record('ETHIRT',{fa:{score:60,profile:'live',cached:true}}),
   record('TONIRT',{fa:{score:60,profile:'fallback'}}),record('UNMAPPEDIRT',{fa:{score:60,profile:'none'}}),
   record('PARTIALIRT',{fa:null}),record('ABSENTIRT',{fa_score:null,fa:null,composite_basis:'ta+liq'}),
   record('MISMATCHIRT',{fa:{score:30,profile:'live'}}),record('FAILEDFAIRT',{fa:{ok:false,score:60}}),
   ...Array.from({length:31},(_,i)=>record('COIN'+i+'IRT'))];
  live.ranked=list;live.unavailable=false;live.timeframe='15m';live.showAll=false;live.lastScanAt=1000;
  const snapshot={scored:list,tf:'15m',ts:'2026-10-29T08:00:00Z',age_s:230,stale:true};
  live.signalSnapshot=fx.signalSnapshotMeta(snapshot,'15m');live.signalSnapshot.received=w.Date.now()-4000;
  live.scanFreshness=null;live.faCoverage={scored:39,total:39,live:4,fallback:35,failed:0};
  live.gates={mode:'shadow',applied:false,counts:{rows:39,ok:20,would_block:1,unknown:18},
   would_block:['THINIRT'],unknown:['BTCIRT'],inputs:{books:21,stale_books:5,oldest_usable_s:30,book_gate_ttl_s:300},
   verdicts:{BTCIRT:{code:'no-book',inputs:{}},ETHIRT:{code:'ok',inputs:{spread_bps:25,depth_usd:1234}},TONIRT:{code:'spread-wide',inputs:{spread_bps:90,depth_usd:0}}}};
  const frozen=JSON.stringify(list);for(const s of list){Object.freeze(s);if(s.fa)Object.freeze(s.fa);}Object.freeze(list);
  eng.candles['BTCIRT:15m']=bars(true);eng.candles['ETHIRT:15m']=bars(false);eng.candles['TONIRT:15m']=bars();
  fx.renderSignalsLive();
  eq(d.querySelectorAll('#sigList .sig-row').length,15);eq(JSON.stringify(list),frozen,'display cannot alter scores/order/TA/veto data');eq(reads,0);
  const states=['server','cached','static','unmapped','score-only','absent','mismatch','score-only'];
  for(let i=0;i<states.length;i++)eq(d.querySelectorAll('#sigList [data-signal-fa]')[i].dataset.signalFa,states[i]);
  ok(text(d,'sgFaProvenanceSummary').includes('39'));ok(!text(d,'sgFaProvenanceSummary').includes('static: 35'));
  const coverage=d.getElementById('sgFaCoverage');ok(coverage.title.includes(lang==='fa'?'نه لزوماً ثابت':'not necessarily static'));ok(!coverage.className.includes('emerald'));
  const cycle=()=>d.querySelector('[data-i18n="sg.cycle"]').textContent;
  const receipt=()=>d.querySelector('[data-i18n="sg.last"]').textContent;
  ok(cycle().includes('120'));ok(receipt().includes(lang==='fa'?'دریافت صفحه':'Page received'));
  ok(text(d,'sgReceiptSummary').includes('230'));ok(text(d,'sgReceiptSummary').includes(lang==='fa'?'تولید اسکن':'scan generation'));
  ok(text(d,'sgReceiptSummary').includes(lang==='fa'?'تازگی کندل نامشخص':'Candle freshness unknown'));
  for(const n of [30,300,0,NaN,-1]){
   interval=n;fx.syncSignalsHeader();
   if(n>0)ok(cycle().includes(String(n)));else ok(cycle().includes(n===0?(lang==='fa'?'دستی':'manual'):(lang==='fa'?'نامشخص':'unknown')));
  }
  eq(reads,0,'header changes never reschedule/fetch');interval=120;
  const gate=text(d,'sgGateSummary');for(const n of ['39','20','18','21','5','30','300'])ok(gate.includes(n),gate);
  ok(gate.includes(lang==='fa'?'پنج‌روزه نیست':'five-day trial'));ok(gate.includes(lang==='fa'?'گروه جدا':'disjoint group'));
  ok(d.querySelector('[data-sig-sym="BTCIRT"] [data-signal-gate]').textContent.includes('no-book'));
  const realBook=d.querySelector('[data-sig-sym="ETHIRT"] [data-signal-gate]').textContent;
  ok(realBook.includes('25'));ok(realBook.includes('1,234'));ok(realBook.includes(lang==='fa'?'نه پذیرش سفارش':'not order approval'));ok(realBook.includes(lang==='fa'?'سن دفتر':'book age'));
  const falling=d.querySelector('[data-sig-sym="BTCIRT"] svg[data-spark]'),rising=d.querySelector('[data-sig-sym="ETHIRT"] svg[data-spark]');
  eq(falling.dataset.spark,'cached');eq(rising.dataset.spark,'cached');eq(falling.querySelector('polyline').getAttribute('stroke'),'#a1a1aa');eq(rising.querySelector('polyline').getAttribute('stroke'),'#a1a1aa');
  ok(falling.getAttribute('aria-label').includes(lang==='fa'?'نه جهت':'not direction'));
  // Bare or another timeframe's cache cannot masquerade as selected-TF bars.
  eng.candles.WRONGIRT=bars();eng.candles['WRONGIRT:1h']=bars();
  const missing=w.spark(true,'WRONGIRT',{big:true,neutral:true,strictTF:true});ok(missing.includes('data-spark="unavailable"'));ok(!missing.includes('<svg'));ok(!missing.includes('unreachable'));
  eng.candles['BROKENIRT:15m']=Array.from({length:12},()=>({c:null}));
  ok(w.spark(false,'BROKENIRT',{neutral:true,strictTF:true}).includes('data-spark="unavailable"'),'null prices do not become a zero curve');
  eng.candleSource=eng.candleSource||{};eng.candleSource.TONIRT='synthetic';
  const synthetic=w.spark(false,'TONIRT',{neutral:true,strictTF:true});ok(synthetic.includes('data-spark="synthetic"'));ok(synthetic.includes('synthetic · '));
  // Default shared spark contract stays colored for other consumers.
  eq(new JSDOM(w.spark(true,'BTCIRT',{})).window.document.querySelector('polyline').getAttribute('stroke'),'#34d399');
  // TF drift is named, not hidden by a fresh redraw time.
  live.timeframe='1h';fx.syncSignalsHeader();ok(text(d,'sgReceiptSummary').includes(lang==='fa'?'گزارش برای 15m':'report is for 15m'));eq(reads,0);live.timeframe='15m';
  const before=w.Date.now;w.Date.now=()=>live.signalSnapshot.received-10000;fx.syncSignalsHeader();ok(receipt().includes('—'),'negative local age is unknown, not abs(age)');w.Date.now=before;
  const bad=fx.signalSnapshotMeta({scored:list,age_s:'0',stale:'false',ts:'bad'},'15m');eq(bad.age,null);eq(bad.stale,null);eq(bad.responseAt,null);
  live.signalSnapshot=bad;fx.syncSignalsHeader();ok(text(d,'sgReceiptSummary').includes(lang==='fa'?'وضعیت cache نامشخص':'cache state unknown'));
  live.signalSnapshot={...bad,rows:[]};fx.syncSignalsHeader();ok(text(d,'sgReceiptSummary').includes(lang==='fa'?'snapshot نامشخص':'snapshot’s receipt'));
  live.faCoverage=null;fx.renderSignalsLive();eq(coverage.textContent,'—');eq(coverage.title,'');
  // Show all may read missing price curves; no FA/score recomputation, ranking mutation or trade.
  w.sigShowAll(true);await turn();eq(d.querySelectorAll('#sigList .sig-row').length,39);eq(JSON.stringify(list),frozen);ok(reads>0);
  w.sigShowAll(false);eq(d.querySelectorAll('#sigList .sig-row').length,15);
  let clicked='';w.toggleSignalDetail=row=>{clicked=row.dataset.sigSym;};
  d.querySelector('[data-sig-sym="BTCIRT"]').click();eq(clicked,'BTCIRT');clicked='';
  d.querySelector('[data-sig-sym="BTCIRT"]').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));eq(clicked,'BTCIRT');
  // Forged gate state/counts and absent inputs are not a green clean verdict.
  for(const block of [null,{counts:{rows:39}}, {...live.gates,applied:true},{...live.gates,mode:'enforced'},
    {...live.gates,counts:{rows:39,ok:39,would_block:0,unknown:18}},{...live.gates,counts:{rows:39,ok:39,would_block:0,unknown:'0'}}]){
   w.sgRenderGates(block);ok(text(d,'sgGateSummary').includes(lang==='fa'?'موجود نیست':'no evaluable evidence'));ok(d.getElementById('sgGateChip').classList.contains('hidden'));
  }
  w.sgRenderGates({mode:'shadow',applied:false,counts:{rows:1,ok:0,would_block:0,unknown:1}});
  ok(text(d,'sgGateSummary').includes('—'));ok(text(d,'sgGateSummary').includes(lang==='fa'?'نیست':'not'));
  w.sgRenderGates({mode:'shadow',applied:false,counts:{rows:1,ok:0,would_block:1,unknown:0},would_block:'not-an-array'});
  ok(text(d,'sgGateSummary').includes('1'),'malformed name list cannot crash scalar summary');
  // Markup/invalid scalar fixtures must not blank the list or inject handlers.
  const attack='<img src=x onerror=alert(1)>';
  live.ranked=[record('BTCIRT',{composite:'bad',score:'75',fa_score:'60',liquidity_score:'90',fa:{vetoes:attack},direction_raw:'NEUTRAL'}),
    record('ETHIRT',{fa:{score:60,profile:attack},direction_raw:attack})];
  live.gates={mode:'shadow',applied:false,verdicts:{BTCIRT:{code:attack,inputs:{spread_bps:'25',depth_usd:-1}}}};
  fx.renderSignalsLive();eq(d.querySelectorAll('#sigList .sig-row').length,2);eq(d.querySelectorAll('#sigList [onerror],#sigList img[src="x"],#sigList script').length,0);
  eq(d.querySelector('#sigList [data-signal-fa] b').textContent,'—');ok(text(d,'sigList').includes(attack));
  // Failure/empty clears every stale provenance label; it does not diagnose all markets.
  live.ranked=[];live.unavailable=true;fx.renderSignalsLive();eq(coverage.title,'');eq(coverage.textContent,'—');
  ok(text(d,'sgReceiptSummary').includes(lang==='fa'?'ثابت نمی‌کند':'does not prove'));ok(text(d,'sgGateSummary').includes(lang==='fa'?'موجود نیست':'no evaluable evidence'));
  ok(!text(d,'sgFaProvenanceSummary').includes('39'));ok(d.querySelector('[data-retry-scan]'));
  eq(w.sgGateNow(),null);eq(w.sgFreshNow(),null);w.sgRenderGates(w.sgGateNow());ok(text(d,'sgGateSummary').includes(lang==='fa'?'موجود نیست':'no evaluable evidence'));
  live.unavailable=false;fx.renderSignalsLive();ok(!d.querySelector('[data-retry-scan]'),'empty nonfailure cannot retain the old failure widget');
  // Real read orchestration writes the metadata; mock read endpoints, not scanner.
  api.checkBackend=async()=>({backend:true,upstream:{ok:true}});api.getBackendUniverse=async()=>({symbols:[],source:'live'});
  const scanRows=[record('BTCIRT')];api.getBackendSignals=async tf=>({ok:true,scored:scanRows,tf,age_s:42,stale:true,ts:'2026-10-29T08:01:00Z',source:'live'});
  api.getPaperPositions=async()=>({ok:false});api.getPaperMetrics=async()=>({ok:false});api.getBackendRiskState=async()=>({ok:false});
  api.getBackendFA=async()=>{throw Error('Complete FA needs no extra read');};api.getBackendCandles=async()=>({rows:[],unavailable:true});
  // Overview's ancillary read is independent of Signals metadata under test.
  await w.NBX_APP.scanNow();
  eq(live.signalSnapshot.rows,scanRows);eq(live.signalSnapshot.tf,'15m');eq(live.signalSnapshot.age,42);eq(live.signalSnapshot.stale,true);
  ok(text(d,'sgReceiptSummary').includes('42'));ok(!text(d,'sgReceiptSummary').includes('230'));
  const other=lang==='fa'?'en':'fa';w.eval(`LANG='${other}';applyStaticI18n();`);w.NBX_APP.refreshDisplay();
  ok(d.querySelector('[data-signal-liquidity]').textContent.includes(other==='fa'?'پروکسی':'turnover proxy'));
  ok(!errors.some(x=>/Uncaught|ReferenceError|SyntaxError/.test(x)),errors.join('\n'));
 }finally{dom.window.close();}
}
console.log(`Phase 8: ${checks} checks PASS (canonical/mobile × FA/EN; offline Signals renderers/read orchestration)`);
})().catch(e=>{console.error(e);process.exit(1);});
