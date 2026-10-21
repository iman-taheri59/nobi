# ADR-0012: Signal Performance Monitoring, Attribution and Prediction Reporting

[فارسی](../fa/ADR-0012-پایش-و-گزارش-عملکرد-سیگنال-ها.md) · **English**

- **Status:** Accepted (partially implemented)
- **Date:** 2026-02-14
- **Deciders:** Technical Architect, Quant Lead
- **Related ADRs:** 0005, 0006, 0007, 0011
- **UI surface:** "Performance Report" page in the `index.html` prototype

---

## 1. Context

The bot emits a composite score per symbol each cycle and selects the best candidates (ADR-0005/0007). To be trustable and auditable — independently of trade execution — the system must later be able to answer: **"what would have happened had every signal been followed?"** This layer serves three purposes:

1. **Signal quality (calibration):** does a higher score actually yield better outcomes?
2. **Strategy selection:** which signal family (trend/momentum/volume/FA) drives success most? (Attribution)
3. **Promotion evidence:** the data needed to graduate from paper trading (ADR-0001/0009).

## 2. Decision — Monitoring Model

### 2.1 Prediction Targets
For every emitted signal, four evaluation horizons are recorded:
- **H1:** short horizon (e.g., +1 hour / +4× 15m candles) — immediate usefulness
- **H2:** mid horizon (+1 day / daily close) — the primary decision horizon
- **H3:** long (+7 days) — resilience across structural noise
- **R-Multiple:** did the position reach TP1 before the initial ATR stop? (R computed with ATR distances)

### 2.2 `signal_log` table (immutable)
```
id, ts, symbol, timeframe, direction, ta_score, fa_score, liquidity_score,
composite_score, rank_in_cycle, regime, mta_flags,
features(jsonb),            # decision-time inputs for reconstruction
predicted_targets(jsonb)    # horizons + TP/SL levels used
```
### 2.3 `signal_outcomes` table (filled progressively per horizon)
```
signal_id, horizon_key, exit_ts, exit_price,
mfe_pct (max favorable excursion), mae_pct (max adverse excursion),
hit_tp1 / hit_sl / timeout, r_multiple, realized_return_pct
```
### 2.4 Reported metrics (filterable uniformly by: symbol, timeframe, regime, family, period)
- **Hit-Rate:** % of resolved directional signals with positive outcomes (neutrals excluded).
- **Expectancy (avg R):** ΣR/n — the core judgment metric.
- **Virtual Profit Factor:** sum of positive vs negative R as if all signals were executed.
- **Calibration Curve:** score-deciles (60–70, 70–80, …) vs hit-rate — "well-estimated" diagonal.
- **Information Coefficient (IC):** Spearman correlation between initial score and realized return per horizon.
- **Family Attribution:** avg R broken down per signal/FA family.
- **Execution shortfall:** Paper Σ − Backtest Σ for the same config and window, surfaced in the UI.

### 2.5 Health gates
- Nightly **MSI (Model Signal Integrity):** if the realized IC over the trailing 30 days falls below half the backtest IC for the same config, trigger a "signal degradation" alert + force re-validation before any live promotion (aligned with ADR-0009).
- **Timing strictness:** entry must fill within 1 candle of the prediction bar or the signal is excluded from Hit-Rate accounting.
- Signal lifecycle states: `PENDING` → `IN_PROGRESS` → `RESOLVED`, plus `VOID` for halted/delisted symbols.

### 2.6 Auditable outputs
- "Performance Report" dashboard page with filters: period, symbol, timeframe, family, direction.
- Per-signal drill-down: mini price path + TP/SL markers + outcome chips (MFE/MAE/R) per horizon.
- Scheduled JSON/CSV exports + periodic Telegram digest.
- Long-term provenance: `config_hash` committed on every row for full replay.

## 3. Alternatives
| Simpler model | Verdict |
|---|---|
| Only realized trade PnL | Rejected: selection bias (missed candidates invisible) and no calibration signal |
| Backtest-only claims | Rejected: representation bias, no live-truth linkage |

## 4. Consequences
**Positive:** full auditability from score to outcome; continuous calibration; feedback data for pack re-weighting (ADR-0005).
**Negative:** incremental storage (cheap — thousands of small rows/day) and extra cleanup rules for symbol halts (mitigated by the VOID state).

---

## Implementation amendment — 2026-10-20

- **Implemented:** the SQLite journal (`server/journal.py`), the VOID rule with a grace
  period, MSI and the `/api/journal/*` endpoints, digest and CSV export.
- **Implemented (2026-10-20):** strategy-stage transitions. `journal.next_stage()` is a
  pure policy function (`train → run → watch → retire`) and `GET /api/journal/stage`
  returns its verdict with a reason and a translatable key.
  - `insufficient` never promotes: absence of evidence is not evidence of health.
  - `corrupted` retires immediately, and `retire` is terminal.
  - Stepping down from `run` to `watch` needs **three consecutive** degraded readings,
    while one healthy window is enough to run — the asymmetry is deliberate, so the rule
    does not thrash on daily noise.
  - **Promotion to live money stays manual** (ADR-0009), and the API payload says so.
- **Rule adopted in practice:** hit rate and PF show "no data" with a reason rather than
  a bare dash when the sample is empty (ADR-0014).
