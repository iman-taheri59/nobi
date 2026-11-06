# ADR-0050 — Market-regime gate on LONG and a horizon-aligned stop, both behind flags

[فارسی](../fa/ADR-0050-گیت-رژیم-بازار-و-stop-همسو.md) · **English**

**Status:** Accepted — code on `main`; reaches production with the next deploy (after the soak ends 2026-11-10) and changes no service behaviour until then
**Date:** 2026-11-06
**Related:** ADR-0008 (risk), ADR-0022 (shadow gates), ADR-0044/0046 (Signal Intelligence and Drift), `docs/analysis/journal-diagnostic-2026-11-06.en.md`

## Context

The production-journal diagnostic (clean window after 28 October, ~5,900 signals, 943 LONG) established two facts:

1. **The composite has no directional edge and LONG is the worst bucket** (1h return: LONG −0.35 %, NEUTRAL −0.23 %, EXIT −0.13 %) while the whole universe fell 3.5–4 % in the window. The TA score was *anti*-correlated with 1h return (IC −0.07); FA and liquidity were positive (+0.11). Plainly: when everything falls together, "strongest chart" is not a buy signal.
2. **The stop does not match the horizon.** stop = 2×ATR(15m) ≈ 1.2 %; at the 24h horizon 93 % of LONGs hit it and R̄ = −0.64. Re-simulating the same data: H1 stop-rate k=1→54 %, k=2→23 %, k=3→5 %, k=4→1 %. The multiplier 2.0 was a literal repeated in four places (`journal.py`, `ta_engine.py`, `risk_engine.py`, `nbx-core.js`).

Project policy (ADR-0008, ADR-0022) is explicit: no trading-behaviour change without a shadow period and journal evidence; no hidden parameters.

## Decision

### 1) Market-regime gate (`server/regime.py`)

- **Input:** the same `market_overview()` the dashboard shows — `change.median_24h` and `breadth.pct_up` over the current universe. No new data source.
- **States:** `risk-off` if median ≤ `NBX_REGIME_MIN_MEDIAN_24H` (default −2.0 %) **or** pct_up ≤ `NBX_REGIME_MIN_BREADTH_PCT` (default 35 %); `risk-on` if median ≥ +2 and pct_up ≥ 65; otherwise `neutral`. If the overview is `ok=False` or lacks the fields → `unknown`, and **`unknown` never blocks** (a market-data failure must not silence signals).
- **Three modes via `NBX_REGIME_GATE`:** `off` (records nothing); **`shadow` (default)**: every snapshot gets `market_regime` and `regime_gate{mode, would_block, applied:false}`, direction untouched; `enforce`: a LONG in `risk-off` becomes NEUTRAL with the composite capped at 67 (`VETO_CAP`); `direction_raw`/`uncapped_composite`/`regime_cap` are kept — exactly the existing veto pattern.
- **Recording:** `market_regime`, `regime_gate`, `regime_cap` join the journal `features`; the regime at scan time is visible in `/api/signals`, `/api/health` (`market_regime`) and `/api/market/overview` (`regime`). `scripts/journal_diagnostic.py` splits LONG returns by `features.market_regime` (section 3b).
- **Promotion to `enforce` only on evidence:** at least two weeks of shadow, and in the diagnostic the mean return of `risk-off` LONGs must be materially worse (separated 95 % CIs) than `neutral`/`risk-on` LONGs. Changing the mode = env change + ADR amendment, not a code edit.

### 2) Horizon-aligned stop — the multiplier became one setting

- `NBX_RISK_K` (default **2.0, no behaviour change**) is parsed once in `risk_engine.parse_risk_k()` with bounds 1–5; a bad value falls back to the default with a warning, like `NBX_RR_LEVELS`. `journal.RISK_K`, `ta_engine.RISK_K` (manual ticket and paper plan) and `RiskConfig.risk_k_multiplier` all read from this source; `test_journal_and_ta_engine_read_the_same_multiplier` locks it.
- **No new multiplier is applied now.** k=3 is the candidate, but it is evaluated only as a 2.6.0 shadow-A/B arm (separate `config_hash`), not by changing production. Reason: a larger k shrinks position size (ADR-0008, fixed 0.75 % risk), and changing k in journal and live at once without a shadow period destroys the before/after comparison.
- `nbx-core.js:333` (browser engine, display only) still says `×2`; once the env value differs from 2 it must read it from `/api/health` or `/api/config` — open note in the CHANGELOG.

## Consequences

- From the next deploy the journal records the regime on the first scan; without that record no claim about "the gate helps / does not help" can be tested.
- In `enforce`, LONGs in a falling tape do not disappear; they become NEUTRAL capped at 67 with `direction_raw` kept — UI and Telegram follow the veto path.
- `unknown` is deliberately open: if `/market/stats` is unreachable the gate is off, not closed.
- The thresholds (−2 % / 35 %) were chosen on the diagnostic window and are in-sample there; the shadow evaluation on post-deploy data is the real holdout.
- This ADR does **not** change composite weights, the liquidity filter, fees-in-R or the shadow A/B — those come in the separate 2.6.0 ADR.
