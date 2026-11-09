/* 2.5.2 — Signal explorer card (ADR-0055): the beginner-facing row shows
 * symbol · direction · score · regime · price · stop · target 1/2 · freshness,
 * and the engineering evidence (gate, spread/depth, FA provenance, component
 * bars, RSI/ADX/ATR, cached curve) stays behind the "Details" button.
 * Levels are model levels from the scan's ATR and the server's published R:R
 * ladder; without that ladder the targets are "—", never a private default.
 * No network, no orders, no policy change. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
let checks=0;const eq=(a,b,m)=>{assert.equal(a,b,m);checks++;};const ok=(a,m)=>{assert.ok(a,m);checks++;};
const turn=()=>new Promise(r=>setImmediate(r));
const app=fs.readFileSync('nbx-app.js','utf8')
  .replace('  window.NBX_APP = {','  window.__card={renderSignalsLive};\n  window.NBX_APP = {')
  .replace("  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);\n  else start();",'');
const nowS=1760000000;
const bars=(px,n=30)=>Array.from({length:n},(_,i)=>({t:nowS-900*(n-i),o:px,h:px*1.01,l:px*0.99,c:px,v:1}));
const row=(symbol,extra={})=>({symbol,score:74,composite:73,composite_basis:'ta+liq',direction:'LONG',regime:'TREND',liquidity_score:70,fa_score:null,fa:null,
  lastClose:1000,risk:{atr:20,stopDistance:40,stopPct:4},components:{trend:78,momentum:64,volume:52,structure:70},diagnostics:{rsi:61.2,adx:31.4,pctB:0.83,hist:0.001},...extra});
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
  api.standaloneMobile=false;api.getBackendCandles=async()=>({ok:true,rows:[],unavailable:true});
  w.eval(app);w.eval(`LANG='${lang}';PREFS.numFmt='en';applyStaticI18n();`);await turn();
  const live=w.live,fx=w.__card;d.getElementById('page-signals').classList.remove('hidden');
  w.Date.now=()=>nowS*1000;
  const list=[row('BTCIRT'),row('ETHIRT',{direction:'NEUTRAL',composite:50,score:48}),row('TONIRT',{lastClose:undefined,risk:{}})];
  live.ranked=list;live.unavailable=false;live.timeframe='15m';live.showAll=false;live.lastScanAt=nowS*1000;
  live.signalSnapshot={rows:list,tf:'15m',received:nowS*1000,age:42,stale:false};
  live.risk={caps:{rrLevels:[1.2,2.0]}};
  live.gates={mode:'shadow',applied:false,verdicts:{BTCIRT:{code:'ok',inputs:{spread_bps:12,depth_usd:5400}}}};
  eng.candles['BTCIRT:15m']=bars(1000);
  const frozen=JSON.stringify(list);Object.freeze(list);list.forEach(Object.freeze);
  fx.renderSignalsLive();
  eq(JSON.stringify(list),frozen,'display never mutates scan rows');
  const btc=d.querySelector('[data-sig-sym="BTCIRT"]');ok(btc);
  const lv=[...btc.querySelectorAll('.sig-lv b')].map(b=>b.textContent.replace(/[\u2066-\u2069\u00a0]/g,' ').trim());
  eq(lv.length,4);
  ok(lv[0].includes('1,000'),'price = lastClose: '+lv[0]);
  ok(lv[1].includes('960') && /-4\.0[%٪]/.test(lv[1]),'stop = px - 2×ATR with pct: '+lv[1]);
  ok(lv[2].includes('1,048') && /\+4\.8[%٪]/.test(lv[2]),'TP1 = px + 1.2×dist: '+lv[2]);
  ok(lv[3].includes('1,080') && /\+8\.0[%٪]/.test(lv[3]),'TP2 = px + 2.0×dist: '+lv[3]);
  ok(btc.querySelector('.sig-ladder'),'ladder drawn when stop and price are known');
  ok(btc.querySelector('[data-sig-fresh]').textContent.includes('15'),'per-symbol freshness from the cached last candle');
  // Evidence is present for tests/screen readers but hidden until asked for.
  const ev=btc.querySelector('[data-sig-evidence]');ok(ev.hasAttribute('hidden'));
  ok(ev.querySelector('[data-signal-gate]').textContent.includes('5,400'));
  eq(ev.querySelectorAll('.sig-comp').length,4,'four component bars');
  ok(ev.textContent.includes('RSI') && ev.textContent.includes('ADX') && ev.textContent.includes('ATR%'));
  ok(ev.querySelector('svg[data-spark]'),'cached curve lives in Details, not the row');
  ok(!btc.querySelector('.sig-head').textContent.includes(lang==='fa'?'پروکسی':'proxy'),'turnover proxy wording not in the default view');
  const btn=btc.querySelector('[data-sig-details]');eq(btn.getAttribute('aria-expanded'),'false');
  btn.click();eq(ev.hasAttribute('hidden'),false);eq(btn.getAttribute('aria-expanded'),'true');
  eq(d.querySelectorAll('#sigList .sig-detail').length,0,'Details button does not open the candle panel');
  fx.renderSignalsLive();eq(d.querySelector('[data-sig-sym="BTCIRT"] [data-sig-evidence]').hasAttribute('hidden'),false,'open state survives a repaint');
  d.querySelector('[data-sig-sym="BTCIRT"] [data-sig-details]').click();
  eq(d.querySelector('[data-sig-sym="BTCIRT"] [data-sig-evidence]').hasAttribute('hidden'),true);
  // Row click opens the candle panel with the same model levels drawn on it.
  w.toggleSignalDetail(d.querySelector('[data-sig-sym="BTCIRT"]'));await turn();const panel=d.querySelector('#sigList .sig-detail');ok(panel,'row click opens candle panel');
  eq(panel.querySelectorAll('[data-level]').length,3,'stop + two targets drawn');
  ok(panel.querySelector('[data-level="stop"]').textContent.includes('960'));ok(panel.querySelector('[data-level="tp2"]').textContent.includes('1,080'));
  ok(panel.querySelector('[data-crosshair]') && panel.querySelector('[data-candle-tip]'),'crosshair + tooltip scaffolding present');
  ok(w.__candleHover && w.__candleHover.BTCIRT && w.__candleHover.BTCIRT.bars.length===30,'hover geometry stored for this symbol');
  panel.querySelector('[data-close-detail]').click();
  // NEUTRAL: stop shown (model), targets refused with a reason.
  const eth=[...d.querySelectorAll('[data-sig-sym="ETHIRT"] .sig-lv b')].map(b=>b.textContent.trim());
  ok(eth[1].includes('960'));eq(eth[2],'—');eq(eth[3],'—');
  ok(d.querySelector('[data-sig-sym="ETHIRT"] .sig-foot').textContent.includes(lang==='fa'?'فقط برای سیگنال خرید':'only for a Long'));
  ok(d.querySelector('[data-sig-sym="ETHIRT"] [data-sig-fresh]').textContent.includes('42'),'no cached candle → scan age');
  // No price / no ATR: dashes, no ladder, no invented numbers.
  const ton=d.querySelector('[data-sig-sym="TONIRT"]');
  eq([...ton.querySelectorAll('.sig-lv b')].map(b=>b.textContent.trim()).join(''),'————');ok(!ton.querySelector('.sig-ladder'));
  // A transient failure of the risk read keeps the last published ladder…
  live.risk=null;fx.renderSignalsLive();
  ok([...d.querySelectorAll('[data-sig-sym="BTCIRT"] .sig-lv b')][2].textContent.includes('1,048'),'last good ladder survives a failed risk read');
  // …but with no ladder ever published, targets stay "—" (no private 1.5/2.5 copy).
  live.rrLevels=null;fx.renderSignalsLive();
  const noRR=[...d.querySelectorAll('[data-sig-sym="BTCIRT"] .sig-lv b')].map(b=>b.textContent.trim());
  ok(noRR[1].includes('960'));eq(noRR[2],'—');eq(noRR[3],'—');
  ok(d.querySelector('[data-sig-sym="BTCIRT"] .sig-foot').textContent.includes(lang==='fa'?'نردبان':'ladder'));
  // 2.5.2 item 10 — advisory thin-depth label beside the freshness line.
  ok(!d.querySelector('[data-sig-sym="BTCIRT"] [data-sig-depth]'),'deep book (5400 $): no label');
  live.gates={mode:'shadow',applied:false,depth_warn:{usd:562,applied:false},verdicts:{
    BTCIRT:{code:'ok',depth_thin:true,inputs:{spread_bps:12,depth_usd:410}},
    TONIRT:{code:'no-book',depth_thin:null,inputs:{}}}};
  fx.renderSignalsLive();
  const dw=d.querySelector('[data-sig-sym="BTCIRT"] [data-sig-depth]');
  ok(dw,'thin book gets the label');ok(dw.className.includes('amber'),'label is yellow');
  ok(/410|۴۱۰/.test(dw.textContent)&&/562|۵۶۲/.test(dw.textContent),'label names depth and threshold: '+dw.textContent);
  ok(dw.closest('[data-sig-fresh]'),'label sits on the freshness line');
  ok(/(سفارشی را نمی‌بندد|blocks nothing)/.test(dw.title),'title says it is advisory');
  ok(!d.querySelector('[data-sig-sym="TONIRT"] [data-sig-depth]'),'unknown depth: no label, no invented number');
  eq([...d.querySelectorAll('[data-sig-sym]')].map(n=>n.dataset.sigSym)[0],'BTCIRT','order unchanged by the label');
  // an older server without depth_thin: nothing shown
  live.gates={mode:'shadow',applied:false,verdicts:{BTCIRT:{code:'ok',inputs:{spread_bps:12,depth_usd:100}}}};
  fx.renderSignalsLive();ok(!d.querySelector('[data-sig-depth]'),'old server: no label');
  ok(!errors.some(x=>/Uncaught|ReferenceError|SyntaxError/.test(x)),errors.join('\n'));
 }finally{dom.window.close();}
}
console.log(`Signal card: ${checks} checks PASS (canonical/mobile × FA/EN)`);
})().catch(e=>{console.error(e);process.exit(1);});
