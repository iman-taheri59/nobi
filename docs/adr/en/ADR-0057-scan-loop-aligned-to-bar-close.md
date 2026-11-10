# ADR-0057 — The scan loop locks to the bar close, not to a stopwatch

[فارسی](../fa/ADR-0057-حلقهٔ-اسکن-قفل-به-مرز-کندل.md) · **English**

**Status:** accepted — code in 2.5.6; timing only, no change to scoring/gates/R:R/queue/universe
**Date:** 2026-11-10
**Related:** ADR-0024 (scan loop), ADR-0052 (frozen holdout), ADR-0054 (plan staleness)

## Context

ADR-0024 wrote the loop as `sleep(NBX_SCAN_LOOP_S)` then scan. The scan itself takes 30–40 s, so the real period was ≈ 935 s and every cycle drifted ~35 s later. Log of 10 Nov: `11:15:27 → 11:31:04 → 11:46:39 → 12:02:14 → 12:17:47 → 12:33:21`.

Consequence: for part of every hour the scan ran just **before** a bar closed and scored the previous one; the new bar closed a minute later and was not seen until the next cycle (~15 min). The signal card lagged the chart by anywhere from ~0.5 to ~30 minutes, which the user correctly reported as "signal updates are broken". The dashboard's freshness text ("last candle: 20 min ago") also counted from the bar's *open*, misleading even when the loop was on time.

## Decision

1. **Server:** `scan_loop_delay(tf, every, grace)` sleeps until "next bar boundary + `grace`". `NBX_SCAN_LOOP_GRACE_S` (default 20, range 5–300; a bad value is **refused**, as in ADR-0024) gives the venue time to finalise the candle. If the next slot is under `SCAN_LOOP_MIN_S` (60 s) away the following slot is taken — never two scans of one bar. An unknown timeframe falls back to the old fixed cadence. `NBX_SCAN_LOOP_S` remains the on/off switch and cap. A value smaller than the timeframe (e.g. 300 in `five-day-validation`) no longer produces three identical scans of one bar — same rows, once.
2. **Dashboard:** per-symbol freshness counts from the bar's *close* and prints the bar's own clock time ("bar 19:45 · closed 5 min ago"). "Stale" starts two missed bars after the close.

## What does not change

Same closed bars, same scores, same one journal row per bar (`INSERT OR IGNORE` on `ts,symbol,timeframe`). The only journal effect: `created_at` moves closer to `ts`, and the price at recording time closer to the bar's. For the soak/holdout (ADR-0052) the series continues; this item is recorded in the deploy log so the pump-chase analysis can read before/after 2.5.6 separately.

## Consequences

- **Positive:** signal latency goes from "0–30 min" to "~20 s + scan time"; card and chart show the same bar; queue plan staleness (ADR-0054) occurs less often.
- **Negative:** scans now all run at the same seconds after a boundary — the instantaneous load on the venue is unchanged (one scan), only its timing became predictable.
- **Neutral:** `scan_loop.runs` in health restarts from zero after deploy (as after any restart).

## Verification

- `server/tests/test_scan_loop_and_daily_tool.py`: slot = boundary + grace; no drift over 10 cycles with a 35 s scan; no second scan of one bar; unknown-timeframe fallback; grace bounds.
- Production log after deploy must show `journal: 39 new prediction(s)` at `hh:00:2x / hh:15:2x / hh:30:2x / hh:45:2x` (+ scan time), without drift.
