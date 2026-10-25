# ADR-0047 — Drift scheduler and configuration inventory

[فارسی](../fa/ADR-0047-زمان‌بند-drift-و-فهرست-پیکربندی.md) · **English**

**Status:** Accepted for 1.8.0  
**Date:** 2026-10-25

Drift scheduling is disabled by default and owner-controlled. Allowed intervals are 6, 12 and 24 hours; a startup or delayed process performs at most one due run and schedules the next interval, never replaying a backlog. The task is supervised by the server lifespan, refuses concurrent runs and records last run/error state. It performs analysis only and cannot alter configuration, risk controls or orders.

The singleton `drift_schedule` row persists enabled state, interval, window, horizon, cohort mode, actor, timestamps and last error. Configuration inventory compares signal counts per `config_hash` across equal baseline/current windows and labels continuity as continued, new or missing. Schedule mutation is owner-only with CSRF and audit; authenticated readers may inspect schedule and inventory.

### Stage 33 — auditable PSI detail and bounded detail queries

PSI evidence now persists baseline/current counts, percentages, bounds, and contribution for every bin. Run detail accepts an optional exact `cohort_key`, keeping detail queries bounded without deriving or guessing attribution. Configuration inventory marks overlapping but under-floor configurations as `not-comparable` rather than implying valid comparison.

### Stage 34 — bounded cohort inventory

The cohort API allowlists global, config, symbol, timeframe, regime, symbol+timeframe, and timeframe+regime dimensions. Results are capped at 100 rows, expose truncation explicitly, and suppress comparison below the performance sample floor. Inventory reports population/comparability only; it does not manufacture statistical findings.

### Stage 35 — bilingual operator surface

The Signal Intelligence drift panel now renders persisted PSI-bin evidence, bounded cohort inventory, CSV export, and schedule state. Schedule mutation is submitted with the authenticated CSRF contract and remains owner-only on the server. Allowed six-, twelve-, and twenty-four-hour intervals and allowlisted cohort modes are selected rather than entered as arbitrary values.

### Stage 36 — cohort statistical findings

A run can now select an allowlisted cohort mode. Comparable cohorts persist expectancy, win-rate, and composite-PSI findings beside global findings in the same immutable run. All non-PSI p-values, global and cohort, share one Benjamini–Hochberg correction family. Cohort PSI retains per-bin evidence. Pipeline findings remain global because blocked candidates cannot always be assigned to config/regime without fabricating attribution. Successful neutral aggregation is named `stable`; `watch`, `alert`, stale-recovery `partial`, and execution `failed` remain distinct lifecycle states.
