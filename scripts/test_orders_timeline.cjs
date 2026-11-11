/* 2.5.5 — Orders table: retained events render as a readable timeline, not a
 * JSON dump. Known kinds/reasons get words; unknown ones stay verbatim; the raw
 * payload remains reachable. Canonical responsive page, FA + EN. No network. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
let checks=0;const ok=(a,m)=>{assert.ok(a,m);checks++;};const eq=(a,b,m)=>{assert.equal(a,b,m);checks++;};
const order={clientOrderId:'q-1234567890',symbol:'BTCUSDT',side:'buy',type:'market',price:100,requested:10,entry_filled:10,filled_pct:100,
  retained_exit_qty:10,remaining_open:0,fee:0.1,adverse_slip_bps:3,state:'CLOSED',position_state:'CLOSED',record_kind:'entry',position_link:'clientOrderId',
  reason:null,closed_at_iso:'2026-11-10T08:00:00Z',close_reason:'sl-hit',pnl_usd:-4.25,pnl_usd_basis:'position-snapshot',opened_at_iso:'2026-11-10T06:00:00Z',
  event_details:[
    {kind:'submit',ts:1762754400,qty:10,price:100,fee_quote:null,pnl_usd:null,reason:null},
    {kind:'fill',ts:1762754401,qty:10,price:100.03,fee_quote:0.1,pnl_usd:null,reason:null},
    {kind:'tp1',ts:1762758000,qty:5,price:103,fee_quote:0.05,pnl_usd:1.5,reason:null},
    {kind:'close',ts:1762761600,qty:5,price:98,fee_quote:0.05,pnl_usd:-5.75,reason:'sl-hit'},
    {kind:'mystery-kind',ts:1762761601,qty:0,price:0,fee_quote:null,pnl_usd:null,reason:'weird-<b>reason</b>'},
  ]};
(async()=>{
 for(const file of ['index.html']) for(const lang of ['en','fa']){
  const html=fs.readFileSync(file,'utf8').replace(/<script id="nbx-inline-nbx-app-js">[\s\S]*?<\/script>/,'');
  const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(html,{url:'https://dashboard.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,
   beforeParse(w){w.setInterval=()=>0;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.fetch=()=>{throw new Error('Real network forbidden');};}});
  const w=dom.window,d=w.document;
  w.eval(fs.readFileSync('nbx-core.js','utf8'));w.NBX.core=w.NBX.createCore();
  w.NBX.core.api.getPaperOrders=async()=>({rows:[order],as_of:'2026-11-10T08:05:00Z'});
  w.eval(`setLang('${lang}');`);await w.loadOrders();
  const body=d.getElementById('ordBody'),txt=body.textContent,tag=`${file} [${lang}]`;
  eq(body.querySelectorAll('pre').length,1,tag+': exactly one raw <pre>, inside the nested raw toggle');
  ok(body.querySelector('details details pre'),tag+': raw JSON is nested two levels deep');
  const items=body.querySelectorAll('li');
  eq(items.length,5,tag+': one timeline row per event');
  const words=lang==='fa'?['ارسال سفارش','پر شد','هدف ۱','بسته شد','حد ضرر خورد']:['Order submitted','Filled (entry)','TP1','Closed','stop-loss hit'];
  for(const wd of words) ok(txt.includes(wd),tag+': timeline says "'+wd+'"');
  ok(txt.includes('mystery-kind'),tag+': unknown kind printed verbatim');
  ok(txt.includes('weird-<b>reason</b>')&&!body.querySelector('li b'),tag+': unknown reason verbatim and escaped');
  ok(txt.includes('sl-hit'),tag+': raw reason code still visible next to its translation');
  const pnls=[...body.querySelectorAll('li .text-red-300')].filter(e=>/PnL/.test(e.textContent));
  eq(pnls.length,1,tag+': the losing exit is red');
  ok([...body.querySelectorAll('li .text-emerald-300')].some(e=>/PnL/.test(e.textContent)),tag+': the TP1 gain is green');
  ok(txt.includes(lang==='fa'?'از وضعیت پوزیشن':'from position snapshot'),tag+': PnL basis translated');
  ok(!/\{\s*"kind"/.test(body.querySelector('details').firstElementChild.nextElementSibling.textContent),tag+': no JSON outside the raw toggle');
  eq(errors.filter(e=>!/Not implemented|canvas|Real network/.test(e)).length,0,tag+': jsdom errors: '+errors.join('|'));
 }
 console.log(`PASS: orders event timeline — ${checks} checks`);
})().catch(e=>{console.error(e);process.exit(1);});
