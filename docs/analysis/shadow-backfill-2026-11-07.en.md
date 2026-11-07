# Shadow arms — retrospective on `journal.db`

rows (15m, live, non-void): **36081** · horizons from the journal's own price series (±20 min) · fee round trip 0.4%

| arm | LONGs | share | 1h mean | 1h net | 4h mean | 24h mean | 24h net | hit 24h | IC 1h | IC 24h |
|---|---|---|---|---|---|---|---|---|---|---|
| live | 5166 | 14.32% | -0.151 | -0.551 | -0.681 | -2.269 | -2.669 | 27.8% | -0.09 | -0.107 |
| D_comp | 6100 | 16.91% | -0.132 | -0.532 | -0.686 | -2.063 | -2.463 | 29.7% | -0.09 | -0.107 |
| B_fa_liq | 8082 | 22.4% | -0.103 | -0.503 | -0.542 | -1.71 | -2.11 | 33.3% | -0.068 | -0.054 |
| TA_only | 5166 | 14.32% | -0.151 | -0.551 | -0.681 | -2.269 | -2.669 | 27.8% | -0.099 | -0.14 |
| ALL_rows | 36081 | 100.0% | -0.041 | -0.441 | -0.208 | -1.347 | -1.747 | 37.4% | -0.09 | -0.107 |
| live_EXIT | 25975 | 71.99% | -0.004 | -0.404 | -0.07 | -1.068 | -1.468 | 40.2% | -0.09 | -0.107 |

## LONG overlap between arms

- `live+D_comp+B_fa_liq+TA_only`: 4419
- `B_fa_liq`: 2431
- `D_comp+B_fa_liq`: 1232
- `live+D_comp+TA_only`: 417
- `live+TA_only`: 330
- `D_comp`: 32

## By universe membership (24h mean %, n)

| arm | core | mid | transient |
|---|---|---|---|
| live | -1.492 (1894) | -2.774 (620) | -3.289 (1030) |
| D_comp | -1.233 (2595) | -2.744 (716) | -3.451 (1116) |
| B_fa_liq | -0.906 (4073) | -2.617 (851) | -3.552 (1277) |
| TA_only | -1.492 (1894) | -2.774 (620) | -3.289 (1030) |
| ALL_rows | -0.868 (12768) | -1.722 (5453) | -1.932 (6524) |
| live_EXIT | -0.705 (9077) | -1.419 (4166) | -1.449 (4586) |

## k=3 bound (from resolver MAE on live LONGs stopped at H2)

- stopped at k=2: **3565**; never saw MAE ≥ 3×ATR (k=3 would have survived): **3130** (87.8%)
- survivors' 24h close-to-close return: **-2.749%**; their mean MFE: 1.785%
- bound, not a simulation: a k=3 stop also changes TP1/TP2 and size; C_gate not backfillable (market regime ships in 2.5.1)
