#!/usr/bin/env node
/**
 * One candle, two engines, one expected answer (ADR-0009 §3).
 *
 * A bar that touches the stop *and* the first target hides which came first.
 * The backtester resolves it pessimistically; until 1.0.9 the browser resolved
 * it optimistically. `server/tests/test_intrabar_parity.py` runs this script and
 * compares its output with the Python rule, so the two cannot drift apart
 * quietly again.
 *
 *   node scripts/js_engine_parity.js
 */
'use strict';

global.window = global;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.fetch = async () => { throw new Error('no network in test'); };
require(require('path').resolve(__dirname, '..', 'nbx-core.js'));

const NBX = global.NBX;

// 66 flat bars, an injected LONG at 61, then a candle at 65 that spans the stop
// and TP1. Flat candles never score LONG on their own, so the signal series is
// injected — the same hook the Python engine exposes as `signals=`.
const price = 100;
const rows = [];
for (let i = 0; i < 66; i++) {
  rows.push({ t: 1_700_000_000 + i * 3600, o: price, h: price, l: price, c: price, v: 1 });
}
rows[65] = { ...rows[65], h: 110, l: 90 };            // spans stop (97) and TP1 (104.5)
const signals = [];
signals[61] = { direction: 'LONG', score: 80, risk: { atr: 1.5 } };

const w = NBX.Backtest.runWindow(rows, 'XUSDT', '1h', {
  initial_equity: 100_000, risk_pct: 1.0, atr_stop_k: 2.0,
  tp1_r: 1.5, tp2_r: 2.5, slippage_bps: 0, min_score: 0,
  _signals: signals,
});

const trades = w.sample_trades || [];
const last = trades.length ? trades[trades.length - 1] : {};
console.log(JSON.stringify({
  browser: {
    trades: w.trades,
    ambiguous_exits: w.ambiguous_exits,
    reason: last.reason || null,
    r_multiple: typeof last.r === 'number' ? last.r : null,
  },
  server: { reason: 'sl-ambiguous' },   // the rule the Python engine implements
}, null, 2));
