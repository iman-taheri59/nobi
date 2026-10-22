# ADR-0021 — Applying adaptive thresholds only after out-of-sample evidence

[فارسی](../fa/ADR-0021-اعمال-آستانه-های-تطبیقی-با-شاهد-خارج-از-نمونه.md) · **English**

**Status:** proposed — nothing has been applied
**Date:** 2026-10-22
**Related:** ADR-0009 (backtest and promotion gate), ADR-0014 (data-honesty contract),
ADR-0020 (timeframe-aware thresholds)

## Context

ADR-0020 measured and proposed; since then every response carries `applied: false`. In
1.0.15 the natural next step was taken: **ask the question "would a higher bar have been
better out-of-sample?"** — with a paired comparison in `server/adaptive_evidence.py` and the
route `GET /api/adaptive/evidence/{symbol}`.

That comparison surfaced something nobody knew before writing it: **a lower bar cannot be
tested with the current engine.** `ta_engine` derives the LONG label from the constant
itself (`return "LONG" if score >= LONG_SCORE_MIN ...`), so a `min_score` below 68 adds no
trades at all. The comparison would have reported "no difference", which actually meant "we
changed nothing" — so it returns a named refusal, `lower-bar-untestable`, instead of a
number.

## Proposed decision

Applying the factors is **not permitted** until all five conditions hold together:

1. **One variable.** The comparison changes only `min_score`; window boundaries, the
   parameter grid, the cost model and slippage stay identical. If two things move at once,
   the test measures neither.
2. **Out-of-sample.** Only evaluation-window numbers are reported; parameters are chosen on
   the train slice alone.
3. **Enough sample.** At least 3 windows and 30 out-of-sample trades in *both* arms.
   Anything less is `inconclusive`, never "favours".
4. **A materiality threshold.** ΔPF ≥ 0.10 **and** Δexpectancy ≥ 0.05R. One without the
   other is not an improvement: a profit-factor gain bought with collapsed expectancy is a
   different strategy, not a better one.
5. **A real feed.** On `source=reference` the verdict is always `inconclusive`; reference
   data can demonstrate a method and can never certify a strategy (ADR-0009 / ADR-0014).

And even when all five hold, the change is **a documented act**: a version bump, a CHANGELOG
entry, and this ADR moving from "proposed" to "accepted" — not a value in an environment
variable and not a switch in the dashboard.

## Technical prerequisite for a lower bar

Testing a lower bar first needs a structural change: `LONG_SCORE_MIN` and
`NEUTRAL_SCORE_MIN` must become **parameters**, in `ta_engine` and in its JS twin (with the
existing 28×10 parity kept), so that the direction label comes from the same parameter
instead of the constant. Until then, `lower-bar-untestable` is the correct answer.

## Consequences

- The dashboard shows one A/B evidence line: the window count with its geometry, PF
  base ← higher, Δ, and a verdict — always alongside "not applied".
- A verdict arrives with its sample size. "Favours the higher bar" without an `n` is a
  slogan, not evidence.
- No code path in this version writes `LONG_SCORE_MIN`; the test
  `test_nothing_is_ever_applied_and_no_promotion_is_offered` keeps it that way.

## What this ADR deliberately does not decide

- The final value of the factor (it is a function of timeframe and regime, not a constant).
- Whether applying should be automatic or manual. The proposal is manual and versioned,
  like every other change in this repository.

## Amendment — 2026-10-22 (version 1.0.16): prerequisite implemented, one refusal retired

The "technical prerequisite for a lower bar" section of this ADR is now done:
`LONG_SCORE_MIN` and `NEUTRAL_SCORE_MIN` are **parameters** in `ta_engine`
(`direction_for(score, long_min, neutral_min)`, and the same two on `ta_score` and
`ta_score_series`), and the browser twin names them too (`nbx-core.js` →
`LONG_SCORE_MIN`/`NEUTRAL_SCORE_MIN`, plus `TA.signalScore(candles, opts)`). **The defaults
are the previous constants**, so this is a structural change, not a behavioural one: 28×10
parity and every existing number stay identical to the digit.

Consequences:

1. `lower-bar-untestable` is **removed** from `adaptive_evidence`; both directions are
   measurable.
2. Each arm labels signals with the same bar its filter uses.
3. A new named refusal, `bad-factor`, for a factor outside `[0.5, 2.0]` — outside that range
   the comparison is not a threshold experiment.
4. **One claim withdrawn:** "a lower bar can only add trades" is false, because the
   walk-forward re-optimises the stop/target grid. What is true is that the sets `LONG` and
   `LONG ∪ NEUTRAL` never shrink under a lower bar.

**The ADR's status does not change:** still *proposed*. The technical prerequisite is gone,
but the five conditions in "Proposed decision" (one variable, out-of-sample, enough sample, a
materiality threshold, a real feed) still have to be met on Nobitex data, and no code path
writes `LONG_SCORE_MIN`.
