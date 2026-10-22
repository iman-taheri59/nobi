#!/usr/bin/env node
/**
 * Behaviour of the standalone (browser) paper broker.
 *
 * The gate-port itself is checked against Python by
 * `scripts/check_preflight_parity.py`; this file checks the parts the parity
 * script cannot see — unit handling, precision, FX, and what ends up on the
 * position — because those are what a user actually sees on the phone.
 *
 *   node scripts/js_broker_test.js        # exit 1 on the first failure
 */
'use strict';

global.window = global;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.fetch = async () => { throw new Error('no network in test'); };
require(require('path').resolve(__dirname, '..', 'nbx-core.js'));

const NBX = global.NBX;
let failures = 0;

function check(name, fn) {
  try {
    const msg = fn();
    console.log(`ok   ${name}${msg ? ' — ' + msg : ''}`);
  } catch (e) {
    failures += 1;
    console.log(`FAIL ${name} — ${e.message}`);
  }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }
function near(a, b, tol) { return Math.abs(a - b) <= (tol === undefined ? 1e-6 : tol); }

function reset() {
  const P = NBX.Paper;
  P.positions.length = 0;
  P.closed.length = 0;
  P.cash = P.start_equity;
  P.equity = P.start_equity;
  P.openHeat = 0;
  P.halted = false;
  P.closedMarkets = new Set();
  NBX.core = { engine: { universe: [] } };
  return P;
}
function withFx(rate) {
  NBX.core = { engine: { universe: [{ symbol: 'USDTIRT', last: rate }], premium: rate } };
}

/* 1. No USDTIRT price ⇒ an IRT order cannot be valued and must be refused,
 *    not silently treated as dollars. */
check('IRT order without an FX rate is refused', () => {
  const P = reset();
  const r = P.placeOrder({ symbol: 'BTCIRT', side: 'buy', qty: 0.001, price: 498_500_000 });
  assert(r.ok === false, 'order was accepted without an FX rate');
  assert(r.error === 'no-fx-rate', `wrong error: ${r.error}`);
  assert(P.positions.length === 0, 'a position was created anyway');
  return r.error;
});

/* 2. With the rate, the ledger moves in USD — 498,500 Toman is $4.95, not
 *    498,500 dollars and not 4.98 billion Rial. */
check('IRT notional is converted to USD before it touches cash', () => {
  const P = reset();
  withFx(100_805);
  const r = P.placeOrder({ symbol: 'BTCIRT', side: 'buy', qty: 0.001, price: 498_500_000,
                           stop: 480_000_000 });
  assert(r.ok === true, `refused: ${r.error}`);
  const usd = 498_500 / 100_805;
  assert(near(r.position.size_usd, usd, 1e-6), `size_usd ${r.position.size_usd} != ${usd}`);
  assert(near(P.cash, 100_000 - usd * 1.002, 0.01), `cash ${P.cash}`);
  assert(r.position.quote === 'IRT' && near(r.position.quote_usd, 100_805), 'quote metadata');
  return `size_usd=${r.position.size_usd.toFixed(4)}`;
});

/* 3. The documented exchange minimum, in the market's own quote currency. */
check('order below the exchange minimum is refused (11 USDT)', () => {
  const P = reset();
  const r = P.placeOrder({ symbol: 'BTCUSDT', side: 'buy', qty: 0.0001, price: 73_040, stop: 70_000 });
  assert(r.ok === false, 'accepted an order under the minimum');
  assert(r.blocked_by === 'min-notional', `blocked_by ${r.blocked_by}`);
  return r.error;
});

check('order below the exchange minimum is refused (3,000,000 Rial)', () => {
  const P = reset();
  withFx(100_805);
  // 100,000 Toman = 1,000,000 Rial, a third of the minimum.
  const r = P.placeOrder({ symbol: 'BTCIRT', side: 'buy', qty: 0.0002, price: 498_500_000,
                           stop: 480_000_000 });
  assert(r.ok === false && r.blocked_by === 'min-notional', `got ${r.blocked_by}`);
  return r.error;
});

/* 4. A market the exchange reports as closed. */
check('closed market is refused', () => {
  const P = reset();
  P.closedMarkets = new Set(['BTCIRT']);
  withFx(100_805);
  const r = P.placeOrder({ symbol: 'BTCIRT', side: 'buy', qty: 0.01, price: 498_500_000,
                           stop: 480_000_000 });
  assert(r.ok === false && r.blocked_by === 'market-closed', `got ${r.blocked_by}`);
  return r.error;
});

/* 5. Precision: truncate the amount, round the price to the tick. */
check('amount truncates and price rounds before the fill', () => {
  const P = reset();
  const r = P.placeOrder({ symbol: 'BTCUSDT', side: 'buy', qty: 0.00023456789,
                           price: 73_040.004, stop: 70_000 });
  assert(r.ok === true, `refused: ${r.error}`);
  assert(near(r.position.filled_qty, 0.000234, 1e-12), `qty ${r.position.filled_qty}`);
  assert(near(r.position.entry_price, 73_040, 1e-9), `price ${r.position.entry_price}`);
  return `qty=${r.position.filled_qty} price=${r.position.entry_price}`;
});

/* 6. Equity marks a persisted IRT position with its own entry rate, so a
 *    missing live rate cannot multiply the account by 100,000. */
check('equity stays sane when the live FX rate disappears', () => {
  const P = reset();
  withFx(100_805);
  const r = P.placeOrder({ symbol: 'BTCIRT', side: 'buy', qty: 0.001, price: 498_500_000,
                           stop: 480_000_000 });
  assert(r.ok === true, `refused: ${r.error}`);
  NBX.core = { engine: { universe: [] } };          // the rate is gone
  P._markEquity();
  assert(near(P.equity, 100_000, 0.5), `equity jumped to ${P.equity}`);
  return `equity=${P.equity.toFixed(2)}`;
});

/* 7. Same ten gates as the server — spot-check the two that used to be missing. */
check('preflight exposes ten gates, including the exchange rules', () => {
  const P = reset();
  withFx(100_805);
  const pf = P.preflightOrder({ symbol: 'BTCIRT', side: 'buy', qty: 0.01,
                                price: 498_500_000, stop: 480_000_000 });
  const ids = pf.projected.gates.map((g) => g.id);
  assert(ids.length === 10, `expected 10 gates, got ${ids.length}: ${ids.join(',')}`);
  for (const need of ['halted', 'market-closed', 'concurrent-cap', 'cash-present',
                      'single-asset-cap', 'min-notional', 'risk-cap', 'heat-cap',
                      'min-cash', 'funds']) {
    assert(ids.includes(need), `missing gate ${need}`);
  }
  return ids.join(',');
});

/* 8. Stats keys: `btc-rls` is not a market, and its price is in Rial. */
check('stats keys become symbols and Rial becomes Toman', () => {
  const U = NBX.Units;
  assert(U.marketSymbol('btc-rls') === 'BTCIRT', 'btc-rls');
  assert(U.marketSymbol('usdt-rls') === 'USDTIRT', 'usdt-rls');
  assert(U.marketSymbol('sol-usdt') === 'SOLUSDT', 'sol-usdt');
  assert(U.marketSymbol('global') === null, 'global should be dropped');
  return 'btc-rls→BTCIRT, global→null';
});

/* 9. Without a backend the exits come from the same ladder the live plan uses.
 *    They used to be three percentages (1.045 / 1.075) that happened to sit near
 *    1.2R and 2.0R against a 3.8 % stop; now the relationship is the definition,
 *    so this test can state it instead of approximating it. */
check('fallback exits are the live RR ladder against the stop distance', () => {
  const seen = [];
  for (const side of ['buy', 'sell']) {
    const P = reset();
    withFx(1);
    const px = 73_040;
    const r = P.placeOrder({ symbol: 'BTCUSDT', side, qty: 0.01, price: px });
    assert(r.ok, `placeOrder(${side}) refused: ${r.error || ''} ${r.blocked_by || ''}`);
    const pos = P.positions[0];
    assert(pos, `no position for ${side}`);
    const dist = Math.abs(px - pos.stop);
    assert(dist > 0, 'the fallback stop equals the entry price');
    const rr = (x) => Math.abs(x - px) / dist;
    assert(near(rr(pos.tp1), 1.2, 1e-9), `${side} tp1 is ${rr(pos.tp1).toFixed(4)}R, expected 1.2R`);
    assert(near(rr(pos.tp2), 2.0, 1e-9), `${side} tp2 is ${rr(pos.tp2).toFixed(4)}R, expected 2.0R`);
    seen.push(`${side}: tp1 ${rr(pos.tp1).toFixed(2)}R tp2 ${rr(pos.tp2).toFixed(2)}R`);
  }
  return seen.join(', ');
});

/* 10. The offline walk-forward pays the same costs the server does. A browser
 *     report that fills at the exact price would be an optimistic report the
 *     page then presents as a promotion-gate result. */
check('offline backtest models slippage and says so in its gate', () => {
  const candles = [];
  let px = 100;
  for (let i = 0; i < 400; i++) {
    px = Math.max(1, px * (1 + Math.sin(i / 7) * 0.01));
    candles.push({ t: 1_700_000_000 + i * 3600, o: px, h: px * 1.01, l: px * 0.99, c: px, v: 10 });
  }
  const rep = NBX.Backtest.runWalkForward(candles, 'BTCUSDT', '1h', { equity: 50_000, risk_pct: 0.75 });
  assert(rep.slippage_bps === 6, `default slippage is ${rep.slippage_bps}, expected 6`);
  assert(rep.costs && rep.costs.modeled === true, 'report does not claim a cost model');
  const free = NBX.Backtest.runWalkForward(candles, 'BTCUSDT', '1h',
    { equity: 50_000, risk_pct: 0.75, slippage_bps: 0 });
  assert(free.gate.checks.costs_modeled.pass === false, 'a frictionless run passed the cost check');
  assert(free.gate.failed.includes('costs_modeled'), 'the failing cost check is not named');
  // Not a direction test: on a 400-bar synthetic series the trade set itself
  // changes with the cost, so the two expectancies are not comparable. The
  // direction is asserted on the server side (test_backtest_costs.py) where
  // the sample is large enough for it to mean something.
  assert(free.config_hash !== rep.config_hash, 'the cost model is missing from the config hash');
  return `default ${rep.slippage_bps} bps, frictionless gate state ${free.gate.state}`;
});

console.log(failures ? `\n${failures} failure(s)` : '\nPASS — standalone broker behaves like the server');
process.exit(failures ? 1 : 0);
