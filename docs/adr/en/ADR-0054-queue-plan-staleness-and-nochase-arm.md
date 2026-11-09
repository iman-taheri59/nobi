# ADR-0054 — Plan staleness in the approval queue + the "no-chase" shadow arm

[فارسی](../fa/ADR-0054-کهنگی-پلن-در-صف-و-بازوی-نه-تعقیب.md) · **English**

**Status:** accepted — code on `main` for 2.5.2; the lock applies only to the human queue path, the arm is record-only
**Date:** 2026-11-09
**Related:** ADR-0022 (shadow gate), ADR-0052 (fees in R, frozen holdout), ADR-0055 (signal card), chase-the-pump backfill `fda10bf` (`scripts/pump_chase_backfill.py`), checklist `docs/releases/2.5.2-checklist.md` items 6 and 8

## Context

Two findings of the 3–8 November week shared one root: **the system was judging a bar that was no longer that bar.**

1. **Approval queue.** The candle window (`expires_at`) only says the source candle is still the last closed one; it says nothing about whether anyone re-scored it since, or whether price is still near the plan. When the tab is hidden or the network drops, scanning stops, and a 15-minute plan could stay "fresh" for half an hour. The automatic path refused this (`_auto_signal_refusal`); the human path did not — i.e. the one path meant to be the only order path until 17 November had the weakest guard.
2. **Chasing pumps.** The 8 November backfill over the journal: before 53% of live LONGs price had already run ≥ 2×ATR in the previous 4 h, and that subset had the worst mean R at every horizon (1h/4h/24h). The natural hypothesis is "refuse a LONG while price is running" — but ADR-0052 says no trading change enters without a recorded holdout, and the holdout is frozen until 12 November.

## Decision

### A) Plan staleness in the queue: two criteria, mark + lock (not a warning)

- `queue_policy.describe` reports **scan age** next to the candle window (`scanned_at`, `scan_age_s`, `max_scan_age_s`, `stale`, `stale_reason`). Budget `NBX_QUEUE_SCAN_MAX_AGE_S` (default 300 s, never below `SIGNALS_TTL_S`). A missing scan clock = stale (fail-closed).
- `GET /api/risk/plan?approval=true` returns **price drift from the plan** (`drift`): `mid` from the same order book the execution preview read (no extra request), signed `drift_pct`, `drift_atr`, `exceeded`/`reason`. Limits: `NBX_QUEUE_DRIFT_PCT` (1.5%) **or** `NBX_QUEUE_DRIFT_ATR` (0.5×ATR), whichever trips first — in a quiet market ATR speaks first. Unknown mid = locked.
- **Locked on the server, not only in the page:** `POST /api/paper/order` with source QUEUE refuses a stale plan with `queue-signal-stale` and a drifted one with `queue-price-moved` — at entry and again in the fill-time guard. These refusals are written to the paper book as `reject` events with `pre_broker: true` (item 7) so "how many approvals never became orders" is countable.
- **In the page:** amber chip ("scanned N min ago" / "price +2.2% (+1.1×ATR) from plan"), approve button disabled with a red sentence, age advancing on a one-second tick, and a "Rescan" button only on a stale row (`GET /api/signals?refresh=true`, not sooner than 20 s after the previous scan). A stale row is **not removed** — it stays locked until a fresh scan; the candle TTL is never extended. An older server without these fields = no lock.
- Why a lock and not a warning: a clickable warning is ignored within three seconds; the cost of one approval on a drifted plan is exactly the "bad entry" the whole shadow gate exists to prevent. The cost of the lock is one click on "Rescan".

### B) Shadow arm `E_nochase`: the same hypothesis, record only

- Fifth arm in `shadow_arms` (`VERSION=2`): **the live verdict**, but LONG → NEUTRAL when the bar is a chase: `run4h_atr ≥ 2.0` **or** `%B ≥ 1.0`. Thresholds fixed in code (like B/C/D), not env — a shadow arm must not silently change between two deploys.
- **The same bar the live verdict saw:** `ta_score` writes `diagnostics.run4h_pct/run4h_atr` at issue; `ta_engine.run_up` counts by candle time stamps, not bar count, so 15m and 1h agree. Later backfills read those numbers, they do not recompute.
- **Unknown inputs cannot refuse:** short history or zero ATR → `run4h_atr: None` → the arm equals the live row and says `chase: null`. A shadow arm that reads "don't know" as "no" looks better in the holdout than it is.
- The arm never promotes, never touches the composite, leaves EXIT as EXIT; `signal_shadow_outcomes` and section 5 of `journal_diagnostic` pick it up unchanged; so does `shadow_backfill` (pre-2.5.2 rows have only `%B`). **The journal config hash is untouched** — deliberately, so the holdout series before 12 November is not broken.
- Promotion criterion (recorded now so it cannot move later): E becomes a candidate to go live when, over ≥ 2 holdout weeks with n ≥ 60 live LONGs, its mean R at 4h and 24h beats live **and** the share of refused LONGs is below 60% (if the arm kills half the signals the problem is the entry threshold, not chasing). No decision before 24 November.

## Consequences

- No change to scores, thresholds, stop k or gate enforcement in 2.5.2; the only new trading behaviour is **refusing a human order on a stale/drifted plan**, which is restrictive, not generative.
- `paper_state.json` now also holds pre-broker refusals; the "approval → order" count tells the truth.
- Reading E's result requires stop slippage (item 9, `stop_slip_r` on close events) next to the arm table: every stop cost 1 R + slip, and the chases are the ones that eat the most slip.
- Tests: `test_queue_policy.py` (51), `scripts/test_queue_stale_plan.cjs`, `test_shadow_arms.py` (15), `test_stop_slip.py`.
- Out of scope: the lock on the AUTO/SEMI paths (`_auto_signal_refusal` was already there), actually refusing chases in the live verdict, changing `min_depth_usd`.
