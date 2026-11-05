# بررسی جامع Nobi / Nobitex Market Analyzer
# Comprehensive Technical & Quantitative Audit — Persian and English

> **Remediation update / به‌روزرسانی اصلاحات — 2026-11-04:** The original findings below are the historical baseline. The closing addendum records the remediated tree and final tests; it does not establish profitability or live readiness. / یافته‌های اصلی شرح baseline تاریخی‌اند؛ الحاقیهٔ پایانی وضعیت اصلاح‌شده و تست‌های نهایی را ثبت می‌کند و اثبات سودآوری یا آمادگی live نیست.

**مخزن / Repository:** `https://github.com/iman-taheri59/nobi`  
**نسخه / Version:** `2.2.2`  
**کامیت بررسی‌شده / Audited commit:** `2b5ea130f53959d357c5c8a664bfb1a52bd781e0`  
**تاریخ بررسی / Audit date:** 2026-11-03  
**وضعیت سورس / Source status:** هیچ فایل پروژه ویرایش نشد؛ working tree تمیز ماند.

> این سند دو بخش مستقل فارسی و انگلیسی دارد. منظور از «پیاده‌شده»، وجود مسیر واقعی در کد است؛ منظور از «اثبات‌شده»، وجود شاهد معتبر روی دادهٔ واقعی و خارج از نمونه است. این دو در تمام گزارش از هم جدا شده‌اند.

---

> **Archive note (2026-11-05):** snapshot review of commit `2b5ea13` (2.2.2); never rewritten. The current status of every finding lives in [`README.en.md`](README.en.md) in this folder.

# Part II — English Report

## 1. Executive assessment

Nobi is a relatively mature, testable software project. It has strong data-provenance controls, closed-candle handling, explicit synthetic/reference-data barriers, shared execution preflight logic, and a fee-aware walk-forward harness. The full Python suite passed: **1,742 tests**, together with lint, type checks, JavaScript checks, and the existing parity gates.

The current output should **not yet be treated as a validated trading signal or as ready for live promotion**, however. The principal reasons are quantitative rather than stylistic:

1. Raw IRT and USDT turnover values are compared directly both when selecting the top-60 universe and when calculating liquidity. This biases the sample and ranking toward IRT markets.
2. The live TVL score saturates at roughly **$2.15k**. A controlled probe returned `onchain=100` for TVL values of $5k, $5m, and $1bn, while the thin-TVL veto remains active up to $10m.
3. Backtest, Journal, and Paper do not model the same trade. TP2 is absent from both Journal and Paper, default backtest targets differ from live targets, trailing differs, and `return_pct` is not the full weighted trade return after TP1.
4. Baseline and live IC use different scores, cohorts, horizons, and outcome labels. MSI can therefore measure a definition mismatch rather than genuine signal drift.
5. EMA200 contributes after only 60 bars, zero-loss RSI becomes NaN in Python, and complete numerical Python/JavaScript TA parity is not established.
6. LONG/NEUTRAL thresholds are applied to TA, not to the final composite. High-composite NEUTRAL and low-composite LONG rows are therefore possible.
7. The repository does not contain current proof of live profitability: all 12 cells in the October 22 adaptive matrix were inconclusive and based on reference data, and the five-day validation document reports a disabled scan loop with `runs=0`.

**Bottom line:** this is a solid research platform, but unit correctness, outcome definitions, decision semantics, and statistical validation must be repaired before the rankings or promotion gates can support a live-trading claim.

## 2. Scope and verification

The audit covered the actual TA, FA, composite/veto, universe, candle cache, freshness, backtest, journal, Paper broker, risk, portfolio, benchmark, market overview, Signal Intelligence, and drift implementations. Documentation catalogs were checked against code rather than accepted as implementation evidence.

| Check | Result |
|---|---|
| Python tests | **1742 passed, 1 warning in 91.84s** |
| Ruff / mypy | Pass; mypy checked 160 files |
| JS lint and test suites | Pass |
| Preflight parity | 28 cases × 10 gates agreed |
| Tick/intrabar parity | 9 vectors agreed |
| Python dependency audit | No known vulnerabilities reported |
| npm audit | No high/critical; five moderate development-chain findings |

No live Nobitex scan, real order, deployment verification, or full Playwright browser run was performed. The independent numerical probes used controlled fixtures; they demonstrate formula behaviour, not market profitability. Project source files were not modified.

## 3. The implemented decision pipeline

1. `main._load_universe_locked` reads Nobitex stats and normalizes Rial prices to Toman. Raw `volumeDst/valueDst` is stored as `vol24`, sorted across all quote currencies, and truncated to 60 rows.
2. `nobitex_client` obtains sorted UDF candles and persists non-synthetic cache entries. The scan applies `queue_policy.closed_rows` to exclude the forming candle.
3. `ta_engine` computes trend, momentum, volume, and structure components, applies an ADX regime multiplier, and assigns the TA direction.
4. `main.rescan` maps raw `vol24` to a logarithmic liquidity proxy.
5. `fa_engine` uses CoinGecko, DefiLlama, Fear & Greed, and USDT/IRT premium to produce five sub-scores and vetoes.
6. `main.composite_score` combines TA, FA, and liquidity; `apply_vetoes` caps the score and may change direction.
7. Rows are ranked by composite. Market-quality gates remain shadow-only and do not remove candidates.
8. The journal records the signal at the scoring candle's close. Paper orders pass risk/preflight checks. Journal and Paper then use separate outcome logic.
9. Backtests replay only the TA component. Journal, IC, calibration, and drift analytics operate on their respective stored outcome definitions.

`EXIT` is not necessarily a short signal or an automatic position-closing instruction. Entries are long-only in the backtest, and Paper exits primarily follow stops, targets, trailing, and time stops.

## 4. Technical analysis — actual formulas

The implemented indicators are EMA9/21/50/200, RSI14, MACD12/26/9, ATR14, ADX/DMI14, Bollinger20 with sample standard deviation and two-sigma bands, relative volume against the previous 29 bars, and a close-based 19-bar breakout.

Core calculations include:

```text
TR = max(H−L, |H−C_prev|, |L−C_prev|)
+DI = 100 × RMA(+DM)/ATR;  −DI = 100 × RMA(−DM)/ATR
DX = 100 × |+DI−(−DI)|/(+DI+(−DI));  ADX = RMA14(DX)
%B = (Close − (SMA20 − 2σ))/(4σ)
stopDistance = 2 × ATR14
```

### Component scoring

- **Trend:** +20 for close above EMA21; +20 for EMA9 above EMA21; +25 for EMA21 above EMA50; +15 for close above EMA200; +20 for ADX>22 with +DI above −DI.
- **Momentum:** starts at 50. RSI adds 20 between 55 and 72, adds 5 at or above 72, and subtracts 15 below 45. A rising positive MACD histogram adds 25; a falling negative histogram subtracts 10.
- **Volume:** starts at 50. Volume above 1.4× its prior average adds 30; below 0.6× subtracts 15. An up close with above-average volume adds another 10.
- **Structure:** starts at 50. %B above 0.70 adds 25; below 0.25 subtracts 15. A close above the highest close of the preceding 19 bars adds 10.

```text
raw_TA = 0.40×Trend + 0.25×Momentum + 0.15×Volume + 0.20×Structure
TA = raw_TA × (1.00 when ADX>25, otherwise 0.85)
LONG at TA≥68; NEUTRAL at 55≤TA<68; EXIT below 55.
```

### Assessment

The formulas are transparent and deterministic, and the Python scalar/vectorized implementations are tested against each other. ATR-based risk and within-symbol relative volume are sensible starting points.

The important weaknesses are:

- **Insufficient warm-up:** EMA200 is allowed to award points after 60 bars. Because pandas seeds EWM from the first observation, the initial seed still has substantial influence; this is not a reliable long-term trend measurement. A minimum of 200 bars, preferably more for stabilization, is needed.
- **Degenerate RSI:** a zero-loss series produces NaN in Python. In the monotonically rising fixture, Python returned score **77.0** with `RSI=NaN`, while JavaScript returned **78.3** with RSI near 99.
- **Incomplete parity:** Python uses sample Bollinger variance and JavaScript population variance; RMA seeding and initial TR also differ. Existing preflight and tick parity tests do not prove complete TA parity.
- **Uncalibrated heuristics:** weights, 68/55 thresholds, and the 0.85 range multiplier are not empirically calibrated live parameters. Adaptive measurement is deliberately not applied.
- **No separate range strategy:** RANGE merely discounts the whole score. It does not implement mean reversion.
- **Score is not probability:** 70 does not mean a 70% win probability, and the score distribution is not stable across symbols and timeframes.

Multi-timeframe analysis, divergence, Ichimoku, VWAP, Supertrend, OBV/MFI/CMF, volume profile, candlestick patterns, Fibonacci/harmonics, and Hurst are not present in the production path.

## 5. Fundamental analysis — actual formulas

Implemented inputs are CoinGecko market/tokenomic fields, DefiLlama protocol TVL, Fear & Greed, USDT/IRT premium, and static fallback profiles. News/NLP, a real macro-economic feed, a real unlock calendar, holder concentration, and most on-chain analytics are not implemented. The declared `LLAMA_CHAINS_MAPPED` mapping is not used by the current protocol matcher.

### Market and tokenomics

Market starts at 85/74/62/50/38 for market-cap thresholds of $50bn/$10bn/$1bn/$100m/below. Volume/market-cap above 5% adds 6; above 50% instead subtracts 10.

```text
tokenomics = min(100, 130 × market_cap/FDV)
subtract another 12 when total_supply/max_supply < 0.6
```

Fallback uses a 95 multiplier rather than 130 and a different TVL formula, so live and fallback values are not the same measurement model.

### On-chain, sentiment, and macro

```text
if TVL>0: onchain = 40 + min(60, 18 × log10(TVL+1))
mapped asset with no TVL: onchain = 55
BTC/LTC/DOGE: add 12
0<TVL≤$10m adds the severe thin-tvl veto

sentiment = 40 + 0.40×FGI
subtract 10 when FGI≥85; FGI≤15 adds severe extreme-fear

macro = 40 for premium>+4%, 62 for premium<−4%, otherwise 55
premium>+4% also adds premium-stretched
```

```text
FA0 = 0.25×Market + 0.25×Tokenomics + 0.20×OnChain
    + 0.20×Sentiment + 0.10×Macro
FA = clip(min(FA0, FA0 + (liquidity−50)×0.20), 0, 100)
```

The `min` construction makes the liquidity adjustment penalty-only; values above 50 cannot improve FA. If this is intentional, it should be named and documented as such.

### Material FA problems

- TVL saturation occurs at `10^(60/18)−1 ≈ $2,153`. The $5k/$5m/$1bn probe all produced 100; the first two also received thin-TVL. The score discards scale precisely where the veto says the protocol is thin.
- Name/symbol matching can select the wrong protocol or chain. TVL should be asset-class-specific and explicitly mapped.
- An unmapped symbol still produced a numeric score of **52.5** with `profile=none`. The label is honest, but the composite still gives this value its full 25% weight.
- Provider failures can become neutral component values without a complete per-component freshness contract.
- The ten-minute FA cache key excludes `nobil_depth_score`. Changing depth from 20 to 100 without clearing cache left the score at 55.8; clearing cache and testing 50/80/100 all produced 61.8.

## 6. Liquidity, composite, and veto semantics

```text
L = round(clip(50 + 4×log10(vol24+1), 0, 100))
```

This is a turnover proxy, not order-book depth or spread. `vol24` is Toman-denominated for IRT and USDT-denominated for USDT. Missing and zero volume both collapse toward a neutral 50 rather than remaining unknown.

At an illustrative rate of 100,000 Toman/USDT, the same $1m turnover produces approximately:

- USDT pair: raw volume 1,000,000 and L≈74;
- IRT pair: raw volume 100,000,000,000 and L≈94.

Before clipping, the unit-only difference is approximately `4×log10(USDTIRT)`. More importantly, raw-volume sorting occurs before top-60 selection. `market_overview.py` already demonstrates the correct principle by converting IRT turnover to USD; that principle must be reused here.

```text
with FA:    Composite = 0.55×TA + 0.25×FA + 0.20×L
without FA: Composite = (0.55×TA + 0.20×L)/0.75
```

Normal vetoes cap composite at 67 and demote LONG to NEUTRAL. Severe vetoes cap it at 45 and set EXIT. Direction initially comes only from TA, however:

- `TA=68, FA=30, L=50` gives composite 54.9 but starts as LONG.
- `TA=60, FA=90, L=90` gives composite 73.5 but starts as NEUTRAL.

“TA controls direction, composite controls ranking” can be a valid design, but it must be explicit in field names and UI. Otherwise, separate `ta_direction`, `rank_score`, and `final_action` are required.

## 7. Risk, execution, backtest, and outcomes

Position sizing is:

```text
risk_amount = equity×risk_pct/100   (default 0.75%)
stop_distance = 2×ATR
quantity = risk_amount/stop_distance
```

The live plan uses TP1=1.2R and TP2=2R. Actual preflight gates cover manual halt, market closure, concurrent positions, per-order notional, exchange minimum, risk, heat, minimum cash, and funds. IRT execution correctly requires a valid FX rate.

Important limitations:

- The single-asset cap applies to the current order, not cumulative exposure to the same asset.
- Daily/weekly/consecutive-loss trigger methods exist, but no live call path was found that updates daily PnL and invokes them. Open heat is synchronized and the manual kill switch works; an automatic daily-loss guarantee does not.
- `loss_at_stop_usd` includes price risk plus entry fee, but not exit fee, slippage, or gaps.

Fees are fixed at 0.2% per leg and backtest slippage defaults to 6 bps per side, roughly 0.52% for a round trip before market impact. The actual exchange fee tier is not verified, so the flat fee cannot be guaranteed conservative for every market.

### Outcome mismatch

| Topic | Risk/Paper | Journal | Backtest |
|---|---|---|---|
| Signal basis | TA/FA/liquidity and vetoes | Stored composite | TA only; explicitly `matches_live=false` |
| Default TP1/TP2 | 1.2R/2R | 1.2R/2R | 1.5R/2.5R; grid searches other values |
| TP2 execution | Not present in `evaluate_positions` | Not present in `simulate_long` | Implemented |
| After TP1 | Half off; stop to `entry×1.002` | Same | Half off; stop to exact entry |
| Costs | Account fees; TP1 uses mid without book execution | None; gross | Entry/exit fee and slippage |
| Time exit | 48h time stop below 1% movement | Horizon end | Window/data end |

In the journal probe, entry=100 and ATR=1 imply stop=98, TP1=102.4, TP2=104. A future bar reaching and closing at 110 produced **3.1R**. Filling the second half at TP2 would instead produce gross **1.6R**. The row also returned `return_pct=10%`, because this field uses the remaining leg's exit price and omits TP1 proceeds.

Backtest strengths include train/eval separation, pessimistic stop-first intrabar handling, costs, and explicit TA-only disclosure. Remaining weaknesses are same-close fill assumptions, repeated same-symbol entries, no aggregate exposure, small-sample parameter selection with PF capped at 99.99, and an R denominator based on pre-cost initial stop risk. Two consecutive LONG bars created two trades in the independent probe.

## 8. IC, MSI, calibration, and drift

The IC comparison is not like-for-like:

- `backtest_ic` uses raw TA scores for all scoreable rows and fixed-horizon close-to-close returns.
- `ic_for_window` uses composite only for LONG rows in H2 and the modeled exit `return_pct`.
- `return_pct` can exclude TP1 proceeds and is therefore not the same label as total trade R or fixed-horizon return.
- LONG-only selection restricts the score range, and sample counts do not measure independent effective observations.

Per-bar signals and overlapping forward returns are autocorrelated. Normal confidence intervals and ordinary mean/proportion tests understate uncertainty. A useful time-aligned block-bootstrap prototype already exists in `scripts/measure_real_ic.py`; it should be integrated into production Journal/Drift evaluation rather than reinvented.

Drift has three additional concerns:

1. An overall run can be marked `stable` when every finding is `insufficient`; no evidence must not become a stability claim.
2. Current windows retain only resolved outcomes selected by signal timestamp, creating right-censoring, especially for H3. A maturity cutoff is required.
3. Cohort outcome/score queries do not apply the global `config_hash` filter, so configurations can be mixed.

Calibration deciles and descriptive dominant-family groups are useful, but they are not causal attribution or validated probabilities. The UI correctly discloses the latter. Weighted ablation/permutation with blocked validation is the appropriate next step.

### Configuration identity and replay

`journal.CONFIG_HASH` hashes only `RISK_K`, `TP1_R`, `TP2_R`, `TRAIL_R`, and `LONG_MIN`. It omits TA/FA/liquidity weights, the NEUTRAL threshold, regime rules, the TVL formula, mappings, provider schemas, and dataset identity. Two materially different engines can therefore share the same hash. The backtest report honestly calls its own parameter hash legacy and not a unique run/dataset identity. Complete replay requires a full config hash, dataset hash with timestamps and coverage, engine version, and source-unit/FX metadata alongside every result.

## 9. Other analytics

- **Market Overview** correctly excludes the FX pair, deduplicates IRT/USDT listings of the same asset, converts turnover to USD, and explicitly says its weighted change is not an index.
- **Benchmark** offers aligned returns, relative performance, capture, and simple alpha. Small autocorrelated samples still need confidence intervals.
- **Portfolio/ERC** is an analytical module, not an execution-linked allocator. Short covariance windows, missing-correlation assumptions, and absent capacity constraints make its weights exploratory.

## 10. Prioritized findings

| ID | Severity | Finding | First corrective action |
|---|---|---|---|
| F01 | High | Mixed raw quote volumes in universe and liquidity | Convert to USD before sorting/scoring; distinguish missing from zero |
| F02 | High | TVL saturation and weak mapping | Explicit asset-class mapping; N/A where inapplicable; redesign scale |
| F03 | High | TP2, return, and IC mismatch | One shared outcome contract and separate prediction/trade labels |
| F04 | High | Dependent samples and unlike baselines | Same cohort, effective N, block/cluster bootstrap |
| F05 | High | TA warm-up, RSI, and parity defects | Longer warm-up, edge-case tests, complete numerical parity |
| F06 | High | TA direction versus composite ranking | Separate `ta_direction` and `final_action`, or threshold the composite |
| F07 | Medium-high | No cumulative cap or operational loss-trigger path | Add aggregate exposure and test the full trigger wiring |
| F08 | Medium | FA cache/fallback/provider freshness | Complete cache key, reliability weighting, per-provider status |
| F09 | Medium | Execution-model differences | Next-open/latency/depth tests and identical target geometry |
| F10 | Medium | Incomplete config/data identity | Full config hash, dataset hash, and engine version |
| F11 | Medium | Drift stability, censoring, and cohort config issues | `insufficient` overall state and maturity cutoffs |
| F12 | Disclosed | Limited Paper/attribution metrics | Net round-trip metrics and ablation; retain current disclosures |

## 11. Strengths to preserve

- Closed-candle scoring and close-time indexing reduce obvious look-ahead.
- Synthetic cache is rejected, and synthetic/reference data cannot certify promotion.
- TA-only backtest scope and `matches_live=false` are explicit.
- Preview and execution share preflight logic, with 28×10 parity coverage.
- IRT execution requires valid FX rather than guessing.
- Walk-forward evaluation, costs, and pessimistic intrabar assumptions are useful foundations.
- Immutable evidence, VOID handling, linked scan cycles, and provenance support future replay.
- Newer interfaces explicitly disclose gross R, non-independence, retained-event limits, non-causal attribution, and unapplied adaptive settings.
- The engineering suite is substantial and all executed gates passed.

## 12. Recommended signal-evaluation improvements

### Decision and outcome contracts

Create a versioned `DecisionSnapshot` containing raw features, component scores, TA direction, rank score, final action, veto/gate reasons, quote units, FX, provider timestamps, engine version, full config hash, dataset hash, and scan ID. Choose explicitly between “TA selects direction, composite ranks” and a fully integrated final-action rule.

Use one outcome library for stops, TP1, TP2, trailing, partial fills, fees, slippage, and time exits across Backtest, Journal, and Paper. Keep three different measurements separate:

1. fixed-horizon close-to-close return for predictive IC;
2. weighted net trade return/R for strategy evaluation;
3. MFE/MAE for path behaviour.

### Data and features

- Store both quote turnover and USD turnover. Do not produce mixed-quote rankings without valid FX.
- Add spread, depth at fixed basis-point bands, expected slippage, and capacity to liquidity.
- Apply TVL only to applicable asset classes and use explicit provider mappings.
- Store point-in-time FA, liquidity, and event data with effective timestamps so the full composite can be backtested without look-ahead.
- Renormalize or reliability-weight missing components instead of assigning full-weight neutral measurements.
- Add higher-timeframe features only from candles already closed at decision time.

### Statistical validation

- Apply outcome-maturity cutoffs and handle overlapping horizons with non-overlapping cohorts, uniqueness weights, or block bootstrap.
- Use nested walk-forward/inner validation for parameter selection and freeze a final holdout.
- Report bootstrap confidence intervals for IC, expectancy, hit rate, and profit factor alongside effective sample size.
- Compare against buy-and-hold, cash, a simple baseline, and component-ablated models.
- Evaluate net expectancy, drawdown, turnover, exposure, tail loss, capacity, and fee/slippage stress—not hit rate alone.
- Mark drift `insufficient` when no adequately comparable sample exists.

### Calibration and modelling

Start with interpretable baselines such as logistic/ridge or a small gradient-boosted model under time-series validation. If scores are to become probabilities, calibrate on a separate validation set using Platt or isotonic regression and report Brier score/log loss. Use blocked ablation/permutation for component value. Additional indicators or complex models should be accepted only after stable out-of-sample net improvement.

## 13. Suggested roadmap and promotion standard

**Phase 0 — correctness:** repair F01/F02/F03/F05, add the numerical probes as regression tests, implement shared TP2 handling, define return/R precisely, lengthen warm-up, and establish full TA parity.

**Phase 1 — unified contracts and risk:** deploy the common decision/outcome models, add aggregate asset/cluster exposure, either wire and test loss triggers or explicitly remove the operational claim, and version complete configurations/datasets.

**Phase 2 — historical data and execution realism:** persist point-in-time FA/liquidity/order-book inputs; test next-open fills, latency, spread/depth, partial fills, actual fee tiers, and cost stress.

**Phase 3 — evidence-based promotion:** require venue-real data, exact live/backtest decision parity, positive out-of-sample and frozen-holdout results after all costs, stability across regimes, confidence intervals based on effective samples, cost/delay sensitivity, and Paper reconciliation against the same signals.

Existing thresholds such as PF≥1.3, three windows, and 30 OOS trades may remain initial policy gates, but they are not independently sufficient proof or empirically established “optimal” thresholds.

## 14. Final conclusion

Nobi is best described as a **defensive research and simulation platform**, not yet as a signal engine with demonstrated alpha. The highest-value next step is not more indicators or a more complex model. It is to correct units and TVL, unify trade outcomes, complete aggregate risk controls, and build genuinely comparable out-of-sample evidence with effective samples and realistic execution. Until then, scores should be treated as exploratory, and neither a green software suite nor a TA-only backtest PASS should be interpreted as authorization for live trading.

---


# Remediation addendum — 2026-11-04

The F01–F12 findings and numerical examples above describe the historical baseline. The remediated working tree now includes USD turnover ranking, component-aware FA evidence/cache semantics, explicit TVL mappings, complete TA numerical parity, Paper and Journal TP2 with weighted two-leg outcomes, shared 1.2R/2R defaults, operational risk-trigger wiring, stronger Journal/Backtest dataset and engine identity, paginated OHLC history, conservative gap/intrabar handling, pure Paper GETs, fail-closed live account preflight, genuine promotion evidence, IS-only grid selection, configurable fee scenarios with provenance, and pairwise timestamp-aligned portfolio correlations.

**Final verification:** **1776 Python tests passed**; mypy reported no issues in 165 files; Ruff, ESLint, and dashboard lint passed; JS broker/intrabar and all 20 DOM/Node 22 targets passed. The standalone mobile artifact was regenerated and its byte contract passed. Full details are in section 9 of `/home/user/nobi-review-of-reviews-and-capoy-en.md`.

The boundaries remain explicit: the backtest is TA-only; execution is still signal-close/OHLC without depth, latency, or partial fills; the real account fee tier is unknown; point-in-time composite data, frozen holdout, effective-sample inference, and original-payload archival are not complete; and pagination cannot defeat exchange retention. This remediation improves software correctness but does **not** prove alpha, live readiness, or deployment. No deployment or real order was performed.
