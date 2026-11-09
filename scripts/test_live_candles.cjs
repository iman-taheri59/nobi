/* 2.5.3 — charts update without a page reload.
 * Before: `engine.candles` was filled once and never refreshed in backend mode,
 * so the overview chart and the signal-list sparklines showed the candles of
 * the first minute until the user reloaded. Now a cached copy older than its
 * freshness window is refetched on the next scan, and the big chart repaints
 * in place (series.update) instead of resetting the view (setData).
 * No network, no orders, no policy change. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
let checks=0;const eq=(a,b,m)=>{assert.equal(a,b,m);checks++;};const ok=(a,m)=>{assert.ok(a,m);checks++;};
const turn=()=>new Promise(r=>setImmediate(r));
const app=fs.readFileSync('nbx-app.js','utf8')
  .replace("  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);\n  else start();",'');
const nowS=1760000000;
const bars=(px,n=30,last=nowS)=>Array.from({length:n},(_,i)=>({t:last-900*(n-1-i),o:px,h:px*1.01,l:px*0.99,c:px,v:1}));
(async()=>{
 const html=fs.readFileSync('index.html','utf8').replace(/<script id="nbx-inline-nbx-app-js">[\s\S]*?<\/script>/,'');
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const dom=new JSDOM(html,{url:'https://dashboard.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,
  beforeParse(w){w.setInterval=()=>0;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.fetch=()=>{throw Error('Network forbidden');};}});
 const w=dom.window,d=w.document;
 if(d.readyState!=='complete')await new Promise(r=>w.addEventListener('load',r,{once:true}));await turn();
 w.eval(fs.readFileSync('nbx-core.js','utf8'));w.NBX.core=w.NBX.createCore();const api=w.NBX.core.api,eng=w.NBX.core.engine;
 api.standaloneMobile=false;
 const calls=[];let px=1000;
 api.getBackendCandles=async(sym,tf)=>{calls.push(sym+':'+tf);return {ok:true,rows:bars(px,30,lastT),source:'nobitex'};};
 let lastT=nowS;
 w.eval(app);w.eval("LANG='en';PREFS.numFmt='en';applyStaticI18n();");await turn();
 const live=w.live,fx=w.NBX_APP._candles;
 let now=nowS*1000;w.Date.now=()=>now;
 // a fake lightweight-charts series: count setData vs update
 const series={sets:0,updates:0,setData(b){this.sets++;this.last=b;},update(b){this.updates++;this.lastUpdate=b;}};
 w.eval('candleSeries = window.__series;');w.__series=series;w.eval('candleSeries = window.__series;');
 api.getPaperState=async()=>({ok:false});
 eng.rankSignals=()=>[{symbol:'BTCIRT',composite:70}];
 live.activeSymbol='BTCIRT';live.timeframe='15m';

 // 1) overview: first scan fetches and paints with setData
 await fx.updateOverviewFromLive();
 eq(calls.filter(c=>c==='BTCIRT:15m').length,1,'first overview fetch');
 eq(series.sets,1,'first paint is setData');
 ok(eng.candleAt&&eng.candleAt['BTCIRT:15m']===now,'receipt time recorded');

 // 2) second scan 10 s later: cache is fresh → no refetch (the bug fix must not spam the server)
 now+=10_000;await fx.updateOverviewFromLive();
 eq(calls.filter(c=>c==='BTCIRT:15m').length,1,'fresh cache is reused within 45 s');

 // 3) a scan 50 s later: stale → refetch; same history → in-place update, no setData
 now+=50_000;px=1010;
 await fx.updateOverviewFromLive();
 eq(calls.filter(c=>c==='BTCIRT:15m').length,2,'stale cache is refetched on the next scan');
 eq(series.sets,1,'same history repaints in place');
 ok(series.updates>=1,'live bar updated');
 eq(series.lastUpdate.close,1010,'updated bar carries the new close');
 eq(d.getElementById('lastPrice').textContent.replace(/[^0-9.]/g,'').slice(0,4),'1010','last price under the chart follows');

 // 4) a newly closed candle (one more bar at the end) still updates in place
 now+=50_000;lastT=nowS+900;
 await fx.updateOverviewFromLive();
 eq(series.sets,1,'one new bar: still update(), no reset');

 // 5) timeframe switch → different key → setData
 now+=50_000;live.timeframe='1h';
 await fx.updateOverviewFromLive();
 eq(series.sets,2,'timeframe switch resets the series');
 eq(calls.filter(c=>c==='BTCIRT:1h').length,1,'new timeframe fetched');

 // 6) sparklines: filled rows are refetched once older than 90 s, not before
 const spark=['ETHIRT','TONIRT'];
 d.getElementById('page-signals').classList.remove('hidden');
 live.ranked=spark.map(s=>({symbol:s,composite:60,direction:'LONG',score:60}));live.unavailable=false;live.timeframe='15m';
 live.signalSnapshot={rows:live.ranked,tf:'15m',received:now,age:1,stale:false};
 await fx.prefetchSparkCandles(spark,'15m');
 eq(calls.filter(c=>c==='ETHIRT:15m').length,1,'spark first fetch');
 now+=30_000;await fx.prefetchSparkCandles(spark,'15m');
 eq(calls.filter(c=>c==='ETHIRT:15m').length,1,'spark fresh within 90 s');
 now+=70_000;await fx.prefetchSparkCandles(spark,'15m');
 eq(calls.filter(c=>c==='ETHIRT:15m').length,2,'spark refetched after 90 s');
 eq(calls.filter(c=>c==='TONIRT:15m').length,2,'every displayed row, not only the first');

 eq(errors.filter(e=>!/Not implemented|canvas|Network forbidden/.test(e)).length,0,'no jsdom errors: '+errors.join(' | '));
 console.log(`PASS: live candle refresh — ${checks} checks`);
})().catch(e=>{console.error(e);process.exit(1);});
