# ADR-0051 — Shadow A/B: fixed weight/rule arms and an alternative stop multiplier, on the same bars

[فارسی](../fa/ADR-0051-آزمون-سایه-بازوهای-وزن-و-stop.md) · **English**

**Status:** Accepted — code on `main`; starts recording with the post-soak deploy (2026-11-10); changes no production weight or stop
**Date:** 2026-11-06
**Related:** ADR-0050 (regime gate, `NBX_RISK_K`), ADR-0012 (MSI), ADR-0022 (shadow), `docs/analysis/journal-diagnostic-2026-11-06.en.md`

## Context

The journal diagnostic showed that the current weights (TA 0.55 / FA 0.25 / liquidity 0.20) carry no 1h edge while FA and liquidity alone have positive IC, and that a 2×ATR stop is hit almost always at the 24h horizon. The obvious temptation is "change the weights" — which would be fitting to one falling 38-hour window. Project policy: any behaviour change is measured in shadow, on forward (out-of-sample) data, before it is applied.

Two technical constraints shaped the design:
- `signal_log` has one row per (symbol, timeframe, bar) (`UNIQUE`); alternative arms cannot be separate rows.
- `signal_outcomes` is joined by 12 consumers (MSI, funnel, drift, intelligence) that expect production LONGs only; writing shadow results there would contaminate production metrics.

## Decision

### 1) Arms are declared in code, not env — `server/shadow_arms.py`
| Arm | Definition | What it isolates |
|---|---|---|
| `A` | the live production verdict (the row itself) | reference |
| `B_fa_liq` | TA 0.30 / FA 0.40 / liq 0.30; LONG iff composite ≥ 68 | the "FA and liquidity are more predictive" hypothesis |
| `C_gate` | live verdict + ADR-0050 regime gate enforced | the gate's value on its own |
| `D_comp` | current weights, but LONG from composite ≥ 68 (not from the TA score) | the threshold rule apart from the weights |

A severe FA veto overrides every arm. The arm set and `VERSION=1` are part of the `journal.py`/`main.py` source and therefore of `config_hash`; changing an arm = new hash = new comparison period. `NBX_SHADOW_ARMS=off` only disables recording (default on; no behavioural effect).

### 2) Recorded on the same row
In `rescan`, after vetoes and the regime, every symbol gets `features.shadow_arms = {arm: {composite, direction}}` and `features.shadow_any_long`. The production row is unchanged.

### 3) Shadow results in a separate table — `signal_shadow_outcomes`
Key `(signal_id, horizon_key, risk_k)`. After filling `signal_outcomes` (unchanged), the resolver also fills this table:
- for production LONGs: a simulation at each alternative k in `NBX_SHADOW_RISK_K` (default `3`);
- for rows only a shadow arm called LONG: at the live k (2) **and** the alternatives.
Same `simulate_long` with a `risk_k` parameter (same TP1/TP2/trail geometry). The resolver's return value counts production outcomes only.

### 4) Evaluation and the winning criterion
`scripts/journal_diagnostic.py` §5: per arm, H1 R̄ and stop-rate on **the bars that arm called LONG**, at k=2 and k=3 side by side. An arm "wins" only if: ≥ 500 LONGs per arm, at least 14 calendar days, 95 % CI of mean R non-overlapping with `A`, and the advantage survives in both the `neutral` and `risk-off` regimes. Only the winning arm goes to production, via its own ADR and a new `config_hash`; Paper stays until 30 real orders.

## Consequences

- Nothing changes in what the user sees, Telegram sends or Paper trades.
- Cost: a few hundred bytes of JSON per row and at most 3 extra simulations per LONG/horizon in the resolver (negligible CPU; same cached candles).
- The comparison is on identical bars and geometry, not between two parallel services, so scan timing and data quality drop out of the comparison.
- `B_fa_liq` renormalises its weights when `fa_score` is `None`; read no-FA rows separately in the report.
- Not in this ADR: fees in R, the IRT liquidity filter, a formal time holdout — next ADR.
