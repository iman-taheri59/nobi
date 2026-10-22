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
 *      of the same lie (1.0.14);
 *   6. the A/B evidence line (1.0.15) shows a verdict **with its sample size**,
 *      refuses on a named error without inventing numbers, and always repeats
 *      that nothing was applied and that a reference feed cannot certify;
 *   7. old candles are named as old (1.0.18): a percentage measured on a series
 *      that stopped three days ago is a claim about three days ago, and the card
 *      must say so in its own words — not fold it into "wrong pair";
 *   8. the backtest card repeats the rule on the largest set of numbers on the
 *      page (1.0.20): a stale chip with the age in it, hidden again when the
 *      candles are current;
 *   9. the market-scan card does it for the ranking it computes in the browser
 *      (1.0.21), and the browser's bar arithmetic is the same arithmetic the
 *      server uses — eight 15m bars is eight bars on both sides.
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

  // ---- 5. the A/B evidence line (1.0.15) ----
  const EV = {
    ok: true, symbol: 'BTCUSDT', tf: '15m', candles_source: 'reference', applied: false,
    promotable: false, verdict: 'inconclusive',
    windows: { train_bars: 255, eval_bars: 85, count: 4, paired: true },
    baseline: { profit_factor: 0.382, expectancy_r: -0.4483, trades: 22 },
    adaptive: { profit_factor: 0.243, expectancy_r: -0.457, trades: 6 },
    delta: { profit_factor: -0.139, expectancy_r: -0.0087, trades: -16 },
    why: 'out-of-sample trades below 30 in at least one arm (base 22, adaptive 6)',
  };
  doc.getElementById('btTf').value = '15m';
  stub(EV);
  await window.adEvidenceRun();
  assert.match(stub.lastPath, /\/adaptive\/evidence\/BTCUSDT\?tf=15m/, `evidence used ${stub.lastPath}`);
  assert.match(stub.lastPath, /limit=900/);
  // The sample size travels with the verdict: a "favours" with no window count
  // would be a slogan, not evidence.
  assert.match(val('adEvWindows'), /4.*255.*85/, `windows line was "${text('adEvWindows')}"`);
  assert.match(val('adEvPf'), /0\.38.*0\.24/, `PF line was "${text('adEvPf')}"`);
  assert.match(text('adEvVerdict'), /inconclusive|نامعلوم/);
  assert.match(text('adEvWhy'), /not applied|اعمال نشد/);
  // Reference data may show the method, never certify a strategy.
  assert.match(text('adEvWhy'), /cannot certify|تأیید نمی‌کند/);

  // A named refusal clears the numbers instead of leaving the previous run.
  stub({ ok: false, error: 'lower-bar-untestable', message: 'a bar below 68 filters nothing' });
  await window.adEvidenceRun();
  assert.equal(val('adEvPf'), '—', 'a refusal left a PF on screen');
  assert.equal(val('adEvWindows'), '—');
  assert.match(text('adEvVerdict'), /lower-bar-untestable/);
  assert.match(text('adEvWhy'), /filters nothing/);

  // The verdict belongs to a pair: moving the selection says so.
  stub({ ...EV, verdict: 'favours-adaptive' });
  doc.getElementById('btTf').value = '15m';
  await window.adEvidenceRun();
  assert.match(text('adEvVerdict'), /favours the higher bar|به نفع آستانهٔ بالاتر/);
  doc.getElementById('btTf').value = '4h';
  doc.getElementById('btTf').dispatchEvent(new window.Event('change'));
  assert.match(text('adEvVerdict'), /run it again|دوباره اجرا کنید/,
    `the evidence did not go stale with the pair: "${text('adEvVerdict')}"`);
  // ... and the debounced re-measure of the threshold card must not resurrect a
  // verdict about the old pair.
  await new Promise((r) => setTimeout(r, 900));
  assert.match(text('adEvVerdict'), /run it again|دوباره اجرا کنید/);

  // ---- 6. stale candles are named as stale, separately from a stale pair (1.0.18) ----
  doc.getElementById('btSym').innerHTML = '<option value="BTCUSDT" selected>BTCUSDT</option>';
  doc.getElementById('btTf').value = '15m';
  stub({ ...FIXTURE, freshness: { stale: true, reason: 'behind', age_bars: 41.2, tolerance_bars: 2 } });
  await window.adMeasureThresholds();
  let st = window.adCardState();
  assert.equal(st.stale, false, 'stale data was reported as a wrong pair');
  assert.equal(st.dataStale, true, 'old candles left the card looking current');
  assert.match(text('adBadge'), /stale data|داده کهنه/, `badge said "${text('adBadge')}"`);
  // The reason carries the arithmetic, from the response, not from a constant.
  // Persian digits by default — normalise, do not fight the formatter.
  assert.ok(plain(text('adState')).includes('41.2'), `the age was not named: "${text('adState')}"`);
  assert.match(text('adState'), /not applied|اعمال نشد/);
  // The numbers stay: they are true for their own (old) window, they just may
  // not wear a present-tense badge.
  assert.equal(val('adLong'), '68 → 75.0');

  // ... and a current series clears it again.
  stub({ ...FIXTURE, freshness: { stale: false, reason: 'current', age_bars: 0.1, tolerance_bars: 2 } });
  await window.adMeasureThresholds();
  st = window.adCardState();
  assert.equal(st.dataStale, false);
  assert.match(text('adBadge'), /measurement only|فقط اندازه‌گیری/);

  // A response with no freshness block must not be treated as stale (older
  // backends keep working) — and must not be treated as fresh either: the badge
  // simply stays on the measurement label.
  stub({ ...FIXTURE });
  await window.adMeasureThresholds();
  assert.equal(window.adCardState().dataStale, false);

  // ---- 7. the backtest result card names the age of its candles (1.0.20) ----
  const chip = doc.getElementById('btFreshChip');
  assert.ok(chip, 'the backtest card has no freshness chip');
  window.btRender({ report: { aggregate: {} }, freshness: { stale: true, reason: 'behind', age_bars: 41.2 } });
  assert.equal(chip.classList.contains('hidden'), false, 'a stale run still looked current');
  assert.match(chip.textContent, /stale data|داده کهنه/, chip.textContent);
  assert.ok(plain(chip.title).includes('41.2'), `the age was not named: "${chip.title}"`);
  // Current candles put the chip away again — no permanent warning that means nothing.
  window.btRender({ report: { aggregate: {} }, freshness: { stale: false, reason: 'current', age_bars: 0.2 } });
  assert.equal(chip.classList.contains('hidden'), true, 'the chip stayed visible on current data');
  // A response without the block (older backend) must not invent staleness.
  window.btRender({ report: { aggregate: {} } });
  assert.equal(chip.classList.contains('hidden'), true, 'a missing block was read as stale');

  // ---- 8. the market-scan card names how old the candles it scanned are (1.0.21) ----
  const sgChip = doc.getElementById('sgFreshChip');
  assert.ok(sgChip, 'the market-scan card has no freshness chip');
  // Current candles: no chip at all. A warning that is always on is furniture.
  window.sgRenderFresh({ stale: false, reason: 'current', age_bars: 0.2 });
  assert.equal(sgChip.classList.contains('hidden'), true, 'a current scan warned anyway');
  // Old candles: named, with the arithmetic in the title.
  window.sgRenderFresh({ stale: true, reason: 'behind', age_bars: 41.2 });
  assert.equal(sgChip.classList.contains('hidden'), false,
    'old candles left the scan card looking current');
  assert.match(sgChip.textContent, /stale data|داده کهنه/, sgChip.textContent);
  assert.ok(plain(sgChip.title).includes('41.2'), `the age was not named: "${sgChip.title}"`);
  // A scan that returned no bars says so, instead of quoting an age of nothing.
  window.sgRenderFresh({ stale: true, reason: 'no-candles', age_bars: null });
  assert.match(sgChip.textContent, /stale data|داده کهنه/);
  assert.match(sgChip.title, /no candle|کندلی/, `title said "${sgChip.title}"`);
  // A response (or an engine) without the block must not invent staleness.
  window.sgRenderFresh(null);
  assert.equal(sgChip.classList.contains('hidden'), true, 'a missing block was read as stale');
  assert.equal(window.sgFreshNow(), null, 'the card invented a freshness with no engine behind it');

  // The browser's rule is the server's rule: same bar length, same tolerance,
  // same reasons — checked by running `nbx-core.js` in its own page context,
  // because this dashboard's inline block does not load it.
  const coreDom = new JSDOM('<!doctype html><html><body></body></html>',
    { url: 'https://page.test/', runScripts: 'outside-only' });
  coreDom.window.eval(fs.readFileSync('nbx-core.js', 'utf8'));
  const F = coreDom.window.NBX.freshness;
  const nowMs = Date.UTC(2026, 9, 22, 12, 0, 0);
  const eight15m = nowMs / 1000 - 8 * 15 * 60;
  const behind = F.assessBar(eight15m, '15m', { now: nowMs });
  assert.equal(behind.reason, 'behind');
  assert.equal(behind.stale, true);
  assert.equal(behind.age_bars, 8);
  assert.equal(behind.bar_seconds, 900);
  assert.equal(behind.tolerance_bars, 2);
  assert.equal(F.assessBar(nowMs / 1000 - 60, '15m', { now: nowMs }).reason, 'current');
  assert.equal(F.assessBar(nowMs / 1000 + 3600, '15m', { now: nowMs }).reason, 'ahead-of-clock');
  assert.equal(F.assessBar(null, '15m', { now: nowMs }).reason, 'no-candles');
  assert.equal(F.assessBar(eight15m, '7m', { now: nowMs }).reason, 'bad-timeframe');
  assert.equal(F.tfMinutes('D'), 1440);
  coreDom.window.close();

  // ---- 9. the disabled-backend path ----
  window.NBX.core.api = undefined;
  await window.adMeasureThresholds();
  assert.match(text('adState'), /backend|بک‌اند/,
    'a missing backend produced no explanation');

  console.log('PASS — adaptive card: numbers, the not-applied label, named refusals, no zeros, staleness named and re-measured, A/B verdict with its sample size, stale candles named on the backtest and scan cards');
  // The page arms intervals (clock, polling) that would keep node alive forever;
  // this script asserts and leaves.
  dom.window.close();
  process.exit(0);
})().catch((e) => {
  console.error(`FAIL — ${e.message}`);
  process.exit(1);
});
