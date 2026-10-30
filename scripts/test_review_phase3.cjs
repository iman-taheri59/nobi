/* Real inline dashboard + core client, fabricated API only. No account/network. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
let checks=0;const eq=(a,b,msg)=>{assert.equal(a,b,msg);checks++;};const ok=(a,msg)=>{assert.ok(a,msg);checks++;};
function deferred(){let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j;});return {promise,resolve,reject};}
(async()=>{
for(const file of ['index.html','nobitex-mobile.html']){
 const raw=fs.readFileSync(file,'utf8'),html=raw.replace(/<script id="nbx-inline-nbx-app-js">[\s\S]*?<\/script>/,'');
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const dom=new JSDOM(html,{url:'https://dashboard.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,
  beforeParse(w){w.setInterval=()=>0;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.fetch=()=>{throw new Error('Real network forbidden');};}});
 const w=dom.window,d=w.document;w.eval(fs.readFileSync('nbx-core.js','utf8'));w.NBX.core=w.NBX.createCore();const api=w.NBX.core.api;
 api.standaloneMobile=false;w.eval("LANG='en';PREFS.numFmt='en';applyStaticI18n();");
 let fetches=0;
 w.NBX.Paper.placeOrder=()=>{throw new Error('Browser fallback forbidden in server mode');};
 const send=api.postPaperOrder.bind(api);
 for(const scenario of ['network','bad-json','503','missing-position','401','403','rejected','success']){
  fetches=0;w.fetch=async()=>{fetches++;if(scenario==='network')throw new Error('lost response');
   const status=['401','403','503'].includes(scenario)?Number(scenario):200;
   return {ok:status===200,status,json:async()=>{if(scenario==='bad-json')throw new Error('bad JSON');
    if(scenario==='success')return {ok:true,position:{id:'fixture',client_order_id:'fixture-coid'}};
    if(scenario==='missing-position')return {ok:true};if(scenario==='rejected')return {ok:false,error:'risk-cap'};
    return {detail:'fixture failure'};}};};
  const response=await send({symbol:'BTCUSDT',qty:1});eq(fetches,1,'no automatic retry');
  if(['network','bad-json','503','missing-position'].includes(scenario)){eq(response.outcome_unknown,true);ok(response.message.includes('may have reached'));}
  else if(['401','403'].includes(scenario)){eq(response.auth_error,true);eq(response.outcome_unknown,undefined);}
  else eq(response.ok,scenario==='success');
 }

 w.NBX.Paper.positions=[{client_order_id:'browser-fixture',symbol:'BTCUSDT',side:'buy',qty:10,filled_qty:6,requested_qty:20,state:'PARTIAL',entry_price:100,opened_at:1000}];
 const local=w.NBX.Paper.orderRows()[0];eq(local.entry_filled,10);eq(local.filled_pct,50);eq(local.remaining_open,6);eq(local.retained_exit_qty,null);eq(local.type,'unknown');
 w.NBX.Paper.positions=[];
 // Orders: failed read does not inherit upstream outage or stale successful rows.
 const order={clientOrderId:'fixture-<img src=x onerror=alert(1)>',symbol:'BTCUSDT',side:'sell',type:'unknown',price:100,requested:10,entry_filled:10,filled_pct:100,retained_exit_qty:4,remaining_open:6,fee:0,adverse_slip_bps:100,state:'REJECTED',position_state:'OPEN',record_kind:'rejected-attempt',position_link:'clientOrderId',reason:'<svg onload=alert(1)>',closed_at_iso:'2026-01-02T00:00:00Z',close_reason:'<img src=x>',pnl_usd:null,event_details:[{kind:'close',price:99}],opened_at_iso:'2026-01-01T00:00:00Z'};
 api.getPaperOrders=async()=>({ok:true,rows:[order],as_of:'2026-01-01T00:00:00Z',source:'paper-broker'});
 await w.loadOrders();eq(d.querySelectorAll('#ordBody [onerror],#ordBody [onload],#ordBody img[src="x"]').length,0,'all server strings escaped');
 ok(d.getElementById('ordBody').textContent.includes(order.clientOrderId),'full id accessible without hover');
 ok(d.getElementById('ordBody').textContent.includes('REJECTED')||d.getElementById('ordBody').textContent.includes('Rejected'));
 ok(d.getElementById('ordBody').textContent.includes('Related position state: OPEN'));
 eq(d.querySelector('#ordBody tr td:nth-child(4)').textContent,'—','unknown type is not Limit');
 ok(d.getElementById('ordBody').textContent.includes('+100.0bp'),'normalized adverse sign');
 ok(d.getElementById('ordersStatus').textContent.includes('2026-01-01'));
 const pending=deferred();api.getPaperOrders=()=>pending.promise;const load=w.loadOrders();
 ok(d.getElementById('ordersStatus').textContent.includes('may be stale'));pending.reject(new Error('503'));await load;
 ok(d.getElementById('ordersStatus').textContent.includes('unknown'));ok(!d.getElementById('ordBody').textContent.includes('fixture-'));
 eq(d.querySelectorAll('#ordBody [data-unavailable]').length,0,'read failure not an exchange outage assertion');
 api.getPaperOrders=async()=>({rows:[]});await w.loadOrders();ok(d.getElementById('ordBody').textContent.includes('not a complete account archive'));
 for(const rows of [[null],[{}]]){api.getPaperOrders=async()=>({rows});await w.loadOrders();ok(d.getElementById('ordersStatus').textContent.includes('unknown'),'malformed records not accepted as a valid list');}
 const older=deferred();let reads=0;api.getPaperOrders=()=>++reads===1?older.promise:Promise.resolve({rows:[order],as_of:'new'});
 const oldLoad=w.loadOrders();await w.loadOrders();older.resolve(null);await oldLoad;ok(d.getElementById('ordersStatus').textContent.includes('new'),'obsolete result ignored');
 // Draft preservation and explicit unsupported controls, without changing order targets.
 w.livePrice=()=>100;w.initTicket();w.eval('clearTimeout(whatifTimer);');
 eq(d.querySelector('#mType option[value="market"]').disabled,true);eq(d.getElementById('mTp').readOnly,true);
 ok(d.querySelector('.mSide[data-side="buy"]').textContent.length>0);ok(d.querySelector('.mSide[data-side="sell"]').textContent.length>0);
 d.getElementById('mPrice').value='105';d.getElementById('mPrice').dispatchEvent(new w.Event('input',{bubbles:true}));
 w.eval('clearTimeout(whatifTimer)');w.livePrice=()=>200;w.fillTicketFromLive();eq(w.eval('manual.price'),105,'scan preserves edited draft');
 w.confirm=()=>false;d.getElementById('manualFresh').click();eq(w.eval('manual.price'),105,'reset cancellation preserves draft');
 w.confirm=()=>true;d.getElementById('manualFresh').click();eq(w.eval('manual.price'),200,'explicit reset accepts fresh price');
 w.eval('clearTimeout(whatifTimer)');
 // Successful risk capacity is not geometry approval; failures/edits erase all numbers.
 const projection={heat_after:1,max_heat:13,loss_at_stop_pct_of_equity:1,loss_at_stop_usd:10,slots_left:4,max_concurrent:5,blocked:false};
 api.postWhatIf=async()=>({ok:true,projection});w.eval('manual.price=100;manual.amount=1;manual.stop=97;');
 await w.refreshWhatIf(w.eval('whatifSeq'));ok(d.getElementById('whatifGate').textContent.includes('not geometry'));
 ok(d.getElementById('whatifHeat').textContent.includes('13'));
 d.querySelector('.mSide[data-side="sell"]').click();w.eval('clearTimeout(whatifTimer)');
 eq(d.getElementById('whatifHeat').textContent,'—','side change invalidates preview immediately');eq(d.getElementById('whatifHeatBar').dataset.w,'0');
 for(const value of [null,{}, {ok:false}, {projection:{...projection,max_heat:null}}]){
  api.postWhatIf=async()=>value;await w.refreshWhatIf(w.eval('whatifSeq'));eq(d.getElementById('whatifHeat').textContent,'—');ok(!d.getElementById('whatifGate').className.includes('emerald'));
 }
 const riskOld=deferred();api.postWhatIf=()=>riskOld.promise;const riskReq=w.refreshWhatIf(w.eval('whatifSeq'));
 w.scheduleWhatIf();w.eval('clearTimeout(whatifTimer)');api.postWhatIf=async()=>({ok:true,projection});await w.refreshWhatIf(w.eval('whatifSeq'));
 riskOld.reject(new Error('old failure'));await riskReq;ok(d.getElementById('whatifGate').textContent.includes('not geometry'),'old failure cannot replace new capacity');
 // Concurrent manual submissions are locally single-flight, not claimed durable idempotency.
 w.eval("audit.length=0;manual.sym='BTCUSDT';manual.side='buy';manual.price=100;manual.amount=1;manual.stop=97;");
 d.getElementById('mType').value='limit';api.getPaperOrders=async()=>({rows:[]});api.getPaperPositions=async()=>({positions:[]});
 const sent=[];const wait=deferred();api.postPaperOrder=p=>{sent.push(p);return wait.promise;};
 const first=w.submitManual();await w.submitManual();eq(sent.length,1);eq(w.eval('audit.length'),0,'no optimistic audit success');eq(d.getElementById('mSubmit').disabled,true);
 wait.resolve({ok:true,position:{id:'fixture',client_order_id:'fixture-coid'}});await first;
 eq(w.eval('audit.length'),1);ok(d.getElementById('auditBody').textContent.includes('confirmed'));ok(d.getElementById('auditBody').textContent.includes('fixture-coid'));eq(d.getElementById('mSubmit').disabled,false);
 eq(sent[0].tp1,undefined,'no unreviewed target policy change');eq(sent[0].tp2,undefined);
 api.postPaperOrder=async()=>({ok:false,error:'<img src=x onerror=alert(1)>'});await w.submitManual();eq(w.eval('audit.length'),2);ok(d.getElementById('auditBody').textContent.includes('server-declined'));eq(d.querySelectorAll('#auditBody [onerror],#auditBody img[src="x"]').length,0);eq(d.querySelectorAll('#toasts [onerror]').length,0,'server failure is also escaped in toast');
 api.postPaperOrder=async()=>{throw new Error('response lost');};await w.submitManual();eq(w.eval('audit.length'),3);ok(d.getElementById('auditBody').textContent.includes('unknown'));ok(d.getElementById('auditBody').textContent.includes('do not automatically retry'));
 api.postPaperOrder=async()=>({ok:true});await w.submitManual();eq(w.eval('audit.length'),4);ok(w.eval("audit[0].d.en.includes('unknown')"),'missing position cannot confirm execution');
 const before=sent.length;d.getElementById('mType').value='market';await w.submitManual();eq(sent.length,before,'unsupported type refused locally');
 w.eval("setLang('fa');");await w.loadOrders();ok(d.getElementById('ordBody').textContent.includes('آرشیو کامل'));
 eq(errors.length,0,'no hidden dashboard exceptions');dom.window.close();console.log(file+': phase3 contracts, Orders and ticket state PASS');
}
console.log('Phase 3: '+checks+' checks PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
