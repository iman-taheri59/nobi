#!/usr/bin/env node
/**
 * Parity check: the browser broker's gates against the Python broker's gates.
 *
 * `nbx-core.js` carries a port of `risk_engine.preflight` because the
 * standalone bundle has no backend to ask. A port that drifts is worse than no
 * port: the phone would admit orders the server refuses. This script feeds the
 * same inputs to both and fails on the first gate that disagrees.
 *
 *   python3 scripts/check_preflight_parity.py
 *
 * Reads a JSON case list on stdin, writes results on stdout.
 */
const fs = require('fs');

const cases = JSON.parse(fs.readFileSync(0, 'utf8'));

global.window = global;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.fetch = async () => { throw new Error('no network in parity check'); };
require(require('path').resolve(__dirname, '..', 'nbx-core.js'));

const NBX = global.NBX;
const out = cases.map((c) => {
  const pf = NBX.Risk.preflight(c);
  return {
    id: c.id,
    blocked: pf.blocked,
    blocked_by: pf.blocked_by,
    risk_pct: pf.risk_pct,
    heat_after: pf.heat_after,
    cash_after: pf.cash_after,
    gates: pf.gates.map((g) => ({ id: g.id, ok: g.ok, value: g.value, limit: g.limit })),
  };
});

// The tables are part of the contract too: a precision that exists in one
// engine and not the other changes what fills.
const t = NBX.Units._tables();
process.stdout.write(JSON.stringify({
  results: out,
  tables: {
    AMOUNT_STEP: t.AMOUNT_STEP,
    PRICE_STEP: t.PRICE_STEP,
    MIN_NOTIONAL_QUOTE: t.MIN_NOTIONAL_QUOTE,
  },
  defaults: NBX.RISK_DEFAULTS,
}));
