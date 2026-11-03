/* Diagnostic-only filtered queue candidates. Offline JSDOM; no account, network,
 * execution-mode write or broker placement. A filtered row must explain the
 * server refusal without becoming an approval/order control. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM, VirtualConsole } = require('jsdom');
const turn = () => new Promise(resolve => setImmediate(resolve));

async function run(file, lang) {
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
  const d = w.document;
  try {
    if (d.readyState !== 'complete') await new Promise(resolve => w.addEventListener('load', resolve, { once: true }));
    await turn();
    assert.equal(errors.length, 0, `${file}: boot errors`);
    if (!w.NBX) w.NBX = { core: { api: {}, engine: {} } };
    const api = w.NBX.core.api;
    let orderCalls = 0;
    api.getExecMode = async () => ({ ok: true, default: 'semi', symbols: {} });
    api.postExecMode = () => { throw Error('Execution-mode writes forbidden'); };
    api.postPaperOrder = async () => { orderCalls++; return { ok: true }; };
    api.postWhatIf = () => { throw Error('Filtered rows must not request what-if'); };
    w.eval(`LANG='${lang}'; PREFS.numFmt='en'; execMode='semi'; symMode={}; execModeTries=99; queueDismissed.clear(); queueUncertain.clear(); queueInflight.clear();`);
    // Apply static i18n without renderDynamic(): that path starts loadExecMode()
    // and its debounced queue rebuild, which would race the explicit refreshes below.
    w.eval(`document.querySelectorAll('[data-i18n]').forEach(el=>{ const k=el.dataset.i18n; if(k) el.textContent=t(k); });`);
    d.getElementById('page-desk').classList.remove('hidden');

    const bar = Math.floor(Date.now() / 900000) * 900 - 900;
    const approval = symbol => ({ ok: true, symbol, tf: '15m', bar_open: bar,
      server_now: bar + 910, expires_at: bar + 1800, score: 80, signal_key: 'a'.repeat(64) });
    const plan = (symbol, execution) => ({ ok: true, entry: 100, stop: 95, tp1: 106, tp2: 110,
      size_quote: 1000, risk_pct: 0.5, blocked: false, approval: approval(symbol), execution });
    const refused = { executable: false, blocked_by: 'execution-rr-too-low', plan_entry: 100,
      execution_entry: 102, execution_rr: 1.2, min_rr: 1.5 };
    const executable = { executable: true, blocked_by: null, plan_entry: 100,
      execution_entry: 101, execution_rr: 1.5, min_rr: 1.5 };

    w.live = { timeframe: '15m', ranked: [
      { symbol: 'XRPIRT', direction: 'LONG', composite: 80 },
      { symbol: 'USOONIRT', direction: 'LONG', composite: 79 },
    ] };
    api.getBackendRiskPlan = async symbol => plan(symbol, refused);
    await w.refreshQueue();

    assert.equal(w.eval('queue.length'), 0, 'refused candidates never enter the approval queue');
    assert.equal(w.eval('queueFiltered.length'), 2);
    assert.equal(d.getElementById('qCount').textContent, '0');
    assert.ok(d.getElementById('deskBadge').classList.contains('hidden'));
    const section = d.getElementById('queueFilteredSection');
    assert.ok(!section.classList.contains('hidden'), 'diagnostic section is visible');
    assert.equal(d.getElementById('queueFilteredCount').textContent, '2');
    const rows = d.querySelectorAll('#queueFilteredList .qFilteredRow');
    assert.equal(rows.length, 2);
    assert.equal(d.querySelectorAll('#queueList .qRow').length, 0);
    assert.equal(section.querySelectorAll('button,input,select,textarea,a').length, 0, 'no controls or links');
    assert.equal(section.querySelectorAll('[data-approve],[data-reject],[data-edit],[data-symmode]').length, 0);
    assert.ok(section.textContent.includes('XRPIRT') && section.textContent.includes('USOONIRT'));
    assert.ok(section.textContent.includes('execution-rr-too-low'));
    assert.ok(section.textContent.includes('1.20') && section.textContent.includes('1.50'));
    assert.ok(section.textContent.includes('102'));
    assert.ok(section.textContent.includes(lang === 'fa' ? 'انقضا' : 'Expires'));
    assert.ok(d.getElementById('queueDiagnostics').textContent.includes('execution-rr-too-low'));

    // Even if a caller guesses the diagnostic id, it is not an approval row.
    const filteredId = w.eval('queueFiltered[0].id');
    w.resolveQ(filteredId, true);
    w.resolveQ(filteredId, false);
    await turn();
    assert.equal(orderCalls, 0, 'filtered ids cannot place or reject orders');
    assert.equal(w.eval('queue.length'), 0);

    // Missing numbers are unknown, not zero; hostile reason codes stay text.
    w.live.ranked = [{ symbol: 'BTCIRT', direction: 'LONG', composite: 80 }];
    api.getBackendRiskPlan = async symbol => plan(symbol, { executable: false,
      blocked_by: '<img src=x onerror="window.__pwned=1">', plan_entry: null,
      execution_entry: null, execution_rr: null, min_rr: null });
    await w.refreshQueue();
    const unknownRow = d.querySelector('#queueFilteredList .qFilteredRow');
    const unknownMetrics = Array.from(unknownRow.querySelectorAll('.grid > div .num')).map(el => el.textContent);
    assert.equal(unknownMetrics.length, 4);
    assert.ok(unknownMetrics[0].includes('100'), 'plan entry falls back to the validated plan close');
    assert.deepEqual(unknownMetrics.slice(1), ['—', '—', '—'], 'missing execution numbers are em dashes, not fabricated zeros');
    assert.ok(unknownRow.textContent.includes('execution-blocked'));
    assert.equal(section.querySelectorAll('img,script').length, 0);
    assert.ok(!w.__pwned);

    // Mixed scan: only the executable candidate is approvable.
    w.live.ranked = [
      { symbol: 'BTCIRT', direction: 'LONG', composite: 80 },
      { symbol: 'ETHIRT', direction: 'LONG', composite: 79 },
    ];
    api.getBackendRiskPlan = async symbol => plan(symbol, symbol === 'BTCIRT' ? executable : refused);
    await w.refreshQueue();
    assert.equal(w.eval('queue.length'), 1);
    assert.equal(w.eval('queueFiltered.length'), 1);
    assert.equal(d.querySelectorAll('#queueList .qRow').length, 1);
    assert.equal(d.querySelectorAll('#queueFilteredList .qFilteredRow').length, 1);
    assert.ok(d.querySelector('#queueList .qRow').textContent.includes('BTCIRT'));
    assert.ok(d.querySelector('#queueFilteredList .qFilteredRow').textContent.includes('ETHIRT'));
    assert.equal(d.querySelectorAll('#queueList .qApp').length, 1);
    assert.equal(section.querySelectorAll('.qApp').length, 0);

    // A language switch repaints the diagnostic rows, not just the static chrome.
    const other = lang === 'fa' ? 'en' : 'fa';
    w.eval(`LANG='${other}'; renderQueue();`);
    assert.equal(d.querySelector('#queueFilteredList .qFilteredRow').dataset.qflang, other);
    assert.ok(section.textContent.includes(other === 'fa' ? 'انقضا' : 'Expires'));
    w.eval(`LANG='${lang}'; renderQueue();`);

    // TTL and preview staleness are diagnostic, never a retry invitation.
    w.eval('queueFiltered[0].clockWall -= 1000000; queueFiltered[0].clockPerf -= 1000000; queueFiltered[0].execPerf = performance.now() - 21000;');
    w.queueTick();
    const expiredRow = d.querySelector('#queueFilteredList .qFilteredRow');
    assert.ok(expiredRow.textContent.includes(lang === 'fa' ? 'منقضی' : 'Expired'));
    assert.ok(expiredRow.textContent.includes(lang === 'fa' ? 'کهنه' : 'stale'));
    assert.equal(section.querySelectorAll('button,input,select').length, 0);
    w.resolveQ(w.eval('queueFiltered[0].id'), true);
    await turn();
    assert.equal(orderCalls, 0);
    // Expired diagnostics stay visible as expired; they are not silently pruned.
    w.renderQueue();
    assert.equal(d.querySelectorAll('#queueFilteredList .qFilteredRow').length, 1);
    assert.ok(d.querySelector('#queueFilteredList .qFilteredRow').textContent.includes(lang === 'fa' ? 'منقضی' : 'Expired'));

    // A new scan with no LONG candidates clears the diagnostic section.
    w.live.ranked = [{ symbol: 'BTCIRT', direction: 'EXIT', composite: 40 }];
    await w.refreshQueue();
    assert.equal(w.eval('queueFiltered.length'), 0);
    assert.ok(d.getElementById('queueFilteredSection').classList.contains('hidden'));

    // Stale async responses cannot resurrect filtered rows after a newer scan.
    let release;
    api.getBackendRiskPlan = () => new Promise(resolve => { release = resolve; });
    w.live.ranked = [{ symbol: 'BTCIRT', direction: 'LONG', composite: 80 }];
    const old = w.refreshQueue();
    w.live.ranked = [];
    await w.refreshQueue();
    release(plan('BTCIRT', refused));
    await old;
    assert.equal(w.eval('queueFiltered.length'), 0);
    assert.ok(d.getElementById('queueFilteredSection').classList.contains('hidden'));

    assert.equal(errors.length, 0, errors.join('; '));
    console.log(`${file} [${lang}]: filtered diagnostics/non-actionable/TTL/stale/race PASS`);
  } finally {
    w.close();
  }
}

(async () => {
  for (const file of ['index.html', 'nobitex-mobile.html']) {
    for (const lang of ['fa', 'en']) await run(file, lang);
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
