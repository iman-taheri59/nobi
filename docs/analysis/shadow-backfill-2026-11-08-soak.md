# Shadow arms — retrospective on `journal.db`

rows (15m, live, non-void): **11324** · horizons from the journal's own price series (±20 min) · fee round trip 0.4%

| arm | LONGs | share | 1h mean | 1h net | 4h mean | 24h mean | 24h net | hit 24h | IC 1h | IC 24h |
|---|---|---|---|---|---|---|---|---|---|---|
| live | 744 | 6.57% | -0.255 | -0.655 | -0.324 | 1.282 | 0.882 | 29.7% | -0.105 | 0.024 |
| D_comp | 603 | 5.32% | -0.157 | -0.557 | -0.588 | 0.181 | -0.219 | 25.5% | -0.105 | 0.024 |
| B_fa_liq | 601 | 5.31% | -0.143 | -0.543 | -0.738 | -1.577 | -1.977 | 20.5% | -0.077 | 0.066 |
| TA_only | 744 | 6.57% | -0.255 | -0.655 | -0.324 | 1.282 | 0.882 | 29.7% | -0.118 | -0.018 |
| ALL_rows | 11324 | 100.0% | -0.08 | -0.48 | -0.376 | -1.477 | -1.877 | 25.4% | -0.105 | 0.024 |
| live_EXIT | 9517 | 84.04% | -0.064 | -0.464 | -0.393 | -1.735 | -2.135 | 25.5% | -0.105 | 0.024 |

## LONG overlap between arms

- `live+D_comp+B_fa_liq+TA_only`: 269
- `live+D_comp+TA_only`: 245
- `B_fa_liq`: 243
- `live+TA_only`: 230
- `D_comp+B_fa_liq`: 89

## By universe membership (24h mean %, n)

| arm | core | mid | transient |
|---|---|---|---|
| live | -2.526 (324) | 9.603 (162) | -8.218 (12) |
| D_comp | -2.575 (311) | 7.911 (120) | -8.664 (8) |
| B_fa_liq | -2.33 (443) | 4.509 (57) | -8.156 (2) |
| TA_only | -2.526 (324) | 9.603 (162) | -8.218 (12) |
| ALL_rows | -1.854 (5401) | 1.606 (931) | -5.512 (206) |
| live_EXIT | -1.755 (4509) | -0.723 (659) | -5.03 (175) |

## k=3 bound (from resolver MAE on live LONGs stopped at H2)

- stopped at k=2: **436**; never saw MAE ≥ 3×ATR (k=3 would have survived): **364** (83.5%)
- survivors' 24h close-to-close return: **-1.558%**; their mean MFE: 1.068%
- bound, not a simulation: a k=3 stop also changes TP1/TP2 and size; C_gate not backfillable (market regime ships in 2.5.1)
