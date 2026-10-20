// UI smoke test for the Performance page against a live local backend.
//
//   npm install                 # once: tailwind + jsdom
//   python3 scripts/ui_smoke_prep.py
//   node scripts/ui_smoke_test.js
//
// It boots index.html offscreen, waits for the journal fetch to settle, then
// asserts the MSI card, the seven KPI cells and the absence of JS errors.
const fs = require('fs');

// jsdom is a devDependency now; the /tmp fallback keeps older checkouts and the
// sandbox working, where the install lived outside the repo.
let JSDOM, VirtualConsole;
try {
  ({ JSDOM, VirtualConsole } = require('jsdom'));
} catch (e) {
  ({ JSDOM, VirtualConsole } = require('/tmp/node_modules/jsdom'));
}

const pagePath = process.argv[2] || '/tmp/page.html';
const html = fs.readFileSync(pagePath, 'utf8');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push('jsdomError: ' + (e.message || e)));
vc.on('error', (m) => errors.push('console.error: ' + m));

const dom = new JSDOM(html, {
  url: 'http://localhost:8000/',
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  virtualConsole: vc,
});
const { window } = dom;
// Node's fetch has no document base: resolve the app's relative /api paths.
const origin = 'http://localhost:8000/';
window.fetch = (u, o) => fetch(/^[a-z]+:\/\//i.test(String(u)) ? u : new URL(String(u), origin).toString(), o);
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
    errors,
  };
  console.log(JSON.stringify(report, null, 2));

  const problems = [];
  if (kpiCells !== 7) problems.push(`expected 7 KPI cells, got ${kpiCells}`);
  if (!report.pVoid) problems.push('#pVoid is empty');
  // Two acceptable states now. When the exchange is unreachable the MSI card
  // must NOT print a verdict computed from synthetic rows — it must say so.
  // When the exchange is up it falls back to the ordinary verdict badge.
  const msiSaysUnavailable = /در دسترس نیست|unreachable|unavailable/i.test(report.msiBadge);
  if (msiSaysUnavailable) {
    if (report.msiWarnVisible) {
      problems.push('MSI shows the "unavailable" badge but still shows a verdict warning');
    }
  } else {
    if (!report.msiWarnVisible) problems.push('synthetic-baseline warning is not visible');
    if (!/insufficient|ناکافی/i.test(report.msiBadge)) {
      problems.push(`MSI badge is neither "unavailable" nor "insufficient": ${report.msiBadge}`);
    }
  }
  if (!report.perfWarnVisible) problems.push('#perfWarn (synthetic sample) not visible');
  if (!report.exportBtns) problems.push('export buttons missing');
  if (!report.auditRows) problems.push('#auBody has no audit rows');
  if (report.settingsRows < 5) problems.push(`settings flags table has ${report.settingsRows} rows`);
  if (report.settingsWeights !== 4) problems.push(`TA weights rendered ${report.settingsWeights}/4`);
  if (report.settingsSecrets !== 5) problems.push(`secrets cards ${report.settingsSecrets}/5`);
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
  if (report.secretWhere !== 5) problems.push(`secrets show ${report.secretWhere}/5 file hints`);
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
  process.exit(problems.length ? 1 : 0);
})().catch((e) => { console.error('harness crash:', e); process.exit(2); });
