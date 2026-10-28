/* Regression: closed-bar approvals use server deadlines, never the chart clock.
 * Uses the user's observed times with mocked plans; never contacts a backend,
 * changes execution mode or places an order.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM, VirtualConsole } = require('jsdom');

async function verify(file) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push(e.message));
  const dom = new JSDOM(fs.readFileSync(file, 'utf8'), {
    url: 'https://dashboard.test/', runScripts: 'dangerously', pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(w) {
      w.setInterval = () => 0;
      w.fetch = async () => ({ ok: false, status: 503, json: async () => ({}) });
      w.scrollTo = () => {};
    },
  });
  const w = dom.window;
  try {
    if (w.document.readyState !== 'complete') await new Promise(resolve => w.addEventListener('load', resolve, { once: true }));
    await new Promise(resolve => setImmediate(resolve));
    if (!w.NBX) w.NBX = { core: { api: {}, engine: {} } };
    const core = w.NBX.core;
    const symbols = ['TRXIRT', 'BLURIRT', 'DOGEIRT', 'SYNIRT', 'POLIRT'];
    const sourceBar = Date.parse('2026-10-28T07:15:00Z') / 1000;
    core.engine.candles = Object.fromEntries(symbols.map(s => [s + ':15m', [{ t: sourceBar-900, c: 100 }]]));
    let serverNow=Date.parse('2026-10-28T07:31:28.372Z')/1000;
    let missingClock=false;
    core.api.getBackendRiskPlan = async (symbol) => ({ action: 'buy', entry: 100, stop: 95,
      tp1: 106, tp2: 110, size_quote: 1000, risk_pct: 0.5, blocked: false,
      approval:missingClock ? undefined : {ok:true, score:80, symbol, tf:'15m', bar_open:sourceBar,
        expires_at:sourceBar+1800, server_now:serverNow, signal_key:'a'.repeat(64)} });
    core.api.postPaperOrder = () => { throw Error('reproduction must not place orders'); };
    core.api.postExecMode = () => { throw Error('reproduction must not change execution mode'); };
    w.live = { timeframe: '15m', ranked: symbols.map(symbol => ({ symbol, direction: 'LONG', composite: 80 })) };
    w.eval("execMode = 'semi'; symMode = {}; queueDismissed.clear();");
    for (const [clock, now] of [
      ['reported browser', '2026-10-28T07:39:39.982Z'],
      ['reported server (no browser skew)', '2026-10-28T07:31:28.372Z'],
      ['slow browser', '2026-10-28T07:00:00.000Z'],
    ]) {
      w.Date.now = () => Date.parse(now);
      await w.refreshQueue();
      const ttls = Array.from(w.eval('queue.map(q => q.ttl)'));
      assert.equal(ttls.length, 5);
      assert.ok(ttls.every(t=>t>800 && t<=812), 'closed bar must retain its bounded server-time window');
      assert.equal(w.eval('queue[0].id'), 'TRXIRT:'+sourceBar, 'identity must not come from unrelated chart cache');
      const expiry=w.eval('queue[0].approval.expires_at');
      await w.refreshQueue();
      assert.equal(w.eval('queue[0].approval.expires_at'),expiry,'refresh must never extend expiry');
      // A backward clock correction cannot add validity to a proposal.
      w.Date.now=()=>Date.parse(now)-1000000;
      assert.ok(w.eval('queueRemaining(queue[0])')<=812);
      // Absolute elapsed time catches up after a sleeping tab or forward jump.
      w.Date.now=()=>Date.parse(now)+1000000;
      assert.equal(w.eval('queueRemaining(queue[0])'),0);
      w.resolveQ(w.eval('queue[0].id'),true); // must refuse before calling order stub
      assert.equal(w.eval('queue[0].state'),'expired');
      console.log(`${file}: closed bar, ${clock}, no refresh extension, sleeping tab and expired click PASS`);
    }
    serverNow=sourceBar+1800;
    await w.refreshQueue();
    assert.ok(Array.from(w.eval('queue')).every(q=>q.state==='expired'), 'expiry boundary is exclusive');
    missingClock=true;
    await w.refreshQueue();
    assert.equal(w.eval('queue.length'),0,'old backend without authoritative clock fails closed');
    assert.ok(w.document.getElementById('queueDiagnostics').textContent.includes('queue-clock-unavailable'));
    assert.deepEqual(errors, []);
  } finally { w.close(); }
}
(async () => {
  for (const file of ['index.html', 'nobitex-mobile.html']) await verify(file);
})().catch(e => { console.error(e); process.exitCode = 1; });
