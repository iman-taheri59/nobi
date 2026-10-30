/* Offline Risk/Controls: real renderers/client, fabricated plans/control replies.
 * No app boot, real endpoint, account, halt/resume, exchange or OS helper. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
let checks=0;const eq=(a,b,m)=>{assert.equal(a,b,m);checks++;};const ok=(a,m)=>{assert.ok(a,m);checks++;};
const turn=()=>new Promise(r=>setImmediate(r));
function deferred(){let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};}
const app=fs.readFileSync('nbx-app.js','utf8').replace('  window.NBX_APP = {','  window.__phase5={paintPaperState,refreshPaperState};\n  window.NBX_APP = {').replace("  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);\n  else start();",'');
(async()=>{
for(const file of ['index.html','nobitex-mobile.html'])for(const lang of ['fa','en']){
 const html=fs.readFileSync(file,'utf8').replace(/<script id="nbx-inline-nbx-app-js">[\s\S]*?<\/script>/,'');
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const dom=new JSDOM(html,{url:'https://dashboard.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,
  beforeParse(w){w.setInterval=()=>0;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.fetch=()=>{throw Error('Unstubbed network forbidden');};}});
 const w=dom.window,d=w.document;
 try{
  if(d.readyState!=='complete')await new Promise(r=>w.addEventListener('load',r,{once:true}));await turn();
  w.eval(fs.readFileSync('nbx-core.js','utf8'));w.NBX.core=w.NBX.createCore();const api=w.NBX.core.api;api.standaloneMobile=false;
  api.getExecMode=async()=>({ok:true,default:'semi',symbols:{}});
  w.eval(app);w.eval(`LANG='${lang}';PREFS.numFmt='en';applyStaticI18n();`);await turn();
  let requests=0,request=null;const auth=api._authHeaders.bind(api);api._authHeaders=()=>({...auth(),'X-CSRF-Token':'fixture-csrf'});
  const rawHalt=api.postHalt.bind(api),rawResume=api.postResume.bind(api);
  // Validate both mutation clients; an error cannot manufacture a target state.
  for(const arm of [true,false])for(const scenario of ['success','401','403','503','network','json','missing','wrong-type','wrong-state','explicit-decline','ambiguous']){
   requests=0;w.fetch=async(url,init)=>{requests++;request={url,init};if(scenario==='network')throw Error('private network details must not escape');
    const status=['401','403','503'].includes(scenario)?Number(scenario):200;
    return {ok:status===200,status,json:async()=>{if(scenario==='json')throw Error('broken json');if(scenario==='missing')return {};if(scenario==='wrong-type')return {halted:String(arm)};if(scenario==='wrong-state')return {halted:!arm};if(scenario==='explicit-decline')return {ok:false,error:'denied'};if(scenario==='ambiguous')return {halted:arm,outcome_unknown:true};return {halted:arm,audit_id:17,telegram:{sent:false}};}};};
   const r=await (arm?rawHalt():rawResume());eq(requests,1,'no retry');eq(request.init.method,'POST');eq(request.init.headers['X-CSRF-Token'],'fixture-csrf');ok(request.url.endsWith(arm?'/paper/halt':'/paper/resume'));
   if(scenario==='success'){eq(r.ok,true);eq(r.halted,arm);eq(r.audit_id,17);}
   else{eq(r.ok,false);eq(r.halted,null,'no invented final control state');if(['401','403'].includes(scenario)){eq(r.auth_error,true);eq(r.outcome_unknown,undefined);}else if(scenario==='explicit-decline')eq(r.outcome_unknown,undefined);else eq(r.outcome_unknown,true);}
  }
  requests=0;eq((await rawHalt(true)).error,'flatten-unsupported');eq(requests,0,'unsupported flatten never reaches network');
  api.standaloneMobile=true;const positions=JSON.stringify(w.NBX.Paper.positions);eq((await rawHalt()).standalone,true);eq(w.NBX.Paper.halted,true);eq((await rawResume()).halted,false);eq(JSON.stringify(w.NBX.Paper.positions),positions,'local halt does not flatten');api.standaloneMobile=false;
  w.fetch=()=>{throw Error('Raw fetch fallback forbidden');};
  const control=()=>w.eval('paperControl');
  const resetControl=(value=false)=>{w.eval(`paperControl.pending=false;paperControl.reading=false;paperControl.uncertain=false;paperControl.known=${JSON.stringify(value)};paperControl.source='server';halted=paperControl.known;++window.paperControlEpoch;renderPaperControl();`);};
  eq(d.getElementById('flattenChk').disabled,true);ok(d.querySelector('[data-i18n="pn.l1"]').textContent.includes(lang==='fa'?'لغو نمی‌کند':'does not cancel'));ok(d.querySelector('[data-i18n="rk.controlLimit"]').textContent.includes('restart'));
  // No optimistic halt, optimistic audit or duplicate command during slow POST.
  resetControl(false);const sending=deferred();let sent=0,argumentsCount=-1;api.postHalt=function(){sent++;argumentsCount=arguments.length;return sending.promise;};
  d.getElementById('flattenChk').checked=true;const pending=w.confirmPanic();eq(argumentsCount,0,'checkbox cannot enable unsupported flatten');eq(sent,1);eq(w.eval('halted'),false);eq(control().known,false);eq(d.getElementById('paperControlNote').dataset.state,'pending');ok(d.getElementById('holdBtnEl').disabled);ok(d.getElementById('panicOpen').disabled);
  await w.confirmPanic();await w.submitPaperControl(false);eq(sent,1,'single-flight across halt and resume');
  w.paintPaperControl(true,'server',w.paperControlEpoch);eq(control().known,false,'poll cannot pre-confirm in-flight command');
  sending.resolve({ok:true,halted:true,audit_id:17,telegram:{sent:false}});await pending;eq(control().known,true);eq(w.eval('halted'),true);eq(d.getElementById('resumeBtn').classList.contains('hidden'),false);eq(d.getElementById('panicOpen').disabled,false);
  ok(!d.getElementById('toasts').textContent.includes(lang==='fa'?'پوزیشن‌ها فلت شدند':'positions flattened'));ok(d.getElementById('toasts').textContent.includes(lang==='fa'?'تأیید نشد':'unconfirmed'));
  // Keyboard hold is cancellable; repeated keydown/short holds cannot send.
  resetControl(false);let frames=0;const originalRAF=w.requestAnimationFrame,originalCancel=w.cancelAnimationFrame;w.requestAnimationFrame=()=>++frames;w.cancelAnimationFrame=()=>{};
  const hold=d.getElementById('holdBtnEl');hold.dispatchEvent(new w.KeyboardEvent('keydown',{key:' ',bubbles:true,cancelable:true}));eq(frames,1);hold.dispatchEvent(new w.KeyboardEvent('keydown',{key:' ',repeat:true,bubbles:true,cancelable:true}));eq(frames,1,'repeat must not restart hold');hold.dispatchEvent(new w.KeyboardEvent('keyup',{key:' ',bubbles:true}));eq(w.eval('holdRAF'),null);eq(sent,1,'short keyboard hold submits nothing');
  hold.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));eq(frames,2);w.dispatchEvent(new w.Event('blur'));eq(w.eval('holdRAF'),null);w.requestAnimationFrame=originalRAF;w.cancelAnimationFrame=originalCancel;
  w.paintPaperControl(true,'server',w.paperControlEpoch);
  // Unknown response locks mutation; a poll or language repaint cannot unlock it.
  api.postResume=async()=>null;await w.submitPaperControl(false);eq(control().uncertain,true);eq(control().known,null);ok(d.getElementById('panicOpen').disabled);
  let retry=0;api.postHalt=async()=>{retry++;return {ok:true,halted:true};};await w.confirmPanic();eq(retry,0);
  w.paintPaperControl(false,'server',w.paperControlEpoch);eq(control().uncertain,true);w.eval('renderPaperControl();');eq(control().known,null);
  api.getBackendRiskState=async()=>null;await w.readPaperControlState();eq(control().uncertain,true);ok(d.getElementById('panicOpen').disabled);
  const reading=deferred();api.getBackendRiskState=()=>reading.promise;const read=w.readPaperControlState();ok(d.getElementById('paperControlRead').disabled);await w.confirmPanic();eq(retry,0);reading.resolve({source:'server',halted:true});await read;eq(control().uncertain,false);eq(control().known,true);eq(d.getElementById('panicOpen').disabled,false);
  api.postResume=async()=>({ok:true,halted:false,telegram:{sent:true}});await w.submitPaperControl(false);eq(control().known,false);eq(d.getElementById('resumeBtn').classList.contains('hidden'),true);
  // Explicit auth/decline failure: unknown state, not optimistic success.
  api.postHalt=async()=>({ok:false,auth_error:true});await w.confirmPanic();eq(control().known,null);eq(control().uncertain,false);
  api.postHalt=async()=>{throw Error('lost response');};await w.confirmPanic();eq(control().uncertain,true);
  // Local book remains explicitly separate; it is not a server control result.
  resetControl();api.standaloneMobile=true;api.postHalt=rawHalt;await w.confirmPanic();eq(control().source,'browser-local');ok(d.getElementById('paperControlNote').textContent.includes(lang==='fa'?'فقط دفتر مرورگر':'browser-local'));api.standaloneMobile=false;
  // A server plan, not hard-coded ATR x equity/fallback FX, owns the size.
  d.getElementById('page-risk').classList.remove('hidden');
  const plan={symbol:'BTCIRT',side:'buy',quote:'IRT',action:'open',entry:100,stop:99,size_quote:25000,equity_quote:100000,risk_pct:.25,blocked:false,warnings:['single-asset-cap-resized']};let plans=0;
  api.getBackendRiskPlan=async(...args)=>{plans++;eq(args.join(','),'BTCIRT,buy,15m');return plan;};
  w.eval('EQUITY=999999999;');w.fxOf=()=>{throw Error('Browser FX must not size the plan');};
  await w.fillRiskSizing();ok(d.getElementById('rkSug').textContent.includes('250'));ok(d.getElementById('rkEq').textContent.includes('100,000'));ok(d.getElementById('rkRpt').textContent.includes('0.25'));ok(d.getElementById('rkDist').textContent.includes('1.00'));ok(d.getElementById('rkSizeNote').textContent.includes('single-asset-cap-resized'));eq(d.querySelectorAll('[id="rkAtr"]').length,0,'ATR is not fabricated from unknown server multiplier');ok(!d.querySelector('[data-i18n="rk.suggestNote"]').textContent.includes('25'));
  for(const bad of [null,{}, {...plan,blocked:true},{...plan,equity_quote:null},{...plan,stop:100},{...plan,entry:'100'},{...plan,risk_pct:null},{...plan,quote:'USDT'},{...plan,symbol:'ETHIRT'}]){
   api.getBackendRiskPlan=async()=>bad;await w.fillRiskSizing();for(const id of ['rkSug','rkEq','rkRpt','rkEntry','rkDist'])eq(d.getElementById(id).textContent,'—','failed plan erases old number');
  }
  const older=deferred();api.getBackendRiskPlan=()=>older.promise;const oldPlan=w.fillRiskSizing();eq(d.getElementById('rkSug').textContent,'—','pending read erases old size');api.getBackendRiskPlan=async()=>({...plan,size_quote:12000});await w.fillRiskSizing();older.resolve(plan);await oldPlan;ok(d.getElementById('rkSug').textContent.includes('120'),'older plan cannot overwrite new one');
  const hidden=deferred();api.getBackendRiskPlan=()=>hidden.promise;const hiddenReq=w.fillRiskSizing();d.getElementById('page-risk').classList.add('hidden');hidden.resolve(plan);await hiddenReq;eq(d.getElementById('rkSug').textContent,'—');
  const before=plans;api.getBackendRiskPlan=async()=>{plans++;return plan;};await w.fillRiskSizing();eq(plans,before,'hidden page spends no plan calls');d.getElementById('page-risk').classList.remove('hidden');api.standaloneMobile=true;await w.fillRiskSizing();eq(plans,before,'browser standalone does not pretend to have server plan');eq(d.getElementById('rkSug').textContent,'—');api.standaloneMobile=false;
  // State cards: unavailable/partial reads are not Off, green zero or daily-stop proof.
  const met={source:'server',metrics:{equity:1000,start_equity:1000,cash:800,trades:0},curve:[]},pos={source:'server',positions:[],metrics:met.metrics};
  resetControl();w.__phase5.paintPaperState(pos,met,{source:'server',equity:1000,halted:false,openHeat:0,dailyPnl:-4,caps:{maxHeatPct:9,maxConcurrent:7,maxSingleAssetPct:18,minCashPct:12,riskPct:.4,minRR:1.5}});
  ok(d.getElementById('capCards').textContent.includes('18'));eq(w.eval('kills[1].ok'),null,'raw daily scalar is not a daily safety pass');ok(d.getElementById('killList').textContent.includes(lang==='fa'?'تطبیق‌نیافته':'unreconciled'));eq(w.eval('kills[1].v.en.includes("-3")'),false,'no fake automatic -3% threshold guarantee');eq(w.eval('kills[2].ok'),null,'unknown market health is neutral');
  w.__phase5.paintPaperState({unavailable:true},{unavailable:true},null);eq(w.eval('RISK_LIMITS'),null);eq(w.eval('kills[0].ok'),null);ok(!d.getElementById('capCards').textContent.includes('18.00'));ok(d.getElementById('capCards').textContent.includes('—'));eq(control().known,null);
  w.__phase5.paintPaperState(pos,met,{source:'server',equity:1000,caps:{maxHeatPct:null,maxSingleAssetPct:'25'},halted:'false'});eq(w.eval('kills[0].ok'),null);eq(w.eval('RISK_LIMITS'),null);ok(!d.getElementById('capCards').textContent.includes('25.00'));
  // Poll begun before a confirmed mutation must not repaint Off afterwards.
  resetControl(false);const oldRisk=deferred();api.getPaperPositions=async()=>pos;api.getPaperMetrics=async()=>met;api.getBackendRiskState=()=>oldRisk.promise;
  const poll=w.__phase5.refreshPaperState();await turn();api.postHalt=async()=>({ok:true,halted:true});await w.confirmPanic();eq(control().known,true);
  oldRisk.resolve({source:'server',equity:1000,halted:false,caps:{}});await poll;eq(control().known,true,'old poll cannot undo newer halt acknowledgement');eq(w.eval('kills[0].ok'),null,'stale risk card does not say Off');
  // Fresh GET can report a new state; no claim of durable halt or historical execution.
  api.getBackendRiskState=async()=>({source:'server',equity:1000,halted:false,caps:{}});await w.__phase5.refreshPaperState();eq(control().known,false);
  w.eval("kills.push({n:{fa:'<img src=x onerror=alert(1)>',en:'<img src=x onerror=alert(1)>'},v:{fa:'<svg onload=alert(1)>',en:'<svg onload=alert(1)>'},ok:null});renderKills();");eq(d.querySelectorAll('#killList img[src="x"],#killList [onload],#killList [onerror]').length,0);
  eq(errors.length,0,errors.join('; '));console.log(`${file} ${lang}: phase5 server plan/neutral unknown/halt outcome/read epoch PASS`);
 }finally{w.close();}
}
console.log(`Phase 5: ${checks} checks PASS`);
})().catch(e=>{console.error(e);process.exitCode=1;});
