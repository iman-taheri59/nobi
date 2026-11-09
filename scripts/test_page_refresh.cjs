/* 2.5.3 — pages that fetched on open and never again now refresh on scan.
 * Performance, Signal intelligence, Orders, Risk audit and the Desk allocation
 * each reload with the scan cadence while they are the visible tab and their
 * copy is older than their window; never while hidden, never while the user is
 * typing in them, and the intelligence page never while a detail is open.
 * No network, no orders, no policy change. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
let checks=0;const eq=(a,b,m)=>{assert.equal(a,b,m);checks++;};const ok=(a,m)=>{assert.ok(a,m);checks++;};
const turn=()=>new Promise(r=>setImmediate(r));
(async()=>{
 const html=fs.readFileSync('index.html','utf8').replace(/<script id="nbx-inline-nbx-app-js">[\s\S]*?<\/script>/,'');
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const dom=new JSDOM(html,{url:'https://dashboard.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,
  beforeParse(w){w.setInterval=()=>0;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.fetch=()=>{throw Error('Network forbidden');};}});
 const w=dom.window,d=w.document;
 if(d.readyState!=='complete')await new Promise(r=>w.addEventListener('load',r,{once:true}));await turn();
 let now=1760000000000;w.Date.now=()=>now;
 const calls={};const spy=n=>{calls[n]=0;w[n]=()=>{calls[n]++;};};
 ['loadPerf','loadSignalIntelligence','loadOrders','loadAudit','runBookAlloc','loadBenchmark','drawCal','loadExecMode','refreshQueue','fillRiskSizing','loadMarketOverview'].forEach(spy);
 w.loadExecMode=()=>{calls.loadExecMode++;return Promise.resolve();};
 w.loadPerf=()=>{calls.loadPerf++;return Promise.resolve();};
 const scan=()=>w.dispatchEvent(new w.CustomEvent('nbx:scan',{detail:{unavailable:false}}));
 const open=tab=>{d.querySelector(`.navbtn[data-tab="${tab}"]`).click();};
 const flush=async()=>{await new Promise(r=>w.setTimeout(r,80));await turn();};
 const spec=w.__pageRefresh.spec;
 ok(spec&&spec.orders&&spec.performance&&spec.intelligence&&spec.risk&&spec.desk,'registry covers the five pages');

 // Overview is the default tab: a scan there touches none of the registered loaders.
 scan();await turn();
 eq(calls.loadOrders+calls.loadAudit+calls.loadSignalIntelligence+calls.loadPerf,0,'hidden pages are not refreshed');

 // Orders: open → one load; scan right away → nothing; scan after 30 s → reload.
 open('orders');await flush();eq(calls.loadOrders,1,'orders load on open');
 scan();await turn();eq(calls.loadOrders,1,'fresh copy reused');
 now+=31_000;scan();await turn();eq(calls.loadOrders,2,'orders refetched on the scan after 30 s');
 // Typing in the page blocks the refresh.
 const inp=d.createElement('input');d.getElementById('page-orders').appendChild(inp);inp.focus();
 now+=31_000;scan();await turn();eq(calls.loadOrders,2,'no refresh while an input in the page has focus');
 inp.blur();inp.remove();
 now+=1;scan();await turn();eq(calls.loadOrders,3,'resumes once the user leaves the field');
 // Browser tab hidden → nothing.
 Object.defineProperty(d,'hidden',{configurable:true,get:()=>true});
 now+=31_000;scan();await turn();eq(calls.loadOrders,3,'hidden window is never refreshed');
 Object.defineProperty(d,'hidden',{configurable:true,get:()=>false});

 // Risk audit: 60 s window.
 open('risk');await flush();eq(calls.loadAudit,1,'audit load on open');
 now+=59_000;scan();await turn();eq(calls.loadAudit,1,'audit within 60 s reused');
 now+=2_000;scan();await turn();eq(calls.loadAudit,2,'audit refetched after 60 s');
 eq(calls.loadOrders,3,'leaving Orders stops its refresh');

 // Intelligence: 120 s, but not while a detail panel is open.
 open('intelligence');await flush();eq(calls.loadSignalIntelligence,1,'intelligence load on open');
 now+=121_000;d.getElementById('siDetail').classList.remove('hidden');scan();await turn();
 eq(calls.loadSignalIntelligence,1,'open detail blocks the refresh');
 d.getElementById('siDetail').classList.add('hidden');scan();await turn();
 eq(calls.loadSignalIntelligence,2,'refreshes once the detail is closed');

 // Performance: first open loads; a second open after 120 s reloads; scans reload too.
 open('performance');await flush();eq(calls.loadPerf,1,'performance load on first open');
 open('orders');await flush();now+=121_000;open('performance');await flush();
 eq(calls.loadPerf,2,'stale performance reloads on re-open (was: never again)');
 now+=121_000;scan();await turn();eq(calls.loadPerf,3,'performance refetched on scan after 120 s');

 // Desk: allocation rerun after 120 s.
 open('desk');await flush();const allocAtOpen=calls.runBookAlloc;
 now+=121_000;scan();await flush();ok(calls.runBookAlloc>allocAtOpen,'desk allocation recomputed on scan after 120 s');

 eq(errors.filter(e=>!/Not implemented|canvas|Network forbidden/.test(e)).length,0,'no jsdom errors: '+errors.join(' | '));
 console.log(`PASS: page refresh on scan — ${checks} checks`);
})().catch(e=>{console.error(e);process.exit(1);});
