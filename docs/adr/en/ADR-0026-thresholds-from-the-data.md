# ADR-0026 — A gate's threshold comes from the data, not from taste

[فارسی](../fa/ADR-0026-آستانه-از-داده.md) · **English**

**Status:** Accepted (implemented in 1.1.4 — measurement only, no threshold applied)
**Date:** 2026-10-24
**Related:** ADR-0014 (data honesty), ADR-0021 (apply only with out-of-sample evidence), ADR-0022 (gates and the shadow ledger), ADR-0023 (smarter only with evidence — S2), ADR-0025 (fundamental coverage)

## Context

ADR-0023 put "honest backfill of the shadow ledger" second (S2) so that the evidence count would
fill faster. Reviewing a real install showed that this **does not solve the actual problem**, for two
reasons, both of them in the code:

1. **Backfill does not move `days` — unless it lies.** In `gate_ledger.counts()` the day of each
   verdict is derived from that row's `ts`, and `ts` is the close time of the bar it judged.
   Backfilling last week's rows would jump `days` from 2 to 5 without a single day of market having
   passed. To stay honest, rows with `source='backfilled'` have to be excluded from the day count —
   and in that case the lock we care about does not move at all.
2. **The two gates S2 would collect evidence for never fire.** `server/main.py` calls the evaluator
   with no limits:

```python
out = gates.evaluate(rows)
```

   and in `gates.GateLimits` the defaults are `max_spread_bps = None` and `min_depth_usd = None`,
   meaning "do not judge". A test pins that on purpose
   (`test_gates.py::…defaults_are_switched_off`). So `spread-wide` and `book-thin` stay at zero in
   the shadow ledger forever — not because the market is calm, but because there is no threshold.

The bottleneck is therefore **not a shortage of samples**; it is **the absence of a number**. A
number for a threshold to come from does not exist in the repository today, and inventing one is a
guess.

## Decision

### 1. The real distribution of the three gate inputs, from the shadow ledger

`gate_ledger` already stores `spread_bps`, `depth_usd` and `age_bars` with every verdict row. A new
function (`gate_ledger.distributions`) reads that table and reports, per metric:

| Key | Meaning |
|---|---|
| `n` | how many usable numeric samples |
| `median`, `p90`, `p95`, `p05`, `min`, `max` | the distribution, linearly interpolated |
| `days` | how many distinct days they span |
| `sufficient` | whether ADR-0023's bar is met (≥30 samples over ≥5 days) |

It ships in `/api/signals` under `gates.ledger.distribution` and in `/api/gates/evidence`, on the
existing 30-second cache (`gate_ledger_summary`) — one query, not one per row.

### 2. A proposal, named and labelled

From that distribution comes a **proposal** and nothing more:

```
max_spread_bps proposal = p95(spread_bps)      ← 95% of the board, not more
min_depth_usd  proposal = p05(depth_usd)       ← the thinnest 5%
```

Every proposal carries `applied: false`, `proposed: true`, `n`, `days` and its `basis`. When the
sample is insufficient (`sufficient: false`) the proposal is **`None`** with a reason — not a number
somebody might copy.

### 3. Three things this ADR deliberately does not do

- **It does not apply the threshold.** `GateLimits` is untouched; no gate blocks anything and
  `applied` stays `false`. Leaving shadow follows ADR-0021 and needs its own ADR.
- **It does not build a distribution from backfilled rows.** `source='backfilled'` is excluded: a
  spread distribution taken from verdicts that were themselves reconstructed is not a distribution.
- **It does not derive a market-wide threshold from one symbol.** The distribution is market-wide;
  per-symbol thresholds are a different question and not in this ADR.

## Consequences

- **Positive:** after a few days there is a number a threshold can come from — with `n` and `days` on
  the table, not taste. And it will show whether `spread-wide` would ever remove anything at all.
- **Positive:** it records why S2 is not needed for the current bottleneck, and replaces it with a
  measurement.
- **Negative:** while the sample is thin the proposal is `None` and nothing moves — which is correct.
- **No trading effect:** no threshold is applied; ranking, score and risk are untouched.

## Refusals

| Proposal | Why it was refused |
|---|---|
| Setting `max_spread_bps` to a "reasonable" number (say 25 bp) and switching the gate on | taste, not data — exactly what ADR-0021 forbids |
| Building the distribution from backfilled rows | reconstructed data does not make a distribution (ADR-0014) |
| Copying the proposal into `.env` automatically | an operational setting must be a human decision, not an upgrade side effect |
| Percentiles instead of the full distribution | a percentile alone hides the tail; both are reported |
| Computing it on the hot `/api/signals` path with no cache | one query per scan × 39 symbols is load nobody needs |

## Acceptance

1. `gate_ledger.distributions()` returns the correct percentiles on known synthetic data (linear
   interpolation, a numeric test).
2. A `source='backfilled'` row is counted in neither `n` nor the distribution.
3. With `n < 30` or `days < 5` the proposal is `None` and carries a `reason`.
4. `applied` is always `false` and the `GateLimits` defaults are still `None` (the existing test does
   not break).
5. The distribution line appears in the gates chip's tooltip with `n` and the "measurement only"
   label, in both languages.
