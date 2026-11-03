/* Offline approval-queue execution-preview regressions.
 * No real account, network, execution-mode write or broker placement. */
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
      w.fetch = async () => { throw Error('Real network forbidden'); };
      w.scrollTo = () => {};
    },
  });
  const w = dom.window;
  try {
    if (w.document.readyState !== 'complete') await new Promise(resolve => w.addEventListener('load', resolve, { once: true }));
    await turn();
    assert.equal(errors.length, 0, `${file}: boot errors`);
    if (!w.NBX) w.NBX = { core: { api: {}, engine: {} } };
    const api = w.NBX.core.api;
    api.getExecMode = async () => ({ ok: true, default: 'semi', symbols: {} });
    api.postExecMode = () => { throw Error('Execution-mode writes forbidden'); };
    api.postPaperOrder = () => { throw Error('Test must not place orders'); };
    w.eval("execMode='semi'; symMode={}; PREFS.numFmt='en'; queueDismissed.clear(); queueUncertain.clear(); queueInflight.clear();");
    w.document.getElementById('page-desk').classList.remove('hidden');
    w.live = { timeframe: '15m', ranked: [{ symbol: 'BTCIRT', direction: 'LONG', composite: 80 }] };

    let bar = Math.floor(Date.now() / 900000) * 900 - 900;
    const plan = (execution) => ({
      ok: true, entry: 100, stop: 95, tp1: 106, tp2: 110, size_quote: 1000,
      risk_pct: 0.5, blocked: false, execution,
      approval: { ok: true, symbol: 'BTCIRT', tf: '15m', bar_open: bar,
        server_now: bar + 910, expires_at: bar + 1800, score: 80, signal_key: 'a'.repeat(64) },
    });
    const executable = { executable: true, blocked_by: null, plan_entry: 100,
      execution_entry: 101, execution_rr: 1.5, min_rr: 1.5 };

    // A definite execution refusal at scan time is not an actionable queue row.
    api.getBackendRiskPlan = async () => plan({ executable: false, blocked_by: 'execution-rr-too-low' });
    await w.refreshQueue();
    assert.equal(w.eval('queue.length'), 0);
    assert.ok(w.document.getElementById('queueDiagnostics').textContent.includes('execution-rr-too-low'));

    // An executable preview is displayed at the execution entry, not the stale plan close.
    api.getBackendRiskPlan = async () => plan(executable);
    await w.refreshQueue();
    assert.equal(w.eval('queue.length'), 1);
    const row = w.document.querySelector('#queueList .qRow');
    assert.ok(row.textContent.includes('R:TP2'));
    assert.equal(row.querySelector('[data-qrr]').textContent, '1.50');
    assert.ok(row.querySelector('[data-qexec]').textContent.includes('1.50'));
    assert.equal(row.querySelector('.qApp').disabled, false);

    // The 20s execution TTL is repainted by the existing one-second queue tick.
    w.eval('queue[0].execPerf = performance.now() - 21000;');
    w.queueTick();
    assert.ok(/stale|کهنه/.test(row.querySelector('[data-qexec]').textContent));

    // Edited levels ask the server for a fresh execution preview and gate Approve.
    const projection = { ok: true, projection: { loss_at_stop_pct_of_equity: 0.42, blocked: false } };
    api.postWhatIf = async () => ({ ...projection, execution: { executable: false, blocked_by: 'execution-rr-too-low' } });
    w.toggleRowEdit(w.eval('queue[0].id'));
    await w.askRowWhatIf(w.eval('queue[0]'));
    assert.equal(w.eval('queue[0].edit.execution.executable'), false);
    assert.equal(w.document.querySelector('.qApp').disabled, true);
    api.postWhatIf = async () => ({ ...projection, execution: executable });
    await w.askRowWhatIf(w.eval('queue[0]'));
    assert.equal(w.document.querySelector('.qApp').disabled, false);
    w.toggleRowEdit(w.eval('queue[0].id'));

    // A definite broker refusal retires the candle instead of inviting an immediate retry.
    let orderCalls = 0;
    api.postPaperOrder = async () => { orderCalls++; return { ok: false, error: 'execution-rr-too-low' }; };
    w.resolveQ(w.eval('queue[0].id'), true);
    await turn();
    assert.equal(orderCalls, 1);
    assert.equal(w.eval('queue.length'), 0);
    assert.equal(w.eval('queueDismissed.size'), 1);
    await w.refreshQueue();
    assert.equal(w.eval('queue.length'), 0, 'definitive refusal must not be re-offered on the same candle');

    // An unknown outcome stays locked, not dismissed as a clean refusal.
    w.eval('queueDismissed.clear(); queueUncertain.clear();');
    bar += 900;
    await w.refreshQueue();
    api.postPaperOrder = async () => ({ ok: false, outcome_unknown: true });
    w.resolveQ(w.eval('queue[0].id'), true);
    await turn();
    assert.equal(w.eval('queueUncertain.size'), 1);
    assert.equal(w.document.querySelector('.qApp').disabled, true);
    await w.refreshQueue();
    assert.equal(w.document.querySelector('.qApp').disabled, true);
    assert.equal(errors.length, 0, errors.join('; '));
    console.log(`${file}: execution preview/TTL/gating/refusal/unknown PASS`);
  } finally {
    w.close();
  }
}

(async () => {
  for (const file of ['index.html', 'nobitex-mobile.html']) await run(file);
})().catch(e => { console.error(e); process.exitCode = 1; });
