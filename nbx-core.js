/* ============================================================
 * nobiTrader — Core (Phase 1–2 + Standalone Mobile Engine)
 * Implements per ADRs: 0003 API client, 0004 data store,
 * 0005 TA engine, 0006 FA engine, 0007 decision scoring,
 * 0008 risk engine, 0009 paper broker & walk-forward backtester.
 * Works both with Python FastAPI backend AND 100% standalone on mobile!
 * ============================================================ */
(function () {
  'use strict';

  if (window.__NBX_CORE_LOADED) return;
  window.__NBX_CORE_LOADED = true;

  window.NBX = window.NBX || {};

  /* ----------------------------------------------------------
   * 0. Config & Seed Universe
   * --------------------------------------------------------- */
  const self = typeof window !== 'undefined' ? window : {};
  const isFileOrMobileLocal =
    typeof location === 'undefined' ||
    location.protocol === 'file:' ||
    location.protocol === 'content:' ||
    location.protocol === 'blob:' ||
    location.hostname === '';

  const defaultPythonApi = !isFileOrMobileLocal ? '/api' : 'http://localhost:8000/api';

  /* Symbol names only. The old table also carried last price / 24h volume /
   * change, and those numbers reached the screen whenever the exchange was
   * unreachable — indistinguishable from a live quote. */
  const SYMBOLS = [
    'BTCIRT', 'ETHIRT', 'USDTIRT', 'SOLIRT', 'TONIRT', 'TRXIRT', 'DOGEIRT',
    'XRPIRT', 'ADAIRT', 'AVAXIRT', 'LINKIRT', 'DOTIRT', 'LTCIRT', 'SHIBIRT',
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'TONUSDT', 'TRXUSDT', 'DOGEUSDT',
    'XRPUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT',
  ];
  NBX.SYMBOLS = SYMBOLS;
  function DOC_LANG() {
    try {
      const l = (typeof document !== 'undefined' && document.documentElement.lang) || 'fa';
      return l.startsWith('en') ? 'en' : 'fa';
    } catch (_) { return 'fa'; }
  }
  NBX.UPSTREAM_MSG = {
    fa: 'دسترسی به سرور نوبیتکس امکان‌پذیر نیست؛ دادهٔ زنده دریافت نشد. اتصال شبکه یا فیلتر را بررسی کن و دوباره تلاش کن.',
    en: 'The Nobitex server is unreachable; no live data was received. Check the network or filtering and try again.',
  };

  const cfg = Object.assign(
    {
      baseUrl: 'https://apiv2.nobitex.ir',
      apiProxy: '',
      pythonApi: defaultPythonApi,
      preferPython: !isFileOrMobileLocal,
      timeoutMs: isFileOrMobileLocal ? 2500 : 5000,
      maxRetries: isFileOrMobileLocal ? 1 : 2,
      ratePerMinute: 120,
      userAgentNote: 'TraderBot/2.0 mobile-ready-client',
      timeframes: ['15m', '1h', '4h', 'D'],
      universeTopN: 40,
      maxCandles: 380,
      seedSymbols: SYMBOLS.slice(),
      // Optional shared secret for mutating endpoints. The server also accepts
      // local requests without it, so this is only needed when NBX_API_TOKEN
      // is configured server-side (or when the dashboard is hosted elsewhere).
      // A sandboxed preview iframe (sandbox="allow-scripts") throws on
      // localStorage; that crash used to take the whole app down with it.
      apiToken: (function () {
        try { return localStorage.getItem('nbx_api_token') || ''; } catch (e) { return ''; }
      })(),
    },
    self.__NBX_CONFIG || {}
  );
  NBX.config = cfg;

  /* Cookies are how the server remembers a signed-in person; the CSRF token has
   * to be echoed in a header because the session cookie itself is HttpOnly. */
  function readCookie(name) {
    try {
      const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
      return m ? decodeURIComponent(m[1]) : '';
    } catch (e) { return ''; }
  }

  const clamp = (v, a, b) => Math.min(Math.max(v, a), b);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const nowMs = () => Date.now();
  const normSymbol = (s) => (s || '').toUpperCase().replace(/\s+/g, '');
  /* Deterministic PRNG for consistent multi-regime candles across mobile/desktop */
  function hashSeed(str) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }


  const NBXError = class extends Error {
    constructor(code, message, meta) {
      super(message);
      this.name = 'NBXError';
      this.code = code;
      this.meta = meta || {};
    }
  };
  NBX.errors = { NBXError };

  /* ----------------------------------------------------------
   * 1. Token bucket
   * --------------------------------------------------------- */
  NBX.TokenBucket = class {
    constructor(perMin) {
      this.capacity = perMin;
      this.tokens = perMin;
      this.refillPerMs = perMin / 60000;
      this.lastTake = nowMs();
    }
    _refill() {
      const now = nowMs();
      const mins = (now - this.lastTake) * this.refillPerMs;
      this.tokens = Math.min(this.capacity, this.tokens + mins);
      this.lastTake = now;
    }
    allow(cost = 1) {
      this._refill();
      if (this.tokens >= cost) {
        this.tokens -= cost;
        return true;
      }
      return false;
    }
  };

  /* ----------------------------------------------------------
   * 2. TA engine (vectorized on array of candles, ADR-0005)
   * --------------------------------------------------------- */
  const TA = {};
  const _ema = (arr, len) => {
    const k = 2 / (len + 1);
    let e = arr[0];
    const out = new Array(arr.length);
    for (let i = 0; i < arr.length; i++) {
      e = i === 0 ? arr[0] : arr[i] * k + e * (1 - k);
      out[i] = e;
    }
    return out;
  };
  const _rma = (arr, len) => {
    let s = 0;
    const out = new Array(arr.length);
    for (let i = 0; i < arr.length; i++) {
      s = i < len ? (i === 0 ? arr[0] : (s * (i - 1) + arr[i]) / i) : (s * (len - 1) + arr[i]) / len;
      out[i] = s;
    }
    return out;
  };

  TA.ema = (closes, len) => _ema(closes, len);
  TA.sma = (closes, len) => {
    const out = new Array(closes.length);
    let sum = 0;
    for (let i = 0; i < closes.length; i++) {
      sum += closes[i];
      if (i >= len) sum -= closes[i - len];
      out[i] = i >= len - 1 ? sum / len : null;
    }
    return out;
  };
  TA.rsi = (closes, len) => {
    const gains = [0], losses = [0];
    for (let i = 1; i < closes.length; i++) {
      const d = closes[i] - closes[i - 1];
      gains.push(d > 0 ? d : 0);
      losses.push(d < 0 ? -d : 0);
    }
    const gE = _rma(gains, len), lE = _rma(losses, len);
    return closes.map((_, i) => {
      if (i < len) return null;
      const rs = lE[i] === 0 ? 100 : gE[i] / lE[i];
      return 100 - 100 / (1 + rs);
    });
  };
  TA.macd = (closes, f = 12, s = 26, sig = 9) => {
    const eF = _ema(closes, f), eS = _ema(closes, s);
    const macd = closes.map((_, i) => eF[i] - eS[i]);
    const signal = _ema(macd, sig);
    const hist = macd.map((v, i) => v - signal[i]);
    return { macd, signal, hist };
  };
  TA.tr = (candles) => {
    const out = [0];
    for (let i = 1; i < candles.length; i++) {
      const c = candles[i], pc = candles[i - 1].c;
      out.push(Math.max(c.h - c.l, Math.abs(c.h - pc), Math.abs(c.l - pc)));
    }
    return out;
  };
  TA.atr = (candles, len = 14) => _rma(TA.tr(candles), len);
  TA.bollinger = (closes, len = 20, mult = 2) => {
    const mid = TA.sma(closes, len);
    const sd = closes.map((_, i) => {
      if (i < len - 1 || mid[i] === null) return null;
      let s = 0;
      for (let j = i - len + 1; j <= i; j++) s += (closes[j] - mid[i]) ** 2;
      return Math.sqrt(s / len);
    });
    return closes.map((_, i) => {
      if (mid[i] === null || sd[i] === null) return { mid: null, up: null, low: null, pctB: null };
      const up = mid[i] + mult * sd[i], low = mid[i] - mult * sd[i];
      return { mid: mid[i], up, low, pctB: (closes[i] - low) / (up - low || 1) };
    });
  };
  TA.di_adx = (candles, len = 14) => {
    const tr = TA.tr(candles);
    const pDM = [0], nDM = [0];
    for (let i = 1; i < candles.length; i++) {
      const up = candles[i].h - candles[i - 1].h;
      const dn = candles[i - 1].l - candles[i].l;
      pDM.push(up > dn && up > 0 ? up : 0);
      nDM.push(dn > up && dn > 0 ? dn : 0);
    }
    const trS = _rma(tr, len), pS = _rma(pDM, len), nS = _rma(nDM, len);
    const pdi = candles.map((_, i) => (trS[i] ? (pS[i] / trS[i]) * 100 : 0));
    const ndi = candles.map((_, i) => (trS[i] ? (nS[i] / trS[i]) * 100 : 0));
    const dx = candles.map((_, i) => {
      const sum = pdi[i] + ndi[i];
      return sum ? (Math.abs(pdi[i] - ndi[i]) / sum) * 100 : 0;
    });
    const adx = _rma(dx, len);
    return { pdi, ndi, adx };
  };

  // The two bars that turn a score into a direction. Named once here so the
  // Python side can be compared with them (server/tests/test_single_source_constants.py)
  // and so a caller can ask "what if the bar were different?" without forking
  // this function. Defaults are the historical constants: unchanged behaviour.
  const LONG_SCORE_MIN = 68;
  const NEUTRAL_SCORE_MIN = 55;

  TA.signalScore = function (candles, opts) {
    const longMin = opts && opts.longMin != null ? opts.longMin : LONG_SCORE_MIN;
    const neutralMin = opts && opts.neutralMin != null ? opts.neutralMin : NEUTRAL_SCORE_MIN;
    if (!(neutralMin < longMin)) {
      throw new Error(`neutralMin (${neutralMin}) must be below longMin (${longMin})`);
    }
    if (!candles || candles.length < 60) {
      return { score: 0, direction: 'NEUTRAL', regime: 'UNKNOWN', components: {}, reason: 'insufficient data' };
    }
    const closes = candles.map((x) => x.c);
    const vols = candles.map((x) => x.v || 0);
    const n = closes.length;
    const [e9, e21, e50, e200] = [TA.ema(closes, 9), TA.ema(closes, 21), TA.ema(closes, 50), TA.ema(closes, 200)];
    const rsi = TA.rsi(closes, 14);
    const macd = TA.macd(closes);
    const atr = TA.atr(candles, 14);
    const bb = TA.bollinger(closes, 20, 2);
    const di = TA.di_adx(candles, 14);
    const i = n - 1;

    const comp = {};
    let trend = 0;
    if (closes[i] > e21[i]) trend += 20;
    if (e9[i] > e21[i]) trend += 20;
    if (e21[i] > e50[i]) trend += 25;
    if (closes[i] > (e200[i] || closes[i])) trend += 15;
    if (di.adx[i] && di.adx[i] > 22 && di.pdi[i] > di.ndi[i]) trend += 20;
    comp.trend = clamp(trend, 0, 100);

    let mom = 50;
    const r = rsi[i], h = macd.hist;
    if (r !== null) {
      if (r > 55 && r < 72) mom += 20;
      else if (r < 45) mom -= 15;
      else if (r >= 72) mom += 5;
    }
    if (h[i] > h[i - 1] && h[i] > 0) mom += 25;
    else if (h[i] < h[i - 1] && h[i] < 0) mom -= 10;
    comp.momentum = clamp(mom, 0, 100);

    let vol = 50;
    const vSlice = vols.slice(-30, -1);
    const vAvg = vSlice.reduce((a, b) => a + b, 0) / Math.max(vSlice.length, 1);
    const vNow = vols[i];
    if (vNow > vAvg * 1.4) vol += 30;
    else if (vNow < vAvg * 0.6) vol -= 15;
    if (closes[i] > closes[i - 1] && vNow > vAvg) vol += 10;
    comp.volume = clamp(vol, 0, 100);

    let strc = 50;
    const bbNow = bb[i];
    if (bbNow.pctB !== null) {
      if (bbNow.pctB > 0.7) strc += 25;
      else if (bbNow.pctB < 0.25) strc -= 15;
    }
    if (closes[i] > Math.max(...closes.slice(-20, -1))) strc += 10;
    comp.structure = clamp(strc, 0, 100);

    const regime = di.adx[i] > 25 ? 'TREND' : 'RANGE';
    const gating = regime === 'TREND' ? 1 : 0.85;
    const raw = 0.4 * comp.trend + 0.25 * comp.momentum + 0.15 * comp.volume + 0.2 * comp.structure;
    const score = clamp(raw * gating, 0, 100);

    const direction = score >= longMin ? 'LONG' : score >= neutralMin ? 'NEUTRAL' : 'EXIT';
    const stopDist = atr[i] * 2;
    return {
      score: +score.toFixed(1),
      direction,
      regime,
      components: comp,
      risk: {
        atr: atr[i],
        stopDistance: stopDist,
        stopPct: +((stopDist / closes[i]) * 100).toFixed(2),
      },
      diagnostics: {
        rsi: r !== null ? +r.toFixed(1) : null,
        adx: di.adx[i] !== null ? +di.adx[i].toFixed(1) : null,
        pctB: bbNow.pctB !== null ? +bbNow.pctB.toFixed(3) : null,
        macdHist: +h[i].toFixed(5),
        e9: e9[i],
        e21: e21[i],
        e50: e50[i],
      },
      lastClose: closes[i],
    };
  };
  NBX.TA = TA;

  /* ----------------------------------------------------------
   * 2b. Freshness — "how many bars old is the newest candle" (1.0.21)
   * --------------------------------------------------------- */
  /* The Python side has answered this since 1.0.18 (`server/freshness.py`); the
   * market scan computes its numbers *here*, so the browser needs the same
   * answer for the same reason: a ranking built on a series that stopped three
   * days ago is a claim about three days ago, and the card must say so instead
   * of wearing a present-tense header.
   *
   * Two constants, one division, and one named reason. They are compared with
   * the server's in `server/tests/test_single_source_constants.py`, because a
   * duplicated threshold is how the fee and the starting equity drifted before.
   */
  const TF_MINUTES = {
    '5m': 5, '15m': 15, '30m': 30, '1h': 60, '2h': 120, '4h': 240, '1d': 1440, D: 1440,
  };
  NBX.TF_MINUTES = TF_MINUTES;

  const FRESH_TOLERANCE_BARS = 2;      // mirrors freshness.DEFAULT_TOLERANCE_BARS
  const FRESH_AHEAD_BARS = 1;          // mirrors freshness.AHEAD_TOLERANCE_BARS

  NBX.freshness = {
    TOLERANCE_BARS: FRESH_TOLERANCE_BARS,
    AHEAD_TOLERANCE_BARS: FRESH_AHEAD_BARS,

    tfMinutes(tf) {
      const key = String(tf == null ? '' : tf).trim();
      return Object.prototype.hasOwnProperty.call(TF_MINUTES, key) ? TF_MINUTES[key] : null;
    },

    /* Freshness for a list of symbols: the worst one decides (mirrors
     * `scan_freshness_block` in server/main.py). Same block shape the cards
     * already read, plus `symbols` and `worst`. */
    assessScan(bars, tf, opts) {
      const map = bars || {};
      const syms = Object.keys(map);
      if (!syms.length) {
        const empty = this.assessBar(null, tf, opts);
        empty.symbols = { count: 0, current: 0, stale: 0, no_candles: 0 };
        empty.worst = null;
        return empty;
      }
      const per = {};
      syms.forEach((sym) => { per[sym] = this.assessBar(map[sym], tf, opts); });
      const staleSyms = syms.filter((sym) => per[sym].stale);
      const rank = (sym) => {
        const age = per[sym].age_bars;
        return (typeof age === 'number' && isFinite(age)) ? Math.abs(age) : Infinity;
      };
      const pool = staleSyms.length ? staleSyms : syms;
      const worstSym = pool.reduce((a, b) => (rank(b) > rank(a) ? b : a), pool[0]);
      const worst = per[worstSym];
      return {
        ...worst,
        symbols: {
          count: syms.length,
          current: syms.length - staleSyms.length,
          stale: staleSyms.length,
          no_candles: syms.filter((sym) => per[sym].reason === 'no-candles').length,
        },
        worst: staleSyms.length
          ? { symbol: worstSym, age_bars: worst.age_bars, reason: worst.reason }
          : null,
      };
    },

    /* `barAt` is the open time of the newest candle, in epoch seconds — the same
     * unit candles carry in `t`. An unknown timeframe or a missing bar is a
     * *named answer*, never a throw: a card that disappears because its age
     * check failed is worse than a card that says "no candles". */
    assessBar(barAt, tf, opts) {
      const o = opts || {};
      const minutes = this.tfMinutes(tf);
      const nowMs = o.now == null ? Date.now() : (o.now instanceof Date ? o.now.getTime() : Number(o.now));
      const asOf = new Date(nowMs).toISOString();
      if (minutes === null || !isFinite(minutes) || minutes <= 0) {
        return { stale: true, reason: 'bad-timeframe', bars: null, bar_seconds: null,
                 tolerance_bars: FRESH_TOLERANCE_BARS, age_bars: null, age_seconds: null,
                 last_bar: null, as_of: asOf };
      }
      const barSeconds = minutes * 60;
      const tolerance = o.toleranceBars == null ? FRESH_TOLERANCE_BARS : Number(o.toleranceBars);
      const stamp = barAt == null ? null : Number(barAt);
      if (stamp === null || !isFinite(stamp) || stamp <= 0) {
        return { stale: true, reason: 'no-candles', bars: null, bar_seconds: barSeconds,
                 tolerance_bars: tolerance, age_bars: null, age_seconds: null,
                 last_bar: null, as_of: asOf };
      }
      const ageSeconds = nowMs / 1000 - stamp;
      const ageBars = ageSeconds / barSeconds;
      let reason = 'current';
      if (ageBars < -FRESH_AHEAD_BARS) reason = 'ahead-of-clock';
      else if (ageBars > tolerance) reason = 'behind';
      return {
        stale: reason !== 'current',
        reason,
        bars: null,
        bar_seconds: barSeconds,
        tolerance_bars: tolerance,
        age_bars: Math.round(ageBars * 100) / 100,
        age_seconds: Math.round(ageSeconds * 10) / 10,
        last_bar: new Date(stamp * 1000).toISOString(),
        as_of: asOf,
      };
    },
  };

  /* ----------------------------------------------------------
   * 3. Standalone FA Engine (ADR-0006 parity in JS)
   * --------------------------------------------------------- */
  NBX.FA = {
    /* The static market-cap table is gone. `scoreSymbol` now reports that no
     * fundamentals are available; the pages render that sentence instead of a
     * score derived from a table that stopped being true years ago. */
    scoreSymbol(symbol) {
      return { ok: false, unavailable: true, symbol: normSymbol(symbol),
               score: null, vetoes: [], evidence: {},
               message: NBX.UPSTREAM_MSG[DOC_LANG()] };
    }

  };

  /* ----------------------------------------------------------
   * 3b. Units and exchange rules (ADR-0015)
   *
   * The backend converts Rial→Toman, rounds to the market's tick and enforces
   * the exchange minimum at its API boundary. The standalone bundle has no
   * backend, so it needs the same rules in the same place — otherwise the two
   * brokers admit different orders and the one on the phone is the permissive
   * one.
   *
   * Table values come from the Nobitex docs (`amountPrecisions`,
   * `pricePrecisions`, `minOrders` on /v2/options, SmallOrder = 3,000,000 Rial
   * or 11 USDT). `scripts/js_broker_test.js` checks the table against
   * `server/units.py`, so the two cannot drift apart silently.
   * --------------------------------------------------------- */
  const RIAL_PER_TOMAN = 10;
  const AMOUNT_STEP = {
    BTCIRT: 0.000001, BTCUSDT: 0.000001,
    ETHIRT: 0.00001, ETHUSDT: 0.0001,
    SOLIRT: 0.0001, SOLUSDT: 0.0001,
    XRPIRT: 1, XRPUSDT: 0.1,
    ADAIRT: 1, ADAUSDT: 1,
    DOGEIRT: 1, DOGEUSDT: 1,
    TONIRT: 0.001, TONUSDT: 0.001,
    TRXIRT: 1, TRXUSDT: 1,
    USDTIRT: 0.1,
  };
  // Normalized Toman/USDT ticks, not raw Rial ticks.
  const PRICE_STEP = { rls: 1, usdt: 0.01 };
  const MIN_NOTIONAL_QUOTE = { rls: 3000000, usdt: 11 };

  // Decimal-string arithmetic avoids binary ties such as 1.005 / 0.01.
  // No source-unit inference: px and step must already share a quote unit.
  function roundDecimalStep(px, step) {
    const parts = (value) => {
      const [mantissa, exponent = '0'] = String(Math.abs(value)).toLowerCase().split('e');
      const [whole, fraction = ''] = mantissa.split('.');
      return { n: BigInt(whole + fraction), e: Number(exponent) - fraction.length };
    };
    const p = parts(px), s = parts(step), e = Math.min(p.e, s.e);
    const numerator = p.n * (10n ** BigInt(p.e - e));
    const denominator = s.n * (10n ** BigInt(s.e - e));
    const count = (2n * numerator + denominator) / (2n * denominator);
    return Number(String(count * denominator) + 'e' + e) * (px < 0 ? -1 : 1);
  }

  const Units = {
    RIAL_PER_TOMAN,
    isRial: (sym) => /IRT$/.test(String(sym || '').toUpperCase()),
    /** `btc-rls` → `BTCIRT`, `btc-usdt` → `BTCUSDT`; `global` → null. */
    marketSymbol(key) {
      const s = String(key || '').trim().toLowerCase();
      if (!s || s === 'global') return null;
      if (s.indexOf('-') === -1) return s.toUpperCase();
      const parts = s.split('-');
      if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
      const dst = parts[1] === 'rls' || parts[1] === 'irt' ? 'IRT' : parts[1].toUpperCase();
      return parts[0].toUpperCase() + dst;
    },
    quoteOf: (sym) => (Units.isRial(sym) ? 'rls' : 'usdt'),
    amountStep: (sym) => AMOUNT_STEP[String(sym || '').toUpperCase()] || 0.00000001,
    priceStep: (sym) => PRICE_STEP[String(sym || '').toUpperCase()]
                        || PRICE_STEP[Units.quoteOf(sym)],
    // Amounts truncate toward zero; prices round half-up (docs §precision).
    truncateAmount(qty, sym) {
      const step = Units.amountStep(sym);
      if (!(step > 0) || !isFinite(qty)) return 0;
      return Math.floor(Math.max(0, qty) / step) * step;
    },
    roundPrice(px, sym) {
      const step = Units.priceStep(sym);
      if (!(step > 0) || !Number.isFinite(step) || !Number.isFinite(px)) return 0;
      return roundDecimalStep(px, step);
    },
    // Quoted in the market's own quote currency (Rial for IRT pairs).
    minNotionalQuote: (sym) => MIN_NOTIONAL_QUOTE[Units.quoteOf(sym)],
    // Same expression as `units.min_notional_usd`: 3,000,000 Rial = 300,000
    // Toman, divided by the Toman-per-USD rate.
    minNotionalUsd(sym, usdtIrtToman) {
      const q = Units.quoteOf(sym);
      if (q === 'usdt') return MIN_NOTIONAL_QUOTE.usdt;
      if (!(usdtIrtToman > 0)) return 0;
      return MIN_NOTIONAL_QUOTE.rls / RIAL_PER_TOMAN / usdtIrtToman;
    },
    applyPriceOptions(payload) {
      const nb = (payload && payload.nobitex) || {};
      let changed = 0;
      for (const [key, raw] of Object.entries(nb.pricePrecisions || {})) {
        const symbol = Units.marketSymbol(key), value = Number(raw);
        if (!symbol || !(value > 0) || !Number.isFinite(value)) continue;
        const [mantissa, exponent = '0'] = String(value).toLowerCase().split('e');
        const step = Units.isRial(symbol) ? Number(mantissa + 'e' + (Number(exponent) - 1)) : value;
        if (!(step > 0)) continue;
        PRICE_STEP[symbol] = step;
        changed += 1;
      }
      return changed;
    },
    _tables() { return { AMOUNT_STEP, PRICE_STEP, MIN_NOTIONAL_QUOTE }; },
  };
  NBX.Units = Units;

  /* ----------------------------------------------------------
   * 3c. preflight — a port of `risk_engine.preflight`
   *
   * Same gates, same order, same arithmetic, same ids. The backend runs the
   * Python function before it fills anything; this runs here before the
   * standalone broker fills anything. `scripts/js_broker_test.js` feeds the
   * same inputs to both and fails if a single gate disagrees.
   *
   * All amounts are USD, exactly as in Python: the caller converts from the
   * pair's quote currency first. Mixing quote currency into a USD ledger is
   * what let a 9-billion-Toman BTC order look like 9 billion dollars.
   * --------------------------------------------------------- */
  const RISK_DEFAULTS = {
    risk_pct: 0.75,
    max_concurrent: 5,
    max_single_asset_pct: 25.0,
    max_heat_pct: 6.0,
    min_cash_pct: 10.0,
  };
  NBX.RISK_DEFAULTS = RISK_DEFAULTS;

  function preflight(opts) {
    const o = opts || {};
    const equity = Math.max(+(o.equity || 0), 1e-9);
    const cash = +(o.cash || 0);
    const notional = +(o.notional_usd || 0);
    const fee = +(o.fee_usd || 0);
    const riskUsd = +(o.risk_usd || 0);
    const minNotional = +(o.min_notional_usd || 0);
    const openHeat = +(o.open_heat || 0);
    const openPositions = +(o.open_positions || 0);
    const concurrentLimit = +(o.concurrent_limit || 0);
    const maxHeat = +(o.max_heat_pct || 0);
    const maxSingle = +(o.max_single_asset_pct || 0);
    const riskCap = +(o.risk_pct_cap || 0);
    const minCashPct = +(o.min_cash_pct || 0);
    const halted = !!o.halted;
    const closed = !!o.market_closed;

    const riskPct = riskUsd / equity * 100;
    const heatBefore = openHeat;
    const heatAfter = heatBefore + riskPct;
    const minCash = minCashPct / 100 * equity;
    const cashAfter = cash - (notional + fee);
    // 1e-9 absorbes the float overshoot of `size = exactly the cap`.
    const EPS = 1e-9;

    const g = (id, ok, value, limit, detail) => ({
      id, ok: !!ok,
      value: value === null || value === undefined ? null : +value,
      limit: limit === null || limit === undefined ? null : +limit,
      detail: detail || '',
    });
    const money = (v) => '$' + Number(v).toLocaleString('en-US',
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    const gates = [
      g('halted', !halted, null, null, halted ? 'kill switch is armed' : ''),
      g('market-closed', !closed, null, null,
        closed ? 'the exchange reports this market as closed' : ''),
      g('concurrent-cap', openPositions < concurrentLimit, openPositions, concurrentLimit,
        openPositions + ' of ' + concurrentLimit + ' slots in use'),
      g('cash-present', cash > 0, cash, 0, cash <= 0 ? 'paper account is empty' : ''),
      g('single-asset-cap', notional <= maxSingle / 100 * equity * (1 + EPS),
        notional / equity * 100, maxSingle,
        'notional ' + (notional / equity * 100).toFixed(2) + '% of equity'),
      g('min-notional', notional >= minNotional - EPS, notional, minNotional,
        (minNotional && notional < minNotional - EPS)
          ? money(notional) + ' is below the exchange minimum (' + money(minNotional) + ')'
          : ''),
      g('risk-cap', riskPct <= riskCap * 1.05, riskPct, riskCap,
        'this order risks ' + riskPct.toFixed(2) + '% of equity'),
      g('heat-cap', heatAfter <= maxHeat, heatAfter, maxHeat,
        'heat ' + heatBefore.toFixed(2) + '% + ' + riskPct.toFixed(2) + '% = ' + heatAfter.toFixed(2) + '%'),
      g('min-cash', cashAfter >= minCash, cashAfter, minCash,
        'cash after the order ' + money(cashAfter) + ', floor ' + money(minCash)),
      g('funds', cashAfter >= 0, cashAfter, 0,
        'needs ' + money(notional + fee) + ', available ' + money(cash)),
    ];
    const firstBlock = gates.find((x) => !x.ok) || null;
    return {
      equity, notional_usd: notional, fee_usd: fee, risk_usd: riskUsd,
      risk_pct: +riskPct.toFixed(6),
      heat_before: heatBefore, heat_after: +heatAfter.toFixed(6),
      max_heat: maxHeat, open_positions: openPositions,
      max_concurrent: concurrentLimit, cash_before: cash, cash_after: cashAfter,
      min_cash: minCash,
      loss_at_stop_usd: riskUsd + fee,
      blocked: gates.some((x) => !x.ok),
      blocked_by: firstBlock ? firstBlock.id : null,
      gates,
    };
  }
  const MIN_RR = 1.5; // parity-tested against risk_engine.MIN_RR
  function executionPlanError(side, entry, stop, tp1, tp2) {
    if (!['buy', 'sell'].includes(side) ||
        [entry, stop, tp1, tp2].some(v => !Number.isFinite(v) || v <= 0)) {
      return { error: 'execution-plan-invalid', rr: null };
    }
    const direction = side === 'buy' ? 1 : -1;
    const distance = direction * (entry - stop);
    const reward1 = direction * (tp1 - entry), reward2 = direction * (tp2 - entry);
    if (distance <= 0 || reward1 <= 0 || reward2 < reward1) {
      return { error: 'execution-plan-invalid', rr: null };
    }
    const rr = reward2 / distance;
    const close = Math.abs(rr - MIN_RR) <= Math.max(1e-12 * Math.max(Math.abs(rr), MIN_RR), 1e-12);
    return { error: rr < MIN_RR && !close ? 'execution-rr-too-low' : null, rr };
  }
  NBX.Risk = { preflight, DEFAULTS: RISK_DEFAULTS, MIN_RR, executionPlanError };
  NBX.preflight = preflight;

  /* ----------------------------------------------------------
   * 4. Standalone Risk & Paper Broker Engine (ADR-0008/0009)
   * --------------------------------------------------------- */
  const PAPER_START_EQUITY = 100000;
  /** Per-leg fee, same 0.2 % the Python broker charges (FEE_PCT). */
  const PAPER_FEE_PCT = 0.002;

  /*: The ladder used when a payload names no stop/targets — i.e. the page is
   * running without a backend, because with one the server always sends all
   * three. These are the live plan's multiples (`rr_levels` in
   * server/risk_engine.py), not the backtester's: the same numbers the Python
   * engine applies, expressed against the same 3.8 % stop, instead of the three
   * percentages that happened to approximate them. */
  const PAPER_STOP_PCT = 0.038;
  const PAPER_RR_LEVELS = [1.2, 2.0];

  /** Stop and take-profits for a side, derived from the stop distance. */
  function fallbackExits(px, side) {
    const dir = side === 'buy' ? 1 : -1;
    const dist = px * PAPER_STOP_PCT;
    return {
      stop: px - dir * dist,
      tp1: px + dir * dist * PAPER_RR_LEVELS[0],
      tp2: px + dir * dist * PAPER_RR_LEVELS[PAPER_RR_LEVELS.length - 1],
    };
  }

  /*: Quote units per USD, exactly like `PaperBroker._quote_rate`: 1 for USDT
   * pairs, USDTIRT for IRT pairs. Everything the gates see is then in USD, so
   * a 9-billion-Toman BTC order is compared with a 100,000-dollar balance
   * instead of being called 9 billion. */
  function quoteRate(symbol) {
    if (!Units.isRial(symbol)) return 1;
    const eng = NBX.core && NBX.core.engine;
    const rows = (eng && eng.universe) || [];
    const row = rows.find((r) => r && r.symbol === 'USDTIRT');
    const px = row ? +row.last : 0;
    if (px > 0) return px;
    // The engine may have a premium figure even when the universe is partial.
    const p = eng && +eng.premium;
    return p > 0 ? p : 0;
  }

  NBX.Paper = {
    start_equity: PAPER_START_EQUITY,
    equity: PAPER_START_EQUITY,
    cash: PAPER_START_EQUITY,
    openHeat: 0,
    dailyPnl: 0,
    halted: false,
    positions: [],
    closed: [],
    /** Betas for markets the exchange has closed, e.g. new Set(['DOGEIRT']). */
    closedMarkets: new Set(),
    /** Risk limits, identical to `risk_engine.RiskConfig` defaults. */
    cfg: Object.assign({}, RISK_DEFAULTS),
    quoteRate,
    halt() { this.halted = true; return this.getMetrics(); },
    resume() { this.halted = false; return this.getMetrics(); },

    _open() { return this.positions.filter((p) => p.state !== 'CLOSED'); },

    _markEquity() {
      const openVal = this._open().reduce((a, p) => {
        const rate = p.quote_usd > 0 ? p.quote_usd : 1;
        return a + (p.filled_qty * (p.last_price || p.entry_price)) / rate;
      }, 0);
      this.equity = this.cash + openVal;
      return this.equity;
    },

    /*: The offline bundle has no event stream to read, so it derives the same
     * shape from its own positions. Same fields, so the table renders one way;
     * anything it cannot know (`fee`, slippage) stays null instead of 0. */
    orderRows() {
      return this.positions.map((p) => ({
        clientOrderId: p.client_order_id || null,
        symbol: p.symbol, side: p.side,
        type: p.execution || 'limit',
        price: p.entry_price, amount: p.qty, filled: p.filled_qty,
        filled_pct: p.qty ? +((p.filled_qty / p.qty) * 100).toFixed(1) : 0,
        fee: null, slip_bps: null, pnl_usd: p.realized_pnl || 0,
        state: p.state, reason: null, source: 'browser',
        opened_at: p.opened_at,
        opened_at_iso: new Date(p.opened_at * 1000).toISOString(),
        closed_at_iso: null, close_reason: null,
      }));
    },

    getMetrics() {
      const open = this._open();
      const pnls = this.closed.map((p) => p.realized_pnl || 0);
      const wins = pnls.filter((v) => v > 0);
      const losses = pnls.filter((v) => v <= 0);
      const gp = wins.reduce((a, b) => a + b, 0);
      const gl = Math.abs(losses.reduce((a, b) => a + b, 0));
      return {
        trades: pnls.length,
        start_equity: this.start_equity,
        openPositions: open.length,
        cash: +this.cash.toFixed(2),
        equity: +this.equity.toFixed(2),
        hit_rate: pnls.length ? +((wins.length / pnls.length) * 100).toFixed(1) : 0,
        profit_factor: gl > 0 ? Math.min(99.99, +(gp / gl).toFixed(2)) : gp > 0 ? 99.99 : 0,
        expectancy: pnls.length ? +(pnls.reduce((a, b) => a + b, 0) / pnls.length).toFixed(2) : 0,
        expectancy_r: 0,
        max_drawdown: 0,
        daily_pnl: this.dailyPnl,
      };
    },

    /*: What the broker would do with this order, without doing it. Same ten
     * gates, same order of evaluation, as `/api/risk/whatif`. */
    preflightOrder(payload) {
      const sym = normSymbol(payload.symbol || 'BTCIRT');
      const side = (payload.side || 'buy').toLowerCase();
      const px = Units.roundPrice(+(payload.price || 0), sym);
      const qty = Units.truncateAmount(+(payload.qty || 0), sym);
      const rate = quoteRate(sym);
      const empty = {
        ok: false, symbol: sym, side, qty, price: px, rate,
        gates: [], blocked: true, blocked_by: null,
      };
      if (!(px > 0) || !(qty > 0)) return Object.assign({}, empty, { error: 'precision' });
      const exits = fallbackExits(px, side);
      const stop = +(payload.stop ?? exits.stop);
      const tp1 = +(payload.tp1 ?? exits.tp1), tp2 = +(payload.tp2 ?? exits.tp2);
      const plan = executionPlanError(side, px, stop, tp1, tp2);
      if (plan.error) {
        return Object.assign({}, empty, { error: plan.error, blocked_by: plan.error });
      }
      if (!(rate > 0)) {
        return Object.assign({}, empty, {
          error: 'no-fx-rate',
          message: 'USDTIRT price unavailable; an IRT order cannot be valued in USD',
        });
      }
      const notionalQuote = px * qty;
      const notionalUsd = notionalQuote / rate;
      const feeUsd = notionalQuote * PAPER_FEE_PCT / rate;
      const riskUsd = Math.abs(px - stop) * qty / rate;
      const pf = preflight({
        equity: this.equity, cash: this.cash,
        open_heat: this.openHeat, open_positions: this._open().length,
        concurrent_limit: this.cfg.max_concurrent,
        max_heat_pct: this.cfg.max_heat_pct,
        min_cash_pct: this.cfg.min_cash_pct,
        max_single_asset_pct: this.cfg.max_single_asset_pct,
        risk_pct_cap: this.cfg.risk_pct,
        halted: !!this.halted,
        notional_usd: notionalUsd, fee_usd: feeUsd, risk_usd: riskUsd,
        min_notional_usd: Units.minNotionalUsd(sym, rate),
        market_closed: this.closedMarkets.has(sym),
      });
      return { ok: !pf.blocked, symbol: sym, side, qty, price: px, rate,
               notional_quote: notionalQuote, notional_usd: notionalUsd,
               projected: pf };
    },

    placeOrder(payload) {
      const sym = normSymbol(payload.symbol || 'BTCIRT');
      const side = (payload.side || 'buy').toLowerCase();
      if (!+(payload.price || 0)) {
        return { ok: false, unavailable: true,
                 error: 'price unavailable: the exchange is unreachable — pass the last traded price explicitly' };
      }
      if (!+(payload.qty || 0)) return { ok: false, error: 'qty is required' };

      const pf = this.preflightOrder(payload);
      if (pf.error) {
        return { ok: false, error: pf.error, blocked_by: pf.blocked_by, message: pf.message, projection: null };
      }
      if (pf.projected.blocked) {
        // The id and the sentence, exactly as the backend refuses: the two
        // brokers must not describe the same rejection differently.
        const gate = pf.projected.gates.find((g) => !g.ok);
        return {
          ok: false,
          error: pf.projected.blocked_by + ': ' + (gate ? gate.detail : ''),
          blocked_by: pf.projected.blocked_by,
          projection: pf.projected,
        };
      }

      const px = pf.price;
      const qty = pf.qty;
      const notional = pf.notional_quote;
      const exits = fallbackExits(px, side);
      const stop = +(payload.stop ?? exits.stop);
      const tp1 = +(payload.tp1 ?? exits.tp1);
      const tp2 = +(payload.tp2 ?? exits.tp2);
      const pos = {
        id: 'pp-' + Date.now() + '-' + sym,
        symbol: sym,
        side,
        entry_price: px,
        qty,
        size_quote: notional,
        stop,
        tp1,
        tp2,
        stop_pct: +((Math.abs(px - stop) / Math.max(px, 1e-6)) * 100).toFixed(2),
        opened_at: Date.now() / 1000,
        state: 'OPEN',
        realized_pnl: 0,
        filled_qty: qty,
        tp1_filled: false,
        last_price: px,
        quote: Units.isRial(sym) ? 'IRT' : 'USDT',
        quote_usd: pf.rate,
        size_usd: pf.notional_usd,
        risk_usd: pf.projected.risk_usd,
        risk_pct: pf.projected.risk_pct,
        client_order_id: 'nobi-' + Date.now() + '-' + Math.random().toString(16).slice(2, 10),
      };
      this.positions.unshift(pos);
      this.cash -= pf.notional_usd + pf.projected.fee_usd;
      this.openHeat = +(this.openHeat + pf.projected.risk_pct).toFixed(4);
      this._markEquity();
      return { ok: true, position: pos, projection: pf.projected, metrics: this.getMetrics() };
    },

    closePosition(id, exitPrice) {
      const pos = this.positions.find((p) => p.id === id && p.state !== 'CLOSED');
      if (!pos) return { ok: false, error: 'position not found' };
      const px = Units.roundPrice(+(exitPrice || pos.last_price || 0), pos.symbol);
      if (!(px > 0)) return { ok: false, error: 'exit price is required' };
      const rate = pos.quote_usd > 0 ? pos.quote_usd : 1;
      const outQuote = px * pos.filled_qty;
      const feeQuote = outQuote * PAPER_FEE_PCT;
      const pnl = (outQuote - pos.size_quote) / rate - feeQuote / rate;
      this.cash += pos.size_usd || (pos.size_quote / rate);
      this.cash += pnl;
      pos.realized_pnl = pnl;
      pos.state = 'CLOSED';
      pos.last_price = px;
      this.closed.unshift(pos);
      this.openHeat = Math.max(0, +(this.openHeat - (pos.risk_pct || 0)).toFixed(4));
      this._markEquity();
      return { ok: true, position: pos, metrics: this.getMetrics() };
    },
  };

  /* ----------------------------------------------------------
   * 5. Standalone Walk-Forward Backtest & Grid Engine (ADR-0009)
   * --------------------------------------------------------- */
  NBX.Backtest = {
    runWindow(candles, symbol, tf, cfg) {
      const initEq = +(cfg.initial_equity || cfg.equity || 50000);
      let equity = initEq;
      let cash = initEq;
      const riskPct = +(cfg.risk_pct || 0.75);
      const stopK = +(cfg.atr_stop_k || 2.0);
      const maxOpen = +(cfg.max_concurrent || 5);
      const feePct = PAPER_FEE_PCT;
      // Same cost model as the server: an offline backtest that fills at the
      // exact price is not a cheaper backtest, it is a different one. 0 is
      // accepted, and then `costs_modeled` below refuses to certify the run.
      const slipBps = cfg.slippage_bps == null ? 6 : Math.max(0, +cfg.slippage_bps);
      const slip = slipBps / 10000;
      const tp1R = +(cfg.tp1_r || 1.5);
      const tp2R = +(cfg.tp2_r || 2.5);
      const openPos = [];
      const closed = [];
      const equityCurve = [];
      let ambiguous = 0;

      for (let i = 60; i < candles.length; i++) {
        const c = candles[i];
        const slice = candles.slice(0, i + 1);
        // Same hook the Python engine has (`signals=`): a caller may hand in a
        // signal series to isolate the execution rules from the scoring ones.
        const sig = (cfg._signals && cfg._signals[i]) || TA.signalScore(slice);

        for (let pIdx = openPos.length - 1; pIdx >= 0; pIdx--) {
          const tr = openPos[pIdx];
          let exitPx = null, reason = null;
          const hitTp = tr.tp1 && c.h >= tr.tp1;
          const hitSl = c.l <= tr.stop;
          if (hitTp && hitSl) {
            // Same candle reached both levels: the intrabar path is unknowable,
            // so take the adverse one and record it. Taking the target here (as
            // this engine used to) is the one bias a backtest must not have.
            ambiguous += 1;
            exitPx = tr.stop * (1 - slip);
            reason = 'sl-ambiguous';
          } else if (hitSl) {
            exitPx = tr.stop * (1 - slip);
            reason = 'sl';
          } else if (hitTp) {
            exitPx = tr.tp1 * (1 - slip);
            reason = 'tp1';
          }
          if (exitPx !== null) {
            const notionalIn = tr.entry * tr.qty;
            const notionalOut = exitPx * tr.qty;
            const pnl = notionalOut - notionalIn - (notionalIn + notionalOut) * feePct;
            const riskUnit = Math.abs(tr.entry - tr.stop) * tr.qty;
            tr.exit = exitPx;
            tr.exit_t = c.t;
            tr.reason = reason;
            tr.realized_pnl = pnl;
            tr.r_multiple = riskUnit > 0 ? pnl / riskUnit : 0;
            cash = Math.max(0, cash + notionalOut * (1 - feePct));
            openPos.splice(pIdx, 1);
            closed.push(tr);
          }
        }

        if (sig.direction === 'LONG' && openPos.length < maxOpen) {
          const entry = c.c;
          const atrV = sig.risk.atr || entry * 0.015;
          const stop = entry - stopK * atrV;
          const stopDist = entry - stop;
          const tp1 = entry + tp1R * stopDist;
          const tp2 = entry + tp2R * stopDist;
          const riskAmt = (equity * riskPct) / 100;
          const qty = stopDist > 0 ? riskAmt / stopDist : 0;
          const cost = qty * entry * (1 + slip) * (1 + feePct);
          if (qty > 0 && cost < cash) {
            cash -= cost;
            openPos.push({ side: 'buy', entry_t: c.t, entry, stop, tp1, tp2, qty });
          }
        }

        const openVal = openPos.reduce((acc, tr) => acc + c.c * tr.qty, 0);
        equity = cash + openVal;
        equityCurve.push([c.t * 1000, +equity.toFixed(2)]);
      }

      if (candles.length) {
        const lastC = candles[candles.length - 1];
        while (openPos.length) {
          const tr = openPos.pop();
          const notionalIn = tr.entry * tr.qty;
          const notionalOut = lastC.c * tr.qty;
          const pnl = notionalOut - notionalIn - (notionalIn + notionalOut) * feePct;
          const riskUnit = Math.abs(tr.entry - tr.stop) * tr.qty;
          tr.exit = lastC.c;
          tr.exit_t = lastC.t;
          tr.reason = 'eod';
          tr.realized_pnl = pnl;
          tr.r_multiple = riskUnit > 0 ? pnl / riskUnit : 0;
          closed.push(tr);
        }
      }

      const wins = closed.filter((t) => t.realized_pnl > 0);
      const losses = closed.filter((t) => t.realized_pnl <= 0);
      const total = closed.length;
      const hitRate = total ? (wins.length / total) * 100 : 0;
      const sumWin = wins.reduce((a, b) => a + b.realized_pnl, 0);
      const sumLoss = Math.abs(losses.reduce((a, b) => a + b.realized_pnl, 0));
      const pf = total && losses.length ? Math.min(99.99, sumWin / Math.max(sumLoss, 1e-9)) : wins.length ? 2.4 : 0;
      const expR = total ? closed.reduce((a, b) => a + (b.r_multiple || 0), 0) / total : 0;

      let peak = initEq, mdd = 0;
      for (const [, v] of equityCurve) {
        if (v > peak) peak = v;
        const dd = ((peak - v) / Math.max(peak, 1)) * 100;
        if (dd > mdd) mdd = dd;
      }

      const fmtIso = (ts) => new Date(ts * 1000).toISOString();
      return {
        symbol,
        tf,
        window_start: candles.length ? fmtIso(candles[0].t) : '',
        window_end: candles.length ? fmtIso(candles[candles.length - 1].t) : '',
        trades: total,
        wins: wins.length,
        losses: losses.length,
        hit_rate: +hitRate.toFixed(1),
        profit_factor: +pf.toFixed(2),
        expectancy_r: +expR.toFixed(3),
        max_drawdown: +mdd.toFixed(1),
        gross_profit: +sumWin.toFixed(2),
        gross_loss: +sumLoss.toFixed(2),
        initial_equity: initEq,
        final_equity: equityCurve.length ? equityCurve[equityCurve.length - 1][1] : initEq,
        ambiguous_exits: ambiguous,
        equity_curve: equityCurve.slice(-300),
        sample_trades: closed.slice(-8).map((t) => ({
          entry: t.entry,
          exit: t.exit,
          reason: t.reason,
          pnl: +t.realized_pnl.toFixed(2),
          r: +(t.r_multiple || 0).toFixed(2),
        })),
      };
    },

    runWalkForward(candles, symbol, tf, cfg = {}) {
      // Browser-only fallback. It does NOT optimise parameters on the train
      // slice (the backend walk-forward does); it only shows how the rules
      // behave on simulated candles, so every report is stamped as such.
      let tb = +(cfg.train_bars || 200);
      let eb = +(cfg.eval_bars || 120);
      const slipBps = cfg.slippage_bps == null ? 6 : Math.max(0, +cfg.slippage_bps);
      let windowMode = 'manual';
      const fits = (t, e) => e > 0 && Math.floor((candles.length - t) / e);
      if (fits(tb, eb) < 2) {
        eb = Math.max(48, Math.min(240, Math.floor(candles.length / 7)));
        tb = 3 * eb;
        if (tb + eb > candles.length) tb = Math.max(84, candles.length - eb - 1);
        windowMode = 'auto';
      }
      const windows = [];
      let start = 0;
      let idx = 0;
      while (start + tb + eb <= candles.length) {
        const evalSlice = candles.slice(start + tb - 60, start + tb + eb);
        const w = this.runWindow(evalSlice, symbol, tf, cfg);
        const params = {
          atr_stop_k: +(cfg.atr_stop_k || 2),
          tp1_r: +(cfg.tp1_r || 1.5),
          tp2_r: +(cfg.tp2_r || 2.5),
        };
        const evalStart = candles[start + tb] ? new Date(candles[start + tb].t * 1000).toISOString() : '';
        const evalEnd = candles[Math.min(start + tb + eb - 1, candles.length - 1)]
          ? new Date(candles[Math.min(start + tb + eb - 1, candles.length - 1)].t * 1000).toISOString()
          : '';
        windows.push({
          ...w,
          index: ++idx,
          train_start: null,
          train_end: null,
          eval_start: evalStart,
          eval_end: evalEnd,
          window_start: w.window_start,
          window_end: w.window_end,
          params,
          params_used: params,
          params_optimised: false,
          is: { trades: 0, expectancy_r: 0, profit_factor: 0, hit_rate: 0, max_drawdown: 0 },
          oos: {
            trades: w.trades,
            wins: w.wins,
            hit_rate: w.hit_rate,
            profit_factor: w.profit_factor,
            expectancy_r: w.expectancy_r,
            max_drawdown: w.max_drawdown,
            final_equity: w.final_equity,
          },
        });
        start += eb;
      }
      if (!windows.length) {
        const w = this.runWindow(candles, symbol, tf, cfg);
        windows.push({
          ...w,
          index: 1,
          params: { atr_stop_k: +(cfg.atr_stop_k || 2), tp1_r: +(cfg.tp1_r || 1.5), tp2_r: +(cfg.tp2_r || 2.5) },
          params_used: { atr_stop_k: +(cfg.atr_stop_k || 2), tp1_r: 1.5, tp2_r: 2.5 },
          params_optimised: false,
          is: { trades: 0, expectancy_r: 0, profit_factor: 0, hit_rate: 0, max_drawdown: 0 },
          oos: { trades: w.trades, hit_rate: w.hit_rate, profit_factor: w.profit_factor, expectancy_r: w.expectancy_r, max_drawdown: w.max_drawdown },
        });
      }

      const totalTrades = windows.reduce((a, w) => a + w.trades, 0);
      const totalWins = windows.reduce((a, w) => a + w.wins, 0);
      const grossP = windows.reduce((a, w) => a + (w.gross_profit || 0), 0);
      const grossL = windows.reduce((a, w) => a + (w.gross_loss || 0), 0);
      const pooledPf = grossL > 0 ? Math.min(99.99, grossP / grossL) : (grossP > 0 ? 99.99 : 0);
      const denom = Math.max(1, totalTrades);
      const expR = windows.reduce((a, w) => a + w.expectancy_r * w.trades, 0) / denom;
      const mdd = Math.max(...windows.map((w) => w.max_drawdown), 0);
      const ambiguousTotal = windows.reduce((a, w) => a + (w.ambiguous_exits || 0), 0);
      const ambiguousShare = totalTrades ? +((ambiguousTotal / totalTrades) * 100).toFixed(1) : 0;
      const profitable = windows.filter((w) => (w.final_equity || 0) > (w.initial_equity || 0)).length;
      const consistency = windows.length ? (profitable / windows.length) * 100 : 0;

      // chain the per-window curves so the chart shows one OOS line
      const initialEquity = +(cfg.equity || cfg.initial_equity || 50000);
      let eq = initialEquity;
      const curve = [];
      for (const w of windows) {
        const wStart = w.initial_equity || initialEquity;
        const scale = eq / Math.max(wStart, 1e-9);
        for (const [ts, v] of (w.equity_curve || [])) curve.push([ts, +(+v * scale).toFixed(2)]);
        eq = eq * (1 + ((w.final_equity || wStart) / Math.max(wStart, 1e-9) - 1));
      }

      const checks = {
        windows_ge_3: { value: windows.length, pass: windows.length >= 3, required: 3 },
        oos_trades_ge_min: { value: totalTrades, pass: totalTrades >= 30, required: 30 },
        profit_factor_ge_1_3: { value: +pooledPf.toFixed(2), pass: pooledPf >= 1.3, required: 1.3 },
        max_drawdown_lt_15: { value: +mdd.toFixed(1), pass: mdd < 15, required: 15 },
        expectancy_r_positive: { value: +expR.toFixed(3), pass: expR > 0, required: 0 },
        profitable_windows_ge_50: { value: +consistency.toFixed(1), pass: consistency >= 50, required: 50 },
        ambiguous_share_le_10: { value: ambiguousShare, pass: ambiguousShare <= 10, required: '<= 10 %' },
        costs_modeled: { value: slipBps, pass: slipBps > 0, required: '> 0 bps' },
      };
      const dataShort = windows.length < 3 || totalTrades < 30;
      const wouldBe = dataShort ? 'INSUFFICIENT_DATA' : (Object.values(checks).every((c) => c.pass) ? 'PASS' : 'FAIL');
      const hash = hashSeed(JSON.stringify({ symbol, tf, cfg })).toString(16).slice(0, 8);

      return {
        config_hash: hash,
        symbol,
        tf,
        mode: 'wf',
        source: 'synthetic',
        bars: candles.length,
        window_mode: windowMode,
        train_bars: tb,
        eval_bars: eb,
        param_grid_size: 1,
        initial_equity: initialEquity,
        slippage_bps: slipBps,
        costs: { slippage_bps: slipBps, fee_pct: PAPER_FEE_PCT, modeled: slipBps > 0 },
        // Same disclosure as the server: this engine replays the TA rules only,
        // while the live decision also weights liquidity/fundamentals and can be
        // capped by vetoes. Saying nothing would make the report look complete.
        decision_basis: {
          components: ['ta'], weights: { ta: 1 }, vetoes: false,
          live_composite: 'ta+fa+liq', live_weights: { ta: 0.55, fa: 0.25, liq: 0.2 },
          matches_live: false,
        },
        aggregate: {
          trades: totalTrades,
          total_wins: totalWins,
          hit_rate: totalTrades ? +((totalWins / totalTrades) * 100).toFixed(1) : 0,
          profit_factor: +pooledPf.toFixed(2),
          expectancy_r: +expR.toFixed(3),
          max_drawdown: +mdd.toFixed(1),
          windows: windows.length,
          profitable_windows: profitable,
          initial_equity: initialEquity,
          final_equity: +eq.toFixed(2),
          ambiguous_exits: ambiguousTotal,
          ambiguous_share: ambiguousShare,
          equity_curve: curve.slice(-400),
        },
        aggregate_is: { trades: 0, expectancy_r: 0, profit_factor: 0 },
        degradation: { is_expectancy_r: 0, oos_expectancy_r: +expR.toFixed(3), retention: null },
        param_stability: { distinct_param_sets: 1, most_common_share: 100 },
        gate: {
          state: 'SYNTHETIC_DATA',
          would_be: wouldBe,
          data_source: 'synthetic',
          checks,
          failed: Object.keys(checks).filter((k) => !checks[k].pass),
          message: ((typeof LANG !== 'undefined' && LANG === 'fa')
            ? 'شمع‌ها از موتور مرورگر (شبیه‌سازی) آمده‌اند؛ دروازه ارتقا روی داده مصنوعی مجوز نمی‌دهد.'
            : 'Candles come from the browser engine (simulated); the gate never certifies simulated data.')
            + ' ' + ((typeof LANG !== 'undefined' && LANG === 'fa')
              ? 'این گزارش فقط جزء تکنیکال را می‌سنجد؛ تصمیم زنده وزن نقدینگی/فاندامنتال و وتو هم دارد.'
              : 'This report measures the technical component only; the live decision also weights liquidity/fundamentals and can be vetoed.'),
          bars_needed: tb + 3 * eb,
        },
        details: windows.slice(-12),
      };
    },

    gridSearch(candles, symbol, tf, customGrid) {
      const t0 = performance.now();
      const grid = customGrid || { risk_pct: [0.5, 1.0, 1.5], atr_stop_k: [1.5, 2.0, 2.5] };
      const xax = grid.risk_pct || [0.5, 1.0, 1.5];
      const yax = grid.atr_stop_k || [1.5, 2.0, 2.5];
      const paths = [];
      let best = null;

      for (const r of xax) {
        for (const k of yax) {
          const params = { risk_pct: r, atr_stop_k: k };
          const wf = this.runWalkForward(candles, symbol, tf, params);
          const a = wf.aggregate;
          const score = a.profit_factor * 0.5 + a.expectancy_r + (a.hit_rate / 100) * 0.2 - (a.max_drawdown / 100) * 0.5;
          const item = { params, aggregate: a, _score: score };
          paths.push(item);
          if (!best || score > best._score) best = item;
        }
      }

      const values = xax.map((x) =>
        yax.map((y) => {
          const found = paths.find((p) => p.params.risk_pct === x && p.params.atr_stop_k === y);
          return Object.assign({ risk_pct: x, atr_stop_k: y }, found ? found.aggregate : {});
        })
      );

      return {
        ok: true,
        symbol,
        tf,
        combo_count: xax.length * yax.length,
        elapsed_s: +((performance.now() - t0) / 1000).toFixed(2),
        best: {
          params: best ? best.params : {},
          aggregate: best ? best.aggregate : {},
          score: best ? +best._score.toFixed(3) : 0,
        },
        heatmap: { x: xax, y: yax, values },
      };
    },
  };

  /* ----------------------------------------------------------
   * 6. API client (browser-safe, with full mobile fallback)
   * --------------------------------------------------------- */
  class NobitexApi {
    constructor(options) {
      this.opts = Object.assign({}, cfg, options || {});
      this.bucket = new NBX.TokenBucket(this.opts.ratePerMinute);
      this.connected = false;
      this.standaloneMobile = isFileOrMobileLocal;
      this.circuitOpenUntil = 0;
      this.stats = { total: 0, ok: 0, fail: 0, lastLatencyMs: 18 };
      this._healthListeners = [];
    }

    _url(path) {
      if (/^https?:\/\//i.test(path) || path.startsWith('/api/') || path === '/api') {
        return path;
      }
      return this.opts.baseUrl + path;
    }

    onHealth(fn) {
      this._healthListeners.push(fn);
      return () => (this._healthListeners = this._healthListeners.filter((x) => x !== fn));
    }
    _emitHealth(h) {
      this.connected = h.ok;
      this._healthListeners.forEach((fn) => {
        try { fn(h); } catch (e) {}
      });
    }

    async _fetch(path) {
      const url = this._url(path);
      const isBackendUrl = url.startsWith('/api/') || url === '/api' || (cfg.pythonApi && url.startsWith(cfg.pythonApi));
      if (!isBackendUrl && Date.now() < this.circuitOpenUntil) {
        throw new NBXError('NETWORK', 'circuit-open');
      }
      const proxy = !isBackendUrl && this.opts.apiProxy ? this.opts.apiProxy : '';
      const finalUrl = proxy ? proxy + encodeURIComponent(url) : url;
      const t0 = performance.now();
      const controller = new AbortController();
      const to = setTimeout(() => controller.abort(), this.opts.timeoutMs);
      try {
        const res = await fetch(finalUrl, { signal: controller.signal, mode: 'cors' });
        clearTimeout(to);
        const latency = performance.now() - t0;
        this.stats.total++;
        this.stats.lastLatencyMs = latency;
        if (!res.ok) {
          this.stats.fail++;
          if (res.status === 429) throw new NBXError('RATE', 'rate limited', { status: res.status });
          throw new NBXError('HTTP', 'bad status ' + res.status, { status: res.status });
        }
        const data = await res.json();
        this.stats.ok++;
        this._emitHealth({ ok: true, latency });
        return data;
      } catch (e) {
        clearTimeout(to);
        this.stats.fail++;
        if (!isBackendUrl) {
          this.circuitOpenUntil = Date.now() + 120000;
        }
        this._emitHealth({ ok: false, error: e });
        if (e instanceof NBXError) throw e;
        throw new NBXError('NETWORK', e.message || 'fetch failed', { error: e });
      }
    }

    async get(path, attempts) {
      const tries = attempts !== undefined ? attempts : this.opts.maxRetries;
      let lastErr;
      for (let i = 0; i < tries; i++) {
        try {
          if (!this.bucket.allow()) await sleep(80);
          return await this._fetch(path);
        } catch (e) {
          lastErr = e;
          if (e.code === 'RATE') await sleep(800 * (i + 1));
          else break;
        }
      }
      throw lastErr;
    }

    async healthCheck() {
      if (this.standaloneMobile) {
        return { ok: false, mobileStandalone: true, latency: 12 };
      }
      try {
        const r = await this.get('/v3/orderbook/BTCIRT', 1);
        return { ok: !!(r && r.status === 'ok'), latency: this.stats.lastLatencyMs };
      } catch (e) {
        return { ok: false, error: e.code };
      }
    }

    async checkBackend() {
      if (!cfg.preferPython) return null;
      try {
        const r = await this.get(cfg.pythonApi + '/health', 1);
        return r && r.backend ? r : null;
      } catch (e) {
        return null;
      }
    }

    async getBackendSignals(tf) {
      const r = await this.get(cfg.pythonApi + '/signals?tf=' + encodeURIComponent(tf), 1);
      if (r && r.scored) return r;
      throw new NBXError('DATA', 'bad backend signals');
    }

    async getBackendUniverse() {
      const r = await this.get(cfg.pythonApi + '/universe', 1);
      if (r && Array.isArray(r.symbols)) return r;
      throw new NBXError('DATA', 'bad backend universe');
    }

    async getBackendCandles(symbol, tf) {
      let why = 'backend unreachable';
      try {
        const r = await this.get(cfg.pythonApi + '/candles/' + encodeURIComponent(symbol) + '?tf=' + encodeURIComponent(tf), 1);
        if (r && Array.isArray(r.rows) && r.rows.length) return r;
        if (r && r.unavailable) why = r.message || why;
      } catch (e) {
        why = (e && e.message) || why;
      }
      /* No synthetic series here on purpose. Returning [] makes every caller
       * show the "exchange unreachable" state instead of a plausible chart. */
      return { ok: false, unavailable: true, symbol: normSymbol(symbol), tf: tf || '15m',
               count: 0, rows: [], error: 'upstream_unavailable',
               message: NBX.UPSTREAM_MSG[DOC_LANG()], detail: String(why).slice(0, 200) };
    }

    async getBackendFA(symbol) {
      if (!this.standaloneMobile) {
        try {
          const r = await this.get(cfg.pythonApi + '/fa/' + encodeURIComponent(symbol), 1);
          if (r && typeof r.score === 'number') return r;
        } catch (e) {}
      }
      // The old fallback scored coins from a hard-coded market-cap table that
      // went stale the day it was typed. No data is better than wrong data.
      return { ok: false, unavailable: true, symbol: normSymbol(symbol),
               score: null, error: 'upstream_unavailable',
               message: NBX.UPSTREAM_MSG[DOC_LANG()] };
    }

    async getBackendRiskState() {
      if (!this.standaloneMobile) {
        try {
          const r = await this.get(cfg.pythonApi + '/risk/state', 1);
          if (r && r.equity !== undefined) return r;
        } catch (e) {}
      }
      return {
        equity: NBX.Paper.equity,
        openHeat: NBX.Paper.openHeat,
        dailyPnl: NBX.Paper.dailyPnl,
        halted: NBX.Paper.halted,
        caps: { riskPct: 0.75, maxConcurrent: 5, maxSingleAssetPct: 25, maxHeatPct: 6, minCashPct: 10 },
      };
    }

    async postWhatIf(body) {
      // The server owns the risk rules; the ticket must ask it rather than
      // repeat the caps in JavaScript (they drifted once already: 0.75 / 25
      // were hard-coded in the page).
      if (this.standaloneMobile) return null;
      const res = await fetch(cfg.pythonApi + '/risk/whatif', {
        method: 'POST',
        headers: this._authHeaders(),
        body: JSON.stringify(body || {}),
      });
      const r = await res.json();
      if (!res.ok) throw new NBXError('DATA', (r && r.detail) || 'whatif failed');
      return r;
    }

    async postAllocate(body) {
      // A3: splitting the heat budget is the server's arithmetic — correlations
      // come from the candle cache, and the caps live in risk_engine.
      if (this.standaloneMobile) return null;
      const res = await fetch(cfg.pythonApi + '/portfolio/allocate', {
        method: 'POST',
        headers: this._authHeaders(),
        body: JSON.stringify(body || {}),
      });
      const r = await res.json();
      if (!res.ok) throw new NBXError('DATA', (r && r.detail) || 'allocate failed');
      return r;
    }

    async getMarketOverview(tf) {
      // A6: breadth/dispersion are computed server-side — the page only prints
      // them. Turnover has to be converted to USD before any of it means
      // anything, and that conversion lives next to the exchange client.
      if (this.standaloneMobile) return null;
      const res = await fetch(cfg.pythonApi + '/market/overview?tf=' + encodeURIComponent(tf || '15m'),
                             { headers: this._authHeaders() });
      const r = await res.json();
      if (!res.ok) throw new NBXError('DATA', (r && r.detail) || 'market overview failed');
      return r;
    }

    async postBenchmark(body) {
      // A7: beta/alpha are computed server-side from the candle cache; the
      // page must not re-derive them from its own price samples.
      if (this.standaloneMobile) return null;
      const res = await fetch(cfg.pythonApi + '/benchmark/relative', {
        method: 'POST',
        headers: this._authHeaders(),
        body: JSON.stringify(body || {}),
      });
      const r = await res.json();
      if (!res.ok) throw new NBXError('DATA', (r && r.detail) || 'benchmark failed');
      return r;
    }

    async getBackendRiskPlan(symbol, side, tf = '15m', approval = false) {
      if (!this.standaloneMobile) {
        try {
          const r = await this.get(cfg.pythonApi + '/risk/plan?symbol=' + encodeURIComponent(symbol) + '&side=' + encodeURIComponent(side || 'buy') + '&tf=' + encodeURIComponent(tf) + (approval ? '&approval=true' : ''), 1);
          if (r && (r.action || r.error || r.ok === false)) return r;
        } catch (e) {
          const status = e && e.meta && e.meta.status;
          return { ok: false, blocked: true, unavailable: true,
                   error: status === 401 || status === 403 ? 'auth-required' : 'plan-request-failed' };
        }
      }
      const sym = normSymbol(symbol);
      const engine = NBX.core && NBX.core.engine;
      const rows = engine && (engine.candles[sym + ':' + tf] || engine.candles[sym]);
      const entry = rows && rows.length ? +rows[rows.length - 1].c : 0;
      if (!entry) {
        return { ok: false, unavailable: true, symbol: sym, side: side || 'buy',
                 error: 'upstream_unavailable', message: NBX.UPSTREAM_MSG[DOC_LANG()],
                 warnings: [], blocked: true };
      }
      // No local plan. This used to answer with `size_quote: 18500`, a 3.6%
      // stop and `blocked: false` — numbers that came from nowhere and that the
      // broker's ten gates had never seen. A ticket built on them promised an
      // order the backend would refuse. If the backend cannot size the trade,
      // the ticket must say so.
      return { ok: false, unavailable: true, symbol: sym, side: side || 'buy',
               error: 'risk_plan_unavailable',
               message: NBX.UPSTREAM_MSG[DOC_LANG()],
               warnings: [], blocked: true };
    }

    _authHeaders() {
      const h = { 'Content-Type': 'application/json' };
      if (cfg.apiToken) h['X-API-Token'] = cfg.apiToken;
      const csrf = readCookie('nbx_csrf');
      if (csrf) h['X-CSRF-Token'] = csrf;
      return h;
    }

    async postPaperOrder(payload) {
      if (!this.standaloneMobile) {
        try {
          const res = await fetch(cfg.pythonApi + '/paper/order', {
            method: 'POST',
            headers: this._authHeaders(),
            body: JSON.stringify(payload),
          });
          const r = await res.json();
          if (res.ok && r && r.ok !== undefined) return r;
          // 401/403 must surface: silently falling back to the browser engine
          // would hide the fact that the order never reached the backend.
          if (res.status === 401 || res.status === 403) {
            return { ok: false, error: (r && r.detail) || 'withheld by server policy (auth)', auth_error: true };
          }
          if (r && r.ok === false) return r;
        } catch (e) {
          // The order never reached the backend. Filling it in the browser
          // produced a position that exists only in this tab — a fake fill,
          // with two gates instead of the broker's ten. Say it failed.
          return { ok: false, network_error: true,
                   error: 'paper_order_unreachable',
                   message: NBX.UPSTREAM_MSG[DOC_LANG()] };
        }
        return { ok: false, error: 'paper_order_failed',
                 message: 'the server answered but did not place the order' };
      }
      return NBX.Paper.placeOrder(payload || {});
    }

    async postResume() {
      if (this.standaloneMobile) {
        if (NBX.Paper && typeof NBX.Paper.resume === 'function') NBX.Paper.resume();
        return { halted: false, standalone: true };
      }
      try {
        const res = await fetch(cfg.pythonApi + '/paper/resume', { method: 'POST', headers: this._authHeaders() });
        const r = await res.json();
        if (res.status === 401 || res.status === 403) return { halted: true, error: r.detail || 'auth required' };
        return r;
      } catch (e) {
        return { halted: true, error: String(e) };
      }
    }

    async postHalt(flat) {
      if (this.standaloneMobile) {
        if (NBX.Paper && typeof NBX.Paper.halt === 'function') NBX.Paper.halt();
        return { halted: true, standalone: true };
      }
      try {
        const res = await fetch(cfg.pythonApi + '/paper/halt', { method: 'POST', headers: this._authHeaders() });
        return await res.json();
      } catch (e) {
        return { halted: false, error: String(e) };
      }
    }

    /* The order log. `null` means "the server did not answer" — distinct from
       `[]`, which means "it answered and the desk has placed nothing". The
       Orders page needs that distinction to avoid blaming the exchange for an
       empty table (ADR-0014). */
    async getPaperOrders(limit) {
      if (this.standaloneMobile) {
        return { ok: true, rows: NBX.Paper.orderRows(), counts: null, offline: true };
      }
      try {
        const r = await this.get(cfg.pythonApi + '/paper/orders?limit=' + (limit || 100), 1);
        if (r && Array.isArray(r.rows)) return r;
        return null;
      } catch (e) {
        return null;
      }
    }

    async getPaperPositions() {
      if (!this.standaloneMobile) {
        try {
          const r = await this.get(cfg.pythonApi + '/paper/positions', 1);
          if (r && Array.isArray(r.positions)) return r;
        } catch (e) {}
      }
      return { positions: NBX.Paper.positions, metrics: NBX.Paper.getMetrics() };
    }

    /* Depth is only meaningful from the exchange: a locally synthesised book
       would look exactly like a traded one. On failure this returns null and the
       panel says why, rather than returning an empty object that reads as data. */
    async getBackendOrderbook(symbol) {
      if (this.standaloneMobile) return null;
      try {
        const r = await this.get(cfg.pythonApi + '/orderbook/' + encodeURIComponent(symbol), 1);
        if (r && r.ok && (r.bids || r.asks)) return r;
        return null;
      } catch (e) {
        return null;
      }
    }

    async getPaperMetrics() {
      if (!this.standaloneMobile) {
        try {
          const r = await this.get(cfg.pythonApi + '/paper/metrics', 1);
          if (r && r.metrics) return r;
        } catch (e) {}
      }
      return { metrics: NBX.Paper.getMetrics(), curve: [] };
    }

    async getWsStatus() {
      if (!this.standaloneMobile) {
        try {
          const r = await this.get(cfg.pythonApi + '/ws/status', 1);
          if (r && r.books) return r;
        } catch (e) {}
      }
      return { enabled: false, subscriptions: [], books: {}, standalone: true };
    }

    async getExecMode() {
      // Who pulls the trigger is server state, not a local variable: the
      // executor reads the same object this returns.
      if (this.standaloneMobile) return null;
      const r = await this.get(cfg.pythonApi + '/exec/mode', 1);
      return r || null;
    }

    async postExecMode(body) {
      // Changing it has consequences the executor acts on, so it is a POST and
      // the server answers with the state that is now in force — the page
      // renders that answer instead of assuming the change landed.
      if (this.standaloneMobile) return { ok: false, standalone: true, error: 'no-server' };
      const res = await fetch(cfg.pythonApi + '/exec/mode', {
        method: 'POST',
        headers: this._authHeaders(),
        body: JSON.stringify(body || {}),
      });
      const r = await res.json();
      if (!res.ok) throw new NBXError('DATA', (r && r.detail) || 'exec mode failed');
      return r;
    }

    async getJournalSummary(opts) {
      const o = opts || {};
      const qs = new URLSearchParams();
      qs.set('days', String(o.days || 90));
      qs.set('limit', String(o.limit || 60));
      if (o.tf) qs.set('tf', o.tf);
      if (o.source) qs.set('source', o.source);
      if (o.origin) qs.set('origin', o.origin);
      if (this.standaloneMobile) return { kpi: {}, horizons: [], families: [], calibration: [], signals: [], origins: {}, standalone: true };
      try {
        const r = await this.get(cfg.pythonApi + '/journal/summary?' + qs.toString(), 1);
        if (r && r.kpi) return r;
      } catch (e) {}
      return { kpi: {}, horizons: [], families: [], calibration: [], signals: [], origins: {}, error: 'unavailable' };
    }

    async getJournalSignal(id) {
      if (this.standaloneMobile) return { error: 'detail needs the python backend' };
      try {
        const r = await this.get(cfg.pythonApi + '/journal/signal/' + encodeURIComponent(id), 1);
        if (r && r.signal) return r;
        return { error: (r && r.detail) || 'not found' };
      } catch (e) {
        return { error: String((e && e.message) || e) };
      }
    }

    async getJournalExport(opts) {
      const o = opts || {};
      const qs = new URLSearchParams();
      qs.set('format', o.format || 'csv');
      if (o.days) qs.set('days', String(o.days));
      if (o.origin) qs.set('origin', o.origin);
      if (o.tf) qs.set('tf', o.tf);
      const res = await fetch(cfg.pythonApi + '/journal/export?' + qs.toString(),
                             { headers: this._authHeaders() });
      if (!res.ok) throw new Error('export failed: HTTP ' + res.status);
      const text = await res.text();
      const rows = res.headers.get('X-Rows');
      return { text, rows: rows === null ? null : Number(rows),
               filename: (res.headers.get('Content-Disposition') || '')
                 .split('filename="')[1]?.replace('"', '') || ('nobi-journal.' + (o.format || 'csv')) };
    }

    async getSettings() {
      if (this.standaloneMobile) return { read_only: true, standalone: true };
      try {
        const r = await this.get(cfg.pythonApi + '/settings', 1);
        if (r && typeof r.read_only === 'boolean') return r;
      } catch (e) {}
      return { error: 'unavailable' };
    }

    async testTelegram() {
      // Asks the server "is the configured bot token real?". The token itself
      // is never sent to — or readable from — the browser.
      if (this.standaloneMobile) return { standalone: true, has_token: false };
      try {
        const res = await fetch(cfg.pythonApi + '/settings/telegram/test', {
          method: 'POST', headers: this._authHeaders(),
        });
        return await res.json();
      } catch (e) {
        return { error: 'unreachable', detail: String(e && e.message || e).slice(0, 80) };
      }
    }

    async getErrors(opts) {
      const o = opts || {};
      const qs = new URLSearchParams();
      qs.set('limit', String(o.limit || 200));
      if (o.level) qs.set('level', o.level);
      if (o.hours) qs.set('hours', String(o.hours));
      if (o.q) qs.set('q', o.q);
      try {
        const r = await this.get(cfg.pythonApi + '/errors?' + qs.toString(), 1);
        return r && Array.isArray(r.errors) ? r : { errors: [], n: 0, counts: {} };
      } catch (e) {
        return { errors: [], n: 0, counts: {}, error: 'unavailable' };
      }
    }

    async postError(payload) {
      /* Fire-and-forget: a failed error report must never raise, or reporting
         one bug would create another. */
      if (this.standaloneMobile) return { ok: false, standalone: true };
      try {
        const res = await fetch(cfg.pythonApi + '/errors', {
          method: 'POST', headers: this._authHeaders(), body: JSON.stringify(payload),
        });
        return await res.json();
      } catch (e) { return { ok: false }; }
    }

    async clearErrors() {
      if (this.standaloneMobile) return { ok: false, standalone: true };
      try {
        const res = await fetch(cfg.pythonApi + '/errors/clear', {
          method: 'POST', headers: this._authHeaders(),
        });
        return await res.json();
      } catch (e) { return { ok: false, error: String(e).slice(0, 80) }; }
    }

    async selfTestErrors() {
      if (this.standaloneMobile) return { ok: false, standalone: true };
      try {
        const res = await fetch(cfg.pythonApi + '/errors/selftest', {
          method: 'POST', headers: this._authHeaders(),
        });
        return await res.json();
      } catch (e) { return { ok: false, error: String(e).slice(0, 80) }; }
    }

    async getDiagnostics() {
      // What can be checked. No results — a catalogue is not a result set.
      if (this.standaloneMobile) return { checks: [], devsuite: { available: false, reason: 'offline' } };
      try {
        const r = await this.get(cfg.pythonApi + '/diagnostics', 1);
        return r && Array.isArray(r.checks) ? r : { checks: [], devsuite: { available: false } };
      } catch (e) {
        return { checks: [], devsuite: { available: false }, error: 'unavailable' };
      }
    }

    async runDiagnostics(opts) {
      // The exchange checks are the slow ones, so the caller decides whether
      // to include them rather than the default being a 3x20s wait.
      const body = { kinds: (opts && opts.kinds) || null, only: (opts && opts.only) || null };
      if (this.standaloneMobile) return { standalone: true, overall: 'skip', results: [] };
      try {
        const res = await fetch(cfg.pythonApi + '/diagnostics/run', {
          method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) return { error: (data && data.detail) || ('HTTP ' + res.status), results: [] };
        return data;
      } catch (e) {
        return { error: 'unreachable', detail: String(e && e.message || e).slice(0, 120), results: [] };
      }
    }

    async getDevSuite() {
      if (this.standaloneMobile) return { available: false, reason: 'offline', gates: [], last: null };
      try {
        return await this.get(cfg.pythonApi + '/devsuite', 1);
      } catch (e) {
        return { available: false, reason: 'unreachable', gates: [], last: null };
      }
    }

    async runDevSuite(gates) {
      if (this.standaloneMobile) return { error: 'offline' };
      try {
        const res = await fetch(cfg.pythonApi + '/devsuite/run', {
          method: 'POST', headers: this._authHeaders(),
          body: JSON.stringify({ gates: gates || null }),
        });
        const data = await res.json();
        if (!res.ok) return { error: (data && data.detail && data.detail.message) || ('HTTP ' + res.status),
                            busy: res.status === 409 };
        return data;
      } catch (e) {
        return { error: 'unreachable', detail: String(e && e.message || e).slice(0, 120) };
      }
    }

    async getDevSuiteRun(runId) {
      try {
        return await this.get(cfg.pythonApi + '/devsuite/' + encodeURIComponent(runId), 1);
      } catch (e) {
        return { error: 'unreachable' };
      }
    }

    async getOperations() {
      if (this.standaloneMobile) return { error: 'offline', archives: [] };
      try { return await this.get(cfg.pythonApi + '/operations', 1); }
      catch (e) { return { error: 'unreachable', archives: [] }; }
    }

    async runOperation(action, body) {
      if (this.standaloneMobile) return { error: 'offline' };
      try {
        const res = await fetch(cfg.pythonApi + '/operations/' + encodeURIComponent(action), {
          method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body || {}),
        });
        const data = await res.json();
        if (!res.ok) return { error: (data && data.detail) || ('HTTP ' + res.status) };
        return data;
      } catch (e) { return { error: 'unreachable' }; }
    }

    async getAudit(opts) {
      const o = opts || {};
      if (this.standaloneMobile) return { events: [], counts: {}, standalone: true };
      try {
        const qs = new URLSearchParams({ limit: String(o.limit || 25) });
        if (o.days) qs.set('days', String(o.days));
        const r = await this.get(cfg.pythonApi + '/audit?' + qs.toString(), 1);
        if (r && Array.isArray(r.events)) return r;
      } catch (e) {}
      return { events: [], counts: {}, error: 'unavailable' };
    }

    async getNotifyStatus() {
      if (this.standaloneMobile) return { configured: false, transport: 'disabled' };
      try {
        const r = await this.get(cfg.pythonApi + '/notify/status', 1);
        if (r && typeof r.configured === 'boolean') return r;
      } catch (e) {}
      return { configured: false, transport: 'unknown' };
    }

    async postMsiBaseline(opts) {
      const o = opts || {};
      if (this.standaloneMobile) return { error: 'baseline needs the python backend' };
      try {
        const res = await fetch(cfg.pythonApi + '/journal/msi/baseline?tf=' + encodeURIComponent(o.tf || '15m')
          + '&symbols=' + encodeURIComponent(o.symbols || 10)
          + '&bars=' + encodeURIComponent(o.bars || 1200),
          { method: 'POST', headers: this._authHeaders() });
        const r = await res.json();
        if (res.status === 401 || res.status === 403) return { error: r.detail || 'auth required' };
        return r;
      } catch (e) {
        return { error: String(e) };
      }
    }

    async postJournalBackfill(opts) {
      const o = opts || {};
      if (this.standaloneMobile) return { error: 'backfill needs the python backend' };
      try {
        const res = await fetch(cfg.pythonApi + '/journal/backfill?tf=' + encodeURIComponent(o.tf || '15m')
          + '&symbols=' + encodeURIComponent(o.symbols || 10)
          + '&bars=' + encodeURIComponent(o.bars || 800),
          { method: 'POST', headers: this._authHeaders() });
        const r = await res.json();
        if (res.status === 401 || res.status === 403) return { error: r.detail || 'auth required' };
        return r;
      } catch (e) {
        return { error: String(e) };
      }
    }

    async getJournalSignals(opts) {
      const o = opts || {};
      const qs = new URLSearchParams();
      ['tf', 'symbol', 'direction', 'source'].forEach(k => { if (o[k]) qs.set(k, o[k]); });
      qs.set('days', String(o.days || 90));
      qs.set('limit', String(o.limit || 200));
      try {
        const r = await this.get(cfg.pythonApi + '/journal/signals?' + qs.toString(), 1);
        return r && r.signals ? r : { count: 0, signals: [] };
      } catch (e) {
        return { count: 0, signals: [], error: String(e) };
      }
    }

    _getCandlesForBacktest(symbol, tf, bars) {
      const sym = normSymbol(symbol);
      const engine = NBX.core && NBX.core.engine;
      const existing = engine && (engine.candles[sym + ':' + tf] || engine.candles[sym]);
      if (existing && existing.length >= 200) return existing;
      // Nothing local to run on: the caller decides how to tell the user.
      return [];
    }

    /* The dashboard can fall back to local computation when the server is
     * unreachable, but a fallback that nobody is told about is indistinguishable
     * from a working server. One warning row per failure keeps it honest and
     * never throws (postError is fire-and-forget). */
    _reportFallback(what, err, context) {
      const msg = String((err && err.message) || err || 'unknown error');
      try {
        if (typeof this.postError === 'function') {
          this.postError({ level: 'warning', source: 'api.' + what + '-fallback',
                           message: 'server ' + what + ' failed, computed locally: ' + msg,
                           stack: err && err.stack, context: context });
        }
      } catch (_) { /* logging must never break the feature */ }
    }

    async runBacktest(symbol, tf, btCfg) {
      let reason = null;
      if (!this.standaloneMobile) {
        try {
          const res = await fetch(cfg.pythonApi + '/backtest/run', {
            method: 'POST',
            headers: this._authHeaders(),
            body: JSON.stringify({ symbol, tf, ...btCfg }),
          });
          const r = await res.json().catch(() => ({}));
          if (res.ok && r && (r.ok || r.error)) return r;
          reason = new Error('HTTP ' + res.status);
        } catch (e) { reason = e; }
        this._reportFallback('backtest', reason, (symbol || '') + ' ' + (tf || ''));
      }
      await sleep(60);
      const sym = normSymbol(symbol || 'BTCIRT');
      const rows = this._getCandlesForBacktest(sym, tf || '1h', btCfg && btCfg.bars);
      if (!rows || rows.length < 120) {
        return { ok: false, unavailable: true, error: 'upstream_unavailable',
                 message: NBX.UPSTREAM_MSG[DOC_LANG()],
                 detail: reason ? String(reason.message || reason) : 'no candles' };
      }
      const report = NBX.Backtest.runWalkForward(rows, sym, tf || '1h', btCfg || {});
      return { ok: true, report, standalone: true, fallback: !!reason,
               fallback_reason: reason ? String(reason.message || reason) : null };
    }

    async runGridSearch(symbol, tf, grid, bars) {
      let reason = null;
      if (!this.standaloneMobile) {
        try {
          const res = await fetch(cfg.pythonApi + '/backtest/grid', {
            method: 'POST',
            headers: this._authHeaders(),
            body: JSON.stringify({ symbol, tf, grid: grid || undefined }),
          });
          const r = await res.json().catch(() => ({}));
          if (res.ok && r && (r.ok || r.error)) return r;
          reason = new Error('HTTP ' + res.status);
        } catch (e) { reason = e; }
        this._reportFallback('grid', reason, (symbol || '') + ' ' + (tf || ''));
      }
      await sleep(80);
      const sym = normSymbol(symbol || 'BTCIRT');
      const rows = this._getCandlesForBacktest(sym, tf || '1h', bars || 600);
      if (!rows || rows.length < 120) {
        return { ok: false, unavailable: true, error: 'upstream_unavailable',
                 message: NBX.UPSTREAM_MSG[DOC_LANG()],
                 detail: reason ? String(reason.message || reason) : 'no candles' };
      }
      const out = NBX.Backtest.gridSearch(rows, sym, tf || '1h', grid);
      return reason ? Object.assign({}, out, { fallback: true,
               fallback_reason: String(reason.message || reason) }) : out;
    }

    async runGridMulti(symbols, tf, grid, bars) {
      let reason = null;
      if (!this.standaloneMobile) {
        try {
          const res = await fetch(cfg.pythonApi + '/grid-multi', {
            method: 'POST',
            headers: this._authHeaders(),
            body: JSON.stringify({ symbols, tf, grid: grid || undefined }),
          });
          const r = await res.json().catch(() => ({}));
          if (res.ok && r && (r.ok || r.error)) return r;
          reason = new Error('HTTP ' + res.status);
        } catch (e) { reason = e; }
        this._reportFallback('grid-multi', reason, (symbols || []).join(','));
      }
      await sleep(100);
      const syms = (symbols || ['BTCIRT', 'ETHIRT', 'TONIRT', 'SOLIRT', 'BTCUSDT', 'ETHUSDT']).slice(0, 8);
      const syms_no_data = [];
      const results = syms.map((s) => {
        const sym = normSymbol(s);
        const rows = this._getCandlesForBacktest(sym, tf || '1h', bars || 400);
        if (!rows || rows.length < 120) {
          syms_no_data.push(sym);
          return { symbol: sym, unavailable: true, error: 'upstream_unavailable' };
        }
        const g = NBX.Backtest.gridSearch(rows, sym, tf || '1h', grid);
        return { symbol: sym, best: g.best, elapsed_s: g.elapsed_s, combo_count: g.combo_count };
      });
      if (syms_no_data.length) {
        return { ok: false, unavailable: true, error: 'upstream_unavailable',
                 message: NBX.UPSTREAM_MSG[DOC_LANG()], symbols: syms_no_data,
                 detail: reason ? String(reason.message || reason) : 'no candles' };
      }
      const out = { ok: true, tf: tf || '1h', results };
      if (reason) { out.fallback = true; out.fallback_reason = String(reason.message || reason); }
      return out;
    }

    async getBacktestQuick(symbol, tf) {
      return this.runBacktest(symbol || 'BTCIRT', tf || '1h', {});
    }

    async getStats() {
      return this.get('/market/stats');
    }

    async getOrderbook(symbol) {
      return this.get('/v3/orderbook/' + normSymbol(symbol));
    }

    async getOrderbookAll() {
      return this.get('/v3/orderbook/all');
    }

    async getCandles(symbol, timeframe, count) {
      if (this.standaloneMobile || Date.now() < this.circuitOpenUntil) {
        throw new NBXError('UPSTREAM', NBX.UPSTREAM_MSG[DOC_LANG()], { symbol });
      }
      const tfs = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, D: 86400 };
      const tfSec = tfs[timeframe] || 900;
      const to = Math.floor(Date.now() / 1000);
      const from = to - Math.min(count || 260, 300) * tfSec;
      const res = timeframe === 'D' ? 'D' : tfSec === 3600 ? '60' : tfSec === 240 * 60 ? '240' : String(tfSec / 60);
      const q = new URLSearchParams({
        symbol: normSymbol(symbol),
        resolution: String(res),
        from: String(from),
        to: String(to),
      });
      const raw = await this.get('/market/udf/history?' + q.toString(), 1);
      if (!raw || raw.s !== 'ok' || !Array.isArray(raw.c) || raw.c.length === 0) {
        throw new NBXError('DATA', 'bad candles payload for ' + symbol, { raw });
      }
      const rows = raw.t.map((t, i) => ({
        t,
        o: +raw.o[i],
        h: +raw.h[i],
        l: +raw.l[i],
        c: +raw.c[i],
        v: +raw.v[i],
      }));
      rows.sort((a, b) => a.t - b.t);
      return rows;
    }
  }
  NBX.NobitexApi = NobitexApi;

  /* ----------------------------------------------------------
   * 7. Market store + scanner (ADR-0004/0007)
   * --------------------------------------------------------- */
  class MarketEngine {
    constructor(api) {
      this.api = api;
      this.universe = [];
      this.books = {};
      this.candles = {};
      this.signals = {};
      this.premium = null;
      this.premium_pct = null;
      this.onUpdate = [];
      this.scanning = false;
      //: Newest candle open (epoch seconds) **per symbol** in the last scan, and
      //: the timeframe it belongs to. A single `max()` across symbols is what let a
      //: dead feed hide behind twenty-three healthy ones, so the map is kept.
      this.scanBars = {};
      this.scanTf = null;
    }
    subscribe(fn) {
      this.onUpdate.push(fn);
      return () => (this.onUpdate = this.onUpdate.filter((x) => x !== fn));
    }
    emit(type, payload) {
      this.onUpdate.forEach((f) => {
        try { f(type, payload); } catch (e) {}
      });
    }

    async loadUniverse() {
      this.unavailable = false;
      this.unavailable_reason = null;
      if (this.api.standaloneMobile) {
        // No backend and no exchange: say so instead of listing invented coins.
        this.unavailable = true;
        this.unavailable_reason = 'standalone';
        this.universe = [];
        this.emit('universe', []);
        return [];
      }
      try {
        const stats = await this.api.getStats();
        if (stats && stats.stats) {
          // `/market/stats` keys markets `btc-rls`, and quotes rial markets in
          // Rial. `normSymbol` alone turned that into `BTC-RLS` — a market the
          // order book and the chart have never heard of — and left every price
          // ten times its Toman label. Same conversion the backend does
          // (server/units.py), applied here because this path talks to the
          // exchange directly when the backend is down.
          const rows = Object.entries(stats.stats).map(([key, d]) => {
            const symbol = Units.marketSymbol(key);
            if (!symbol) return null;                    // deprecated `global`
            const scale = Units.isRial(symbol) ? 1 / RIAL_PER_TOMAN : 1;
            return {
              symbol,
              last: (+d.latest || +d.last || 0) * scale,
              vol24: (+d.volumeDst || +d.valueDst || +d['24h_volume'] || 0) * scale,
              change24: +d.dayChange || 0,               // a ratio: never scaled
              closed: !!d.isClosed,
            };
          }).filter(Boolean);
          this.universe = rows.filter((r) => r.symbol && r.last > 0);
          this.premium = (rows.find((x) => x.symbol === 'USDTIRT') || {}).last || null;
        }
      } catch (e) {
        this.universe = [];
        this.unavailable = true;
        this.unavailable_reason = (e && e.message) || 'exchange unreachable';
      }
      if (!this.universe.length) {
        this.unavailable = true;
        this.unavailable_reason = this.unavailable_reason || 'exchange returned no rows';
      }
      this.universe = this.universe.sort((a, b) => b.vol24 - a.vol24).slice(0, cfg.universeTopN);
      this._computePremium();
      this.emit('universe', this.universe);
      return this.universe;
    }

    _computePremium() {
      const d = {};
      this.universe.forEach((r) => (d[r.symbol] = r.last));
      if (d.BTCIRT > 0 && d.BTCUSDT > 0 && d.USDTIRT > 0) {
        const implied = d.BTCIRT / d.BTCUSDT;
        if (implied > 0) this.premium_pct = +(((d.USDTIRT / implied) - 1) * 100).toFixed(3);
      }
      return this.premium_pct;
    }

    async scan(timeframe) {
      if (this.scanning) return;
      this.scanning = true;
      const tf = timeframe || cfg.timeframes[0];
      if (!this.universe.length) await this.loadUniverse();
      const failed = [];
      // A scan is a claim about the candles it read, so each symbol's newest stamp
      // is kept: the rows themselves are not (260 per symbol).
      const seen = {};
      for (const u of this.universe) {
        const sym = u.symbol;
        try {
          const rows = await this.api.getCandles(sym, tf, cfg.maxCandles);
          if (!rows || !rows.length) throw new Error('empty candle series');
          this.candles[sym] = rows;
          this.candles[sym + ':' + tf] = rows;
          const lastT = +rows[rows.length - 1].t;
          if (isFinite(lastT)) seen[sym] = lastT;
          this.signals[sym] = NBX.TA.signalScore(rows);
        } catch (e) {
          failed.push(sym);
          delete this.signals[sym];
        }
      }
      this.unavailable = failed.length > 0 && Object.keys(this.signals).length === 0;
      this.unavailable_reason = failed.length ? `${failed.length} symbol(s) had no candles` : null;
      this.scanTf = tf;
      this.scanBars = seen;
      this.scanning = false;
      this.emit('signals', this.rankSignals());
    }

    /* Where the numbers just emitted stand against the clock, in bars of the
     * timeframe they were computed on. Same rule and same vocabulary as the
     * server: `behind`, `ahead-of-clock`, `no-candles`, `current` — and the same
     * aggregation: the worst symbol decides, because a stale row in the list makes
     * the list stale. */
    freshness(tf) {
      const use = tf || this.scanTf || cfg.timeframes[0];
      return NBX.freshness.assessScan(this.scanBars, use);
    }

    rankSignals() {
      // Symbols without a real candle series are absent, not scored at zero:
      // a 0.0 "score" next to a real one reads as a genuine weak signal.
      return this.universe
        .filter((u) => this.signals[u.symbol])
        .map((u) => {
          const s = this.signals[u.symbol];
          const liq = Math.round(clamp(50 + Math.log10((u.vol24 || 1) + 1) * 4, 0, 100));
          // Mirrors python composite_score(ta, liq, fa=None): the two weights
          // are renormalised over their own sum (0.55 + 0.20 = 0.75), not left
          // as fractions of a 1.0 that no longer has four terms.
          const composite = clamp((0.55 * s.score + 0.2 * liq) / 0.75, 0, 100);
          return {
            ...u,
            ...s,
            liquidity_score: liq,
            fa_score: null,
            fa: null,
            composite_basis: 'ta+liq',
            composite: +composite.toFixed(1),
          };
        })
        .sort((a, b) => b.composite - a.composite);
    }
  }
  NBX.MarketEngine = MarketEngine;

  /* ----------------------------------------------------------
   * 8. Facade helpers
   * --------------------------------------------------------- */
  NBX.createCore = function (overrides) {
    const api = new NobitexApi(overrides);
    const engine = new MarketEngine(api);
    return { api, engine };
  };

  NBX.state = { ready: false };
  NBX.boot = async function () {
    if (NBX.state.ready) return NBX.core;
    const core = NBX.createCore();
    NBX.core = core;
    const backend = await core.api.checkBackend();
    if (!backend || !backend.ok) {
      await core.engine.loadUniverse();
      await core.engine.scan(cfg.timeframes[0]);
    }
    NBX.state.ready = true;
    return core;
  };
})();
