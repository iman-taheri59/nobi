# ADR-0022 — Hard gates: from labels to gates, only with recorded evidence

[فارسی](../fa/ADR-0022-دروازه-های-سخت-از-برچسب-به-گیت.md) · **English**

**Status:** Proposed — no gate blocks anything; this document says *how* one becomes allowed to
**Date:** 2026-10-22
**Related:** ADR-0007 (hard gates), ADR-0009 (promotion gate and live execution), ADR-0012
(journal and VOID), ADR-0014 (data honesty contract), ADR-0020 (data age and `applied: false`),
ADR-0021 (applying thresholds only with evidence)

## Context

ADR-0007 defined six hard gates for **candidate selection**. The 2026-10-20 review in that same
document recorded that only one of them actually changes a decision today. This ADR closes that
gap — but not by switching the gates on. Today's state, with code references:

| Gate | What it does today | Where |
|---|---|---|
| Fundamental (FA) veto | **Really blocks**: caps 67 and 45, changes direction | `apply_vetoes()` in `server/main.py` |
| Stale data | Label only: `stale`/`unavailable`. Even 1.0.23, which knows the age **per symbol**, removes nothing | `scan_freshness_block()` |
| Symbol suspension | Goes to `VOID` in the journal, but is not removed from the ranking | `journal.py` (VOID law) |
| Spread | Computed and shown; not a gate | `GET /api/orderbook/{symbol}` |
| Book depth | Same; liquidity is only a 0.20 weight in the composite | `GET /api/orderbook/{symbol}` |
| Daily loss stop | Enforced in the paper broker, not in the ranking | `paper_trading` |

That table is its own warning: five gates out of six wear a gate's clothing without being gates.
And "a label that stops nobody" is exactly what a user learns to ignore.

## Why we do not simply close them now

Closing these gates **changes trading behaviour**: names leave the list, the queue shortens, a
different selection happens. In this repository a change in trading behaviour without evidence is
forbidden (ADR-0009, and ADR-0021 which wrote the same rule for thresholds). But there is one
property that separates gates from thresholds and makes the work possible:

> A gate does not have to **act** first in order to be measurable. It is enough to measure what
> *would* have happened if it had acted — and that can be recorded on every scan, without changing
> a single row of the list.

## Proposed decision

### 1. A pure evaluation layer with named verdicts

Add `server/gates.py`: a pure function that turns one row (symbol + candle age + spread + depth +
suspension state) into a verdict:

| Code | Meaning |
|---|---|
| `ok` | no gate objects |
| `stale-data` | the candle age exceeds the tolerance of that timeframe (the 1.0.18 bar rule) |
| `spread-wide` | spread relative to mid exceeds the threshold |
| `book-thin` | depth within ±1 % of mid is below the threshold |
| `halted` | the symbol left the scan universe, or its market is closed |
| `no-book` | no book arrived; **it cannot be measured** — and "I do not know" is not "fine" |
| `no-data` | candle or price missing for that gate's arithmetic |

None of these removes anything in this version. They are verdicts only.

### 2. The thresholds are parameters, and their default is "off"

Exactly the 1.0.16 lesson: a threshold written into a line of code cannot be tested. So:

- `max_age_bars` defaults to `2` (the same `DEFAULT_TOLERANCE_BARS` in `freshness.py` — one
  number, one home).
- `max_spread_bps` and `min_depth_usd` default to `None`, meaning **off**. We do not invent a
  number with no provenance; first the existing distribution has to be measured so that the
  threshold has somewhere to come from.
- `enforcing` defaults to `False`. A flag that only turns on with evidence and a new version.

### 3. Shadow mode: the response says what *would* have happened

`/api/signals` (and the depth route) gain a `gates` block:

```json
"gates": {
  "mode": "shadow",
  "applied": false,
  "would_block": ["XYZIRT"],
  "verdicts": {"XYZIRT": {"code": "stale-data", "age_bars": 288.0, "limit": 2}},
  "counts": {"ok": 22, "would_block": 1, "unknown": 1}
}
```

`applied: false` is the contract every evidence response has carried since 1.0.15. `unknown` is
deliberately separate from `would_block`: a book that never arrived is not "blocked", it is "not
known".

### 4. The evidence needed for a gate to leave shadow mode

For **each gate on its own** (never all at once):

1. **One variable.** Only that gate is switched on; ranking, weights, thresholds and the parameter
   grid stay untouched.
2. **The forward outcome of the blocked names.** For every symbol the gate would have blocked at
   scan time, compare its forward return over `k` bars (only figures after the decision instant)
   with **the median of the allowed names over the same window**. Measured in bars, not days: the
   horizon must belong to the timeframe that produced the verdict.
3. **Enough sample.** At least 30 blocking verdicts across at least 5 distinct trading days;
   anything less is `inconclusive`, not "in favour of the gate".
4. **A pre-registered effect size.** The mean forward return of blocked names must be worse than
   the allowed median by at least `0.5`× the standard deviation of that series. That number is
   written here, **before** any result is seen, so it cannot be argued with afterwards.
5. **A real feed.** On `source=reference` the verdict is always `inconclusive` (ADR-0009/0014).

And even with all five satisfied, switching a gate on is a **documented act**: a new version, a
CHANGELOG bullet, an amendment to this ADR, and `applied: true` in the response — not an
environment variable.

### 5. The "never an empty list" rule

If the gates ever do act and block everything, the response must not be an empty list that looks
like "nothing interesting today". In that state the route must say "everything was blocked", with
a count per code, and return the rows with their verdicts. An empty list is allowed only when
there is genuinely no data.

## Consequences

- **No number changes in this version.** `mode: "shadow"` and `applied: false` in every response;
  the `test_nothing_is_ever_applied` test guards exactly this.
- From day one the dashboard shows which symbols "would have been removed if the gates acted",
  and with which code — which moves the decision from guesswork to observation.
- The measurement happens on real scans, not on a reconstructed backtest, because every scan
  already carries the age, the spread and the depth.
- Cost: one extra block in the response and one module; no new network request (the book is
  already fetched).

## What this ADR deliberately does not decide

- The **number** of the spread and depth thresholds. Until the real distribution of spread/depth
  across the Nobitex universe is measured, any number is a guess; and a quiet guess wearing a
  gate's clothing is the worst kind.
- Which gate **wins first**. The order is decided by evidence, not by taste.
- Live execution. This document has nothing to do with enabling order submission (ADR-0009 still
  governs).
- Changing weights. Liquidity keeps its 0.20 weight; this ADR is about **gates**, not weights.

## Risks and rollback

| Risk | Answer |
|---|---|
| A gate that blocks everything makes the market look empty | The "never an empty list" rule + per-code counts |
| Shadow mode costs CPU | A pure, O(n) evaluation over rows already in hand; no new request |
| A threshold "derived" from data and then tested on the same data | Measurement window and test window must be disjoint; the forward horizon starts after the decision instant |
| A shadow verdict mistaken for a result | `applied: false`, `mode: "shadow"` in every response, and printed on the card |

Rollback: drop the `gates` block; no row in any route depends on it, because it changes nothing.

## What would move this ADR out of "Proposed"

1) `gates.py` with those seven codes and its tests, 2) the `gates` block in shadow mode on the
existing routes, 3) a dashboard that shows the shadow verdicts, 4) and then clause 4's evidence for
**one** gate. Until that day the status stays as it is: proposed, and nothing is blocked.

---

## Implementation amendment — version 1.0.25 (2026-10-22)

Stage 1 of the decision above is implemented. The decision text is untouched; this section only
says where things stand today.

**What exists:** `server/gates.py` with the seven codes in the same precedence order
(`halted` → `no-data` → `stale-data` → `no-book` → `spread-wide` → `book-thin` → `ok`),
thresholds as parameters with the two money-shaped ones defaulting to off, and a `gates` block on
`/api/signals` with `mode: "shadow"` and `applied: false` in **every** response. The dashboard has
a chip that says "gates (shadow): 4 of 24" and puts both the symbol names and the sentence
"nothing was removed" in its title.

**Every input already existed:** the per-symbol age from 1.0.23, the closed-market set from
`closed_markets`, and depth/spread from a book the `/api/orderbook/{symbol}` route **already** read
and now keeps. The result: this layer adds **zero network requests** — and a symbol nobody opened
gets `no-book`, which is the most honest answer available.

**Status:** still **proposed**. No gate blocks anything and `enforcing` is never read; the test
`test_the_module_has_no_way_to_apply_anything` guards exactly that. Leaving "proposed" requires
clause 4 of the main text: the forward-return evidence for **one** gate.
