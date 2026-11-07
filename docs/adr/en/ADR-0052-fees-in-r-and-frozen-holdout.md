# ADR-0052 — Fees in R (new columns, no history rewrite) and a frozen holdout with a weekly report

[فارسی](../fa/ADR-0052-کارمزد-در-R-و-holdout-منجمد.md) · **English**

**Status:** Accepted — code on `main`; active with the post-soak deploy; changes no trading behaviour
**Date:** 2026-11-07
**Related:** ADR-0008 (`fees.py` as the only place a fee is written), ADR-0050/0051, `docs/analysis/journal-diagnostic-2026-11-0{6,7}.en.md`

## Context

Both diagnostic reports ended with "fees are in no number". At 0.2 % per side (`fees.FEE_PCT`, labelled `default-assumption-unverified-account-tier`) the round trip is 0.4 %, which at the 1h horizon with a ≈1.3 % stop is **0.3 R** — the same order of magnitude as the R̄ values under discussion. Without fees a "slightly positive" arm can be genuinely negative.

Second problem: the 6 November report was written on 38 hours of data and the ADR-0050 thresholds came from that window; the 7 November report showed the findings flip sign with the market regime. Without a recorded cut line, every later report can unknowingly be evaluated on data the thresholds were derived from.

## Decision

### 1) Fees in R — new columns only
- `simulate_long` returns three new fields: `return_net_pct = return_pct − fee`, `r_net = r_multiple − fee / stop%`, `fee_pct` (= `2 × FEE_PCT × 100`, currently 0.4). Both legs pay on full notional, so splitting the exit into TP1/TP2 does not change the total.
- `signal_outcomes` and `signal_shadow_outcomes` gain `return_net_pct`, `r_net`, `fee_pct` via the idempotent migration. **Historical rows stay NULL**; nothing is rewritten. `fee_pct` is stored per row so that if the real account tier is verified tomorrow, old rows are read with the old assumption.
- `r_multiple`/`return_pct` stay gross; every existing consumer (MSI, funnel, drift) is unchanged. `journal_diagnostic.py` §3 prints `R̄net` with its own n next to R̄.

### 2) Frozen holdout
- One `journal_meta` key: `holdout_cutoff = {cutoff_ts, frozen_at, note, config_hash, previous}`. `journal.freeze_holdout()` **raises** if a value exists unless `force=True`, in which case the previous value is kept inside the new record — moving the cut after looking at the data cannot be hidden.
- CLI: `scripts/journal_holdout.py show | freeze --at ISO [--note] [--force]`. Official cut: **`2026-11-10T07:20:00Z`** (the post-soak deploy) — everything before it is in-sample (the 6/7 November reports and the ADR-0050/0051 thresholds), everything after it is held out.
- `journal_diagnostic.py` states in its header whether the report window is entirely post-cut or contains in-sample rows.

### 3) Weekly repetition without hands
`deploy/nobi-journal-diagnostic.{service,timer}`: Mondays 00:30Z, as user `nobi`, takes a consistent copy (sqlite online backup) in `mktemp`, writes the last-14-days report to `/srv/nobi-data/reports/journal-diagnostic-YYYY-MM-DD.txt`, deletes the copy; 180-day retention. Read-only, `Nice=10`, `IOSchedulingClass=idle`; no access to the service, env or Telegram.

## Consequences

- The "gross" numbers in earlier reports remain valid and comparable; from the deploy on, every report has both.
- The ADR-0051 winning criterion is judged **on `r_net`**, not `r_multiple`.
- If the real fee tier is verified (`FEE_ACCOUNT_TIER_VERIFIED`), only `fees.py` changes; new rows record the new value, old rows keep the old one.
- The holdout is "frozen", not "hidden": held-out data is looked at, but thresholds are not tuned on it; any new tuning = a new cut with `--force` and an ADR amendment.
- Installing the timer is part of the 2.5.1 release runbook (`cp deploy/nobi-journal-diagnostic.* /etc/systemd/system/ && systemctl enable --now nobi-journal-diagnostic.timer`) and is not done during the soak.
