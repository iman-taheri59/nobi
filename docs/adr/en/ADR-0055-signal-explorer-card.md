# ADR-0055 — Signal explorer card: the decision first, the evidence behind "Details"

[فارسی](../fa/ADR-0055-کارت-سیگنال-کاوشگر.md) · **English**

**Status:** accepted — local code for 2.5.2; display only, no change to scores/ordering/server/policy
**Date:** 2026-11-09
**Related:** ADR-0022 (shadow gate), ADR-0025 (per-pair premium), ADR-0052 (R:R ladder published at `/api/risk/state`), the user guide's old card schematic (removed in `61bc2be`)

## Context

The explorer row had turned into an audit receipt: score, the full shadow-gate verdict with spread/depth/book age, chips for TA/FA/"24h turnover proxy"/trend/momentum/regime/stop %, and a neutral sparkline. All true, none of it answering a beginner's first three questions on a phone: *what is the price, where is the stop, where is the target?* — exactly the columns of the guide's old schematic (symbol | direction | score | regime | price | stop | target 1/2 | freshness), which the UI had drifted away from once that picture was dropped. Direct user feedback: "the chart says nothing", "what is a turnover proxy?".

## Decision

1. **Default row hierarchy** (`signalRowHtml` in `nbx-app.js`): rank + symbol + direction chip + regime chip → composite (large number + bar) → level table **price / stop / target 1 / target 2** with distance % → a "level ladder" (bar with pins for stop, price, targets at their real proportional distances) → freshness.
2. **Honest provenance of levels:** price = the scan's `lastClose`; stop = price − `risk.stopDistance` (2×ATR of that scan); targets = price + stop distance × `caps.rrLevels[0]` / `[-1]` as published by the server at `/api/risk/state` (ADR-0052). If the ladder has not arrived, targets stay "—" and the row footer says why; **there is no private 1.5/2.5 copy in the browser.** Non-LONG rows get no targets (model stop remains). Every row's footer: "Model levels … — not an order."
3. **Per-symbol freshness:** age of that symbol's last cached candle for the selected timeframe; "stale" from 2.5 bars; otherwise the scan age (`age_s`); otherwise "freshness unknown". Never one symbol's age standing in for another's.
4. **Evidence behind "Details"** (`data-sig-evidence`, button with `aria-expanded`): the four weighted TA component bars (0.4/0.25/0.15/0.2), RSI/ADX/ATR%/%B, the neutral cached price curve (same `spark()` contract: neutral colour, selected timeframe only), the shadow-gate text with spread/depth, FA provenance, liquidity (the "turnover proxy" wording only here, with "from 24h volume; not book depth"). The block is in the DOM (tests and screen readers see it) and merely `hidden`. Open state is kept in `sigEvidenceOpen` so periodic repaints do not close it. Clicking the row itself still opens the candle chart.
5. **Layout by the card's own width** (container queries in `dashboard.css`): < 560px stacked (levels one per line, full-width ladder); ≥ 560px a four-column level table under the identity line; ≥ 960px one full line. Reason: with the sidebar open a 1024px screen gives the list ~700px and a viewport media query lied.

6. **Row candle chart:** the same `signalLevels` (via `NBX_APP`) draws the dashed stop/target lines on the chart — one source for the row's number and the chart's line. Crosshair/tooltip on mouse and touch (`bindCandleHover`), no fetch, nothing recomputed; a narrower canvas on phones. The last published ladder is kept in `live.rrLevels`.

## Consequences

- Scan rows are never mutated (`Object.freeze` in the test); ordering and scores unchanged.
- The "price" column is the scan's closed-candle `lastClose`, not a live ticker; the freshness label says so.
- Test `scripts/test_signal_card.cjs` (CI/release): level maths, refused targets without ladder/ATR/LONG, freshness, toggle, no jump into the candle panel, data untouched. `test_review_phase8.cjs` passes unchanged (the neutral sparkline now lives inside "Details").
- Out of scope: score/threshold/k changes; the "real outcome" column (item 15 of the 2.5.2 list) is separate.
