# Signal-journal diagnostic — day 2 (2026-11-07, 07:09Z) — compared with 6 November

[فارسی](journal-diagnostic-2026-11-07.md) · **English** · previous: [6 November](journal-diagnostic-2026-11-06.en.md)

Source: copy of `journal.db` (83 MB, 39,975 rows to `07:00Z`); `scripts/journal_diagnostic.py` (read-only).
"New" window = only data after the previous report: `--hash 0337bc4e6ff384ff --since 2026-11-06T06:00` → 4,017 signals, 402 LONG, 60 symbols, ~25 h.

## 1. What happened to yesterday's findings with 25 h of fresh data

| Metric | 6 Nov (38 h, sell-off) | **New window (25 h, flat tape)** | Whole post-era (from 31 Oct) |
|---|---|---|---|
| Market (median universe change in window) | −3.5 to −4 % | **0.00 %**, 35 % of symbols up; BTC +0.3, ETH +0.6 | — |
| 1h return: EXIT / NEUTRAL / LONG | −0.13 / −0.23 / **−0.35** | +0.04 / −0.03 / **+0.24** (n=402) | −0.02 / −0.12 / −0.17 |
| 4h LONG vs EXIT | — | **+1.92 vs −0.04** (n=362) | +0.10 vs −0.24 |
| 1h IC: ta / fa / liq / composite | −0.07 / +0.11 / +0.11 / 0.00 | **+0.07 / +0.06 / +0.07 / +0.13** | −0.01 / +0.02 / +0.08 / +0.05 |
| H1: R̄ / stop / timeout | −0.22 / 23 % / 71 % | **−0.10 / 24 % / 73 %** (n=406) | −0.17 / 26 % / 73 % |
| H2: R̄ / stop | −0.64 / 93 % | −0.24 / 78 % (n=9, meaningless) | −0.49 / **89 %** (n=1175) |
| H1 stop-hit if k=3 | 5 % | 2 % | 4 % |

## 2. Reading — short and honest

1. **The IC sign flipped with the market regime.** In the sell-off TA was anti-predictive; on a flat tape the same TA turned positive and the composite reached +0.13. That is exactly the ADR-0050 hypothesis: the primary problem was not the weights but the **market context**. Until the regime is recorded (post-soak deploy) this is a two-point observation, not proof.
2. **In the new window LONG genuinely beat the universe** (1h +0.24 vs 0.00 market median; 4h +1.9). n=402 is small, but the direction contradicts "the system only buys sell-offs". **Conclusion: re-weighting on yesterday's report would have been wrong** — which is why we waited.
3. **Stop geometry is broken regardless of regime.** Even in a window where LONG was positive, H1 R̄ stayed negative (−0.10): 24 % stops at 2×ATR and 73 % timeouts means trades are either stopped early or reach no target. H2 over the whole era: 89 % stops. This repeated **in both regimes** → the only finding stable across both reports, and the k=3 shadow test (ADR-0051) is better justified.
4. Symbols: the worst list changed completely (PUMP/BEAMX/NEAR yesterday → DOOD/ONEUSDT/1M_PEPE today, all small losses); no "always bad" symbol with enough n → **manual symbol removal is unfounded** (the user's constraint was right). ONEIRT at n=88, +1.9 % is a positive outlier, not a pattern.

## 3. Consequence for the plan

- **No weight/threshold/symbol change.** The confirmed order stands: 10 Nov soak report → 2.5.1 + deploy of ADR-0050/0051 (shadow) → two weeks of data → arm comparison on identical bars.
- Actions 4 (fees in R) and 6 (frozen holdout) remain data-independent; action 5 (liquidity filter) got **weaker** with this report (liq IC +0.11 → +0.07; concentrated loss vanished) → shadow arm only, not a gate.
- Sections 3b and 5 of the report stay empty until the deploy (the script now also runs on a DB without the shadow table).

Fees are in no number (~0.4 % round trip); net of fees the new window's 1h LONG is roughly zero.
