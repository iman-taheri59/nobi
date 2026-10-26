// UI smoke test for the Performance page against a live local backend.
//
//   npm install                 # once: tailwind + jsdom
//   python3 scripts/ui_smoke_prep.py
//   node scripts/ui_smoke_test.js
//
// It boots index.html offscreen, waits for the journal fetch to settle, then
// asserts the MSI card, the seven KPI cells and the absence of JS errors.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

// jsdom is a devDependency now; the /tmp fallback keeps older checkouts and the
// sandbox working, where the install lived outside the repo.
let JSDOM, VirtualConsole, CookieJar;
try {
  ({ JSDOM, VirtualConsole, CookieJar } = require('jsdom'));
} catch (e) {
  // The /tmp fallback keeps older checkouts working, where the install lived
  // outside the repo. jsdom >= 30 needs Node >= 22.22.2 (undici 8 calls
  // zlib.createZstdDecompress); on older Node the real failure is inside
  // undici, but the fallback used to hide it behind a "module not found".
  try {
    ({ JSDOM, VirtualConsole, CookieJar } = require('/tmp/node_modules/jsdom'));
  } catch {
    throw new Error(
      `jsdom could not be loaded (${e.message}). ` +
      `jsdom 30 requires Node >= 22.22.2; this process is ${process.version}.`);
  }
}

// Same default as ui_smoke_prep.py, on every platform.
const pagePath = process.argv[2] || process.env.NBX_SMOKE_PAGE
  || path.join(os.tmpdir(), 'page.html');
const html = fs.readFileSync(pagePath, 'utf8');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push('jsdomError: ' + (e.message || e)));
vc.on('error', (m) => errors.push('console.error: ' + m));

// Where the page should send its /api calls. CI and local runs both use :8000;
// an override lets the empty-journal case be exercised without touching it.
const origin = process.env.SMOKE_ORIGIN || 'http://localhost:8000/';

// The page polls in the background (ws status every five seconds) and jsdom has
// no WebSocket, so a timer could throw *after* the verdict was printed — which
// once killed a green run with a stack trace, not with evidence about the page.
// Rejections and exceptions are therefore reported by their own exit code, and
// the window is torn down before exiting so nothing fires afterwards.
let reported = false;
function finish(code) {
  reported = true;
  try { dom.window.close(); } catch (e) {}
  process.exit(code);
}
process.on('uncaughtException', (e) => {
  if (reported) return;
  console.error('harness crash:', (e && e.stack) || e);
  finish(2);
});
process.on('unhandledRejection', (e) => {
  if (reported) return;
  console.error('harness crash:', (e && e.stack) || e);
  finish(2);
});

const cookieJar = new CookieJar();
const smokeUser = process.env.SMOKE_USERNAME;
const smokePassword = process.env.SMOKE_PASSWORD;
if (smokeUser && smokePassword) {
  const payload = JSON.stringify({ username: smokeUser, password: smokePassword });
  const responseHeaders = execFileSync('curl', [
    '-sS', '-D', '-', '-o', '/dev/null', '-X', 'POST',
    '-H', 'Content-Type: application/json', '--data-binary', payload,
    new URL('/api/auth/login', origin).toString(),
  ], { encoding: 'utf8' });
  for (const line of responseHeaders.split(/\r?\n/)) {
    if (/^set-cookie:/i.test(line)) {
      cookieJar.setCookieSync(line.replace(/^set-cookie:\s*/i, ''), origin);
    }
  }
}

const dom = new JSDOM(html, {
  url: origin,
  cookieJar,
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  virtualConsole: vc,
});
const { window } = dom;
// Node's fetch has no document base or browser cookie integration. Resolve
// relative URLs and mirror Set-Cookie/Cookie through jsdom's own jar so the
// smoke test follows the real login flow instead of bypassing authorization.
window.fetch = async (u, o = {}) => {
  const url = /^[a-z]+:\/\//i.test(String(u)) ? String(u) : new URL(String(u), origin).toString();
  const headers = new globalThis.Headers(o.headers || {});
  const cookie = dom.cookieJar.getCookieStringSync(url);
  if (cookie) headers.set('cookie', cookie);
  const init = { ...o, headers };
  // Node >= 22's undici requires `signal` to be an instance of *its own*
  // AbortSignal. A signal created inside the jsdom window (AbortSignal.timeout,
  // AbortController) lives in another realm and is rejected with
  // "RequestInit: Expected signal ... to be an instance of AbortSignal" —
  // which silently kills every timed request and leaves the page half-rendered.
  // Bridge the foreign signal into a Node-realm controller instead of dropping
  // it, so the page's own timeouts still apply.
  if (init.signal && !(init.signal instanceof globalThis.AbortSignal)) {
    const foreign = init.signal;
    const bridge = new globalThis.AbortController();
    if (foreign.aborted) bridge.abort(foreign.reason);
    else foreign.addEventListener('abort', () => bridge.abort(foreign.reason), { once: true });
    init.signal = bridge.signal;
  }
  const response = await fetch(url, init);
  const setCookies = typeof response.headers.getSetCookie === 'function'
    ? response.headers.getSetCookie() : [];
  for (const value of setCookies) dom.cookieJar.setCookieSync(value, url);
  return response;
};
window.onerror = (m) => errors.push('window.onerror: ' + m);
// jsdom does not implement scrolling; switching tabs would otherwise report
// "Not implemented: Window's scrollTo()" as an app error.
window.scrollTo = () => {};
window.HTMLElement.prototype.scrollIntoView = () => {};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  // let the app's first paint + journal fetch run
  await sleep(4000);
  try {
    if (typeof window.loadPerf === 'function') await window.loadPerf();
    if (typeof window.loadAudit === 'function') await window.loadAudit();
    if (typeof window.loadSettings === 'function') await window.loadSettings();
  } catch (e) {
    errors.push('loadPerf/loadAudit threw: ' + e.message);
  }
  await sleep(2500);

  const doc = window.document;

  // Open one row: the detail panel must paint without any chart library.
  const firstRow = doc.querySelector('#perfBody tr[data-sig]');
  let detail = null, detailSvg = 0, detailText = '';
  if (firstRow) {
    firstRow.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    await sleep(2500);
    detail = doc.querySelector('tr.sig-detail');
    detailSvg = detail ? detail.querySelectorAll('svg').length : 0;
    detailText = (detail?.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  }

  // Backtest gate bar: a report that cannot claim the live rule must say so in
  // the bar the operator reads, not only in the JSON. Driven through the page's
  // own renderer with a shaped report — no network, no candles.
  const basisChip = doc.getElementById('btBasisChip');
  let taOnlyChip = null, taOnlyCleared = null;
  if (typeof window.btRenderGate === 'function' && basisChip) {
    window.btRenderGate({
      gate: { state: 'PASS', decision_basis: {
        matches_live: false, live_weights: { ta: 0.55, fa: 0.25, liq: 0.2 } } },
      aggregate: { windows: 3 }, bars: 600,
    });
    taOnlyChip = {
      text: (basisChip.textContent || '').trim(),
      visible: !basisChip.classList.contains('hidden'),
      title: (basisChip.getAttribute('title') || '').slice(0, 80),
    };
    window.btRenderGate({ gate: { state: 'PASS', decision_basis: { matches_live: true } },
      aggregate: { windows: 3 }, bars: 600 });
    taOnlyCleared = basisChip.classList.contains('hidden');
  }

  // Render a full shaped report through the page's own renderer and read the
  // gate bar. A broken template used to leak `(LANG==='fa'?'٪':'%')` into the
  // bar as literal text — visible to the user, invisible to every gate.
  let barText = '', barLeak = null;
  if (typeof window.btRender === 'function') {
    window.btRender({ report: {
      symbol: 'BTCIRT', tf: '1h', bars: 600, train_bars: 240, eval_bars: 120,
      aggregate: { trades: 34, windows: 3, profit_factor: 1.4, expectancy_r: 0.9,
        hit_rate: 60, max_drawdown: 4, ambiguous_share: 50.9, ambiguous_exits: 17 },
      costs: { slippage_bps: 6, modeled: true },
      decision_basis: { matches_live: false, live_weights: { ta: 0.55, fa: 0.25, liq: 0.2 } },
      gate: { state: 'FAIL', failed: ['ambiguous_share_le_10'],
        checks: { ambiguous_share_le_10: { value: 50.9, pass: false, required: '<= 10 %' } },
        decision_basis: { matches_live: false, live_weights: { ta: 0.55, fa: 0.25, liq: 0.2 } },
        message: 'Failed: ambiguous_share_le_10' },
      details: [],
    } });
    barText = (doc.getElementById('btGateBar')?.textContent || '').replace(/\s+/g, ' ').trim();
    const bad = /LANG\s*===|undefined|NaN|\[object Object\]/.exec(barText);
    barLeak = bad ? bad[0] : null;
  }

  const txt = (sel) => (doc.querySelector(sel)?.textContent || '').trim();
  const kpiGrid = doc.querySelector('#pVoid')?.closest('.grid');
  const kpiCells = kpiGrid ? kpiGrid.children.length : 0;
  const msiBadge = doc.querySelector('#msiBadge');
  const warn = doc.querySelector('#msiWarn');

  const report = {
    kpiCells,
    pVoid: txt('#pVoid'),
    pHit: txt('#pHit'),
    pExp: txt('#pExp'),
    pSig: txt('#pSig'),
    pRes: txt('#pRes'),
    msiBadge: txt('#msiBadge'),
    msiBadgeClass: msiBadge?.className || '',
    msiMsg: txt('#msiMsg'),
    msiWarnVisible: !!warn && !warn.classList.contains('hidden') && (warn.textContent || '').trim().length > 10,
    msiWarnText: (warn?.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120),
    perfRows: doc.querySelectorAll('#perfBody tr[data-sig]').length,
    exportBtns: !!(doc.querySelector('#perfExportCsv') && doc.querySelector('#perfExportJson')),
    auditRows: doc.querySelectorAll('#auBody tr').length,
    auditFirst: (doc.querySelector('#auBody tr')?.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 90),
    telegramChip: txt('#auTg'),
    detailOpened: !!detail,
    detailSvg,
    detailText,
    settingsRows: doc.querySelectorAll('#setFlags tr').length,
    settingsSecrets: doc.querySelectorAll('#setSecrets > div').length,
    settingsWeights: doc.querySelectorAll('#setTaWeights .bar-fill').length,
    settingsProviders: doc.querySelectorAll('#setProviders > div').length,
    settingsHash: txt('#setHash'),
    settingsLeak: /supersecret|NBX_API_TOKEN=|sk-live/.test(doc.body.innerHTML),
    navTabs: [...doc.querySelectorAll('.navbtn')].map(b => b.dataset.tab),
    mnavTabs: [...doc.querySelectorAll('#mobileBottomNav [data-mtab]')].map(b => b.dataset.mtab),
    mnavLabels: [...doc.querySelectorAll('#mobileBottomNav [data-mtab]')].map(b => b.textContent.trim()),
    prefControls: [...doc.querySelectorAll('#setPrefs select[data-pref]')].map(s => s.dataset.pref),
    prefScan: doc.querySelector('select[data-pref="scanSec"]')?.value,
    prefLockedControls: doc.querySelectorAll('#setTaWeights input, #setThresholds input, #setRiskLimits input').length,
    envLine: txt('#setEnvFile'),
    secretWhere: [...doc.querySelectorAll('#setSecrets .num')].filter(e => /server\/.env/.test(e.textContent)).length,
    tgButton: !!doc.getElementById('setTgTest'),
    perfWarnVisible: !!doc.querySelector('#perfWarn') && !doc.querySelector('#perfWarn').classList.contains('hidden'),
    perfWarn: (doc.querySelector('#perfWarn')?.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120),
    perfChip: txt('#perfOrigin'),
    taOnlyChip,
    taOnlyCleared,
    barText,
    barLeak,
    errors,
  };
  console.log(JSON.stringify(report, null, 2));

  const problems = [];
  if (kpiCells !== 7) problems.push(`expected 7 KPI cells, got ${kpiCells}`);
  if (!report.pVoid) problems.push('#pVoid is empty');

  // The MSI card may never print a verdict it cannot support. Synthetic rows
  // are gone, so the two honest states are "unavailable" (no feed) and
  // "insufficient" (a feed, too few resolved outcomes) — and the badge has to
  // agree with the sample count printed next to it.
  const msiSaysUnavailable = /در دسترس نیست|unreachable|unavailable|ناموجود/i.test(report.msiBadge);
  const msiSaysInsufficient = /insufficient|ناکافی/i.test(report.msiBadge);
  if (!msiSaysUnavailable && !msiSaysInsufficient) {
    problems.push(`MSI badge is neither "unavailable" nor "insufficient": ${report.msiBadge}`);
  }
  const zeroSamples = /(نمونه|sample)[^0-9۰-۹]{0,3}[۰0]/.test(report.msiMsg);
  if (zeroSamples && !(msiSaysUnavailable || msiSaysInsufficient)) {
    problems.push('MSI reports zero samples but still prints a verdict');
  }
  if (msiSaysUnavailable && report.msiWarnVisible) {
    problems.push('MSI shows the "unavailable" badge and still warns about the verdict');
  }

  // The performance page may only warn about provenance when it has rows to
  // warn about; with an empty journal the warning stays hidden and the origin
  // chip must not collapse into a bare number ("0" is not provenance).
  if (report.perfWarnVisible && report.perfWarn.length < 40) {
    problems.push('#perfWarn is visible but says nothing');
  }
  if (report.perfChip && /^[\s۰-۹0-9.,٪%]+$/.test(report.perfChip)) {
    problems.push(`perf origin chip is a bare number: "${report.perfChip}"`);
  }
  // A TA-only backtest may not look like a verdict on the whole live decision.
  if (basisChip) {
    if (!taOnlyChip || !taOnlyChip.visible) problems.push('TA-only report did not raise the basis chip');
    else if (!taOnlyChip.text) problems.push('the basis chip is visible but empty');
    else if (!taOnlyChip.title) problems.push('the basis chip has no tooltip naming the live weights');
    if (taOnlyCleared !== true) problems.push('the basis chip did not clear when the basis matched live');
  }
  // The gate bar must never print code, placeholders or unformatted objects.
  if (barLeak) problems.push(`the gate bar leaks "${barLeak}": ${barText.slice(0, 100)}`);
  if (barText && !/bps|لغزش|slippage/.test(barText)) {
    problems.push(`the gate bar does not state the cost model: ${barText.slice(0, 100)}`);
  }
  if (!report.exportBtns) problems.push('export buttons missing');
  if (!report.auditRows) problems.push('#auBody has no audit rows');
  if (report.settingsRows < 5) problems.push(`settings flags table has ${report.settingsRows} rows`);
  if (report.settingsWeights !== 4) problems.push(`TA weights rendered ${report.settingsWeights}/4`);
  // The secrets list grows every time a credential is added; pinning a count
  // here turned each addition into a red CI run that said nothing about the
  // page. The floor catches a render that drops the section, and the pairing
  // below is the part that actually has to hold.
  if (report.settingsSecrets < 5) problems.push(`secrets cards ${report.settingsSecrets}/5 at least`);
  if (report.settingsProviders < 4) problems.push(`provider cards ${report.settingsProviders}`);
  if (!/config [0-9a-f]{6,}/.test(report.settingsHash)) problems.push(`config hash not shown: "${report.settingsHash}"`);
  if (report.settingsLeak) problems.push('a secret leaked into the page');

  // The phone is where the sidebar is off-canvas, so the bottom bar is the only
  // navigation. It used to be a hardcoded list that omitted `settings` — the tab
  // was in the DOM but unreachable on a phone. It must mirror the sidebar now.
  const missing = report.navTabs.filter(t => !report.mnavTabs.includes(t));
  if (missing.length) problems.push(`mobile nav is missing tabs: ${missing.join(', ')}`);
  if (report.mnavTabs.length !== report.navTabs.length) {
    problems.push(`mobile nav has ${report.mnavTabs.length} tabs, sidebar has ${report.navTabs.length}`);
  }
  if (report.mnavLabels.some(l => !l)) problems.push('mobile nav has an unlabelled tab');

  // A settings page with nothing settable is not a settings page. These four
  // must exist AND the engine parameters must stay non-writable.
  // Keys must stay unwritable, but the page must say *where* they live and
  // whether the server actually read that file — otherwise "token set" and
  // "token ignored" look identical.
  if (!/server\/\.env/.test(report.envLine)) problems.push(`env status line missing: "${report.envLine}"`);
  if (report.secretWhere !== report.settingsSecrets) {
    problems.push(`secrets show ${report.secretWhere}/${report.settingsSecrets} file hints`);
  }
  if (!report.tgButton) problems.push('Telegram test button missing');

  const want = ['scanSec', 'numFmt', 'motion', 'lang'];
  const lost = want.filter(k => !report.prefControls.includes(k));
  if (lost.length) problems.push(`missing preference controls: ${lost.join(', ')}`);
  if (report.prefLockedControls !== 0) problems.push('engine parameters rendered as inputs (must be display-only)');

  // Prove the control changes behaviour, not just the DOM.
  doc.documentElement.dataset.noReload = '1';
  const scanSel = doc.querySelector('select[data-pref="scanSec"]');
  const before = window.NBX_APP && window.NBX_APP.intervalSec;
  if (scanSel && window.NBX_APP) {
    scanSel.value = '30';
    scanSel.dispatchEvent(new window.Event('change'));
    await sleep(200);
    if (window.pref('scanSec') !== 30) problems.push('scanSec was not persisted');
    if (window.NBX_APP.intervalSec !== 30) problems.push(`scan timer stayed ${window.NBX_APP.intervalSec}s after change`);
    if (before === 30) problems.push('scan timer never had the default value');
    const motionSel = doc.querySelector('select[data-pref="motion"]');
    motionSel.value = 'reduce';
    motionSel.dispatchEvent(new window.Event('change'));
    await sleep(100);
    if (!doc.documentElement.classList.contains('reduce-motion')) problems.push('reduced motion did not apply');
    scanSel.value = '0';
    scanSel.dispatchEvent(new window.Event('change'));
    await sleep(100);
    if (!window.live || window.live.interval !== null) problems.push('manual mode left a running timer');
  }
  if (!report.detailOpened) problems.push('clicking a row did not open the detail panel');
  // Either a real path, or levels + a stated reason. Never a silent blank.
  if (report.detailOpened && !report.detailSvg) {
    problems.push('detail panel rendered no chart at all');
  }
  if (report.detailOpened && /ساخته می‌شوند|regenerated/i.test(report.detailText) && report.detailSvg > 1) {
    problems.push('simulated source must not draw a price line');
  }
  if (!/تنظیم‌نشده|not configured/i.test(report.telegramChip)) {
    problems.push(`telegram chip is not honest: "${report.telegramChip}"`);
  }
  // Persian digits; a live void row makes this "۱" (see the 2026-10-19 run).
  if (!/^[\u06F0-\u06F9\u0660-\u06690-9٬,]+$/.test(report.pVoid)) problems.push(`#pVoid is not a number: "${report.pVoid}"`);
  if (/^\s*\w+\.\w+\s*$/.test(report.msiBadge)) problems.push(`MSI badge is an untranslated key: ${report.msiBadge}`);
  if (/msi\.(realized|reference|samples)/.test(report.msiMsg)) problems.push('MSI message leaks untranslated i18n keys');
  // Regression: the four error-log buttons used to be silent no-ops whenever
  // the API object was missing (offline build, sandboxed preview, early JS
  // failure). An enabled button that does nothing is the bug being tested.
  {
    const doc = window.document;
    const tab = doc.querySelector('[data-tab="errors"]');
    if (tab) tab.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    await sleep(200);
    const saved = window.NBX;
    window.NBX = undefined;
    const host = () => doc.getElementById('toasts');
    for (const id of ['errRefresh', 'errSelfTest', 'errExport', 'errClear']) {
      const el = doc.getElementById(id);
      if (!el) { problems.push('missing control ' + id); continue; }
      if (host()) host().innerHTML = '';
      el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
      await sleep(150);
      const n = host() ? host().querySelectorAll('.toast').length : 0;
      if (!n) problems.push(id + ' gave no feedback when the API is unavailable');
    }
    window.NBX = saved;
  }

  // Regression: the local grid fallback dereferenced an undeclared `btCfg`
  // (and `_getCandlesForBacktest` read an undeclared `bars`), so the moment the
  // server did not answer the button died with "btCfg is not defined" — and
  // because the handler caught it, nothing reached the error log.
  try {
    const realFetch = window.fetch;
    window.fetch = (u, o) => (/\/backtest\/grid/.test(String(u))
      ? Promise.reject(new Error('simulated offline'))
      : realFetch(u, o));
    const g = await window.NBX.core.api.runGridSearch('BTCIRT', '1h', undefined, 400);
    window.fetch = realFetch;
    // With no candles there is nothing to search. The contract is now
    // "say so", not "compute on invented data".
    if (g && g.ok) {
      if (!(g.combo_count > 0)) problems.push('grid produced no combos from real candles');
    } else {
      if (!g || !g.unavailable) problems.push('grid failed without saying the exchange is unreachable');
      if (g && g.combo_count) problems.push('grid reported combos while unavailable');
    }
  } catch (e) {
    problems.push('grid fallback threw: ' + e.message);
  }
  if (errors.length) problems.push(`${errors.length} JS error(s): ` + errors.slice(0, 2).join(' | ').slice(0, 300));
  console.log(problems.length ? '\nFAIL:\n- ' + problems.join('\n- ') : '\nPASS');
  finish(problems.length ? 1 : 0);
})().catch((e) => { console.error('harness crash:', e); finish(2); });
