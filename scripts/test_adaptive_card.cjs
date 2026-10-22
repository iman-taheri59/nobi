/* The adaptive-thresholds card, executed (not regex-matched).
 *
 * server/tests/test_adaptive_ui_wiring.py checks that the ids exist and that the
 * safety label is wired. This runs the real inline block in jsdom with a stubbed
 * `NBX.core.api.get` and asserts what a person would see:
 *
 *   1. a successful measurement fills every cell and says, in words, that the
 *      number was NOT applied — the one property ADR-0020 depends on;
 *   2. a refusal (`ok: false`, e.g. the venue is unreachable) clears the numbers
 *      and prints the named reason: no zero, no em-dash pretending to be data;
 *   3. a network throw leaves a sentence instead of a blank card;
 *   4. the request carries the symbol, the timeframe and the *same candle source*
 *      as the backtest control next to it;
 *   5. when the pair changes under the numbers, the card says so and then
 *      re-measures — stale numbers wearing a fresh header are the quiet version
 *      of the same lie (1.0.14).
 *
 * No CDN, no server: the page is read from disk and the API is a function.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('index.html', 'utf8');
const dom = new JSDOM(html, { url: 'https://dashboard.test/', runScripts: 'outside-only' });
const { window } = dom;
const doc = window.document;

// lucide and lightweight-charts come from a CDN and are absent here; the page
// already degrades without them, so the stubs are enough.
window.refreshIcons = () => {};
window.requestAnimationFrame = (fn) => setTimeout(fn, 0);   // jsdom has no rAF
window.cancelAnimationFrame = (id) => clearTimeout(id);
window.matchMedia = window.matchMedia || (() => ({ matches: false, addEventListener() {},
  removeEventListener() {}, addListener() {}, removeListener() {} }));
window.lucide = { createIcons: () => {} };
window.LightweightCharts = {
  createChart: () => ({
    addLineSeries: () => ({ setData: () => {}, applyOptions: () => {} }),
    addCandlestickSeries: () => ({ setData: () => {} }),
    timeScale: () => ({ fitContent: () => {} }),
    applyOptions: () => {}, remove: () => {},
  }),
  CrosshairMode: { Normal: 0 },
};

// Run every inline <script> in document order, exactly as a browser would.
for (const el of [...doc.querySelectorAll('script:not([src])')]) {
  try {
    window.eval(el.textContent);
  } catch (e) {
    // A script that legitimately needs a CDN object is allowed to fail here;
    // the card's own IIFE must not be one of them, which is asserted below.
    if (!/createChart|lucide|LightweightCharts/.test(String(e && e.message))) throw e;
  }
}

assert.equal(typeof window.adMeasureThresholds, 'function',
  'the adaptive card did not install its entry point');

const text = (id) => (doc.getElementById(id) || {}).textContent || '';
// The page renders numbers in its own locale — Persian digits and the Persian
// decimal separator by default. The assertions are about the value, so normalise
// rather than fight the formatter (test_display.cjs already pins the formatting).
const plain = (s) => String(s)
  .replace(/[۰-۹]/g, (d) => String(d.charCodeAt(0) - 0x6f0))
  .replace(/٫/g, '.').replace(/[٬,]/g, '').trim();
const val = (id) => plain(text(id));
const FIXTURE = {
  ok: true, symbol: 'BTCUSDT', tf: '15m', candles_source: 'reference', applied: false,
  regime: 'calm',
  volatility: { atr: 141.2, atr_pct_price: 0.17, percentile: 0.12, window: 200, period: 14 },
  scales: { timeframe: 1.25, regime_entry: 1.0, regime_stop: 0.85, entry: 1.25, stop: 0.85 },
  thresholds: { base: { long: 68, neutral: 55 }, recommended: { long: 75, neutral: 58.8 } },
  frequency: { bars: 340, long_share: 0.4, median_score: 51 },
  why: '15m is noisier than 1h: the same score is weaker evidence there, so the bar rises to 1.25x.',
};

function stub(result) {
  window.NBX = window.NBX || {};
  window.NBX.core = window.NBX.core || {};
  window.NBX.config = window.NBX.config || { pythonApi: '/api' };
  window.NBX.core.api = {
    get: async (path) => {
      stub.lastPath = path;
      if (result instanceof Error) throw result;
      return result;
    },
  };
}

(async () => {
  // ---- 0. a control change before anything was measured fires nothing ----
  // Someone who has not asked for a measurement must not get one because they
  // touched the symbol box.
  // The API is stubbed first, so a request would be *visible* rather than
  // silently absorbed by the "no backend" branch — otherwise this test passes
  // for the wrong reason.
  stub(FIXTURE);
  assert.equal(stub.lastPath, undefined, 'a measurement ran before anyone asked');
  doc.getElementById('btTf').value = 'D';
  doc.getElementById('btTf').dispatchEvent(new window.Event('change'));
  await new Promise((r) => setTimeout(r, 900));
  assert.equal(stub.lastPath, undefined, 'changing the controls fired a measurement nobody asked for');

  // ---- 1. a real measurement ----
  doc.getElementById('btSym').innerHTML = '<option value="BTCUSDT" selected>BTCUSDT</option>';
  doc.getElementById('btTf').value = '15m';
  const src = doc.getElementById('btSrc');
  if (src) src.value = 'reference';
  stub(FIXTURE);
  await window.adMeasureThresholds();

  assert.equal(val('adLong'), '68 → 75.0', `adLong was ${text('adLong')}`);
  assert.equal(val('adNeutral'), '55 → 58.8', `adNeutral was ${text('adNeutral')}`);
  assert.equal(val('adFactor'), '1.25');
  assert.equal(val('adPct'), '12%');
  assert.equal(val('adShare'), '40.0%');
  assert.equal(val('adBars'), '340');
  assert.match(text('adWhy'), /noisier than 1h/);
  // The safety property: never a bare number without the label.
  assert.match(text('adState'), /not applied|اعمال نشد/,
    `the card did not say it was not applied: "${text('adState')}"`);
  assert.match(text('adBadge'), /measurement only|فقط اندازه‌گیری/);
  assert.match(stub.lastPath, /\/adaptive\/thresholds\/BTCUSDT\?tf=/);
  assert.match(stub.lastPath, /source=reference/, 'the card ignored the candle source');
  assert.match(stub.lastPath, /limit=400/);

  // ---- 2. a named refusal ----
  stub({ ok: false, error: 'upstream_unavailable', unavailable: true });
  await window.adMeasureThresholds();
  assert.equal(val('adLong'), '—', 'a refusal left a number on screen');
  assert.equal(val('adBars'), '—');
  assert.match(text('adState'), /upstream_unavailable/, 'the refusal was not named');
  assert.doesNotMatch(text('adState'), /\b0\b/, 'the refusal rendered a zero');
  assert.equal(text('adWhy'), '', 'stale prose survived a refusal');

  // ---- 3. a thrown request ----
  stub(new Error('Failed to fetch'));
  await window.adMeasureThresholds();
  assert.match(text('adState'), /Failed to fetch|measurement failed|اندازه‌گیری انجام نشد/);
  assert.equal(val('adLong'), '—');

  // ---- 4. the pair changed under the numbers (1.0.14) ----
  doc.getElementById('btTf').value = '15m';
  doc.getElementById('btSym').innerHTML = '<option value="BTCUSDT" selected>BTCUSDT</option>';
  stub(FIXTURE);
  await window.adMeasureThresholds();
  let state = window.adCardState();
  assert.equal(state.stale, false, 'a fresh measurement reported itself stale');
  assert.equal(state.asked, state.now);

  // Someone flips the timeframe. Nothing is auto-fired yet, but the card must
  // stop claiming the numbers describe what is selected.
  doc.getElementById('btTf').value = '4h';
  doc.getElementById('btTf').dispatchEvent(new window.Event('change'));
  state = window.adCardState();
  assert.equal(state.stale, true, 'the card kept a fresh badge after the pair changed');
  assert.match(text('adBadge'), /stale|کهنه/, `badge did not go stale: "${text('adBadge')}"`);
  assert.ok(text('adState').includes(`${state.asked} → ${state.now}`),
    `the drift was not named as "${state.asked} → ${state.now}": "${text('adState')}"`);
  // The stale numbers stay, because they are still true for their own pair —
  // they just may not wear the current header.
  assert.equal(val('adLong'), '68 → 75.0');

  // ... and after the debounce, it re-measures for the newly selected pair.
  await new Promise((r) => setTimeout(r, 900));
  assert.match(stub.lastPath, /BTCUSDT\?tf=4h/, `re-measure used ${stub.lastPath}`);
  state = window.adCardState();
  assert.equal(state.stale, false, 'the re-measure did not clear the stale flag');

  // ---- 5. the disabled-backend path ----
  window.NBX.core.api = undefined;
  await window.adMeasureThresholds();
  assert.match(text('adState'), /backend|بک‌اند/,
    'a missing backend produced no explanation');

  console.log('PASS — adaptive card: numbers, the not-applied label, named refusals, no zeros, staleness named and re-measured');
  // The page arms intervals (clock, polling) that would keep node alive forever;
  // this script asserts and leaves.
  dom.window.close();
  process.exit(0);
})().catch((e) => {
  console.error(`FAIL — ${e.message}`);
  process.exit(1);
});
