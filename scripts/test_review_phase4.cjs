/* Approval queue only. Fabricated plans, deferred replies and offline JSDOM;
 * no real account, network, execution-mode write or broker placement. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
let checks=0;const eq=(a,b,m)=>{assert.equal(a,b,m);checks++;};const ok=(a,m)=>{assert.ok(a,m);checks++;};
const turn=()=>new Promise(r=>setImmediate(r));
function deferred(){let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};}
(async()=>{
for(const file of ['index.html','nobitex-mobile.html']) for(const lang of ['fa','en']){
 const html=fs.readFileSync(file,'utf8').replace(/<script id="nbx-inline-nbx-app-js">[\s\S]*?<\/script>/,'');
 const errors=[],timers=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const dom=new JSDOM(html,{url:'https://dashboard.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,
  beforeParse(w){w.setInterval=(fn,ms)=>{timers.push({fn,ms});return timers.length;};w.scrollTo=()=>{};w.fetch=()=>{throw Error('Real network forbidden');};}});
 const w=dom.window,d=w.document;
 try{
  if(d.readyState!=='complete')await new Promise(r=>w.addEventListener('load',r,{once:true}));await turn();
  w.eval(fs.readFileSync('nbx-core.js','utf8'));w.NBX.core=w.NBX.createCore();const api=w.NBX.core.api;
  api.getExecMode=async()=>({ok:true,default:'semi',symbols:{}});await w.loadExecMode(true);await turn();w.eval('clearTimeout(queueRefreshTimer);');
  w.eval(`LANG='${lang}'; PREFS.numFmt='en'; applyStaticI18n(); execMode='semi'; symMode={};`);await turn();w.eval('clearTimeout(queueRefreshTimer);');
  d.getElementById('page-desk').classList.remove('hidden');
  const initialBar=1793185200;let bar=initialBar,key='a'.repeat(64),risk=0.5,planCalls=0,whatCalls=0,orderCalls=0,orderReads=0;
  const clock=Date.now();let wall=clock;w.Date.now=()=>wall;
  function plan(symbol='BTCIRT'){
   const tf=w.live.timeframe,step=w.tfSec(tf);
   return {ok:true,entry:100,stop:95,tp1:106,tp2:110,size_quote:1000,risk_pct:risk,blocked:false,
    approval:{ok:true,symbol,tf,bar_open:bar,server_now:bar+step+10,expires_at:bar+2*step,score:80,signal_key:key}};
  }
  const projection={ok:true,projection:{loss_at_stop_pct_of_equity:0.42,blocked:false}};
  api.getBackendRiskPlan=async sym=>{planCalls++;return plan(sym);};
  api.postWhatIf=async()=>{whatCalls++;return projection;};
  api.postExecMode=()=>{throw Error('Execution-mode writes forbidden');};
  api.postPaperOrder=async()=>{orderCalls++;return {ok:true,position:{id:'synthetic'}};};
  w.loadOrders=()=>{orderReads++;};w.updatePaperPositions=()=>{};
  w.live={timeframe:'15m',ranked:[{symbol:'BTCIRT',direction:'LONG',composite:80}]};
  const get=()=>w.eval('queue[0]');const editor=()=>d.querySelector('.qIn[data-field="sizeQ"]');
  const stopTimers=()=>w.eval('for(const q of queue) if(q.edit) clearTimeout(q.edit.timer);');
  const input=(field,value)=>{const n=d.querySelector(`.qIn[data-field="${field}"]`);n.value=value;n.dispatchEvent(new w.Event('input',{bubbles:true}));stopTimers();return n;};
  const open=()=>{w.toggleRowEdit(get().id);stopTimers();};
  const fresh=async()=>{w.eval('for(const q of queue) invalidateQueuePreview(q);queue=[];queueDismissed.clear();queueUncertain.clear();queueInflight.clear();');bar=initialBar;key='a'.repeat(64);wall=clock;risk=0.5;w.live.timeframe='15m';w.live.ranked=[{symbol:'BTCIRT',direction:'LONG',composite:80}];api.getBackendRiskPlan=async sym=>{planCalls++;return plan(sym);};api.postWhatIf=async()=>{whatCalls++;return projection;};await w.refreshQueue();};
  await fresh();
  ok(timers.some(x=>x.fn===w.queueTick && x.ms===1000),'real one-second timer uses non-destructive tick');
  eq(get().id,`BTCIRT:15m:${bar}:${key}`,'draft identity includes authoritative timeframe/bar/material key');
  open();await turn();
  const node=input('sizeQ','۱۲۳۴.');node.focus();node.setSelectionRange(2,4,'backward');
  eq(node.value,'1234.','Persian digits normalize without deleting intermediate decimal');
  eq(get().edit.raw.sizeQ,'1234.');eq(get().edit.sizeQ,1234);
  const obj=get(),e=obj.edit,startPlans=planCalls,startWhat=whatCalls,ttl=obj.ttl;
  for(let n=0;n<5;n++){wall+=1000;w.queueTick();}
  eq(editor(),node,'countdown preserves exact input node');eq(d.activeElement,node);eq(node.value,'1234.');eq(node.selectionStart,2);eq(node.selectionEnd,4);eq(node.selectionDirection,'backward');
  eq(planCalls,startPlans,'tick does not request plans');eq(whatCalls,startWhat,'tick does not request capacity');ok(get().ttl<=ttl-5);ok(d.querySelector('[data-qttl]').textContent.length>0);
  await w.refreshQueue();stopTimers();
  eq(get(),obj,'same proposal reuses row object');eq(get().edit,e);eq(editor(),node,'scan retains exact active DOM');eq(d.activeElement,node);eq(node.value,'1234.');eq(node.selectionStart,2);eq(node.selectionEnd,4);eq(get().approval.expires_at,bar+1800);
  // Blank/incomplete input is unknown, never the old number or fabricated zero.
  input('stop','-');eq(get().edit.stop,null);eq(get().edit.raw.stop,'-');
  await w.refreshQueue();stopTimers();eq(d.querySelector('.qIn[data-field="stop"]').value,'-');
  const priorOrders=orderCalls;w.resolveQ(get().id,true);await turn();eq(orderCalls,priorOrders,'invalid draft cannot silently send fallback levels');
  input('stop','95');
  // A response loses authority immediately on keystroke, not 350ms later.
  const pending=deferred();api.postWhatIf=()=>pending.promise;const req=w.askRowWhatIf(get());
  input('sizeQ','1400.');eq(get().edit.risk,null);eq(d.querySelector('[data-qrisk]').textContent,'—');
  pending.resolve({ok:true,projection:{loss_at_stop_pct_of_equity:9,blocked:true,blocked_by:'portfolio-risk'}});await req;
  eq(get().edit.risk,null,'obsolete response cannot repaint edited values');
  // A refresh also invalidates capacity computed before its new plan read.
  const preScan=deferred();api.postWhatIf=()=>preScan.promise;const preScanReq=w.askRowWhatIf(get());
  await w.refreshQueue();stopTimers();preScan.resolve(projection);await preScanReq;eq(get().edit.risk,null,'same-identity refresh invalidates old capacity response');eq(editor(),node);
  // Reset invalidates the old request even while the replacement request waits.
  const beforeReset=deferred(),afterReset=deferred();let n=0;api.postWhatIf=()=>++n===1?beforeReset.promise:afterReset.promise;
  const resetOld=w.askRowWhatIf(get());w.resetRowEdit(get().id);stopTimers();
  eq(get().edit.sizeQ,1000);eq(editor().value,'1000');
  beforeReset.resolve(projection);await resetOld;eq(get().edit.risk,null);
  afterReset.resolve({ok:true,projection:{loss_at_stop_pct_of_equity:0.25,blocked:false}});await turn();eq(get().edit.risk,0.25);
  // Closing restores base plan and rejects late answers without issuing more work.
  const closing=deferred();api.postWhatIf=()=>closing.promise;const closeOld=w.askRowWhatIf(get());w.toggleRowEdit(get().id);
  closing.resolve(projection);await closeOld;eq(get().edit.on,false);eq(get().edit.risk,null);eq(get().edit.sizeQ,1000);
  // Malformed projection is not a 0%/within-limits claim, including numeric strings.
  api.postWhatIf=async()=>projection;open();await turn();
  for(const bad of [null,{}, {ok:true}, {ok:true,projection:{}}, {ok:true,projection:{loss_at_stop_pct_of_equity:null,blocked:false}}, {ok:true,projection:{loss_at_stop_pct_of_equity:'0',blocked:false}}, {ok:true,projection:{loss_at_stop_pct_of_equity:0.1}}, {ok:false,projection:projection.projection}]){
   api.postWhatIf=async()=>bad;await w.askRowWhatIf(get());eq(get().edit.risk,null);eq(d.querySelector('[data-qrisk]').textContent,'—');ok(!d.querySelector('[data-qrisk]').className.includes('emerald'));
  }
  api.postWhatIf=async()=>({ok:true,projection:{loss_at_stop_pct_of_equity:0,blocked:false}});await w.askRowWhatIf(get());eq(get().edit.risk,0,'real numeric zero remains a valid capacity result');
  ok(get().edit.gate.includes(lang==='fa'?'نه تضمین':'not a guarantee'));ok(w.eval("t('col.tp2')")!=='col.tp2');
  const malicious='x<img src=x onerror=alert(1)>';api.postWhatIf=async()=>({ok:true,projection:{loss_at_stop_pct_of_equity:1,blocked:true,blocked_by:malicious}});
  await w.askRowWhatIf(get());w.renderQueue();eq(d.querySelectorAll('#queueList img[src="x"],#queueList [onerror]').length,0,'capacity error is text, not markup');
  // New material key on the same candle is a new draft; removed row replies die.
  const oldProposal=get(),oldNode=editor(),late=deferred();api.postWhatIf=()=>late.promise;const lateReq=w.askRowWhatIf(oldProposal);
  input('sizeQ','1999.');key='b'.repeat(64);await w.refreshQueue();stopTimers();
  ok(get()!==oldProposal);ok(editor()!==oldNode);eq(get().edit,undefined);eq(get().sizeQ,1000);
  late.resolve(projection);await lateReq;eq(oldProposal.edit.risk,null);
  open();input('sizeQ','1222');w.live.timeframe='1h';await w.refreshQueue();stopTimers();eq(get().tf,'1h');eq(get().edit,undefined,'timeframe change cannot inherit draft');
  // Reject really exercises the action: same candle blocked, next candle/tf allowed.
  await fresh();w.resolveQ(get().id,false);eq(w.eval('queue.length'),0);eq(w.eval('queueDismissed.size'),1);
  await w.refreshQueue();eq(w.eval('queue.length'),0);key='b'.repeat(64);await w.refreshQueue();eq(w.eval('queue.length'),0,'revised key cannot defeat same-candle decision');
  w.live.timeframe='1h';await w.refreshQueue();eq(w.eval('queue.length'),1,'decision does not blacklist symbol across timeframes');
  w.live.timeframe='15m';bar+=900;await w.refreshQueue();eq(w.eval('queue.length'),1,'next candle of symbol is eligible');
  wall+=1000000;w.queueTick();eq(w.eval('queueDismissed.size'),0,'expired decision memory pruned');
  // Reject while refresh waits cannot resurrect the already dismissed candle.
  await fresh();const delayedPlan=deferred();api.getBackendRiskPlan=()=>delayedPlan.promise;const refreshing=w.refreshQueue();w.resolveQ(get().id,false);delayedPlan.resolve(plan());await refreshing;eq(w.eval('queue.length'),0);
  // At most one in-flight submission per candle even if material key changes.
  await fresh();const sending=deferred();let payload;api.postPaperOrder=body=>{orderCalls++;payload=body;return sending.promise;};
  open();await turn();input('sizeQ','1350.');input('tp2','112');const sent=get(),count=orderCalls;
  w.resolveQ(sent.id,true);eq(orderCalls,count+1);eq(payload.size_quote,1350);eq(payload.tp2,112);eq(payload.approval.signal_key,key);eq(payload.source,'QUEUE');
  ok(d.querySelector('.qApp').disabled);ok(editor().disabled);ok(d.querySelector('.qRej').disabled);
  w.resolveQ(sent.id,true);eq(orderCalls,count+1);w.resolveQ(sent.id,false);eq(w.eval('queue.length'),1,'cannot dismiss in-flight row');
  key='b'.repeat(64);await w.refreshQueue();stopTimers();ok(get()!==sent);ok(d.querySelector('.qApp').disabled,'new revision shares candle in-flight lock');
  w.resolveQ(get().id,true);eq(orderCalls,count+1);
  sending.resolve({ok:true,position:{id:'fabricated'}});await turn();eq(w.eval('queue.length'),0,'success removes current revision of same candle');
  await w.refreshQueue();eq(w.eval('queue.length'),0,'successful candle not immediately offered again');
  bar+=900;await w.refreshQueue();eq(w.eval('queue.length'),1,'success does not blacklist future signal');
  // A network/unknown outcome remains locally locked through revision/refresh.
  await fresh();api.postPaperOrder=async()=>{orderCalls++;return {ok:false,outcome_unknown:true};};
  const unknownCount=orderCalls,reads=orderReads;w.resolveQ(get().id,true);await turn();eq(orderCalls,unknownCount+1);ok(orderReads>reads);ok(d.querySelector('.qApp').disabled);ok(d.querySelector('[data-qunknown]'));
  key='b'.repeat(64);await w.refreshQueue();ok(d.querySelector('.qApp').disabled);w.resolveQ(get().id,true);await turn();eq(orderCalls,unknownCount+1,'unknown response is not a retry invitation');
  wall+=1000000;w.queueTick();eq(w.eval('queueUncertain.size'),0,'only local context lifetime, not proof outcome resolved');
  for(const reply of [null,{}, {ok:true,outcome_unknown:true}]){
   await fresh();api.postPaperOrder=async()=>{orderCalls++;return reply;};const attempts=orderCalls;
   w.resolveQ(get().id,true);await turn();eq(w.eval('queueUncertain.size'),1);ok(d.querySelector('.qApp').disabled);w.resolveQ(get().id,true);await turn();eq(orderCalls,attempts+1);
  }
  // Explicit server-declined is distinguishable from unknown; no candle dismissal.
  await fresh();api.postPaperOrder=async()=>{orderCalls++;return {ok:false,error:'execution-rr-too-low'};};w.resolveQ(get().id,true);await turn();eq(w.eval('queueUncertain.size'),0);eq(w.eval('queueDismissed.size'),0);eq(d.querySelector('.qApp').disabled,false);
  // Unrelated row expiry must not destroy the surviving active editor.
  await fresh();w.live.ranked.push({symbol:'ETHIRT',direction:'LONG',composite:75});await w.refreshQueue();open();await turn();
  const survivor=input('sizeQ','1222.');survivor.focus();survivor.setSelectionRange(3,3);
  w.live.ranked.reverse();await w.refreshQueue();stopTimers();eq(d.querySelector('.qIn[data-id="'+survivor.dataset.id+'"][data-field="sizeQ"]'),survivor);eq(d.activeElement,survivor);eq(survivor.selectionStart,3);ok(d.querySelector('[data-row="'+w.eval("queue.find(q=>q.sym==='BTCIRT').id")+'"] [data-qrank]').textContent.includes('2'));
  w.eval("queue.find(q=>q.sym==='ETHIRT').clockWall-=1000000;");w.queueTick();eq(d.querySelectorAll('#queueList .qRow').length,1);eq(editor(),survivor);eq(d.activeElement,survivor);eq(survivor.selectionStart,3);
  // The edited proposal itself expires: replies must not resurrect preview/order.
  const expiredRisk=deferred();api.postWhatIf=()=>expiredRisk.promise;const expiredObj=w.eval("queue.find(q=>q.sym==='BTCIRT')"),expReq=w.askRowWhatIf(expiredObj),endOrders=orderCalls;
  wall+=1000000;w.queueTick();expiredRisk.resolve(projection);await expReq;eq(expiredObj.edit.risk,null);eq(d.querySelectorAll('#queueList .qRow').length,0);w.resolveQ(expiredObj.id,true);await turn();eq(orderCalls,endOrders);
  eq(errors.length,0,errors.join('; '));console.log(`${file} ${lang}: phase4 stable draft/TTL/candle decision/late reply/local submission lock PASS`);
 }finally{w.close();}
}
console.log(`Phase 4: ${checks} checks PASS`);
})().catch(e=>{console.error(e);process.exitCode=1;});
