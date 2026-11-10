#!/usr/bin/env node
'use strict';
// Independent expected vectors, not merely two copies of the same formula.
const path = require('path');
const fs = require('fs');
const { execFileSync } = require('child_process');
global.window = global;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.fetch = async () => { throw new Error('network forbidden in tick test'); };
require(path.resolve(__dirname, '..', 'nbx-core.js'));
const U = global.NBX.Units;
const vectors = [
  ['BTCIRT', '10', 490000000.5, 1, 490000001],
  ['DOGEIRT', '1', 21695.45, 0.1, 21695.5],
  ['DOGEIRT', '1', 21695.44, 0.1, 21695.4],
  ['DOGEIRT', '0.07', 0.0105, 0.007, 0.014],
  ['XRPIRT', '0.01', 20.0005, 0.001, 20.001],
  ['DOGEUSDT', '0.01', 1.005, 0.01, 1.01],
  ['DOGEUSDT', '0.000001', 1.2345675, 0.000001, 1.234568],
  ['BTCUSDT', '0.01', 73000.004, 0.01, 73000],
  ['TESTUSDT', '1e-8', 1.5e-8, 1e-8, 2e-8],
];
const root = path.resolve(__dirname, '..');
const candidates = [path.join(root, '.venv', 'bin', 'python'), path.join(root, '.venv', 'Scripts', 'python.exe')];
const python = process.env.PYTHON || candidates.find((p) => fs.existsSync(p)) || 'python3';
const program = `import sys,json
sys.path.insert(0,'server')
import units
out=[]
for symbol,raw,price,step,expected in json.load(sys.stdin):
 units.reset_documented_defaults()
 units.apply_options({'nobitex':{'pricePrecisions':{symbol:raw}}})
 out.append([units.price_step(symbol),units.round_price(price,symbol)])
print(json.dumps(out))
`;
const backend = JSON.parse(execFileSync(python, ['-c', program], { cwd: root, input: JSON.stringify(vectors), encoding: 'utf8' }));
const near = (a, b) => Math.abs(a-b) <= Math.max(1e-15, Math.abs(b)*1e-14);
vectors.forEach(([symbol, raw, price, step, expected], i) => {
  const payload = { nobitex: { pricePrecisions: { [symbol]: raw } } };
  const saved = JSON.stringify(payload);
  U.applyPriceOptions(payload);
  U.applyPriceOptions(payload); // Refresh must not divide an already-normalized rule again.
  if (JSON.stringify(payload) !== saved || !near(U.priceStep(symbol), step)
      || !near(U.roundPrice(price, symbol), expected)
      || !near(backend[i][0], step) || !near(backend[i][1], expected)) {
    throw new Error('tick contract mismatch: ' + JSON.stringify({ symbol, raw, price, step, expected, python: backend[i], js: U.roundPrice(price,symbol) }));
  }
});
for (const bad of ['NaN','Infinity','0','-1','not-a-number']) {
  const before = U.priceStep('BTCIRT');
  if (U.applyPriceOptions({ nobitex: { pricePrecisions: { BTCIRT: bad } } }) !== 0 || U.priceStep('BTCIRT') !== before) {
    throw new Error('invalid tick overwrote a good rule');
  }
}
console.log(`PASS — ${vectors.length} independent price/tick vectors in Python and JS; refresh and invalid-rule guards`);
