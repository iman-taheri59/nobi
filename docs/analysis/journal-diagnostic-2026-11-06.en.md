# Signal-journal diagnostic — 2026-11-06

**English** · [فارسی](journal-diagnostic-2026-11-06.md)

**Data:** copy of `journal.db` from backup `nobitrader-state-20261106T001501Z` (journal only; no `auth.db`).
**Range:** 2026-10-27 09:12Z → 2026-11-06 00:03Z — 33,426 signals, 8,363 outcomes, 3,424 scan cycles, 108 symbols, `15m` only.
**Question:** why is the journal IC negative and expectancy −0.39R — "slow" or "wrong"?

> Diagnosis, not proof: the clean period (after the 2.3+ remediation) is only **~38 hours**, symbols are correlated and windows overlap. Nothing here is enough for a real-money decision; it is enough to decide what to test first.

## 0. Two eras that must not be mixed

| Era | `config_hash` | Signals | Note |
|---|---|---|---|
| pre (1.8.0 → 2.2.2) | `463c14c224`, `cad604…`, `71cf67…` | 27,484 | the IRT price contract switched rial→toman on Oct 28 (`irt-toman-v2`); raw mean returns of this era are meaningless (×10 jumps in 19 symbols). Medians are usable. |
| **post (2.3+)** | `e00e9f17…`, `0337bc4e…` | 5,942 (943 LONG) | from 2026-11-04 10:15Z (~38 h). **All numbers below are from this era** unless stated. |

## 1. Main finding: the market fell during the clean window and the system only goes LONG

Forward return of the **signal price series itself** (close-to-close, no stop/TP), by engine label:

| Horizon | EXIT (n) | NEUTRAL (n) | **LONG (n)** |
|---|---|---|---|
| 1h | −0.13% (3947) | −0.23% (840) | **−0.35%** (934) |
| 4h | −0.67% (3412) | −0.47% (794) | **−0.62%** (886) |
| 24h | −4.06% (868) | −3.40% (352) | **−3.51%** (553) |

- The whole universe dropped 3.5–4% per 24h. **Any LONG-only scoring loses in this tape.** Most of the "−0.39R" is this, not a formula bug.
- Within that tape, LONGs did **worse** than EXIT/NEUTRAL at 1h and 4h (−0.35 vs −0.13): short-horizon selection is anti-predictive — buying momentum in a downtrend buys local tops.

## 2. Which score component carries information? (Spearman IC vs 1h return, n=934 — output of `scripts/journal_diagnostic.py`)

| Component | IC |
|---|---|
| `ta_score` | **−0.071** |
| `fa_score` | **+0.114** |
| `liquidity_score` | **+0.106** |
| `composite` | 0.000 |

The composite weights TA heavily and TA is negative in this window; FA and liquidity are positive and get cancelled. (The server's `msi_baseline` over 9 symbols / 96 bars also gave IC≈0.003 — consistent.)
**Caveat:** 38 hours. A hint, not a verdict — but exactly what B1 (shadow A/B) should adjudicate.

Regime and rank separate nothing: TREND −0.40% (n=735) vs RANGE −0.19% (n=199); ranks 1–3 (−0.34) are no better than 9+ (−0.41).

## 3. Trade geometry: the stop does not match the horizon

- stop = 2×ATR(15m) ≈ **1.26% median** (p10 0.61%, p90 2.31%). TP1 ≈ 1.2× and TP2 ≈ 2× that distance.
- H2 (24h) outcomes: **93% stop, 7% TP2/trail, 0% timeout.** Almost every coin moves 1.3% down within a day; a 15-minute stop with a 24-hour horizon is a negative coin flip.
- H1 (1h): 71% timeout, 23–28% stop, only 8% TP1; mean MFE +0.52% vs MAE −0.62%.
- Re-simulated with recorded MAE (H1 only): stop-hit share at 1×ATR 54%, 1.5× 35%, 2× 23%, **3× 5%**. (Widening the stop without shrinking size raises risk — they must move together.)
- H2 expectancy (−0.64R) being much worse than H1 (−0.22R) is mostly geometry, not scoring.

## 4. Symbols

Worst in the clean window (1h return after LONG): BEAMXIRT −0.96% (n=32), SIRT −0.81% (25), ZECIRT −0.67% (37), ZECUSDT −0.54% (38), SUIUSDT −0.39% (25). Near flat: XAUTUSDT +0.03% (79), ETHUSDT +0.08% (46). Thin IRT alts concentrate the loss — consistent with the positive `liquidity_score` IC.

## 5. Checked and healthy

- the resolver writes only with a complete horizon (`i + bars ≤ len−1`); no partial timeouts. ✓
- stop filled at `min(open, stop)`; same-candle TP1+stop marked `ambiguous` (43 in H1, 45 in H2). ✓
- fees are **not** in `return_pct` — the numbers above are optimistic (another ~0.4% round trip comes off).
- 363 clean-era LONGs have no H2 yet (less than 24h old) — correct pending, not a leak.

## 6. What to do — by impact/cost

| # | Action | Why | Cost | Where |
|---|---|---|---|---|
| 1 | **Market-regime gate on LONG issuance**: if benchmark 24h return (BTC/ETH or breadth from `market_overview`) < −2% or breadth < 35% → no LONG / half size | biggest loss source; the data already exists (A6) | low | `scoring`/gates + ADR |
| 2 | **Horizon-consistent stop**: ATR(1h) or 3×ATR(15m) for the 24h horizon with proportional size (fixed IRT risk) | 93% stops at H2 | low; pre-testable on this journal with archive candles | `risk_engine`/`journal.simulate_long` |
| 3 | **Real B1 — shadow A/B** of three weight configs: current / FA+liquidity heavy / TA inverted in downtrend; all journaled under separate `config_hash`, no effect on the desk | the only valid way to decide a reweight | medium | `scan_loop` + journal |
| 4 | fees in `return_pct`/R (and the real fee tier — limitation 9.3 #4) | current numbers are optimistic | low | `journal.py` |
| 5 | stricter liquidity filter for thin IRT pairs (BEAMX, S, …) | positive liquidity IC; concentrated loss | low | gates |
| 6 | frozen holdout + rerun this report weekly with the same script | avoid fooling ourselves with 38 hours | low | `scripts/journal_diagnostic.py` |

**Do not:** reweight production directly on these 38 hours, or add ML. Regime and geometry first; measure with shadow A/B; weights last.

## 7. Constraints

- Nothing here is deployed before the soak ends (Nov 10). Items 1 and 2 are candidates for 2.6.0 with an ADR.
- Script behind these numbers: `scripts/journal_diagnostic.py` (read-only, on a copy of `journal.db`).
