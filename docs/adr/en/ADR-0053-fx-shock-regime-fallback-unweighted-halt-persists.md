# ADR-0053 — `fx-shock` regime, zero weight for the `fallback` FA profile, and a kill switch that survives a restart

[فارسی](../fa/ADR-0053-رژیم-fx-shock-و-وزن-صفر-FA-fallback.md) · **English**

**Status:** accepted — code on `main` for 2.5.1; two of the three items change no behaviour (the regime is recorded only; the zero weight applies only while data is cut), the third closes a safety gap
**Date:** 2026-11-07
**Related:** ADR-0050 (regime gate), ADR-0014 (three-state contract), ADR-0007 (premium veto), external review of 7 Nov §6-2, `docs/analysis/shadow-backfill-2026-11-07.md`

## Context

The server sits inside Iran and the reference market is rial-quoted. Three things happen together and none of them is an ordinary down market:

1. **The rial moves, not the coin.** 79 % of journal rows are IRT pairs. When the toman falls, every IRT pair "trends" in TA terms (EMAs, breakouts, %B) while the same coin is flat on USDT. The `premium-stretched` veto (> +4 %) blocks entries, but only if the premium stretches; a slower slide (premium under the bar, rate moving 3 %/day) walks past it. The ADR-0050 regime gate reads breadth, and breadth is **green** in that tape.
2. **International connectivity drops.** CoinGecko/DefiLlama/F&G disappear and `fa_engine` falls back to `FALLBACK_FA_PROFILES`: 14 hand-written profiles tagged `as_of: unversioned-legacy-profile`. The label is honest (`profile: fallback`); the number was still multiplied into the composite at weight 0.25. The external review listed this as finding 7.
3. **Restarts are likely** (power, network, `MemoryMax`). The kill switch (`risk.halted`, `broker.halted`) lived in memory only. The automatic triggers re-arm from persisted exit events, but a **manual halt** — the one an operator reaches for in a crisis — died with the process. My earlier claim that "the latch survives a restart" was wrong.

## Decision

### 1) `fx-shock` regime in `regime.py`
- New state, checked **before** `risk-off`, needs no breadth: `|premium_pct| ≥ NBX_REGIME_FX_PREMIUM_PCT` (default 6) **or** `|USDTIRT 24h change| ≥ NBX_REGIME_FX_MOVE_24H_PCT` (default 3). Inputs: the existing implied premium (`_premium_pct`) and the USDTIRT ticker's `change24`, which `_overview_for_regime` now attaches to the overview.
- A missing input is no verdict (neither `fx-shock` nor a forced `unknown`); a thin overview with a known premium is still a verdict.
- `apply()` treats `fx-shock` exactly like `risk-off`: `would_block` in `shadow`, LONG → NEUTRAL capped at 67 in `enforce`. Default stays `shadow`; `enforce` is the 12 Nov decision.
- Why 6 and 3: 4 % premium is already the ADR-0007 veto, 6 % means "past the veto and staying there"; a 3 % daily move in the free rate is not seen in normal conditions. Both are env-tunable and reviewed with data, like the rest of ADR-0050.

### 2) A `fallback` FA profile is shown, not weighted
- In `rescan`: when `fa.evidence.profile == "fallback"` the composite is built with `fa=None` (the same `ta+liq` renormalisation that already exists for `unavailable`). `fa_score` and `fa.profile` stay on the row, `fa_weighted: false` is added, `composite_basis = "ta+liq"` — the UI already shows its "no fundamentals" chip for that.
- Vetoes still apply; they come from live inputs (premium), not from the profile.
- Effect today: zero (CoinGecko reachable). Effect in a connectivity cut: no score is built from a number nobody can date.

### 3) The kill switch in `paper_state.json`
- `save()` writes `halted` and `halt_reasons`; `_apply_state()` restores them into the broker **and** the risk engine. `paper_halt` records `manual-halt` and saves immediately; `paper_resume` clears the reasons and saves. Test: `test_halt_survives_restart.py`.

## Consequences
- `/api/health` and `/api/market/overview` may report `fx-shock`; from 2.5.1 the journal records it on every row, so the diagnostic can separate LONG returns in that regime — a required input for the 12 Nov decision.
- Arm C_gate (ADR-0051) is now "live + regime" with both blocking states.
- The root problem (TA on an IRT pair = analysis of the dollar) is **not** solved; this ADR only blunts its worst consequence. The fix — TA on the coin's USDT series, execution on IRT — is a separate ADR after 12 Nov.

## Rejected
- Tighter thresholds or `enforce` by default: without recorded data that is the ADR-0009 mistake again.
- Deleting `FALLBACK_FA_PROFILES`: a displayed number with an honest label is useful to the operator; the weight was the problem, not the display.
