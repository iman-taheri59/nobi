/* 2.5.5 — Performance page: "holdout only" window.
 * The selector sends window=holdout to the summary route, the status line says
 * which window the numbers cover, and a holdout the server could not apply
 * reads as "whole window", never as a clean split. No network. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
let checks=0;const eq=(a,b,m)=>{assert.equal(a,b,m);checks++;};const ok=(a,m)=>{assert.ok(a,m);checks++;};
const turn=()=>new Promise(r=>setImmediate(r));
const sigRow={id:7,ts:1762760000,sym:'BTCUSDT',tf:'15m',dir:'long',score:71.2,rank:3,entry:101234.5,stop:99000,tp1:104000,tp2:106000,h1:true,h2:null,h3:null,r:null,o1:null,o2:null,o3:null,r1:0.4,st:'inprogress',source:'nobitex',void_reason:null,origin:'live'};
const summary=(holdout)=>({ok:true,kpi:{sig:2,res:1,void:0,hit:1,vpf:null,avg:0.5},horizons:[],families:[],calibration:[],signals:[sigRow,{...sigRow,id:8,dir:'exit',entry:null}],origins:{live:2},outcomes:1,
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
  // 2.5.5 — entry price column, and the path legend explaining the exit dot
  const cells=[...d.querySelectorAll('#perfBody tr[data-sig] td[data-entry]')];
  eq(cells.length,2,'entry cell per row');
  ok(/1|۱/.test(cells[0].textContent)&&cells[0].textContent.length>4,'entry price printed: '+cells[0].textContent);
  eq(cells[1].textContent,'—','missing entry prints a dash, not 0');
  const hdr=[...d.querySelectorAll('#perfBody').length?d.querySelectorAll('thead th'):[]].map(x=>x.textContent);
  ok(hdr.includes(lang==='fa'?'قیمت ورود':'Entry price'),'entry column header ('+lang+')');
  w.NBX.core.api.getJournalSignal=async(id)=>({signal:{id,symbol:'BTCUSDT',entry:101234.5,stop:99000,tp1:104000,tp2:106000,atr:1100,components:{}},
    path:[{t:1762760000,o:101200,h:101500,l:101000,c:101234.5},{t:1762760900,o:101234,h:102000,l:101100,c:101900},{t:1762761800,o:101900,h:104200,l:101800,c:104050}],
    entry_index:0,outcomes:{H2:{r_multiple:1.0,hit_tp1:1,hit_sl:0,exit_ts:1762761800}}});
  await w.toggleSignalRow(d.querySelector('#perfBody tr[data-sig="7"]'));
  const det=d.querySelector('#perfBody .sig-detail');ok(det,'detail opened');if(!det.querySelector('[data-path-legend]'))console.log('DETAIL:',det.textContent.slice(0,300));
  ok(det.querySelector('svg line[data-level="entry"]'),'entry level line drawn');
  ok(det.querySelector('svg circle[fill="#34d399"]'),'TP1 exit dot is green');
  const legend=det.querySelector('[data-path-legend]');ok(legend&&/H[2۲]/.test(legend.textContent),'legend explains the H2 exit dot: '+(legend&&legend.textContent));
  ok(legend.textContent.includes(lang==='fa'?'سبز':'green'),'legend names the colours ('+lang+')');
  eq(errors.filter(e=>!/Not implemented|canvas|Network forbidden/.test(e)).length,0,'no jsdom errors '+errors.join('|'));
 }
 console.log(`PASS: performance holdout window — ${checks} checks`);
})().catch(e=>{console.error(e);process.exit(1);});
