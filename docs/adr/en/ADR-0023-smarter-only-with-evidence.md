# ADR-0023 — Smarter only with evidence: six steps and five refusals

[فارسی](../fa/ADR-0023-هوشمندتر-شدن-فقط-با-شاهد.md) · **English**

**Status:** Proposed (S1 implemented in 1.0.28) — the document itself changes no behaviour; it states *what* may be added and *in what order*
**Date:** 2026-10-23
**Related:** ADR-0005 (technical analysis), ADR-0006 (fundamentals), ADR-0007 (decision engine), ADR-0008 (risk), ADR-0009 (promotion gates), ADR-0012 (journal), ADR-0014 (data honesty), ADR-0020 (timeframe thresholds), ADR-0021 (apply only with evidence), ADR-0022 (gates and the shadow ledger)

## Context

“How do we make the system smarter?” is a trap in this repository. Anything can be added, but
almost every addition changes the score, and changing the score changes selection — and changing
selection without evidence is forbidden by ADR-0009, ADR-0021 and ADR-0022. So the answer cannot be
a list of tools; it has to be a list whose every item can be *rejected*.

The system has three layers today, and together they define the current ceiling:

| Layer | What it holds today | The gap |
|---|---|---|
| Computation | `ta_engine` (four weighted components + regime gate), `fa_engine` (five components), liquidity, `risk_engine` (ten gates), `portfolio` (A3 correlation) | The numbers are right but carry no **probabilistic meaning**: “80” means 80, not “a 63% chance” |
| Recording | `journal` (every signal + three horizons), `gate_ledger` (one verdict per bar) | The record exists, but nothing **reads it back** for attribution or calibration |
| Decision | `exec_mode` (auto/semi/manual) — all paper | There is no real decision, so “intelligence” can only grow in the first two layers |

The real gap is elsewhere, and 1.0.26 exposed it: **the shadow ledger is effectively empty.** An
order book is only read when someone opens `/api/orderbook/{symbol}`; on a normal scan most rows
get `no-book`, so `spread-wide`/`book-thin` evidence is never produced. Making a system “smarter”
while it collects no evidence is decoration.

## Decision

### 0. Definition of “smarter” — three admission tests

Any change claiming intelligence enters this repository only if it passes all three:

1. **Measurable:** it produces a specific number that does not exist today, or is not trustworthy today.
2. **Falsifiable:** a state can be constructed in which the change is *wrong*, and a test catches it.
3. **No behaviour change in the first step:** it either changes display/recording only, or reports
   in shadow (`applied: false`) — and leaving shadow follows the ADR-0021/0022 rule.

Anything that fails these, however modern it looks, is rejected (see “Refusals”).

### 1. Six steps, in this order

The order is deliberate: each step produces the input for the next. Jumping from 1 to 4 means
building a conclusion on a sample that does not exist.

#### S1 — Prefetch the order book on every scan (prerequisite for everything)

- **What:** at the end of each scan, for the top N symbols (default 10, configurable), make one
  `/api/orderbook/{symbol}` request and write `spread_bps`/`depth_usd` into `_cached["books"]`.
- **Why:** without it, `no-book` is the dominant state and the spread/depth gates never have data.
- **Ceiling:** N must respect ADR-0003's rate budget; requests go through the existing limiter, and
  an N above the ceiling **raises** rather than silently shrinking.
- **What it does not do:** no row is removed and no ranking changes; it only fills the gates' input.
- **Acceptance:** after a full scan, `gates.inputs.books` rises from zero to ≥N and the `unknown`
  count drops visibly; a test that rejects an N above the ceiling.

#### S2 — An honest backfill of the shadow ledger

- **What:** reconstruct verdicts for **past** bars, only for the codes whose inputs are derivable
  from candles: `stale-data`, `halted`, `no-data`.
- **Why:** evidence needs 30 verdicts over 5 days; this turns a five-day wait into one run.
- **Hard boundary:** `spread-wide` and `book-thin` are **not backfilled** — past order books do not
  exist and inventing them is fabricating data (ADR-0014). Reconstructed rows carry
  `source = "backfilled"` and are counted separately.
- **What it does not do:** it never turns an `insufficient` into `supports`/`contradicts`; it only
  clears `insufficient` sooner, and a measurement taken on a backfilled sample is labelled as such.
- **Acceptance:** a test rejecting an attempt to backfill `spread-wide`; a test keeping backfilled
  rows out of the live counts.

#### S3 — Score calibration: from number to probability

- **What:** using the journal, for each score bucket (55–60, 60–65, …) compute the positive rate on
  all three horizons: “score 80 on the 24-hour horizon, with n=140, was positive in 63% of cases”.
- **Why:** the only change that alters the *meaning* of the number without altering selection.
- **Restraint:** below a minimum n (proposed: 30 per bucket) no number is shown; it stays “unknown”.
- **What it does not do:** it does not move the 68 threshold and does not reorder the ranking.
- **Acceptance:** a calibration curve on the performance report; a test keeping an under-sampled
  bucket “unknown”.

#### S4 — Multi-timeframe confluence

- **What:** per symbol, report how many of 15m/1h/4h/daily point the same way (0 to 4); and record
  it in the shadow ledger as a candidate gate (`no-confluence`).
- **Why:** it is cheap (all timeframes are already scanned) and intuitively strong — but intuition
  is not evidence, hence shadow first.
- **What it does not do:** in the first step no row is removed; the ranking is untouched.
- **Acceptance:** a card saying “3 of 4 timeframes agree”; the ledger measures `no-confluence` under
  the ADR-0022 rules.

#### S5 — The correlation gate: wiring `portfolio.py` into the ranking

- **What:** before opening a new position, compute its correlation with open positions and, above
  the ceiling, record a `correlated` verdict (in shadow first).
- **Why:** today you can hold five same-direction positions in correlated coins, so “five trades”
  means one trade at five times the size. `portfolio.py` already does this maths and nobody calls it.
- **What it does not do:** it does not change `risk_engine`'s caps; it only reports.
- **Acceptance:** a test that an unknown correlation is not treated as zero (today `None` is
  returned; it must stay `unknown`); the `correlated` verdict appears in the ledger.

#### S6 — Applying thresholds with evidence (executing ADR-0021)

- **What:** the first place a number actually changes: the per-timeframe `min_score`, and only if
  `adaptive_evidence.compare` returns a favourable verdict on a sufficient sample.
- **Why:** the machinery has been ready since 1.0.16 and is waiting for a decision.
- **What it does not do:** this ADR moves no threshold itself; that step needs its own ADR and PR.
- **Acceptance:** a before/after table of thresholds with each one's sample, in that version's document.

### 2. Two invariants across all six steps

1. **Shadow first:** no new gate or filter removes a row in its first step; `applied: false` stays on
   every path.
2. **Reference data cannot promote:** any result built on the mock feed is at best `inconclusive`
   (ADR-0009/0014) — the same rule already coded in `gate_ledger` and `adaptive_evidence`.

## Refusals — and why

| Idea | Why it is rejected |
|---|---|
| **A machine-learning ranker** | Its tooling (CV with purge/embargo) exists in ADR-0009, but until there is a sufficient sample and out-of-sample evidence, every number it produces is a claim. If it ever comes, it comes in shadow, under the same three tests as section 0. |
| **Reinforcement learning for position sizing** | “Why this size?” must have an answer. A model whose decision cannot be audited cannot be defended in this repository (ADR-0008 requires explicit caps). |
| **Lowering the 68 threshold for “more signals”** | More signals is not intelligence. This is exactly what ADR-0021 made measurable, and the measurement came back `inconclusive`. |
| **Price prediction with a single number** | Structurally a lie; the system only ranks. Any UI that prints “tomorrow's price” burns trust. |
| **Enabling order submission to feel “complete”** | ADR-0009's three promotion gates have not passed, and no `POST /market/orders` path exists in the repository. Intelligence is not a licence to take risk. |

## Consequences

**Positive:** intelligence is defined and rejectable; each step feeds the next; no behaviour change
enters without evidence; the shadow ledger goes from empty to populated and everything else is built
on data.

**Negative:** S1 means more exchange requests (capped, but more); S2 without its label would be
misleading, so the implementation is more expensive than the task; S3 shows “unknown” until the
sample fills, and a user may read that as a defect — the same cost ADR-0022 accepted.

## Open questions

- What should N be in S1? Proposed 10; it must be measured against the rate budget and the scan size.
- What is the minimum sample in S3? Proposed 30 per bucket; below that, “unknown”.
- What threshold should `no-confluence` use in shadow (3 of 4 or 4 of 4)? Decided by evidence, not taste.
- Should `correlated` be evaluated before `spread-wide` or after? Precedence is part of ADR-0022's
  contract, and changing it is an independent decision.

## Acceptance criterion for this whole document

This ADR becomes “accepted” when S1 and S2 are implemented and, within one working week, the shadow
ledger brings at least one gate to `n ≥ 30` and `days ≥ 5`. Until then its status is **Proposed**.

**Implementation status:** S1 shipped in 1.0.28 — the order book is prefetched on every scan under a
ceiling of `GATE_PREFETCH_MAX=20` (above it a `ValueError`, never a trim), with a TTL and a time
budget, and the pass is reported under `gates.inputs.prefetch`. The ranking is untouched and
`applied` stays `false`. 1.0.30 closed one flaw: a book older than `BOOK_GATE_TTL_S` is no longer
counted as evidence — the cache had been treated as if it were a measurement. S2…S6 remain open.
