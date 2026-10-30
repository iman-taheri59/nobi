/* Offline approval-queue integration tests. Fixtures never place an order. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM, VirtualConsole } = require('jsdom');
const turn = () => new Promise(resolve => setImmediate(resolve));

async function run(file) {
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
    await turn();
    assert.equal(errors.length, 0, `${file}: boot errors`);
    if (!w.NBX) w.NBX = { core: { api: {}, engine: {} } };
    const core = w.NBX.core;
    assert.ok(core, `${file}: core fixture initialized`);
    const calls = [];
    const plan = { action: 'buy', entry: 100, stop: 95, tp1: 106, tp2: 110,
      size_quote: 1000, risk_pct: 0.5, blocked: false,
      get approval() {
        const tf = (w.live && w.live.timeframe) || '15m';
        const step = ({'15m':900, '1h':3600, '4h':14400, D:86400})[tf];
        const now = Date.now()/1000, bar = Math.floor(now/step)*step-step;
        return {ok:true, score:80, symbol:'BTCIRT', tf, bar_open:bar, expires_at:bar+2*step,
                server_now:now, signal_key:'a'.repeat(64)};
      } };
    core.api.getBackendRiskPlan = async (...args) => { calls.push(args); return { ...plan }; };
    core.api.postPaperOrder = () => { throw Error('test must not place orders'); };
    core.api.postExecMode = () => { throw Error('test must not change execution mode'); };
    // Candle timestamps are epoch SECONDS, not browser milliseconds.
    const barStart = Math.floor(Date.now() / 900000) * 900;
    assert.ok(barStart < 1e11, 'fixture candle timestamp must use epoch seconds');
    core.engine.candles = { 'BTCIRT:15m': [{ t: barStart, c: 100 }] };
    w.live = { timeframe: '15m', ranked: [], unavailable: false };
    w.eval("execMode = 'semi'; symMode = {}; queueDismissed.clear();");
    const desk = w.document.getElementById('page-desk');
    desk.classList.remove('hidden');
    await w.refreshQueue();
    assert.equal(w.eval('queue.length'), 0);
    w.live.ranked = [{ symbol: 'BTCIRT', direction: 'LONG', composite: 80 }];
    // The actual backend scan publishes nbx:scan; it does not emit engine signals.
    w.dispatchEvent(new w.CustomEvent('nbx:scan'));
    await new Promise(resolve => setTimeout(resolve, 1650));
    assert.equal(w.eval('queue.length'), 1, `${file}: new scan must fill a previously empty queue`);
    assert.deepEqual(calls.at(-1), ['BTCIRT', 'buy', '15m', true]);
    assert.equal(w.document.querySelectorAll('#queueList .qRow').length, 1);
    const count = calls.length;
    desk.classList.add('hidden');
    w.dispatchEvent(new w.CustomEvent('nbx:scan'));
    await new Promise(resolve => setTimeout(resolve, 1650));
    assert.equal(calls.length, count, 'hidden desk must not spend plan requests');
    desk.classList.remove('hidden');

    w.live.timeframe = '1h';
    core.engine.candles['BTCIRT:1h'] = [{ t: Math.floor(Date.now() / 3600000) * 3600, c: 100 }];
    await w.refreshQueue();
    assert.deepEqual(calls.at(-1), ['BTCIRT', 'buy', '1h', true]);
    assert.ok(w.document.getElementById('queueTimeframe').textContent.includes('1h'));
    core.api.getBackendRiskPlan = async () => ({ ...plan, blocked: true, warnings: ['bad-atr'] });
    await w.refreshQueue();
    assert.equal(w.eval('queue.length'), 0, 'blocked plan must never be offered');
    assert.ok(w.document.getElementById('queueDiagnostics').textContent.includes('bad-atr'));
    core.api.getBackendRiskPlan = async () => ({ ok: false, error: 'no-fx-rate' });
    await w.refreshQueue();
    assert.ok(w.document.getElementById('queueDiagnostics').textContent.includes('no-fx-rate'));
    core.api.getBackendRiskPlan = async () => { throw Error('do not echo this private error'); };
    await w.refreshQueue();
    assert.ok(w.document.getElementById('queueDiagnostics').textContent.includes('plan-request-failed'));
    assert.ok(!w.document.getElementById('queueDiagnostics').textContent.includes('private error'));

    core.api.getBackendRiskPlan = async () => ({ ...plan });
    w.eval("execMode = 'manual';");
    await w.refreshQueue();
    assert.equal(w.eval('queue.length'), 0);
    assert.ok(w.document.getElementById('queueDiagnostics').textContent.includes('semi'));
    w.eval("execMode = 'auto';");
    await w.refreshQueue();
    assert.equal(w.eval('queue.length'), 0, 'automatic symbols do not await approval');
    w.eval("execMode = 'semi';");
    await w.refreshQueue();
    w.resolveQ(w.eval("queue[0].id"), false);
    await w.refreshQueue();
    assert.equal(w.eval('queue.length'), 0, 'same-candle decision stays dismissed');
    w.eval('queueDismissed.clear();');
    // An old request must not overwrite a newer result.
    let release;
    core.api.getBackendRiskPlan = () => new Promise(resolve => { release = resolve; });
    const oldRequest = w.refreshQueue();
    w.live.ranked = [];
    await w.refreshQueue();
    release({ ...plan });
    await oldRequest;
    assert.equal(w.eval('queue.length'), 0, 'stale async result must be ignored');
    // Exercise the real API wrapper as well as the queue's stubbed API.
    if (!w.NBX.createCore) w.eval(fs.readFileSync('nbx-core.js', 'utf8'));
    const realApi = w.NBX.createCore().api;
    realApi.standaloneMobile = false;
    let requested;
    realApi.get = async url => { requested = new URL(url, w.location.origin); return { ...plan }; };
    await realApi.getBackendRiskPlan('BTCIRT', 'buy', '1h', true);
    assert.equal(requested.searchParams.get('approval'), 'true');
    assert.equal(requested.searchParams.get('tf'), '1h');
    await realApi.getBackendRiskPlan('BTCIRT', 'buy');
    assert.equal(requested.searchParams.get('tf'), '15m');
    assert.equal(requested.searchParams.get('approval'), null, 'non-queue previews keep existing behavior');
    realApi.get = async () => ({ ok: false, error: 'no-fx-rate' });
    assert.equal((await realApi.getBackendRiskPlan('BTCIRT', 'buy')).error, 'no-fx-rate');
    realApi.get = async () => { throw new w.NBX.errors.NBXError('HTTP', 'private text', { status: 403 }); };
    assert.equal((await realApi.getBackendRiskPlan('BTCIRT', 'buy')).error, 'auth-required');
    // Expiration is still enforced and is now explained, not bypassed.
    w.live.ranked = [{ symbol: 'BTCIRT', direction: 'LONG', composite: 80 }];
    core.api.getBackendRiskPlan = async () => ({ ...plan });
    await w.refreshQueue();
    w.eval("queue[0].state = 'expired'; renderQueue();");
    assert.equal(w.document.querySelectorAll('#queueList .qRow').length, 0);
    assert.ok(w.document.getElementById('queueDiagnostics').textContent.includes('TTL'));
    // Entering Desk waits for server mode/overrides before picking candidates.
    w.eval("execMode = 'semi'; symMode = {}; runBookAlloc = () => {};");
    core.api.getExecMode = async () => {
      await new Promise(resolve => setTimeout(resolve, 25));
      return { ok: true, default: 'manual', symbols: {} };
    };
    let modePlanCalls = 0;
    core.api.getBackendRiskPlan = async () => { modePlanCalls++; return { ...plan }; };
    w.document.querySelector('.navbtn[data-tab="desk"]').click();
    await new Promise(resolve => setTimeout(resolve, 180));
    assert.equal(modePlanCalls, 0, 'mode must load before requesting semi candidates');
    assert.equal(w.eval('queue.length'), 0);
    assert.equal(errors.length, 0, `${file}: runtime errors`);
    console.log(`${file}: approval queue scan/visibility/timeframe/reasons/modes/dismissal/race PASS`);
  } finally { w.close(); }
}

(async () => {
  for (const file of ['index.html', 'nobitex-mobile.html']) await run(file);
})().catch(e => { console.error(e); process.exitCode = 1; });
