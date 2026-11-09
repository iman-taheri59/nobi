/* 2.5.2 — a queue plan whose *scan* is too old is shown with its age and locked.
 * The candle window (`expires_at`) is unchanged; this is the second clock the
 * server now reports (`scan_age_s`, `max_scan_age_s`, `stale`). Mocked plans
 * only: no backend, no mode change, no order placed.
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
    const sourceBar = Date.parse('2026-10-28T07:15:00Z') / 1000;
    const serverNow = sourceBar + 1000;
    let placed = 0;
    // fresh / stale / unknown-clock / old-server rows, all inside the candle window
    const plans = {
      TRXIRT:  { scan_age_s: 50,  max_scan_age_s: 300, stale: false, stale_reason: null },
      DOGEIRT: { scan_age_s: 400, max_scan_age_s: 300, stale: true,  stale_reason: 'scan-too-old' },
      SYNIRT:  {},                                    // 2.5.1 server: no scan fields at all
      BLURIRT: { scan_age_s: 280, max_scan_age_s: 300, stale: false, stale_reason: null }, // goes stale 21 s later
    };
    // drift (second yardstick): fresh scan, but the market moved
    const drifts = {
      ETHIRT: { mid: 102.2, drift_pct: 2.2, drift_atr: 1.1, limit_pct: 1.5, limit_atr: 0.5, exceeded: true, reason: 'price-moved-pct' },
      TRXIRT: { mid: 100.1, drift_pct: 0.1, drift_atr: 0.05, limit_pct: 1.5, limit_atr: 0.5, exceeded: false, reason: null },
    };
    plans.ETHIRT = { scan_age_s: 10, max_scan_age_s: 300, stale: false, stale_reason: null };
    const symbols = Object.keys(plans);
    let rescans = 0;
    core.api.getBackendRiskPlan = async (symbol) => ({ action: 'buy', entry: 100, stop: 95,
      tp1: 106, tp2: 110, size_quote: 1000, risk_pct: 0.5, blocked: false,
      ...(drifts[symbol] ? { drift: drifts[symbol] } : {}),
      approval: { ok: true, score: 80, symbol, tf: '15m', bar_open: sourceBar,
        expires_at: sourceBar + 1800, server_now: serverNow, signal_key: 'a'.repeat(64), ...plans[symbol] } });
    core.api.getBackendSignals = async (tf, refresh) => { if (refresh) rescans++; return { scored: [], refreshing: false, age_s: 3 }; };
    core.api.postPaperOrder = async () => { placed++; return { ok: false, error: 'queue-signal-stale', blocked: true, scan_age_s: 401 }; };
    core.api.postExecMode = () => { throw Error('must not change execution mode'); };
    w.live = { timeframe: '15m', ranked: symbols.map(symbol => ({ symbol, direction: 'LONG', composite: 80 })) };
    w.eval("execMode = 'semi'; symMode = {}; queueDismissed.clear();");
    const t0 = Date.parse('2026-10-28T07:31:40Z');
    w.Date.now = () => t0;
    w.performance.now = () => 0;
    await w.refreshQueue();
    const byId = sym => w.eval(`queue.find(q => q.sym === '${sym}')`);
    assert.equal(w.eval('queue.length'), 5, 'stale rows stay visible, they are not dropped');
    const row = sym => w.document.querySelector(`[data-row="${byId(sym).id}"]`);
    const chip = sym => row(sym).querySelector('[data-qage]');
    const btn = sym => row(sym).querySelector('.qApp');

    // fresh: age shown, button live
    assert.ok(!chip('TRXIRT').classList.contains('hidden'));
    assert.match(chip('TRXIRT').textContent, /50|۵۰/);
    assert.ok(!/stale|کهنه/.test(chip('TRXIRT').textContent));
    assert.equal(btn('TRXIRT').disabled, false);
    assert.equal(w.queueStale(byId('TRXIRT')), false);

    // stale: amber chip with minutes, button locked, sentence names the budget
    assert.match(chip('DOGEIRT').textContent, /(stale|کهنه)/);
    assert.match(chip('DOGEIRT').textContent, /6|۶/);
    assert.ok(chip('DOGEIRT').className.includes('amber'));
    assert.equal(btn('DOGEIRT').disabled, true);
    assert.match(row('DOGEIRT').querySelector('[data-qexec]').textContent, /(5 min|۵ دقیقه)/);
    assert.equal(w.eval(`queueRemaining(queue.find(q => q.sym === 'DOGEIRT'))`), 800, 'candle TTL untouched by staleness');

    // unknown scan clock: fail closed, says so (swap one row's plan and refresh)
    plans.DOGEIRT = { scan_age_s: null, max_scan_age_s: 300, stale: true, stale_reason: 'scan-clock-unavailable' };
    await w.refreshQueue();
    assert.equal(btn('DOGEIRT').disabled, true);
    assert.match(chip('DOGEIRT').textContent, /(unknown|نامعلوم)/);
    plans.DOGEIRT = { scan_age_s: 400, max_scan_age_s: 300, stale: true, stale_reason: 'scan-too-old' };
    await w.refreshQueue();

    // old server: nothing to show, nothing locked
    assert.ok(chip('SYNIRT').classList.contains('hidden'));
    assert.equal(btn('SYNIRT').disabled, false);

    // clicking a stale row never reaches the order API
    w.resolveQ(byId('DOGEIRT').id, true);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(placed, 0, 'stale approval must be refused locally');
    assert.equal(w.eval('queue.length'), 5);

    // age advances with the clock: BLURIRT crosses the budget after 21 s, no refresh needed
    assert.equal(btn('BLURIRT').disabled, false);
    w.Date.now = () => t0 + 21000;
    w.performance.now = () => 21000;
    w.queueTick();
    assert.equal(btn('BLURIRT').disabled, true, 'a row goes stale between refreshes');
    assert.match(chip('BLURIRT').textContent, /(stale|کهنه)/);
    assert.equal(btn('TRXIRT').disabled, false);

    // drift: fresh scan but price moved → amber chip names the move, locked, rescan button visible
    assert.equal(w.queueStale(byId('ETHIRT')), true);
    assert.equal(btn('ETHIRT').disabled, true);
    assert.match(chip('ETHIRT').textContent, /(stale|کهنه)/);
    assert.match(chip('ETHIRT').textContent, /(2[.,٫]20?|۲٫۲۰)/);
    assert.match(chip('ETHIRT').textContent, /ATR/);
    assert.match(row('ETHIRT').querySelector('[data-qexec]').textContent, /(1[.,٫]5|۱٫۵)/);
    assert.ok(!row('ETHIRT').querySelector('.qRescan').classList.contains('hidden'), 'rescan offered on a stale row');
    assert.ok(row('TRXIRT').querySelector('.qRescan').classList.contains('hidden'), 'no rescan button on a fresh row');
    assert.ok(!/stale|کهنه|ATR/.test(chip('TRXIRT').textContent), 'fresh drift within limits adds nothing to the chip');
    w.resolveQ(byId('ETHIRT').id, true);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(placed, 0, 'drifted approval refused locally');
    // rescan button → forced refresh request, nothing placed
    row('ETHIRT').querySelector('.qRescan').click();
    for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));
    assert.equal(rescans, 1, 'rescan asks the server once with refresh=true');
    assert.equal(placed, 0);

    // item 10 — thin-depth chip in the queue row, from the same gates block
    w.live.gates = { mode: 'shadow', applied: false, depth_warn: { usd: 562, applied: false },
      verdicts: { DOGEIRT: { code: 'ok', depth_thin: true, inputs: { depth_usd: 300 } }, TRXIRT: { code: 'ok', depth_thin: false, inputs: { depth_usd: 4000 } } } };
    await w.refreshQueue();
    assert.ok(row('DOGEIRT').querySelector('[data-qdepth]'), 'thin book chip on the queue row');
    assert.match(row('DOGEIRT').querySelector('[data-qdepth]').textContent, /300|۳۰۰/);
    assert.equal(row('TRXIRT').querySelector('[data-qdepth]'), null);
    assert.equal(btn('TRXIRT').disabled, false, 'depth label never locks approval');

    // a server-side refusal with queue-signal-stale locks the row instead of dismissing the candle
    const trx = byId('TRXIRT');
    trx.approval.stale = false; trx.approval.scan_age_s = 50;
    w.resolveQ(trx.id, true);
    for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve));
    assert.equal(placed, 1);
    assert.equal(w.eval(`queue.some(q => q.sym === 'TRXIRT')`), true, 'row kept, not dismissed');
    assert.equal(w.eval(`queueDismissed.size`), 0);
    assert.equal(btn('TRXIRT').disabled, true);
    assert.equal(w.queueStale(byId('TRXIRT')), true);
    assert.equal(w.eval(`queueRemaining(queue.find(q => q.sym === 'TRXIRT'))`), 779, 'TTL not extended by the refusal');
    assert.deepEqual(errors, []);
    console.log(`${file}: stale plan badge, lock, tick ageing, local + server refusal PASS`);
  } finally { w.close(); }
}
(async () => {
  for (const file of ['index.html', 'nobitex-mobile.html']) await verify(file);
})().catch(e => { console.error(e); process.exitCode = 1; });
