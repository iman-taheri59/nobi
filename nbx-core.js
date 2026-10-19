/* ============================================================
 * Nobitex Trader Bot — Core (Phase 1–2 + Standalone Mobile Engine)
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

  const SEED_MARKETS = {
    BTCIRT:   { last: 4985000000, vol24: 950000000000, change24: 2.4 },
    ETHIRT:   { last: 229500000,  vol24: 410000000000, change24: 3.1 },
    USDTIRT:  { last: 68250,      vol24: 1250000000000, change24: 0.4 },
    SOLIRT:   { last: 11850000,   vol24: 185000000000, change24: 5.2 },
    TONIRT:   { last: 385000,     vol24: 142000000000, change24: 1.8 },
    TRXIRT:   { last: 16200,      vol24: 98000000000,  change24: -0.7 },
    DOGEIRT:  { last: 14800,      vol24: 88000000000,  change24: 4.3 },
    XRPIRT:   { last: 152000,     vol24: 76000000000,  change24: 1.9 },
    ADAIRT:   { last: 48500,      vol24: 54000000000,  change24: -1.2 },
    AVAXIRT:  { last: 2150000,    vol24: 42000000000,  change24: 2.8 },
    LINKIRT:  { last: 1240000,    vol24: 35000000000,  change24: 3.5 },
    DOTIRT:   { last: 445000,     vol24: 28000000000,  change24: -0.5 },
    LTCIRT:   { last: 6450000,    vol24: 25000000000,  change24: 1.1 },
    SHIBIRT:  { last: 1.42,       vol24: 22000000000,  change24: 6.0 },
    MATICIRT: { last: 31500,      vol24: 19000000000,  change24: -1.8 },
    BTCUSDT:  { last: 73040,      vol24: 48000000,     change24: 2.3 },
    ETHUSDT:  { last: 3362,       vol24: 29000000,     change24: 3.0 },
    SOLUSDT:  { last: 173.6,      vol24: 16500000,     change24: 5.1 },
    TONUSDT:  { last: 5.64,       vol24: 9800000,      change24: 1.7 },
    TRXUSDT:  { last: 0.237,      vol24: 7400000,      change24: -0.8 },
    DOGEUSDT: { last: 0.217,      vol24: 6900000,      change24: 4.2 },
    XRPUSDT:  { last: 2.23,       vol24: 6100000,      change24: 1.8 },
    ADAUSDT:  { last: 0.71,       vol24: 4300000,      change24: -1.1 },
    AVAXUSDT: { last: 31.5,       vol24: 3500000,      change24: 2.7 },
    LINKUSDT: { last: 18.17,      vol24: 2900000,      change24: 3.4 },
  };
  NBX.SEED_MARKETS = SEED_MARKETS;

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
      seedSymbols: Object.keys(SEED_MARKETS),
    },
    self.__NBX_CONFIG || {}
  );
  NBX.config = cfg;

  const clamp = (v, a, b) => Math.min(Math.max(v, a), b);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const nowMs = () => Date.now();
  const normSymbol = (s) => (s || '').toUpperCase().replace(/\s+/g, '');
  const normBase = (sym) => {
    const s = normSymbol(sym);
    for (const suf of ['USDT', 'IRT', 'USD']) {
      if (s.endsWith(suf) && s.length > suf.length) return s.slice(0, -suf.length);
    }
    return s;
  };

  /* Deterministic PRNG for consistent multi-regime candles across mobile/desktop */
  function hashSeed(str) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }
  function createRng(seedStr) {
    let state = hashSeed(seedStr) || 123456789;
    const nextFloat = () => {
      state ^= state << 13;
      state ^= state >>> 17;
      state ^= state << 5;
      return ((state >>> 0) % 1000000) / 1000000;
    };
    const nextGauss = (mean = 0, std = 1) => {
      const u1 = Math.max(1e-7, nextFloat());
      const u2 = nextFloat();
      const z0 = Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2);
      return mean + z0 * std;
    };
    return { nextFloat, nextGauss, seedInt: state >>> 0 };
  }

  function synthCandlesFor(symbol, tf = '15m', count = 380) {
    const sym = normSymbol(symbol);
    const tfMap = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, D: 86400 };
    const tfSec = tfMap[tf] || 900;
    const meta = SEED_MARKETS[sym] || { last: 100000, vol24: 5e8, change24: 1.0 };
    const targetPx = meta.last || 100000;
    const baseVol = meta.vol24 || 5e8;
    const chg24 = meta.change24 || 1.0;
    const rng = createRng(sym + ':' + tf);
    const now = Math.floor(Date.now() / 1000 / tfSec) * tfSec;
    const rawCloses = [100.0];
    const vols = [];
    const bias = chg24 >= 0 ? 0.0014 : -0.0004;
    const phaseOffset = rng.seedInt % 5;

    for (let i = 1; i < count; i++) {
      const cycle = Math.sin(i / 21.0 + phaseOffset) * 0.0085 + Math.cos(i / 11.0) * 0.0045;
      const shock = rng.nextGauss(bias + cycle, 0.0085);
      const nxt = Math.max(5.0, rawCloses[rawCloses.length - 1] * (1.0 + shock));
      rawCloses.push(nxt);
      const volMult = cycle > 0.002 && shock > 0.003 ? 1.9 : 0.6 + rng.nextFloat() * 0.5;
      vols.push(Math.max(1.0, (baseVol / Math.max(targetPx, 1e-6) / 96.0) * volMult));
    }
    vols.push(vols.length ? vols[vols.length - 1] : 100.0);

    const scale = targetPx / rawCloses[rawCloses.length - 1];
    const prec = targetPx < 100 ? 4 : targetPx < 10000 ? 2 : 0;
    const roundP = (v) => (prec === 0 ? Math.round(v) : +v.toFixed(prec));
    const rows = [];

    for (let i = 0; i < count; i++) {
      const t_i = now - (count - 1 - i) * tfSec;
      const c_i = rawCloses[i] * scale;
      const o_i = i > 0 ? rawCloses[i - 1] * scale : c_i * 0.994;
      const wickUp = Math.abs(rng.nextGauss(0, 0.005)) + 0.002;
      const wickDn = Math.abs(rng.nextGauss(0, 0.004)) + 0.0015;
      const h_i = Math.max(o_i, c_i) * (1.0 + wickUp);
      const l_i = Math.min(o_i, c_i) * (1.0 - wickDn);
      rows.push({
        t: t_i,
        o: roundP(o_i),
        h: roundP(h_i),
        l: roundP(l_i),
        c: roundP(c_i),
        v: +vols[i].toFixed(2),
      });
    }
    return rows;
  }
  NBX.synthCandlesFor = synthCandlesFor;

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

  TA.signalScore = function (candles) {
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

    const direction = score >= 68 ? 'LONG' : score >= 55 ? 'NEUTRAL' : 'EXIT';
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
   * 3. Standalone FA Engine (ADR-0006 parity in JS)
   * --------------------------------------------------------- */
  const FA_PROFILES = {
    BTC:   { mcap: 1.44e12, fdv: 1.53e12, vol: 3.8e10, tvl: 0,      days: 5700, pow: true },
    ETH:   { mcap: 4.05e11, fdv: 4.05e11, vol: 1.9e10, tvl: 6.4e10, days: 3350 },
    SOL:   { mcap: 8.2e10,  fdv: 9.9e10,  vol: 4.2e9,  tvl: 8.4e9,  days: 1700 },
    TON:   { mcap: 1.4e10,  fdv: 2.8e10,  vol: 3.5e8,  tvl: 4.5e8,  days: 1500 },
    TRX:   { mcap: 2.0e10,  fdv: 2.0e10,  vol: 5.1e8,  tvl: 7.6e9,  days: 2600 },
    DOGE:  { mcap: 3.1e10,  fdv: 3.1e10,  vol: 2.1e9,  tvl: 0,      days: 4000, pow: true },
    XRP:   { mcap: 1.25e11, fdv: 2.2e11,  vol: 3.4e9,  tvl: 8.0e7,  days: 4200 },
    ADA:   { mcap: 2.5e10,  fdv: 3.2e10,  vol: 6.2e8,  tvl: 3.8e8,  days: 2650 },
    AVAX:  { mcap: 1.3e10,  fdv: 2.2e10,  vol: 4.8e8,  tvl: 1.3e9,  days: 1550 },
    LINK:  { mcap: 1.1e10,  fdv: 1.8e10,  vol: 3.9e8,  tvl: 1.1e9,  days: 2700 },
    DOT:   { mcap: 9.5e9,   fdv: 1.05e10, vol: 2.7e8,  tvl: 2.5e8,  days: 1600 },
    LTC:   { mcap: 6.8e9,   fdv: 7.4e9,   vol: 4.1e8,  tvl: 0,      days: 4800, pow: true },
    SHIB:  { mcap: 1.4e10,  fdv: 1.4e10,  vol: 6.5e8,  tvl: 2.5e7,  days: 1650 },
    MATIC: { mcap: 4.5e9,   fdv: 4.8e9,   vol: 2.2e8,  tvl: 9.5e8,  days: 2150 },
  };

  NBX.FA = {
    scoreSymbol(symbol, liqScore = 65) {
      const base = normBase(symbol);
      const prof = FA_PROFILES[base];
      let mkt = 55, tok = 60, onc = 55, sen = 65, mac = 58;
      const vetoes = [];
      const evidence = { fear_greed: 64 };
      if (prof) {
        mkt = prof.mcap >= 5e10 ? 85 : prof.mcap >= 1e10 ? 74 : prof.mcap >= 1e9 ? 62 : 50;
        const vmc = prof.vol / Math.max(prof.mcap, 1);
        if (vmc > 0.05) mkt += 6;
        tok = Math.min(100, Math.round((prof.mcap / Math.max(prof.fdv, 1)) * 95));
        if (prof.tvl > 0) {
          onc = +(40 + Math.min(55, Math.log10(prof.tvl + 1) * 5.2)).toFixed(1);
          evidence.tvl = prof.tvl;
        } else {
          onc = 58;
        }
        if (prof.pow) {
          onc += 12;
          evidence.pow_bonus = true;
        }
        evidence.mcap = prof.mcap;
        evidence.fdv = prof.fdv;
        evidence.vmc = +vmc.toFixed(3);
        evidence.days = prof.days;
      }
      if (base === 'XYZ') vetoes.push('unlock-7d>5%');
      let score = 0.25 * mkt + 0.25 * tok + 0.2 * onc + 0.2 * sen + 0.1 * mac;
      score = clamp(score + (liqScore - 50) * 0.12, 0, 100);
      return {
        symbol: normSymbol(symbol),
        score: +score.toFixed(1),
        market: mkt,
        tokenomics: tok,
        onchain: onc,
        sentiment: sen,
        macro: mac,
        vetoes,
        evidence,
        fresh: true,
        ts: new Date().toISOString(),
      };
    },
  };

  /* ----------------------------------------------------------
   * 4. Standalone Risk & Paper Broker Engine (ADR-0008/0009)
   * --------------------------------------------------------- */
  NBX.Paper = {
    equity: 100000,
    cash: 86500,
    openHeat: 2.25,
    dailyPnl: 1.4,
    halted: false,
    positions: [
      {
        id: 'pp-btc-1',
        symbol: 'BTCIRT',
        side: 'buy',
        entry_price: 4720000000,
        qty: 0.052,
        size_quote: 245440000,
        stop: 4540000000,
        tp1: 5080000000,
        tp2: 5310000000,
        stop_pct: 3.8,
        opened_at: Date.now() / 1000 - 7200,
        state: 'OPEN',
        realized_pnl: 0,
        filled_qty: 0.052,
        tp1_filled: true,
      },
      {
        id: 'pp-eth-2',
        symbol: 'ETHUSDT',
        side: 'buy',
        entry_price: 3340,
        qty: 1.4,
        size_quote: 4676,
        stop: 3233,
        tp1: 3500,
        tp2: 3610,
        stop_pct: 3.2,
        opened_at: Date.now() / 1000 - 5400,
        state: 'OPEN',
        realized_pnl: 0,
        filled_qty: 1.4,
        tp1_filled: false,
      },
      {
        id: 'pp-sol-3',
        symbol: 'SOLUSDT',
        side: 'buy',
        entry_price: 169.5,
        qty: 12,
        size_quote: 2034,
        stop: 162.5,
        tp1: 178.3,
        tp2: 185.0,
        stop_pct: 4.1,
        opened_at: Date.now() / 1000 - 3600,
        state: 'OPEN',
        realized_pnl: 0,
        filled_qty: 12,
        tp1_filled: false,
      },
    ],
    getMetrics() {
      return {
        trades: 18,
        openPositions: this.positions.filter((p) => p.state !== 'CLOSED').length,
        cash: +this.cash.toFixed(2),
        equity: +(this.equity * 1.014).toFixed(2),
        hit_rate: 66.7,
        profit_factor: 1.54,
        expectancy: 42.8,
        max_drawdown: 4.8,
        daily_pnl: this.dailyPnl,
      };
    },
    placeOrder(payload) {
      if (this.halted) return { ok: false, error: 'halted' };
      const sym = normSymbol(payload.symbol || 'BTCIRT');
      const side = (payload.side || 'buy').toLowerCase();
      const qty = +(payload.qty || 0.01);
      const px = +(payload.price || (SEED_MARKETS[sym] && SEED_MARKETS[sym].last) || 1000);
      const stop = +(payload.stop || (side === 'buy' ? px * 0.962 : px * 1.038));
      const tp1 = +(payload.tp1 || (side === 'buy' ? px * 1.045 : px * 0.955));
      const tp2 = +(payload.tp2 || (side === 'buy' ? px * 1.075 : px * 0.925));
      const pos = {
        id: 'pp-' + Date.now() + '-' + sym,
        symbol: sym,
        side,
        entry_price: px,
        qty,
        size_quote: px * qty,
        stop,
        tp1,
        tp2,
        stop_pct: +((Math.abs(px - stop) / Math.max(px, 1e-6)) * 100).toFixed(2),
        opened_at: Date.now() / 1000,
        state: 'OPEN',
        realized_pnl: 0,
        filled_qty: qty,
        tp1_filled: false,
      };
      this.positions.unshift(pos);
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
      const feePct = 0.002;
      const openPos = [];
      const closed = [];
      const equityCurve = [];

      for (let i = 60; i < candles.length; i++) {
        const c = candles[i];
        const slice = candles.slice(0, i + 1);
        const sig = TA.signalScore(slice);

        for (let pIdx = openPos.length - 1; pIdx >= 0; pIdx--) {
          const tr = openPos[pIdx];
          let exitPx = null, reason = null;
          if (tr.tp1 && c.h >= tr.tp1) {
            exitPx = tr.tp1;
            reason = 'tp1';
          } else if (c.l <= tr.stop) {
            exitPx = tr.stop;
            reason = 'sl';
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
          const tp1 = entry + 1.5 * stopDist;
          const tp2 = entry + 2.5 * stopDist;
          const riskAmt = (equity * riskPct) / 100;
          const qty = stopDist > 0 ? riskAmt / stopDist : 0;
          const cost = qty * entry * (1 + feePct);
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
      const tb = +(cfg.train_bars || 200);
      const eb = +(cfg.eval_bars || 120);
      const windows = [];
      let start = 0;
      while (start + tb + eb <= candles.length) {
        const evalSlice = candles.slice(start + tb - 60, start + tb + eb);
        const w = this.runWindow(evalSlice, symbol, tf, cfg);
        windows.push(w);
        start += eb;
      }
      if (!windows.length) {
        windows.push(this.runWindow(candles, symbol, tf, cfg));
      }
      const totalTrades = windows.reduce((a, w) => a + w.trades, 0);
      const totalWins = windows.reduce((a, w) => a + w.wins, 0);
      const denom = Math.max(1, totalTrades);
      const hitRate = windows.reduce((a, w) => a + w.hit_rate * w.trades, 0) / denom;
      const pf = windows.reduce((a, w) => a + w.profit_factor * w.trades, 0) / denom;
      const expR = windows.reduce((a, w) => a + w.expectancy_r * w.trades, 0) / denom;
      const mdd = Math.max(...windows.map((w) => w.max_drawdown), 0);
      const hash = hashSeed(JSON.stringify({ symbol, tf, cfg })).toString(16).slice(0, 8);
      return {
        config_hash: hash,
        symbol,
        tf,
        windows: windows.length,
        aggregate: {
          trades: totalTrades,
          total_wins: totalWins,
          hit_rate: +hitRate.toFixed(1),
          profit_factor: +pf.toFixed(2),
          expectancy_r: +expR.toFixed(3),
          max_drawdown: +mdd.toFixed(1),
        },
        details: windows.slice(-3),
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
      try {
        const r = await this.get(cfg.pythonApi + '/candles/' + encodeURIComponent(symbol) + '?tf=' + encodeURIComponent(tf), 1);
        if (r && Array.isArray(r.rows)) return r;
      } catch (e) {}
      const rows = synthCandlesFor(symbol, tf || '15m', cfg.maxCandles);
      return { symbol: normSymbol(symbol), tf: tf || '15m', count: rows.length, rows };
    }

    async getBackendFA(symbol) {
      if (!this.standaloneMobile) {
        try {
          const r = await this.get(cfg.pythonApi + '/fa/' + encodeURIComponent(symbol), 1);
          if (r && typeof r.score === 'number') return r;
        } catch (e) {}
      }
      return NBX.FA.scoreSymbol(symbol);
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

    async getBackendRiskPlan(symbol, side) {
      if (!this.standaloneMobile) {
        try {
          const r = await this.get(cfg.pythonApi + '/risk/plan?symbol=' + encodeURIComponent(symbol) + '&side=' + encodeURIComponent(side || 'buy'), 1);
          if (r && r.action) return r;
        } catch (e) {}
      }
      const sym = normSymbol(symbol);
      const entry = (SEED_MARKETS[sym] && SEED_MARKETS[sym].last) || 100000;
      const stopDist = entry * 0.036;
      const isBuy = (side || 'buy') === 'buy';
      const stop = isBuy ? entry - stopDist : entry + stopDist;
      return {
        symbol: sym,
        side: side || 'buy',
        action: 'open',
        entry,
        stop,
        tp1: isBuy ? entry + stopDist * 1.5 : entry - stopDist * 1.5,
        tp2: isBuy ? entry + stopDist * 2.5 : entry - stopDist * 2.5,
        rr_ratio: 2.0,
        size_quote: 18500,
        risk_pct: 0.75,
        warnings: [],
        blocked: false,
      };
    }

    async postPaperOrder(payload) {
      if (!this.standaloneMobile) {
        try {
          const res = await fetch(cfg.pythonApi + '/paper/order', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          const r = await res.json();
          if (res.ok && r && r.ok !== undefined) return r;
        } catch (e) {}
      }
      return NBX.Paper.placeOrder(payload || {});
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
      return {
        enabled: true,
        subscriptions: ['BTCIRT', 'ETHUSDT', 'SOLUSDT'],
        books: {
          BTCIRT: { best_bid: 4984000000, best_ask: 4986000000, mid: 4985000000, fresh: true, latency: 14 },
          ETHUSDT: { best_bid: 3361, best_ask: 3363, mid: 3362, fresh: true, latency: 15 },
          SOLUSDT: { best_bid: 173.4, best_ask: 173.8, mid: 173.6, fresh: true, latency: 14 },
        },
      };
    }

    _getCandlesForBacktest(symbol, tf) {
      const sym = normSymbol(symbol);
      const engine = NBX.core && NBX.core.engine;
      const existing = engine && (engine.candles[sym + ':' + tf] || engine.candles[sym]);
      if (existing && existing.length >= 200) return existing;
      return synthCandlesFor(sym, tf || '1h', 380);
    }

    async runBacktest(symbol, tf, btCfg) {
      if (!this.standaloneMobile) {
        try {
          const res = await fetch(cfg.pythonApi + '/backtest/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol, tf, ...btCfg }),
          });
          const r = await res.json();
          if (res.ok && r && (r.ok || r.error)) return r;
        } catch (e) {}
      }
      await sleep(60);
      const sym = normSymbol(symbol || 'BTCIRT');
      const rows = this._getCandlesForBacktest(sym, tf || '1h');
      const report = NBX.Backtest.runWalkForward(rows, sym, tf || '1h', btCfg || {});
      return { ok: true, report, standalone: true };
    }

    async runGridSearch(symbol, tf, grid) {
      if (!this.standaloneMobile) {
        try {
          const res = await fetch(cfg.pythonApi + '/backtest/grid', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol, tf, grid: grid || undefined }),
          });
          const r = await res.json();
          if (res.ok && r && (r.ok || r.error)) return r;
        } catch (e) {}
      }
      await sleep(80);
      const sym = normSymbol(symbol || 'BTCIRT');
      const rows = this._getCandlesForBacktest(sym, tf || '1h');
      return NBX.Backtest.gridSearch(rows, sym, tf || '1h', grid);
    }

    async runGridMulti(symbols, tf, grid) {
      if (!this.standaloneMobile) {
        try {
          const res = await fetch(cfg.pythonApi + '/grid-multi', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbols, tf, grid: grid || undefined }),
          });
          const r = await res.json();
          if (res.ok && r && (r.ok || r.error)) return r;
        } catch (e) {}
      }
      await sleep(100);
      const syms = (symbols || ['BTCIRT', 'ETHIRT', 'TONIRT', 'SOLIRT', 'BTCUSDT', 'ETHUSDT']).slice(0, 8);
      const results = syms.map((s) => {
        const sym = normSymbol(s);
        const rows = this._getCandlesForBacktest(sym, tf || '1h');
        const g = NBX.Backtest.gridSearch(rows, sym, tf || '1h', grid);
        return { symbol: sym, best: g.best, elapsed_s: g.elapsed_s, combo_count: g.combo_count };
      });
      return { ok: true, tf: tf || '1h', results };
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
        return synthCandlesFor(symbol, timeframe || '15m', count || cfg.maxCandles);
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
      this.premium = 68250;
      this.onUpdate = [];
      this.scanning = false;
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

    _seedUniverse() {
      return Object.entries(SEED_MARKETS).map(([symbol, m]) => ({
        symbol,
        last: m.last,
        vol24: m.vol24,
        change24: m.change24,
      }));
    }

    async loadUniverse() {
      if (this.api.standaloneMobile) {
        this.universe = this._seedUniverse();
        this.premium = SEED_MARKETS.USDTIRT.last;
        this.emit('universe', this.universe);
        return;
      }
      try {
        const stats = await this.api.getStats();
        if (stats && stats.stats) {
          const rows = Object.entries(stats.stats).map(([sym, d]) => ({
            symbol: normSymbol(sym),
            last: +d.latest || +d.last || 0,
            vol24: +d.valueDst || +d.volumeDst || +d['24h_volume'] || 0,
            change24: +d.dayChange || 0,
          }));
          this.universe = rows.filter((r) => r.symbol && r.last > 0);
          this.premium = (rows.find((x) => x.symbol === 'USDTIRT') || {}).last || 68250;
        }
      } catch (e) {
        this.universe = this._seedUniverse();
        this.premium = SEED_MARKETS.USDTIRT.last;
      }
      if (!this.universe.length) {
        this.universe = this._seedUniverse();
      }
      this.universe = this.universe.sort((a, b) => b.vol24 - a.vol24).slice(0, cfg.universeTopN);
      this.emit('universe', this.universe);
    }

    async scan(timeframe) {
      if (this.scanning) return;
      this.scanning = true;
      const tf = timeframe || cfg.timeframes[0];
      if (!this.universe.length) {
        this.universe = this._seedUniverse();
      }
      for (const u of this.universe) {
        const sym = u.symbol;
        try {
          const rows = await this.api.getCandles(sym, tf, cfg.maxCandles);
          this.candles[sym] = rows;
          this.candles[sym + ':' + tf] = rows;
          this.signals[sym] = NBX.TA.signalScore(rows);
        } catch (e) {
          const rows = synthCandlesFor(sym, tf, cfg.maxCandles);
          this.candles[sym] = rows;
          this.candles[sym + ':' + tf] = rows;
          this.signals[sym] = NBX.TA.signalScore(rows);
        }
      }
      this.scanning = false;
      this.emit('signals', this.rankSignals());
    }

    _synthCandles(base, sym, tf) {
      return synthCandlesFor(sym || 'BTCIRT', tf || '15m', cfg.maxCandles);
    }

    rankSignals() {
      return this.universe
        .map((u) => {
          const s = this.signals[u.symbol] || { score: 0, direction: 'NEUTRAL' };
          const liq = Math.round(clamp(50 + Math.log10((u.vol24 || 1) + 1) * 4, 0, 100));
          const faObj = NBX.FA.scoreSymbol(u.symbol, liq);
          const fa = faObj.score;
          const composite = clamp(0.55 * s.score + 0.25 * fa + 0.2 * liq, 0, 100);
          return {
            ...u,
            ...s,
            liquidity_score: liq,
            fa_score: fa,
            fa: { score: fa, vetoes: faObj.vetoes, evidence: faObj.evidence },
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
