'use strict';
global.window = global;
require('/home/user/nobi/nbx-core.js');

let input = '';
process.stdin.on('data', (chunk) => { input += chunk; });
process.stdin.on('end', () => {
  const fixtures = JSON.parse(input);
  const ta = fixtures.map((rows) => {
    const out = NBX.TA.signalScore(rows);
    return {
      score: out.score,
      direction: out.direction,
      regime: out.regime,
      components: out.components,
      diagnostics: out.diagnostics,
      atr: out.risk && out.risk.atr,
    };
  });

  const candles = Array.from({ length: 62 }, (_, i) => ({
    t: 1700000000 + i * 900, o: 100, h: 100.5, l: 99.5, c: 100, v: 1000,
  }));
  // Entry closes at 100 with a 98 stop. The entry candle itself traded at 90,
  // but the signal is only known at its close; the next candle reaches TP1.
  candles[60].l = 90;
  candles[61] = { ...candles[61], o: 100, h: 104, l: 99, c: 103.5 };
  const signals = Array.from({ length: 62 }, () => ({ direction: 'NEUTRAL', score: 50, risk: { atr: 1 } }));
  signals[60] = { direction: 'LONG', score: 80, risk: { atr: 1 } };
  const bt = NBX.Backtest.runWindow(candles, 'TESTUSDT', '15m', {
    initial_equity: 100000, risk_pct: 0.75, atr_stop_k: 2,
    tp1_r: 1.5, tp2_r: 2.5, max_concurrent: 5, slippage_bps: 6,
    _signals: signals,
  });
  console.log(JSON.stringify({ ta, backtest: bt }));
});
