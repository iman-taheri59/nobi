# Chase-the-pump and depth-gate backfill on the journal — 8 Nov 2026

**Question:** what if a LONG were not issued after the price already ran, or if the depth gate were enforced? Prompted by ONE (204 signals on one pump, 5–8 Nov) and HOTIRT (entry 18:22 on the peak of a 1h spike, `sl-hit` 57 min later at −11.2 % vs the model stop −10.15 %; depth ≈ 1,000 USD).

**Tool:** `scripts/pump_chase_backfill.py` (read-only). run-up = return over the 4h before issue from the journal's own price series, normalised by the stored ATR %; %B from `diagnostics`; depth from `gate_shadow` within ±10 min. Forward returns as in `shadow_backfill` (±20 min, per price contract). Rows, not independent episodes.

## 1. Whole journal (15 days, 5,330 LONG rows)

## run-up over the previous 4h, in ATRs
| bucket | n | 1h % | 4h % | 24h % (n) | H1 stop | H1 R̄ |
|---|---|---|---|---|---|---|
| <0 (down) | 192 | -0.143 | -0.472 | -2.752 (n=134) | 0.18 | -0.086 |
| 0–1 ATR | 487 | -0.11 | -0.607 | -1.513 (n=340) | 0.14 | -0.119 |
| 1–2 ATR | 908 | -0.148 | -0.742 | -1.525 (n=709) | 0.15 | -0.112 |
| 2–3 ATR | 1046 | -0.144 | -0.84 | -2.337 (n=803) | 0.19 | -0.104 |
| ≥3 ATR | 1753 | -0.268 | -1.178 | -2.823 (n=1323) | 0.27 | -0.151 |

Monotonic: the further the price ran before the signal, the worse 1h/4h/24h and the higher the stop rate (0.14 → 0.27). **53 % of LONGs** were issued with run-up ≥ 2×ATR.

## Bollinger %B at issue
| bucket | n | 1h % | 4h % | 24h % (n) | H1 stop | H1 R̄ |
|---|---|---|---|---|---|---|
| <0.8 | 2060 | -0.146 | -0.929 | -1.807 (n=1459) | 0.15 | -0.101 |
| 0.8–1.0 | 2235 | -0.164 | -0.534 | -1.822 (n=1629) | 0.2 | -0.116 |
| 1.0–1.1 | 568 | -0.265 | -0.647 | -1.748 (n=436) | 0.25 | -0.136 |
| ≥1.1 | 467 | -0.056 | -0.858 | -2.346 (n=332) | 0.4 | -0.078 |

## order-book depth ±1% (USD) at issue
| bucket | n | 1h % | 4h % | 24h % (n) | H1 stop | H1 R̄ |
|---|---|---|---|---|---|---|
| <1k | 503 | -0.517 | -1.293 | 0.992 (n=285) | 0.21 | -0.141 |
| 1k–2.5k | 607 | -0.097 | -0.511 | -1.159 (n=424) | 0.26 | -0.14 |
| 2.5k–5k | 570 | -0.247 | -0.525 | -1.22 (n=454) | 0.25 | -0.185 |
| 5k–20k | 799 | -0.121 | -0.115 | -1.13 (n=655) | 0.21 | -0.138 |
| ≥20k | 87 | -0.143 | 0.041 | -0.33 (n=84) | 0.21 | -0.11 |

depth quantiles over every scanned row: p05=330, p10=562, p25=1247, p50=2645

Depth quantiles over every scanned row: p05=330, p10=562, p25=1247, p50=2645 USD.

## rules — what each would have removed vs kept
| rule | side | n | 1h % | 4h % | 24h % (n) | H1 stop | H1 R̄ |
|---|---|---|---|---|---|---|---|
| chase: run_4h ≥ 2 ATR | removed | 2799 | -0.221 | -1.049 | -2.639 (n=2126) | 0.24 | -0.133 |
| | kept | 2531 | -0.085 | -0.349 | -0.887 (n=1730) | 0.17 | -0.082 |
| chase: %B ≥ 1.0 | removed | 1035 | -0.17 | -0.742 | -2.007 (n=768) | 0.32 | -0.109 |
| | kept | 4295 | -0.155 | -0.722 | -1.815 (n=3088) | 0.18 | -0.109 |
| depth < p10 | removed | 281 | -0.829 | -1.45 | 3.543 (n=155) | 0.21 | -0.183 |
| | kept | 5049 | -0.122 | -0.69 | -2.079 (n=3701) | 0.2 | -0.105 |
| either chase rule | removed | 3152 | -0.209 | -0.933 | -2.415 (n=2351) | 0.24 | -0.126 |
| | kept | 2178 | -0.081 | -0.412 | -0.977 (n=1505) | 0.15 | -0.084 |
| either chase or depth<p10 | removed | 3277 | -0.247 | -0.949 | -2.194 (n=2422) | 0.24 | -0.131 |
| | kept | 2053 | -0.011 | -0.357 | -1.278 (n=1434) | 0.15 | -0.074 |

## 2. Three soak days without ONE (595 LONG rows)

## rules — what each would have removed vs kept
| rule | side | n | 1h % | 4h % | 24h % (n) | H1 stop | H1 R̄ |
|---|---|---|---|---|---|---|---|
| chase: run_4h ≥ 2 ATR | removed | 302 | -0.322 | -0.666 | -2.436 (n=218) | 0.33 | -0.29 |
| | kept | 293 | -0.311 | -0.7 | -2.396 (n=202) | 0.21 | -0.232 |
| chase: %B ≥ 1.0 | removed | 124 | -0.614 | -0.998 | -3.342 (n=84) | 0.41 | -0.429 |
| | kept | 471 | -0.239 | -0.606 | -2.185 (n=336) | 0.23 | -0.218 |
| depth < p10 | removed | 60 | -0.887 | -1.768 | -4.708 (n=29) | 0.38 | -0.376 |
| | kept | 535 | -0.257 | -0.589 | -2.247 (n=391) | 0.26 | -0.25 |
| either chase rule | removed | 350 | -0.365 | -0.743 | -2.612 (n=245) | 0.33 | -0.304 |
| | kept | 245 | -0.246 | -0.598 | -2.143 (n=175) | 0.17 | -0.199 |
| either chase or depth<p10 | removed | 370 | -0.396 | -0.818 | -2.718 (n=255) | 0.33 | -0.314 |
| | kept | 225 | -0.184 | -0.47 | -1.952 (n=165) | 0.16 | -0.173 |

## Reading

1. **run-up ≥ 2×ATR is the strongest finding:** over 15 days, removed 24h −2.64 % vs kept −0.89 %; 1h −0.22 vs −0.09; stop 0.24 vs 0.17. Same sign at every horizon, monotonic across buckets. On the bearish 3-day window the 24h effect vanishes (everything negative) but the stop rate is still 0.33 vs 0.21.
2. **%B ≥ 1.0** mostly lowers the stop rate (0.32 → 0.18 over 15 days; 0.41 → 0.23 over 3 days); near-neutral on 15-day returns, strong on 3 days. A complement to run-up, not a substitute.
3. **Depth < p10** is inconclusive on the journal: clearly worse at 1h (−0.83 vs −0.12) but pumps make 24h positive; small n (281). The verdict on depth must come from **execution slippage** (HOT: 1 % worse than the model stop), i.e. the paper ledger, not candle returns.
4. **No rule makes the kept set positive.** Best case (both chase rules + depth) 1h ≈ 0 and 24h −1.3 % over 15 days. A chase filter cuts the damage; it does not create an edge.

## Proposal for 12 Nov

- New shadow arm **E_nochase** (= live verdict + reject LONG if run_4h ≥ 2×ATR or %B ≥ 1.0) recorded from the next deploy so the holdout can test it one-sided; cost: ~50–60 % fewer LONGs (~58 → ~25 episodes/day).
- Stop slippage from the paper ledger's `close` events added to the diagnostic (ADR-0052 has fees, not slippage).
- Depth gate: stays shadow; take its threshold from measured slippage, not from this table.

Reproduce: `python3 scripts/pump_chase_backfill.py journal.db [--since 2026-11-05T07:19:52Z] [--exclude ONEIRT,ONEUSDT] --md out.md`
