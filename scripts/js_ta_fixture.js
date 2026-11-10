#!/usr/bin/env node
/* Emit browser TA values for a candle JSON fixture; consumed by the parity test. */
const fs = require('node:fs');
global.window = global;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.fetch = async () => { throw new Error('no network in TA fixture'); };
require('../nbx-core.js');
const NBX = global.NBX;

const rows = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const closes = rows.map((r) => r.c);
const bb = NBX.TA.bollinger(closes, 20, 2).map((r) => r.pctB);
const di = NBX.TA.di_adx(rows, 14);
process.stdout.write(JSON.stringify({
  scores: NBX.TA.scoreSeries(rows, { start: 60 }),
  scalar: NBX.TA.signalScore(rows),
  rsi: NBX.TA.rsi(closes, 14),
  atr: NBX.TA.atr(rows, 14),
  pctB: bb,
  pdi: di.pdi,
  ndi: di.ndi,
  adx: di.adx,
}));
