const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {JSDOM, VirtualConsole} = require('jsdom');
const strip = x => String(x).replace(/[\u2066-\u2069\u200e\u200f\u061c]/g,'');
let assertions = 0;
const check = (a,b) => { assert.equal(a,b); assertions++; };
for (const file of ['index.html','nobitex-mobile.html']) {
  const html = fs.readFileSync(file,'utf8');
  const helper = html.split('/* DISPLAY-FORMAT-START:')[1]?.split('/* DISPLAY-FORMAT-END */')[0];
  assert.ok(helper, `${file}: helper missing`);
  const inputDom = new JSDOM('<input id="n" type="number" min="0" step="0.5" value="1234.5">');
  const document=inputDom.window.document;
  const ctx = {window:{}, document, LANG:'fa', format:'auto',
    numLocale(){return ctx.format==='auto'?(ctx.LANG==='fa'?'fa-IR':'en-US'):(ctx.format==='fa'?'fa-IR':'en-US');}};
  vm.createContext(ctx);
  vm.runInContext(helper.slice(helper.indexOf('*/')+2),ctx);
  for (const lang of ['fa','en']) for (const format of ['auto','fa','en']) {
    ctx.LANG=lang; ctx.format=format;
    const isFa = format==='fa' || (format==='auto' && lang==='fa');
    check(ctx.fmtNum(1234.5,1), isFa?'۱٬۲۳۴٫۵':'1,234.5');
    const money=ctx.fmtMoney(-1234.5,'USD',1);
    assert.ok(money.startsWith(lang==='fa'?'\u2067':'\u2066'));
    assert.ok(strip(money).includes(lang==='fa'?'دلار':'USD'));
    assert.ok(strip(money).includes(isFa?'۱٬۲۳۴٫۵':'1,234.5'));
    check((money.match(/[\u2066-\u2068]/g)||[]).length,(money.match(/\u2069/g)||[]).length);
    check(ctx.quoteUnit('BTCIRT'),'IRT'); check(ctx.quoteUnit('BTCUSDT'),'USDT');
    assert.ok(strip(ctx.fmtMarketPrice(125,'BTCIRT')).includes(lang==='fa'?'تومان':'toman'));
    assert.ok(strip(ctx.fmtMarketPrice(125,'BTCUSDT')).includes(lang==='fa'?'تتر':'USDT'));
    assert.ok(strip(ctx.fmtQty(0.5,'BTCIRT')).includes(lang==='fa'?'بیت‌کوین':'BTC'));
    check(ctx.parseInputNumber('۱٬۲۳۴٫۵'),1234.5);
    check(ctx.parseInputNumber('١٢٣٤٫٥'),1234.5);
    check(ctx.parseInputNumber('1,234.5'),1234.5);
    assert.ok(Number.isNaN(ctx.parseInputNumber('0xff')));
    assert.ok(!/^0[.,]0+ /.test(strip(ctx.fmtMarketPrice(1e-9,'BTCUSDT'))));
    check(new Intl.DateTimeFormat(ctx.dateLocale()).resolvedOptions().numberingSystem,isFa?'arabext':'latn');
    check(ctx.parseInputNumber('−۰٫۵'),-0.5);
    assert.ok(Number.isNaN(ctx.parseInputNumber('')));
    for(const missing of [null,undefined,'','   ',false,[]]) check(ctx.fmtMoney(missing,'USD'),'—'); check(ctx.fmtMoney(Infinity,'USD'),'—');
    check(ctx.fmtMarketPrice(12,'UNKNOWN'),'—'); check(ctx.PC(null),'—');
    assert.ok(strip(ctx.fmtMoney(0,'USD')).includes(isFa?'۰':'0'));
    check(ctx.localizeText('12 / ۳۴'),isFa?'۱۲ / ۳۴':'12 / 34');
    check(ctx.localizeText('ADR-0018'),'ADR-0018');
    check(ctx.uiLabel('LONG'),lang==='fa'?'خرید':'Long');
    check(ctx.tfLabel('15m'),(isFa?'۱۵':'15')+(lang==='fa'?' دقیقه':' min'));
  }
  ctx.LANG='fa';ctx.format='auto';ctx.prepareNumericInputs();
  const el=document.getElementById('n'); check(el.value,'۱۲۳۴.۵');
  el.value='۱۲٫۵'; check(ctx.parseInputNumber(el.value),12.5);
  check(el.inputMode,'decimal');
  el.value='-۱';el.dispatchEvent(new inputDom.window.Event('blur'));check(el.validity.valid,false);
  el.value='۱٫۲';el.dispatchEvent(new inputDom.window.Event('blur'));check(el.validity.valid,false);
  el.value='۱٫۵';el.dispatchEvent(new inputDom.window.Event('blur'));check(el.validity.valid,true);
  el.value='123';el.setSelectionRange(1,1);el.dispatchEvent(new inputDom.window.Event('input'));check(el.value,'۱۲۳');check(el.selectionStart,1);
  el.value='45';el.dispatchEvent(new inputDom.window.InputEvent('input',{isComposing:true}));check(el.value,'45');
  el.dispatchEvent(new inputDom.window.CompositionEvent('compositionend'));check(el.value,'۴۵');
  inputDom.window.close();
  console.log(`${file}: locale/preference, financial units, bidi isolation, parsing PASS`);
}
// Boot the real inline dashboard without external scripts, server or network.
const errors=[];
const virtualConsole=new VirtualConsole();
virtualConsole.on('jsdomError', e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync('index.html','utf8'), {
  url:'https://dashboard.test/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole,
  beforeParse(w){w.setInterval=()=>0; w.fetch=async()=>({ok:false,status:503,json:async()=>({})});
    w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};}
});
const w=dom.window;
check(errors.length,0);
for (const lang of ['fa','en']) {
  w.eval(`LANG='${lang}'; PREFS.numFmt='auto';manual.sym='BTCIRT';document.getElementById('mSym').innerHTML='<option value="BTCIRT">BTCIRT</option>'; applyStaticI18n();`);
  check(w.document.documentElement.dir,lang==='fa'?'rtl':'ltr');
  check(w.document.getElementById('mPrice').type,'text');
  check(w.document.getElementById('mType').options[0].textContent,lang==='fa'?'محدود':'Limit');
  assert.ok(w.document.querySelector('[data-unit-for="mPrice"]').textContent.includes(lang==='fa'?'تومان':'toman'));
  w.eval(`positions.length=0;positions.push({sym:'BTCIRT',label:{fa:'بیت‌کوین',en:'BTC'},entry:100,price:110,size:'0.5',stop_pct:3,sl:'—',tp1:'—',state:{fa:'باز',en:'Open'}});renderPositions();`);
  const cells=w.document.querySelectorAll('#posBody tr td');
  assert.ok(cells[1].textContent.includes(lang==='fa'?'تومان':'toman'));
  assert.ok(cells[3].textContent.includes(lang==='fa'?'بیت‌کوین':'BTC'));
  check(w.eval('positions[0].size'),'0.5'); // presentation must never mutate maths
  w.eval(`manual.sym='BTCUSDT';document.getElementById('mSym').innerHTML='<option value="BTCUSDT">BTCUSDT</option>';manual.price=10;manual.amount=2;manual.stop=9;manual.tp=12;updateTicket();`);
  assert.ok(w.document.getElementById('pNotional').textContent.includes(lang==='fa'?'تتر':'USDT'));
  w.document.getElementById('mPrice').value=lang==='fa'?'۱۲٫۵':'12.5';
  w.document.getElementById('mPrice').dispatchEvent(new w.Event('input',{bubbles:true}));
  check(w.eval('manual.price'),12.5);
  w.eval(`renderOB({ok:true,symbol:'BTCUSDT',mid:12,bids:[[11,2]],asks:[[13,3]],bid_depth:22,ask_depth:39});`);
  assert.ok(w.document.getElementById('obRows').textContent.includes(lang==='fa'?'تتر':'USDT'));
  w.eval(`btRender({report:{symbol:'BTCUSDT',aggregate:{trades:1,windows:1},details:[{sample_trades:[{entry:10,exit:11,pnl:2,r:1}]}]}});`);
  assert.ok(w.document.getElementById('btTradesBody').textContent.includes(lang==='fa'?'تتر':'USDT'));
}
check(errors.length,0);
// Hold/progress widths must remain ASCII CSS percentages, never localized.
w.eval('holdTick(0);mTick(0)');
check(w.document.querySelector('#holdBtnEl .fill').style.width,'0%');
check(w.document.querySelector('#mSubmit .fill').style.width,'0%');
w.eval('resetHold();resetM()');
const sent=[];
let equity=1002;
w.NBX={core:{engine:{metrics:{}},api:{
  getPaperPositions:async()=>({positions:[{symbol:'BTCIRT',entry_price:100,last_price:110,qty:1e-7,state:'OPEN'}]}),
  getPaperMetrics:async()=>({metrics:{equity,start_equity:1000}}),
  getBackendRiskState:async()=>({openHeat:0}),
  postPaperOrder:async p=>{sent.push(p);return {ok:true,position:{id:"fixture",client_order_id:"fixture"}};}
}}};
// Expose the real private renderer only in this test, with automatic network boot disabled.
const app=fs.readFileSync('nbx-app.js','utf8')
  .replace('  window.NBX_APP = {','  window.__displayTest = {refreshPaperState};\n  window.NBX_APP = {')
  .replace("  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);\n  else start();",'');
w.eval(app);
(async()=>{
  for(const lang of ['fa','en']) {
    w.eval(`LANG='${lang}';PREFS.numFmt='auto';`);
    equity=1002;await w.__displayTest.refreshPaperState();
    assert.ok(w.document.getElementById('kEquity').textContent.includes(lang==='fa'?'دلار':'USD'));
    check(w.eval('positions[0].size'),1e-7);
    equity=null;await w.__displayTest.refreshPaperState();check(w.document.getElementById('kEquity').textContent,'—');
    equity=0;await w.__displayTest.refreshPaperState();assert.ok(w.document.getElementById('kEquity').textContent.includes(lang==='fa'?'۰':'0'));
    await w.eval(`manual.sym='BTCUSDT';manual.side='buy';manual.amount=2;manual.stop=9;manual.price=12.5;submitManual();`);
    const payload=sent[sent.length-1];
    assert.deepEqual(JSON.parse(JSON.stringify(payload)), {symbol:'BTCUSDT',side:'buy',qty:2,price:12.5,stop:9,source:'MANUAL',size_quote:25});
  }
  check(JSON.stringify(sent[0]),JSON.stringify(sent[1]));
  // Actual language button path must repaint cached account data immediately.
  w.eval("PREFS.numFmt='en';setLang('fa');");
  assert.ok(w.document.getElementById('kEquity').textContent.includes('دلار'));
  assert.ok(w.document.getElementById('kEquity').textContent.includes('0.00'));
  check(w.eval('pref(\'numFmt\')'),'en');
  w.eval("setLang('en');");
  assert.ok(!/[۰-۹]/.test(w.document.getElementById('kEquity').textContent));
  w.document.getElementById('qCount').textContent='9';w.eval('updateTicket()');check(w.document.getElementById('qCount').textContent,'9');
  assert.ok(w.eval("baseOpts('USDT').localization.priceFormatter(1)").includes('USDT'));
  w.eval("window.testUnit='IRT';window.testOpts=baseOpts(()=>window.testUnit)");
  assert.ok(w.testOpts.localization.priceFormatter(1).includes('toman'));
  w.testUnit='USDT';assert.ok(w.testOpts.localization.priceFormatter(1).includes('USDT'));
  check(errors.length,0);w.close();
  console.log(`Dashboard + application: FA/RTL, EN/LTR, account USD, units, inputs/caret, unchanged numeric order payloads PASS (${assertions} checks total)`);
})().catch(e=>{w.close();console.error(e);process.exitCode=1;});
