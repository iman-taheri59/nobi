/* 2.5.5 — Performance page: "holdout only" window.
 * The selector sends window=holdout to the summary route, the status line says
 * which window the numbers cover, and a holdout the server could not apply
 * reads as "whole window", never as a clean split. No network. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
let checks=0;const eq=(a,b,m)=>{assert.equal(a,b,m);checks++;};const ok=(a,m)=>{assert.ok(a,m);checks++;};
const turn=()=>new Promise(r=>setImmediate(r));
const summary=(holdout)=>({ok:true,kpi:{sig:2,res:1,void:0,hit:1,vpf:null,avg:0.5},horizons:[],families:[],calibration:[],signals:[],origins:{live:2},outcomes:1,
  stage_read_only:true,vpf_state:'no_losses',generated_at:'2026-11-10T08:00:00Z',holdout});
(async()=>{
 for(const lang of ['fa','en']){
  const html=fs.readFileSync('index.html','utf8').replace(/<script id="nbx-inline-nbx-app-js">[\s\S]*?<\/script>/,'');
  const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(html,{url:'https://dashboard.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,
   beforeParse(w){w.setInterval=()=>0;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.fetch=()=>{throw Error('Network forbidden');};}});
  const w=dom.window,d=w.document;
  if(d.readyState!=='complete')await new Promise(r=>w.addEventListener('load',r,{once:true}));await turn();
  w.eval(fs.readFileSync('nbx-core.js','utf8'));w.NBX.core=w.NBX.createCore();
  w.eval(`LANG='${lang}';applyStaticI18n();`);
  const asked=[];let reply=summary({cutoff_ts:null,frozen_at:null,requested:'holdout',applied:false});
  w.NBX.core.api.getJournalSummary=async(o)=>{asked.push(o);return reply;};
  w.NBX.core.api.getJournalStage=async()=>({ok:true,stage:null,read_only:true});
  const sel=d.getElementById('perfWindowSel');ok(sel,'window selector exists');
  eq(sel.options.length,2,'two windows');ok(sel.options[1].textContent.length>3,'holdout option labelled ('+lang+')');
  // whole window: no window param sent
  await w.loadPerf();await turn();
  eq(asked[0].window,undefined,'default sends no window');
  // holdout requested but nothing frozen: server says applied=false → status says whole window
  sel.value='holdout';sel.dispatchEvent(new w.Event('change'));await turn();await turn();
  eq(asked[1].window,'holdout','selector sends window=holdout');
  
  const statusText=()=>(d.getElementById('perfReadStatus')||{textContent:''}).textContent;
  ok(/holdout/i.test(statusText()),'status mentions holdout: '+statusText());
  ok(!/(holdout from|holdout از) /.test(statusText()),'no cutoff date when not applied: '+statusText());
  // frozen: status names the cutoff
  reply=summary({cutoff_ts:1762759200,frozen_at:1762682087,requested:'holdout',applied:true});
  await w.loadPerf();await turn();
  ok(/holdout/i.test(statusText())&&/\d|[۰-۹]/.test(statusText()),'status shows holdout cutoff date: '+statusText());
  eq(errors.filter(e=>!/Not implemented|canvas|Network forbidden/.test(e)).length,0,'no jsdom errors '+errors.join('|'));
 }
 console.log(`PASS: performance holdout window — ${checks} checks`);
})().catch(e=>{console.error(e);process.exit(1);});
