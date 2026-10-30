/* Offline Phase 12 regressions: Overview Market State, allocation donut, positions header/refresh. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM, VirtualConsole} = require('jsdom');

let checks = 0;
function eq(a, b, m) { assert.equal(a, b, m); checks++; }
function ok(v, m) { assert.ok(v, m); checks++; }

const core = fs.readFileSync('nbx-core.js', 'utf8');
const app = fs.readFileSync('nbx-app.js', 'utf8')
  .replace('  window.NBX_APP = {', '  window.__phase12 = {paintPaperState, refreshPaperState};\n  window.NBX_APP = {')
  .replace("  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);\n  else start();", '');
const head = require('node:child_process').execFileSync('git', ['show', 'HEAD:index.html'], {encoding: 'utf8'});
const strip = x => String(x || '').replace(/[\u2066-\u2069\u200e\u200f\u061c]/g, '').replace(/٪/g, '%');
function deferred() { let resolve, reject; const promise = new Promise((r, j) => { resolve = r; reject = j; }); return {promise, resolve, reject}; }

function validOverview(tf = '15m', overrides = {}) {
  return Object.assign({
    ok: true,
    tf,
    as_of: '2026-10-30T12:22:13+00:00',
    as_of_basis: 'universe_snapshot',
    universe_size: 6,
    priced: 4,
    excluded: {no_change: 1, no_volume: 1, no_fx: 0, same_asset: 2},
    breadth: {up: 2, down: 1, flat: 1, pct_up: 50.0, pct_up_vol_weighted: 66.67},
    change: {median_24h: 1.25, mean_24h: 1.1, vol_weighted_24h: 2.35, dispersion_24h: 3.4},
    turnover: {total_usd: 1250000, top3_share_pct: 84.5, top: [{symbol: 'BTCIRT', vol_usd: 700000, change24: 3.2}]},
    leaders: [{symbol: 'BTCIRT', change24: 3.2, last: 9000000000, vol_usd: 700000}, {symbol: 'SOLIRT', change24: 1.8, last: 15000000, vol_usd: 300000}],
    laggards: [{symbol: 'ETHIRT', change24: -1.3, last: 300000000, vol_usd: 250000}],
    all_down: false,
    all_up: false,
    premium_pct: 0.85,
    fx: {symbol: 'USDTIRT', last: 101200, change24: 0.2},
    signals: {directions: {LONG: 2, NEUTRAL: 1, EXIT: 1}, regimes: {TREND: 2, RANGE: 2}, avg_composite: 69.5, scored: 4},
    freshness: {
      universe: {as_of: '2026-10-30T12:22:13+00:00', age_s: 540, ttl_s: 120, stale: true},
      signals: {tf, available: true, as_of: '2026-10-30T12:30:30+00:00', age_s: 43, ttl_s: 90, stale: false},
      response_generated_at: '2026-10-30T12:31:13+00:00',
    },
  }, overrides);
}

(async () => {
  for (const file of ['index.html', 'nobitex-mobile.html']) {
    for (const lang of ['fa', 'en']) {
      const errors = [];
      const vc = new VirtualConsole();
      vc.on('jsdomError', e => errors.push(e.message));
      const html = fs.readFileSync(file, 'utf8')
        .replace(/<script id="nbx-inline-nbx-core-js">[\s\S]*?<\/script>/, '')
        .replace(/<script id="nbx-inline-nbx-app-js">[\s\S]*?<\/script>/, '');
      const dom = new JSDOM(html, {
        url: 'https://dashboard.test/',
        runScripts: 'dangerously',
        pretendToBeVisual: true,
        virtualConsole: vc,
        beforeParse(w) {
          w.setInterval = () => 0;
          w.scrollTo = () => {};
          w.HTMLElement.prototype.scrollIntoView = () => {};
          w.fetch = () => { throw new Error('Unstubbed network forbidden'); };
        },
      });
      const w = dom.window, d = w.document;
      try {
        if (file === 'nobitex-mobile.html' && lang === 'fa') {
          const raw = new JSDOM(fs.readFileSync(file, 'utf8'));
          eq(raw.window.document.getElementById('nbx-inline-nbx-core-js').textContent.trim(), core.trim(), 'standalone core parity');
          eq(raw.window.document.getElementById('nbx-inline-nbx-app-js').textContent.trim(), fs.readFileSync('nbx-app.js', 'utf8').trim(), 'standalone app parity');
          raw.window.close();
        }
        const ref = new JSDOM(head);
        for (const id of ['page-users', 'page-account']) {
          const raw = new JSDOM(fs.readFileSync(file, 'utf8'));
          eq(raw.window.document.getElementById(id).outerHTML, ref.window.document.getElementById(id).outerHTML, id + ' unchanged vs HEAD');
          raw.window.close();
        }
        ref.window.close();

        w.eval(core);
        w.NBX.core = w.NBX.createCore();
        const api = w.NBX.core.api;
        api.standaloneMobile = false;

        // Adapter contract: auth header, no-store, allowed timeframe, HTTP/malformed refusal, standalone null.
        w.NBX.config.apiToken = 'test-session-token';
        w.document.cookie = 'nbx_csrf=csrf-token-12';
        let lastUrl = '', lastOpts = null;
        w.fetch = async (url, opts) => {
          lastUrl = String(url);
          lastOpts = opts;
          return {ok: true, status: 200, json: async () => validOverview('4h')};
        };
        const fetched = await api.getMarketOverview('4h');
        ok(lastUrl.endsWith('/market/overview?tf=4h'), 'passes selected timeframe');
        eq(lastOpts.cache, 'no-store', 'no-store cache');
        eq(lastOpts.headers['X-API-Token'], 'test-session-token', 'sends API token header');
        eq(lastOpts.headers['X-CSRF-Token'], 'csrf-token-12', 'sends CSRF header');
        eq(fetched.tf, '4h');
        await api.getMarketOverview('invalid-tf');
        ok(lastUrl.endsWith('/market/overview?tf=15m'), 'falls back to allowed 15m');
        for (const status of [401, 403, 503]) {
          w.fetch = async () => ({ok: false, status, json: async () => ({detail: 'err-' + status})});
          await assert.rejects(() => api.getMarketOverview('15m'), new RegExp('err-' + status));
          checks++;
        }
        w.fetch = async () => ({ok: true, status: 200, json: async () => null});
        await assert.rejects(() => api.getMarketOverview('15m'), /malformed/);
        checks++;
        api.standaloneMobile = true;
        eq(await api.getMarketOverview('15m'), null, 'standalone never fabricates market overview');
        api.standaloneMobile = false;
        w.fetch = () => { throw new Error('Direct fetch forbidden after adapter check'); };

        w.eval(app);
        w.eval(`LANG='${lang}';PREFS.numFmt='en';live.timeframe='15m';applyStaticI18n();`);

        // 1. Valid Market State render, locale-aware time, snapshot vs receipt provenance, stale indicator, scope note.
        api.getMarketOverview = async tf => validOverview(tf);
        await w.loadMarketOverview();
        const srcText = strip(d.getElementById('mkSrc').textContent);
        ok(srcText.includes(lang === 'fa' ? 'تیکر:' : 'Tickers:'), 'names ticker snapshot');
        ok(srcText.includes(lang === 'fa' ? 'دریافت:' : 'Received:'), 'names browser receipt separately');
        ok(srcText.includes('540'), 'shows universe age');
        ok(srcText.includes(lang === 'fa' ? 'کهنه' : 'stale'), 'flags stale universe');
        ok(d.getElementById('mkSrc').className.includes('amber'), 'stale provenance highlighted');
        if (lang === 'en') ok(!/[۰-۹]/.test(d.getElementById('mkSrc').textContent), 'English mode never forces fa-IR Persian digits in mkSrc');
        const bodyText = strip(d.getElementById('mkBody').textContent);
        ok(bodyText.includes('2 / 4 (50.0%)'), 'breadth count and percentage');
        ok(bodyText.includes('+1.25%'), 'median change');
        ok(bodyText.includes('+2.35%'), 'turnover-weighted change');
        ok(bodyText.includes('3.40%'), 'dispersion');
        ok(bodyText.includes('84.5%'), 'top-3 concentration');
        ok(bodyText.includes('+0.85%'), 'premium pct');
        ok(bodyText.includes('101,200'), 'USDTIRT toman rate');
        ok(bodyText.includes('69.5') && bodyText.includes('n=4'), 'signal mix average composite and count');
        const scopeText = strip(d.getElementById('mkScopeNote').textContent);
        for (const token of ['4', '6', '1,250,000']) ok(scopeText.includes(token), 'scope note includes ' + token);
        ok(scopeText.includes(lang === 'fa' ? 'شاخص بازار نیست' : 'Not a market index'));

        // Language switch re-localizes Market State and active candle timeframe without network.
        const other = lang === 'fa' ? 'en' : 'fa';
        w.eval(`LANG='${other}';applyStaticI18n();`);
        w.NBX_APP.refreshDisplay();
        ok(d.getElementById('mkSrc').textContent.includes(other === 'fa' ? 'تیکر:' : 'Tickers:'), 'mkSrc re-localized on language switch');
        ok(d.getElementById('mkScopeNote').textContent.includes(other === 'fa' ? 'شاخص بازار نیست' : 'Not a market index'), 'mkScopeNote re-localized');
        w.eval(`LANG='${lang}';applyStaticI18n();`);
        w.NBX_APP.refreshDisplay();

        // 2. Timeframe selector updates active timeframe label and reloads Market State for that timeframe.
        let requestedTf = '';
        api.getMarketOverview = async tf => { requestedTf = tf; return validOverview(tf, {freshness: {universe: {age_s: 20, ttl_s: 120, stale: false}, signals: {tf, available: false, age_s: null, ttl_s: 90, stale: null}}}); };
        w.eval("live.timeframe='4h';applyStaticI18n();");
        await w.loadMarketOverview();
        eq(requestedTf, '4h', 'requests active timeframe 4h');
        ok(strip(d.querySelector('[data-i18n="ov.tf"]').textContent).includes('4'), 'candle timeframe label reflects 4h, not hardcoded 15m');
        ok(strip(d.getElementById('mkSrc').textContent).includes(lang === 'fa' ? 'ناموجود' : 'unavailable'), 'missing timeframe signal mix explicitly stated');
        w.eval("live.timeframe='15m';applyStaticI18n();");

        // 3. Error / ok:false / standalone clears stale numbers and marks data-unavailable.
        api.getMarketOverview = async () => ({ok: false, reason: 'no USDTIRT rate <img src=x onerror=alert(1)>'});
        await w.loadMarketOverview();
        eq(d.getElementById('mkSrc').textContent, '', 'failed overview clears mkSrc');
        ok(d.querySelector('#mkBody [data-unavailable="1"]'), 'error state has data-unavailable');
        eq(d.querySelectorAll('#mkBody img, #mkBody [onerror], #mkBody #mkBreadthBar').length, 0, 'error reason escaped and old bars removed');
        ok(d.getElementById('mkBody').textContent.includes('no USDTIRT rate <img src=x onerror=alert(1)>'));

        // 4. Malformed payloads are refused rather than rendered as NaN% / 0 / 0.
        const badPayloads = [
          validOverview('15m', {tf: '1h'}),
          validOverview('15m', {breadth: {up: 2, down: 1, flat: 0, pct_up: 50}}),
          validOverview('15m', {priced: 4, universe_size: 3}),
          validOverview('15m', {breadth: {up: 1, down: 1, flat: 0, pct_up: 50}, priced: 2}),
          validOverview('15m', {breadth: {up: 2, down: 1, flat: 1, pct_up: 120}}),
          validOverview('15m', {change: {median_24h: null, vol_weighted_24h: 1, dispersion_24h: 1}}),
          validOverview('15m', {change: {median_24h: 1, vol_weighted_24h: 1, dispersion_24h: -0.1}}),
          validOverview('15m', {turnover: {total_usd: 100, top3_share_pct: 140}}),
          validOverview('15m', {fx: {symbol: 'USDTIRT', last: 0, change24: 0.1}}),
          validOverview('15m', {leaders: [{symbol: 'BTCIRT', change24: -1.0}]}),
          validOverview('15m', {laggards: [{symbol: 'ETHIRT', change24: 1.0}]}),
        ];
        for (const bad of badPayloads) {
          api.getMarketOverview = async () => bad;
          await w.loadMarketOverview();
          ok(d.querySelector('#mkBody [data-unavailable="1"]'), 'malformed payload refused');
          eq(d.getElementById('mkBreadthBar'), null, 'no breadth bar rendered for malformed payload');
          ok(!d.getElementById('mkBody').textContent.includes('NaN'), 'never prints NaN');
        }

        // 5. Escape hostile strings in valid payload.
        const xss = '<svg onload=alert(1)>BTCIRT';
        api.getMarketOverview = async () => validOverview('15m', {
          leaders: [{symbol: xss, change24: 4.2}],
          laggards: [{symbol: '<img src=x onerror=alert(2)>ETHIRT', change24: -2.1}],
        });
        await w.loadMarketOverview();
        eq(d.querySelectorAll('#mkBody svg[onload], #mkBody img[onerror], #mkBody script').length, 0, 'leader/laggard symbols escaped');
        ok(d.getElementById('mkBody').textContent.includes(xss));

        // 6. Late response race: old slow response cannot overwrite newer response.
        const slow = deferred();
        let call = 0;
        api.getMarketOverview = () => ++call === 1 ? slow.promise : Promise.resolve(validOverview('15m', {
          change: {median_24h: 4.44, mean_24h: 4.0, vol_weighted_24h: 4.5, dispersion_24h: 1.2},
        }));
        const first = w.loadMarketOverview('15m');
        const second = w.loadMarketOverview('15m');
        await second;
        slow.resolve(validOverview('15m', {
          change: {median_24h: -9.99, mean_24h: -9.0, vol_weighted_24h: -9.5, dispersion_24h: 8.8},
        }));
        await first;
        ok(strip(d.getElementById('mkBody').textContent).includes('+4.44%'), 'newer overview kept');
        ok(!strip(d.getElementById('mkBody').textContent).includes('9.99'), 'late old overview ignored');

        // 7. Allocation donut: 100% cash hides zero-share arcs and removes round-cap dot; missing FX refuses false USD allocation.
        const risk = {source: 'server', equity: 99921.95, openHeat: 0, caps: {maxHeatPct: 6, maxConcurrent: 5, maxSingleAssetPct: 25, minCashPct: 10, riskPct: 0.75, minRR: 1.5}};
        const met = {source: 'server', metrics: {equity: 99921.95, start_equity: 100000, cash: 99921.95, trades: 1, profit_factor: 0}, curve: []};
        w.__phase12.paintPaperState({source: 'server', positions: [], metrics: met.metrics}, met, risk);
        eq(strip(d.getElementById('allocCash').textContent), '+100.0%', '100% cash label');
        eq(strip(d.getElementById('allocAlts').textContent), '0.0%', '0% alts label');
        eq(d.getElementById('arcCash').style.opacity, '1', 'cash arc visible');
        eq(d.getElementById('arcCash').getAttribute('stroke-linecap'), 'round', 'positive arc keeps round cap');
        for (const arcId of ['arcBtc', 'arcEth', 'arcAlts']) {
          const arc = d.getElementById(arcId);
          eq(arc.style.opacity, '0', arcId + ' zero share hidden');
          eq(arc.getAttribute('stroke-linecap'), 'butt', arcId + ' zero share cannot draw round dot');
          ok(arc.getAttribute('stroke-dasharray').startsWith('0.00 '), arcId + ' zero dash length');
        }
        eq(strip(d.getElementById('posCount').textContent), '0', 'empty known book sets posCount to 0');

        // IRT position without USDT/IRT conversion rate must not divide tomans by 1 as dollars.
        w.NBX.core.engine.premium = 0;
        const irtPos = [{symbol: 'ADAIRT', state: 'OPEN', entry_price: 50000, last_price: 51000, stop_pct: 2, side: 'buy', qty: 100}];
        w.__phase12.paintPaperState({source: 'server', positions: irtPos, metrics: met.metrics}, met, risk);
        eq(d.getElementById('allocCash').textContent, '—', 'missing IRT FX refuses cash allocation');
        eq(d.getElementById('allocAlts').textContent, '—', 'missing IRT FX refuses alts allocation');
        eq(d.getElementById('arcAlts').style.opacity, '0', 'unverified IRT position does not draw arc');
        ok(d.getElementById('allocNoteTxt').textContent.includes('USDT/IRT'));

        // With valid USDT/IRT rate, IRT position converts to USD and draws only positive arcs.
        w.NBX.core.engine.premium = 100000;
        w.__phase12.paintPaperState({source: 'server', positions: irtPos, metrics: met.metrics}, met, risk);
        ok(strip(d.getElementById('allocAlts').textContent).includes('0.1%'), 'valid FX computes small alt share');
        eq(d.getElementById('arcAlts').style.opacity, '1', 'positive alt share visible');
        eq(d.getElementById('arcBtc').style.opacity, '0', 'zero BTC share still hidden');

        // 8. Open Positions count chip, 8-of-N truncation note, unknown state, and read-only Refresh paper book button.
        const twelve = Array.from({length: 12}, (_, i) => ({
          symbol: 'BTCUSDT', state: 'OPEN', entry_price: 100, last_price: 101, stop_pct: 2, side: 'buy', qty: 1, id: 'p' + i,
        }));
        w.__phase12.paintPaperState({source: 'server', positions: twelve, metrics: met.metrics}, met, risk);
        eq(strip(d.getElementById('posCount').textContent), '8 / 12', 'posCount shows displayed / total open positions');
        ok(strip(d.getElementById('allocNoteTxt').textContent).includes('8') && strip(d.getElementById('allocNoteTxt').textContent).includes('12'), 'allocation note discloses 8 of 12 slice');

        w.__phase12.paintPaperState({unavailable: true, positions: null}, {unavailable: true, metrics: null}, null);
        eq(d.getElementById('posCount').textContent, '—', 'unknown positions sets posCount to em dash');
        eq(d.getElementById('allocCash').textContent, '—', 'unknown equity/positions clears allocation');
        eq(d.getElementById('arcCash').style.opacity, '0', 'unknown allocation hides cash arc');

        let paperRefreshes = 0;
        api.getPaperPositions = async () => { paperRefreshes++; return {source: 'server', positions: [], metrics: met.metrics}; };
        api.getPaperMetrics = async () => ({source: 'server', metrics: met.metrics, curve: []});
        api.getBackendRiskState = async () => risk;
        const btn = d.getElementById('posRefreshBtn');
        ok(!btn.textContent.includes('صرافی') && !btn.textContent.toLowerCase().includes('exchange'), 'button no longer claims exchange reconciliation');
        btn.click();
        await new Promise(r => w.setTimeout(r, 0));
        eq(paperRefreshes, 1, 'refresh button triggers read-only paper state refresh');
        eq(strip(d.getElementById('posCount').textContent), '0', 'refresh updates posCount');

        eq(errors.length, 0, 'no jsdom console errors: ' + errors.join('; '));
      } finally {
        dom.window.close();
      }
    }
  }
  console.log(`Phase12 Overview Market State & Allocation: ${checks} checks PASS (canonical/mobile × fa/en; offline mocked reads only).`);
})();
