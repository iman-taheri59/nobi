/* Fundamentals offline regression. Real client/renderers; fake scan/API only.
 * No provider/account/network, score-model change, orders or deployment. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
let checks=0;const eq=(a,b,m)=>{assert.equal(a,b,m);checks++;};const ok=(a,m)=>{assert.ok(a,m);checks++;};
const turn=()=>new Promise(r=>setImmediate(r));
function deferred(){let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};}
const app=fs.readFileSync('nbx-app.js','utf8').replace('  window.NBX_APP = {','  window.__phase6={updateFaPreview,renderFaPreview,invalidateFaPreview,faView};\n  window.NBX_APP = {').replace("  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);\n  else start();",'');
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
  const fx=w.__phase6,live=w.live;d.getElementById('page-fa').classList.remove('hidden');
  // Endpoint failures retain their actual layer; never accuse Nobitex/providers.
  const rawFA=api.getBackendFA.bind(api);const good={symbol:'BTCIRT',score:72,profile:'live',fresh:true,vetoes:[]};
  for(const value of [null,{}, {...good,score:NaN},{...good,score:Infinity},{...good,score:-1},{...good,score:101},{...good,score:'72'},{...good,symbol:'ETHIRT'},{...good,ok:false},{...good,unavailable:true}]){
   let gets=0;api.get=async()=>{gets++;return value;};const r=await rawFA('BTCIRT');eq(gets,1);eq(r.score,null);eq(r.error,'invalid-fa-response');
  }
  for(const status of [401,403,503]){api.get=async()=>{throw {meta:{status}};};const r=await rawFA('BTCIRT');eq(r.error,status===503?'fa-request-failed':'auth-required');eq(!!r.auth_error,status!==503);}
  api.get=async()=>good;eq((await rawFA('BTCIRT')).score,72);api.standaloneMobile=true;let localGets=0;api.get=async()=>{localGets++;return good;};eq((await rawFA('BTCIRT')).error,'fa-backend-required');eq(localGets,0);api.standaloneMobile=false;
  let requests=0;api.getBackendFA=async()=>{requests++;throw Error('Unexpected detail call');};api.getBackendRiskState=()=>{throw Error('FA must not read unrelated account risk');};
  const full=(symbol,score=60,extra={})=>({symbol,direction:'LONG',composite:80,fa_score:score,fa:{score,profile:'live',fresh:true,vetoes:[],...extra}});
  const set=rows=>{live.ranked=rows;live.unavailable=false;live.timeframe='15m';live.lastScanAt=1000;w.NBX.core.engine.premium_pct=.17;};
  const records=Array.from({length:39},(_,i)=>full('COIN'+i+'IRT'));
  records[0]=full('ADAIRT',72,{profile:'fallback',cached:true,evidence:{tvl:380000000}});
  records[1]=full('UNMAPPEDIRT',52.6,{profile:'none',source:'no-provider'});
  records[2]=full('CACHEDIRT',60,{source:'cached',cached:true,fresh:false,vetoes:['risk-a','risk-b']});
  records[3]=full('SERVERIRT',40,{vetoes:['risk-a']});records[4]=full('FRESHFLAGIRT',80);records[5]=full('UNKNOWNVETOIRT',50,{vetoes:null});
  set(records);await fx.updateFaPreview();eq(requests,0,'reuse full scan, do not re-fetch top six');eq(fx.faView.rows.length,39);eq(d.querySelectorAll('#faList [data-fa-symbol]').length,15);
  const expected=(72+60+40+80+50+33*60)/38;
  eq(d.getElementById('faAvg').textContent,'60.1','exclude unmapped neutral default');ok(Math.abs(parseFloat(d.getElementById('faAvgBar').style.width)-expected)<.000001);ok(Math.abs(Number(d.getElementById('faAvgBar').dataset.w)-expected)<.000001);
  eq(d.getElementById('faVetoes').textContent,'2 / 38','symbols, not reasons; one missing veto array unknown');ok(d.getElementById('faVetoSub').textContent.includes('3'));ok(d.getElementById('faVetoSub').textContent.includes('1'));
  eq(fx.faView.rows[0].state,'static');eq(fx.faView.rows[1].state,'unmapped');eq(fx.faView.rows[2].state,'cached');eq(fx.faView.rows[4].state,'server');eq(fx.faView.rows[4].cached,false,'fresh flag alone is not cached or verified receipt');
  ok(d.getElementById('faCoverageNote').textContent.includes(lang==='fa'?'تأیید نشده':'unverified'));ok(d.getElementById('faList').textContent.includes(lang==='fa'?'دریافت اختصاصی تأیید نشده':'provider receipt unverified'));ok(d.getElementById('faList').textContent.includes(lang==='fa'?'نه دادهٔ امروز':'not today'));
  ok(d.getElementById('faList').textContent.includes('380,000,000'));ok(d.getElementById('faCoverageNote').textContent.includes(lang==='fa'?'نه مشاهده':'not source observation'));
  d.querySelector('[data-fa-show-all]').click();eq(d.querySelectorAll('#faList [data-fa-symbol]').length,39);eq(requests,0,'show all adds no FA requests');d.querySelector('[data-fa-show-all]').click();eq(d.querySelectorAll('#faList [data-fa-symbol]').length,15);eq(requests,0);
  w.NBX_APP.refreshDisplay();await turn();eq(requests,0,'language/preference redraw is not a detail fetch');eq(d.getElementById('faAvg').textContent,'60.1');
  // Legacy/partial scan: only six requests, all other rows explicitly not requested.
  const partial=Array.from({length:39},(_,i)=>({symbol:'LEGACY'+i+'IRT',fa_score:90,direction:'LONG',composite:80}));set(partial);requests=0;
  api.getBackendFA=async symbol=>{requests++;return {symbol,score:50,profile:'live',vetoes:[]};};await fx.updateFaPreview();eq(requests,6);eq(fx.faView.rows.filter(r=>r.state==='not-requested').length,33);eq(fx.faView.rows.filter(r=>r.state==='server').length,6);eq(d.getElementById('faAvg').textContent,'50.0');eq(d.getElementById('faVetoes').textContent,'0 / 6');
  d.querySelector('[data-fa-show-all]').click();eq(requests,6);eq(d.querySelectorAll('[data-fa-state="not-requested"]').length,33);ok(d.getElementById('faList').textContent.includes(lang==='fa'?'فقط عدد اسکن':'scan-only'));ok(!d.getElementById('faList').textContent.includes('provider did not answer'));
  // Selective detail calls are for holes, not first six symbols already covered.
  set([...records.slice(0,10),{symbol:'MISSINGIRT',fa_score:99}]);requests=0;let symbolAsked;api.getBackendFA=async symbol=>{requests++;symbolAsked=symbol;return {symbol,score:20,profile:'fallback',vetoes:[]};};await fx.updateFaPreview();eq(requests,1);eq(symbolAsked,'MISSINGIRT');eq(fx.faView.rows.at(-1).state,'static');
  // An unmapped/default response does not enter sample mean; zero reported veto
  // is explicitly scoped to replies, not equivalent to fully measured coverage.
  set([full('NONEIRT',52.6,{profile:'none'})]);await fx.updateFaPreview();eq(d.getElementById('faAvg').textContent,'—');eq(d.getElementById('faAvgBar').style.width,'0%');eq(d.getElementById('faVetoes').textContent,'0 / 1');eq(d.querySelector('[data-fa-score]').textContent,'—');
  // Failed requests are not provider outage; auth is distinguishable.
  set([{symbol:'BTCIRT'}]);api.getBackendFA=async()=>({ok:false,auth_error:true});await fx.updateFaPreview();eq(fx.faView.rows[0].state,'failed');ok(d.getElementById('faList').textContent.includes(lang==='fa'?'خطای دسترسی':'access denied'));eq(d.getElementById('faAvg').textContent,'—');eq(d.getElementById('faVetoes').textContent,'—');eq(d.getElementById('faAvgBar').dataset.w,'0');
  // Premium has a separate source, is repainted even when all FA reads fail.
  w.NBX.core.engine.premium_pct=null;await fx.updateFaPreview();eq(d.getElementById('faPrem').textContent,'—');ok(d.querySelector('[data-i18n="fa.premSub"]').textContent.includes('BTCIRT'));w.NBX.core.engine.premium_pct=0;await fx.updateFaPreview();ok(d.getElementById('faPrem').textContent.includes('0.00'),'real zero is not missing');
  // Clear every KPI/subtitle/bar when scan disappears, including received label.
  set([]);await fx.updateFaPreview();for(const id of ['faAvg','faVetoes','faPrem'])eq(d.getElementById(id).textContent,'—');eq(d.getElementById('faAvgBar').style.width,'0%');eq(fx.faView.received,null);ok(!d.getElementById('faCoverageNote').textContent.includes(lang==='fa'?'زمان دریافت':'received time'));eq(d.querySelectorAll('#page-fa [data-unavailable]').length,0,'missing FA is not an exchange outage assertion');
  // Late detail reply cannot repaint a newer scan or trigger remaining old calls.
  set(partial);const old=deferred();requests=0;api.getBackendFA=()=>{requests++;return old.promise;};const older=fx.updateFaPreview();eq(requests,1);eq(d.getElementById('faRefresh').disabled,true);
  set([full('NEWIRT',30)]);await fx.updateFaPreview();old.resolve({symbol:'LEGACY0IRT',score:99,profile:'live',vetoes:['stale']});await older;eq(requests,1);eq(d.getElementById('faAvg').textContent,'30.0');eq(d.getElementById('faRefresh').disabled,false);ok(!d.getElementById('faList').textContent.includes('stale'));
  set([{symbol:'BTCIRT'}]);const lateTf=deferred();api.getBackendFA=()=>lateTf.promise;const tfReq=fx.updateFaPreview();live.timeframe='1h';lateTf.resolve(good);await tfReq;eq(d.getElementById('faAvg').textContent,'—');eq(fx.faView.rows.length,0);
  // Scan timestamp change also invalidates a response even if array is reused.
  set([{symbol:'BTCIRT'}]);const lateScan=deferred();api.getBackendFA=()=>lateScan.promise;const scanReq=fx.updateFaPreview();live.lastScanAt=2000;lateScan.resolve(good);await scanReq;eq(d.getElementById('faAvg').textContent,'—');
  // A record is bound to the requested symbol; malformed score/veto isn't proof.
  set([{symbol:'BTCIRT'}]);api.getBackendFA=async()=>({...good,symbol:'ETHIRT'});await fx.updateFaPreview();eq(fx.faView.rows[0].state,'failed');eq(d.getElementById('faAvg').textContent,'—');
  set([full('BTCIRT',60,{vetoes:'not-an-array'}),full('ETHIRT',40,{vetoes:[false]})]);await fx.updateFaPreview();eq(d.getElementById('faVetoes').textContent,'—');ok(d.getElementById('faVetoSub').textContent.includes('2'));
  // Score zero is valid. Duplicates cannot double-weight a mean/veto count.
  set([full('ZEROIRT',0),full('ZEROIRT',100),full('OTHERIRT',100,{vetoes:['same','same']})]);await fx.updateFaPreview();eq(fx.faView.rows.length,2);eq(d.getElementById('faAvg').textContent,'50.0');eq(d.getElementById('faVetoes').textContent,'1 / 2');eq(fx.faView.rows[1].vetoes.length,1);
  // Public metadata is still untrusted HTML. Unknown symbols are not requested.
  const attack='BTC<img src=x onerror=alert(1)>IRT';set([{symbol:attack},full('SAFEIRT',60,{profile:'fallback',vetoes:['<svg onload=alert(1)>']})]);requests=0;api.getBackendFA=async()=>{requests++;throw Error('Invalid symbol must not request');};await fx.updateFaPreview();eq(requests,0);eq(d.querySelectorAll('#faList img[src="x"],#faList [onerror],#faList [onload]').length,0);ok(d.getElementById('faList').textContent.includes(attack));ok(d.getElementById('faList').textContent.includes('<svg'));
  // Events are not connected, independently of market feed health.
  for(const unavailable of [false,true]){live.unavailable=unavailable;w.eval('news.length=0;renderNews();');eq(d.querySelector('#newsFeed [data-feed-state]').dataset.feedState,'not-connected');ok(!d.getElementById('newsFeed').textContent.includes('feed is live'));}
  // Coverage chip is explicitly the server's label, never independent freshness.
  live.unavailable=false;live.faCoverage={scored:2,live:2,fallback:0,failed:0,total:2};live.ranked=[full('BTCIRT'),full('ETHIRT')];w.NBX_APP.refreshDisplay();ok(d.getElementById('sgFaCoverage').textContent.includes(lang==='fa'?'برچسب':'Server-labelled'));ok(!d.getElementById('sgFaCoverage').className.includes('emerald'));ok(d.getElementById('sgFaCoverage').title.includes(lang==='fa'?'نه تأیید':'not independently'));
  // Public refresh is read-only and obeys the same budget, no engine fallback.
  set([{symbol:'BTCIRT'}]);api.getBackendFA=async symbol=>({symbol,score:61,profile:'live',vetoes:[]});await w.NBX_APP.refreshFundamentals();eq(d.getElementById('faAvg').textContent,'61.0');
  eq(errors.length,0,errors.join('; '));console.log(`${file} ${lang}: phase6 coverage/scan reuse/budget/provenance/late response/events PASS`);
 }finally{w.close();}
}
console.log(`Phase 6: ${checks} checks PASS`);
})().catch(e=>{console.error(e);process.exitCode=1;});
