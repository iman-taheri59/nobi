# ADR-0046 — Signal Intelligence v1.2 drift evidence

[فارسی](../fa/ADR-0046-پایش-تغییر-هوشمندی-سیگنال-۱-۲.md) · **English**

**Status:** Accepted for 1.7.0  
**Date:** 2026-10-25

## Decision

Drift compares a current window with the immediately preceding, equal, non-overlapping baseline. Resolved outcomes are isolated by horizon and optional `config_hash`; pipeline candidates are linked through scan cycles. Recorded findings cover win rate, expectancy, composite-score PSI, scan-error rate, gate-block rate and emission rate.

Win/proportion findings use two-proportion tests, expectancy uses an unequal-variance standard error, and all non-PSI hypotheses receive Benjamini–Hochberg correction. PSI bins are derived only from baseline quantiles. Minimum samples are 30 resolved outcomes, 50 pipeline candidates and 100 scores per window. Results are `insufficient`, `stable`, `watch`, or `alert`; insufficient evidence never alerts.

`drift_runs`, `drift_findings`, and append-only `drift_reviews` preserve boundaries, actor, engine, thresholds, correction and evidence. Runs are operator/owner mutations with CSRF and audit; reads remain authenticated. Analysis never tunes configuration, blocks trading, or claims causality.

## Limits

Full replay, causal attribution, execution-cost attribution, automatic strategy changes, capital allocation, and PostgreSQL remain out of scope.
