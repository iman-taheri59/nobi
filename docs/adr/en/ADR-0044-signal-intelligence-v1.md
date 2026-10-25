# ADR-0044 — Signal Intelligence v1

[فارسی](../fa/ADR-0044-هوشمندی-سیگنال-نسخه-اول.md) · **English**

**Status:** Accepted — implemented in 1.6.0
**Date:** 2026-10-25

## Decision

- A Signal Intelligence tab is read-only for every signed-in role and computes directly from `signal_log`, `signal_outcomes` and `gate_shadow`; no synthetic result exists.
- Summary reports signal/resolved/unresolved/void counts, win rate, expectancy and average R, return, MFE/MAE and a 30-sample sufficiency floor.
- Funnel shows judged, data-ready, gate-passed, generated-signal and resolved counts with actual gate-code taxonomy.
- Cohorts group by symbol/timeframe/regime/config hash so versions and market conditions are not mixed.
- Signal rows expose H1/H2/H3 outcome, MFE/MAE and review count; detail shows actual feature snapshots, contributions and every outcome.
- Viewers may read; review/tag mutation requires operator/owner plus CSRF. Tags use a fixed allowlist and append `signal.review` audit.
- Review notes are capped at 1,000 characters and detail data is escaped before HTML insertion.

## V1 limitation

Full execution linkage, latency breakdown, calibration, drift and deterministic replay remain pending. Gate and signal stages come from two real ledgers and are not necessarily one-to-one; UI presents stage counts, never an invented conversion rate.
