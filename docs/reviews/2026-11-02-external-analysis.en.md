# Independent Review of nobi — Nobitex Market Analyzer

> **Archive note (2026-11-05):** snapshot review of commit `2b5ea13` (2.2.2); never rewritten. The current status of every finding lives in [`README.en.md`](README.en.md) in this folder.


**Repository:** https://github.com/iman-taheri59/nobi  
**Branch:** `main` · **Version:** `2.2.2` · **Reviewed commit:** `2b5ea130f53959d357c5c8a664bfb1a52bd781e0`  
**Review date:** 3 November 2026  
**Scope:** source review, formula audit, existing tests, independent offline probes, and improvement recommendations. No API credentials, no real orders, no live Nobitex profitability measurement.

> This is a technical/quantitative review, not investment advice. A “signal” in this project is a rule-based label, not a calibrated probability of profit.

---

## 1. Executive summary

`nobi` is not a thin prototype. It is a bilingual dashboard with a FastAPI backend, technical-analysis engine, limited fundamental engine, risk management, paper trading, walk-forward backtesting, signal journal, drift monitoring, and a guarded live-execution boundary. Its engineering hygiene, documentation, and test culture are materially stronger than a typical trading-dashboard prototype.

However, at the reviewed commit, **the full live decision strategy cannot yet be described as validated**. The live score blends `TA + FA + Liquidity`, while the backtest explicitly tests only `TA`. In addition, independent probes found numerical and semantic mismatches between engines, plus several evaluation biases.

**Maturity assessment:**

| Area | Assessment |
|---|---|
| Architecture, tests, CI, docs | Strong; with maintainability debt in large files and duplicated engines |
| Technical analysis | Medium; standard primitives, but heuristic scoring and Python/JS divergence |
| Fundamental analysis | Limited-to-medium; partial coverage, static fallback, no point-in-time history |
| Signal fusion and liquidity | Needs correction; quote-currency bias and non-live inputs in the decision score |
| Backtesting | Medium; good walk-forward structure, but far from the live decision/execution path |
| Signal evaluation and drift | Good infrastructure; statistics need population alignment and dependence-aware uncertainty |
| Risk and live execution | Careful engineering; operational readiness must not be confused with strategy validation |

**Top priorities before relying on signals for live execution:**

1. Unify Python/JS technical and backtest semantics with shared golden vectors.
2. Normalize liquidity to USD/executable depth rather than nominal quote-currency volume.
3. Remove or explicitly down-weight static/stale fundamental profiles in the decision score.
4. Fix backtest treatment of gaps, entry-candle risk, aggregate asset caps, and post-slippage risk.
5. Make the adaptive experiment test the same threshold that the UI recommends.
6. Align baseline and live IC/MSI populations, score definitions, horizons, and cost basis.
7. Separate operational order-execution readiness from strategy approval.

---

## 2. Review method and execution evidence

The review was pinned to the commit above. Application source was not modified. Independent probes live in the review workspace and use deterministic synthetic inputs and mocked providers only.

| Check | Result |
|---|---|
| `pytest -q server/tests` | **1,742 passed**, one Starlette/httpx deprecation warning |
| `ruff check server scripts` | passed |
| `mypy` | passed; 160 source files |
| `npm run lint` and `lint:dashboard` | passed |
| 23 JS/JSDOM suites | all passed on Node `v24.22.2` |
| Broker, tick parity, preflight parity, intrabar parity | passed |
| `pip-audit -r server/requirements.txt` | no known vulnerabilities found |
| `npm audit --audit-level=high` | exit 0; 5 **moderate** advisories in the ESLint/minimatch development chain |
| Independent review probes | **17 probe groups**, including one positive parity control; results in `evidence/independent-probes.json` |

Environment: Python `3.13.16`, repository-pinned dependencies, pandas `3.1.0`, numpy `2.5.4`, Node `24.22.2`. The project CI targets Python 3.12, so this review does not replace the official CI run.

**Limits:** no live Nobitex connection, real order, historical profitability claim, full browser E2E against a real deployment, or penetration test. Lack of profitability evidence in this snapshot is not proof that the strategy loses money.

---

## 3. Architecture and data flow

Main decision path:

```text
Nobitex REST/WS
  → units / symbol normalization / closed-candle filter
  → candle cache
  → TA score
  → liquidity score from market stats
  → FA score from CoinGecko / DefiLlama / alternative.me
  → composite + vetoes
  → ranked signals
  → shadow market-quality gates
  → journal / scan-cycle linkage
  → outcomes / IC / MSI / drift / cohorts
  → approval queue
  → risk preflight
  → paper broker or live boundary
```

Architectural strengths:

- Currency and symbol conversion are centralized in `server/units.py`; rial/toman conversion and `btc-rls → BTCIRT` happen at the API-client boundary.
- Closed candles are separated from forming candles (`queue_policy.closed_rows`).
- Data source, candle age, book age, and `live/cache/fallback/none` states are largely traceable.
- The journal uses one row per `symbol,timeframe,bar_close`; repeated polls do not multiply the same bar.
- Risk preflight and the paper broker share one `preflight` function.
- Live execution is disabled by default; dry-run, explicit confirmation, roles, CSRF, idempotency, and unknown-state ledgers exist.
- There are 48 ADRs and bilingual docs. The capability catalogues honestly mark many items as partial or absent.

Technical debt:

- `server/main.py` is about 5,538 lines and mixes API routes, state, scanning, journaling, gates, and execution.
- Python and JS are parallel implementations; current parity tests cover part of the contract, not all of it.
- Some documentation numbers are stale; for example, the README mentions 1,449 tests while the reviewed suite ran 1,742.

---

## 4. Implemented technical analysis

Primary sources: [`server/ta_engine.py`](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/ta_engine.py#L20-L307) and the JS mirror in [`nbx-core.js`](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/nbx-core.js#L140-L330).

### 4.1 Actual components

| Component | Implementation | Technical view |
|---|---|---|
| EMA | 9, 21, 50, 200; `ewm(span=n, adjust=False)` | Standard formula; EMA200 is not fully warmed with only 60 bars |
| RSI | 14; Wilder smoothing via `alpha=1/14` | Core formula is right; Python returns NaN when all changes are gains |
| MACD | 12, 26, 9; histogram = MACD − signal | Correct; score uses positive/rising histogram, not only crossovers |
| ATR | 14; Wilder/RMA over True Range | Appropriate for stop distance; gap/slippage modeling is separate |
| Bollinger | 20; sample standard deviation in Python | JS uses population standard deviation; this alone creates divergence |
| ADX/DI | 14; Wilder smoothing | Reasonable; regime threshold is 25 while trend-score threshold is 22 |
| Volume | Mean of previous 29 bars versus current volume | Causal; “30-bar average” wording is imprecise |
| Structure | Break of previous 19-bar high plus %B | Not full market structure; no pivots, S/R, divergence, or MTA |

Score formula:

```text
TA_raw = 0.40·Trend + 0.25·Momentum + 0.15·Volume + 0.20·Structure
TA     = TA_raw × (1.0 if ADX>25 else 0.85)

LONG    if TA ≥ 68
NEUTRAL if 55 ≤ TA < 68
EXIT    if TA < 55
```

`EXIT` means exit/avoid, not short. The engine is long-only spot, which is a defensible scope; it should not be read as a symmetric bearish-strength score.

### 4.2 Major finding: Python and JS do not always produce the same score

Across 100 deterministic synthetic 80-bar series, seeds 0–99:

- **17 score mismatches**
- **2 direction mismatches**
- **maximum score gap: 10.1 points**
- 4 component-vector mismatches

Largest example: seed=18. Python produced score `67.5`, regime `TREND`, ADX≈25.7. JS produced score `57.4`, regime `RANGE`, ADX≈24.8. The main cause is different RMA/ADX initialization and smoothing; Bollinger and RSI differences also exist.

These percentages are **not an estimate of real-market error frequency**. They prove that the “same scoring contract” does not hold in general. The existing `js_engine_parity.js` mainly checks the ambiguous-candle exit rule, not full indicator/score parity.

**Recommendation:** define one executable strategy specification and shared golden vectors for RSI/ATR/ADX/Bollinger/EMA/score/direction/stop/exit. Acceptance: rounded score difference ≤0.1 and zero direction differences.

### 4.3 RSI, warmup, and score meaning

- On a strictly rising series, Python yields NaN instead of RSI=100; JS sets `rs=100`, yielding RSI≈99.01. Zero-loss and zero-gain/zero-loss cases need explicit definitions.
- Sixty bars is not enough warmup for EMA200 and ADX14. Either require longer history or expose component warmup quality.
- The 40/25/15/20 weights, 68/55 thresholds, and 0.85 range gate are **heuristic**. Weights sum to one, but a 0–100 score is not a probability.
- Adding more indicators is not the first fix. The main issues are overlapping information, calibration, and out-of-sample validation.

### 4.4 Adaptive thresholds: recommendation and experiment differ

[`adaptive.py`](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/adaptive.py#L306-L364) moves thresholds around anchor 40:

```text
threshold_new = 40 + (threshold_base − 40) × factor
```

But [`adaptive_evidence.compare`](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/adaptive_evidence.py#L149-L260) multiplies directly:

```text
scaled_long    = 68 × factor
scaled_neutral = 55 × factor
```

Probe at factor=1.25:

| Path | LONG | NEUTRAL |
|---|---:|---:|
| UI/adaptive recommendation | 75.0 | 58.8 |
| Walk-forward experiment | 85.0 | 68.75 |

The experiment labeled as evidence for the proposed threshold does not test that threshold. This is a priority fix. The `0.35` exponent and regime multipliers also need out-of-sample justification, not only the intuition that shorter timeframes are noisier.

Positive note: adaptive output explicitly returns `applied:false` and `promotable:false`, and reference data cannot certify a strategy.

---

## 5. Implemented fundamental analysis

Source: [`server/fa_engine.py`](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/fa_engine.py#L80-L373).

### 5.1 What actually exists

```text
FA = 0.25·Market + 0.25·Tokenomics + 0.20·OnChain
   + 0.20·Sentiment + 0.10·Macro
```

- Market: market-cap buckets and volume/market-cap ratio.
- Tokenomics: mostly `MCap/FDV`, plus a fixed penalty when `total_supply/max_supply < 0.6`.
- OnChain: mostly DefiLlama TVL; fixed +12 bonus for BTC/LTC/DOGE.
- Sentiment: global Fear & Greed, not asset-specific news sentiment.
- Macro: effectively the USDT/IRT premium.
- Vetoes: new-listing, thin-tvl, extreme-fear, premium-stretched, and a demo unlock for `XYZ`.

Absent from the current code: news/NLP, real unlock calendars, protocol revenue/fees, exchange flows, NVT/MVRV, holder concentration, DXY/rates, and YAML asset-class weights. The project’s own fundamental capability catalogue correctly marks many of these as absent or partial.

### 5.2 Static fallback enters the decision score

When CoinGecko fails, a static profile such as BTC market cap `1.44e12` is used. Probe result:

```text
FA profile = fallback
fresh = false
FA score = 75.0
composite(TA=80, LIQ=90, FA=75) = 80.8
```

The fallback label exists, but the number still enters the composite with 25% weight. That is inconsistent with the “do not substitute invented numbers for live data” principle at the decision layer. A labeled static prior may be useful in the UI; in ranking it should be removed, coverage-weighted, or marked research-only.

### 5.3 Cache and circuit-breaker behavior

Independent probes reproduced:

1. The cache key includes symbol and premium bucket, but not `nobil_depth_score`; depth changes can be ignored until TTL expiry.
2. Depth adjustment only penalizes: `min(score, score + (depth−50)×0.2)`. Better-than-50 depth receives no reward. That may be intentional, but should be named as an asymmetric penalty.
3. The circuit breaker is shared. An alternative.me failure can block CoinGecko/DefiLlama paths. In the probe, BTC fell back without a CoinGecko call after an FNG failure.
4. An HTTP 503 with a JSON error object can become FGI=50 without opening the circuit.

**Recommendation:** per-provider breakers and caches; cache keys that include depth/premium/provider snapshot; schema and status validation; coverage-aware scoring:

```text
FA_valid = Σ(w_i · q_i · x_i) / Σ(w_i · q_i)
q_i ∈ [0,1] = freshness × availability × source reliability
```

If valid coverage falls below a threshold, remove FA from the decision or mark the signal research-only rather than filling gaps with neutral 50s.

### 5.4 Semantic limits

- `genesis_date` is project age, not Nobitex listing age.
- TVL does not mean the same thing for every asset class; the PoW bonus is a constant, not a security-budget measurement.
- `total_supply/max_supply` is not circulating/max supply.
- The applied premium is a global BTC-implied rate; per-pair premium is measured but not applied to the score.
- There is no point-in-time FA history, so today’s FA cannot be honestly replayed into the past.

---

## 6. Liquidity, fusion, and gates

### 6.1 Quote-currency bias in liquidity score

In [`main.py`](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/main.py#L1217-L1227):

```text
liq = round(clamp(50 + 4·log10(vol24 + 1), 0, 100))
```

`vol24` comes from `volumeDst`, i.e. quote-currency volume. Nobitex’s official docs show `volumeSrc` and `volumeDst` as volumes in the two currencies [1](https://apidocs.nobitex.ir/websocket/websocket-market-stats-channel). Toman volume and dollar volume are therefore compared directly.

Probe with identical USD turnover of $1,000,000 and an assumed 100,000 toman/USD rate:

| Market | Liquidity score |
|---|---:|
| IRT | 94 |
| USDT | 74 |

With TA=80 and FA=60, composites were `77.8` and `73.8`. Four score points came only from currency units. That is a serious cross-pair ranking bias.

Positive note: `market_overview.py` already solves this for market statistics by converting turnover to USD. Reuse that logic for liquidity scoring. Better still, use executable depth, spread, and expected price impact for the intended order size.

### 6.2 Composite and vetoes

```text
Composite = 0.55·TA + 0.25·FA + 0.20·LIQ
If FA=None: Composite = (0.55·TA + 0.20·LIQ) / 0.75
```

Renormalization is algebraically correct when FA is truly None. The problem is that fallback/unmapped profiles still return numeric scores instead of None.

Vetoes cap the composite at 67 normally and 45 for severe flags; severe vetoes force EXIT. This is understandable, but `extreme-fear` is global and may belong in a portfolio-risk overlay rather than an asset-specific fundamental veto.

Scan-level market-quality gates (`stale-data`, `no-book`, `spread-wide`, `book-thin`) are **shadow** gates and do not remove ranked rows. That matches the UI labels. The ten order-level preflight gates are separate and should not be confused with shadow gates.

---

## 7. Risk, sizing, and live execution

Core sizing:

```text
stop_distance = k·ATR
quantity      = (equity × risk_pct) / stop_distance
notional      = quantity × entry
```

Single-asset, heat, cash, minimum-order, market-closed, and kill-switch gates are then applied. The core formula is sound, and centralizing it in `risk_engine.preflight` is a strength.

Important limits:

- `loss_at_stop_usd = risk_usd + fee_usd` adds entry fee only. Probe: risk=100 and entry fee=20 produced 120; including a 20 exit fee gives at least 140, still before slippage/gap risk.
- True worst-case loss is not bounded by the stop when gaps occur. “Worst case” should become “estimate assuming stop execution.”
- In the backtest, `risk_amount` uses the pre-slippage close-to-stop distance while the entry fill includes slippage. Probe with 1% slippage: requested risk 0.75%, actual net loss 1.094% of equity.
- Paper metrics honestly expose `entry_fees_included:false`; this is a disclosed limitation, not a hidden claim. Still, comparison with backtests requires a complete net ledger.
- The ERC allocator shrinks correlations toward zero by a fixed 0.3 and treats missing pairs as independent. In crypto this can underestimate systemic risk. Prefer Ledoit–Wolf, correlation=1 stress, and cluster caps.
- The fixed 0.2% fee is not verified against the account’s actual schedule. The official API profile example shows different rial/USDT fee fields, so 0.2% should not be described as universally conservative [3](https://old-apidocs.nobitex.ir/).

### Operational readiness is not strategy validation

[`live_execution.promotion_evidence`](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/live_execution.py#L160-L186) counts records, days, and ambiguous states. A probe with **30 dry-run records over 5 days** returned `ready:true`.

That is useful for transport/ledger soak, but it does not prove profitability or model validity. In the inspected `live_api.place` path, I did not find a mandatory binding to a validated strategy artifact matching the order’s symbol/timeframe/config. Recommended split:

1. **Operational readiness:** soak, reconciliation, idempotency, error handling.
2. **Strategy approval:** aligned backtest, net paper evidence, compatible IC/MSI, acceptable uncertainty, human approval, and a full configuration fingerprint.

---

## 8. Walk-forward backtesting

Source: [`server/backtest.py`](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/backtest.py#L46-L705).

Strengths:

- Parameters are selected on train slices and reported on evaluation slices.
- Default grid varies `atr_stop_k`, `tp1_r`, and `tp2_r`.
- Fees and default slippage are modeled; zero slippage cannot PASS.
- Ambiguous candles use stop-first resolution.
- Synthetic/reference data cannot certify a strategy.
- Gates include ≥3 windows, ≥30 trades, PF≥1.3, MDD<15%, positive expectancy, 40% edge retention, ≥50% profitable windows, and ≤10% ambiguous exits.
- Reports honestly state that only TA was tested.

Reproduced issues:

| Issue | Evidence |
|---|---|
| Entry occurs at the signal candle close, but that candle’s high/low are not checked afterward | stop=98, same-candle low=80; trade closed as `eod`, not stop |
| A gap through the stop fills at the stop, not the worse open | next open=90, stop=98; model exit=98 |
| Single-asset cap is per entry, not aggregate exposure | three entries, cap=25%, aggregate notional≈74.96% |
| R denominator and targets use pre-slippage close | actual net loss exceeded requested risk budget |
| JS backtest closes the full position at TP1; Python keeps half for TP2 | same data: Python pnl≈359.2 via `tp2`; JS pnl≈259.4 via `tp1` |
| JS no-loss profit factor becomes fixed 2.4 | conflicts with the “no invented numbers” principle; should be undefined/flagged |

Additional limits:

- The backtest is TA-only; it does not replay FA, liquidity, vetoes, market-quality gates, or a multi-asset portfolio.
- Default backtest targets are `1.5/2.5`, while live/journal defaults are `1.2/2.0`; this should be more prominent in decisions.
- `sharpe_like = mean(R)/std(R)×sqrt(N_trades)` is not annualized Sharpe. The name is honest, but it should not be compared with standard Sharpe.
- Windows flatten at boundaries and chain equity; this is not a continuous portfolio simulation.
- Thirty trades and three windows are operational minimums, not strong statistical sufficiency. Grid search and human tuning can contaminate OOS evidence.

**Recommendations:** next-bar execution with latency, gap modeling, conservative intrabar logic or lower-timeframe data, aggregate asset caps, net ledger, shared Python/JS engine, portfolio-level cash/heat, nested/purged walk-forward, final holdout, cost stress, and bootstrap uncertainty.

---

## 9. Journal, IC, MSI, calibration, and drift

The journal and Signal Intelligence infrastructure is valuable: scan cycles, linkage, H1/H2/H3 horizons, MFE/MAE, cohorts, CSV/JSON export, human review, Wilson win-rate intervals, and explicit `modeled-ohlc-gross-r` / `independent_samples_established:false` flags.

Keep these distinctions clear:

### 9.1 R is gross; return_pct is not total realized return

`journal.simulate_long` has no fees/slippage. It uses TP1=1.2R, half exit, then a trail at `entry×1.002`; TP2 is not used by the resolver. `return_pct` comes from the last exit price, not the weighted return of partial exits. Probe: `R=0.65` but `return_pct=0.2%`.

These are useful research outputs if labeled “gross modeled R” and “last-exit return.” They are not net account PnL.

### 9.2 MSI compares different populations

- Baseline: `backtest_ic` uses **TA score** over all eligible candles.
- Live: `ic_for_window` uses **composite** only on resolved **LONG** rows.
- Horizon, costs, population, and selection filters differ.

Therefore `realized_IC / baseline_IC` does not necessarily measure model drift; it can measure population or score-definition changes. Fix this before using MSI for decisions.

**Recommendation:** same score, symbols, timeframe, horizon, direction filter, data source, and cost policy. Use cross-sectional IC per timestamp for ranking and time-series IC per symbol for timing, each with dependence-aware confidence intervals.

### 9.3 Dependence and calibration

- Consecutive bars with overlapping horizons are not independent; assets are correlated. Normal CIs and two-sample drift tests can be overconfident.
- On daily bars, H1 and H2 both collapse to one bar; they should not count as two independent horizons.
- Current calibration is win rate by score bucket, not probability calibration. If the output is presented as a probability, add Brier/log-loss/ECE and train-only isotonic/Platt mapping.
- Family attribution via `max(components)` is not causal attribution. Prefer ablation, grouped permutation importance, or carefully validated SHAP on OOS data.
- PSI and Benjamini–Hochberg are implemented; the PSI formula is standard, but 0.10/0.25 thresholds are heuristic and performance p-values need dependence-aware treatment.
- `journal.CONFIG_HASH` mainly fingerprints risk geometry. TA/FA weights, thresholds, universe, fee schedule, provider snapshots, and code commit belong in a full strategy fingerprint.

The repository’s own evidence is cautious: the reference-feed adaptive matrix in `docs/evidence/adaptive-evidence-2026-10-22.json` has **12/12 inconclusive results** and `promotable:false`. That is honest, but it is not live-performance approval.

---

## 10. Formula audit verdicts

| Formula | Verdict |
|---|---|
| Base EMA/MACD/ATR/ADX | Mostly standard; unify warmup and edge cases |
| RSI | Wilder core is right; define loss=0 and flat-market cases explicitly |
| Bollinger | Python ddof=1 vs JS ddof=0; choose one contract |
| Weighted TA score | Understandable expert rule; not a calibrated probability |
| Adaptive thresholds | Anchored recommendation and direct-multiplied experiment differ |
| Log-volume liquidity | Quote-currency biased; convert to USD/executable depth |
| Weighted FA score | Weights sum correctly; coverage/freshness/asset class are missing |
| Composite and renormalization | Algebra is correct; input quality is the issue |
| ATR position sizing | Core is correct; costs, gaps, and aggregate caps are incomplete |
| Journal R | Gross and modeled; not net PnL |
| Spearman IC | Formula is correct; baseline/live populations differ |
| MSI ratio | Not a reliable drift interpretation until bases are aligned |
| PSI | Formula is correct; thresholds are empirical |
| BH/q-values | Implementation is orderly; sample-dependence assumptions need review |
| Annualized beta/alpha | Definitions are explicit; short samples imply large uncertainty |
| Fixed-shrinkage ERC | Useful, but can underestimate crisis correlation in crypto |

---

## 11. Prioritized findings

**P1 = fix before decision reliance/live promotion. P2 = important next improvement. P3 = maintainability debt.**

| ID | Priority | Finding | Impact |
|---|---|---|---|
| F01 | P1 | Python/JS TA divergence | Inconsistent score/direction across paths |
| F02 | P1 | Liquidity uses quote-currency volume | IRT vs USDT ranking bias |
| F03 | P1 | Static FA fallback enters composite | Non-live data drives decisions |
| F04 | P1 | TA-only backtest vs TA+FA+LIQ live decision | Full strategy is not validated |
| F05 | P1 | Gaps, entry candle, aggregate cap, risk denominator | Optimistic/inconsistent execution and risk |
| F06 | P1 | Adaptive recommendation ≠ tested threshold | Evidence supports the wrong policy |
| F07 | P1 | MSI baseline/live population mismatch | Misleading drift warnings |
| F08 | P2 | JS backtest exits and PF differ | Standalone report is not equivalent |
| F09 | P2 | FA breaker/cache/schema handling | Cross-provider contamination and false neutral values |
| F10 | P2 | Gross R, last-exit return, paper entry-fee exclusion | Research metrics confused with net accounting |
| F11 | P2 | Sample dependence, non-probabilistic calibration, simple attribution | Overstated statistical confidence |
| F12 | P2 | Live readiness based on dry-run counts | Operational readiness mistaken for model validity |
| F13 | P2 | Short warmup, RSI edge, heuristic weights | Score instability |
| F14 | P3 | Large main module, incomplete hash, history unit metadata, stale doc counts | Maintenance and configuration-drift risk |

---

## 12. Recommended roadmap

### Phase A — correctness and numerical trust

- Extract one `StrategySpec`: weights, thresholds, exit geometry, costs, universe, data contract.
- Shared Python/JS golden vectors for indicators and the exit state machine.
- Normalize liquidity to USD, then move toward executable depth/price impact.
- Coverage-aware FA; remove static fallback from decisions or explicitly down-weight it.
- Make adaptive experiments use the exact recommended thresholds.
- Add regressions for gaps, entry-candle risk, aggregate caps, net R, and exit fees.

### Phase B — statistical validation

- Backtest the same basis as the live decision; without point-in-time FA/LIQ history, either replay it honestly or label the run research-only.
- Align IC/MSI populations; use block bootstrap or Newey-West uncertainty and effective sample size.
- Nested walk-forward and final holdout; cost stress and stable sensitivity.
- Probability calibration only after defining a net-profit label.
- Dependence-aware drift tests and a full configuration fingerprint.

### Phase C — analytical expansion

- Point-in-time store for FA and order books.
- Multi-timeframe confirmation and market regime, not just more indicators.
- Unlock calendars, verified news, and asset-class-specific on-chain metrics.
- Portfolio backtest with shared correlation, heat, and cash.
- Conservative meta-model only after the base edge is demonstrated; ablate each feature family.

### Suggested acceptance criteria

- Parity: zero direction differences; score difference ≤0.1; identical exit ledgers on golden vectors.
- Liquidity: identical USD turnover and book ⇒ identical score regardless of IRT/USDT quote.
- FA: provider outage ⇒ static prior is not treated as live data; coverage and source age are reported.
- Backtest: gap, entry-candle, and aggregate-cap regressions exist; net R reconciles with paper ledger within a defined tolerance.
- Evaluation: baseline and live use one score/population/horizon; edge confidence intervals exclude zero by a pre-specified margin.
- Live: no real order without a matching strategy artifact, net paper evidence, acceptable MSI state, risk gates, and human approval.

---

## 13. Bottom line

The project is valuable as a dashboard, data-provenance system, test harness, and cautious execution framework. It often chooses the right path: closed candles, source labels, risk gates, journaling, and refusal to certify synthetic data.

To become a dependable Nobitex market analyzer, the next priority is **not more features; it is numerical consistency and like-for-like validation**. If only six things are done: technical/backtest engine parity, USD/executable liquidity, coverage-aware FA, corrected backtest execution, one adaptive threshold definition, and aligned IC/MSI. After that, signal calibration, fundamental expansion, and richer market analysis can be evaluated with much more confidence.

---

## Code sources

- [TA Python](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/ta_engine.py)
- [TA/Backtest JavaScript](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/nbx-core.js)
- [FA](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/fa_engine.py)
- [Composite/scan/liquidity](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/main.py)
- [Backtest](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/backtest.py)
- [Adaptive](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/adaptive.py) · [Adaptive evidence](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/adaptive_evidence.py)
- [Journal](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/journal.py) · [Signal Intelligence](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/signal_intelligence.py) · [Drift](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/signal_drift.py)
- [Risk](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/risk_engine.py) · [Paper](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/paper_trading.py) · [Live boundary](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/server/live_execution.py)
- [Existing adaptive evidence](https://github.com/iman-taheri59/nobi/blob/2b5ea130f53959d357c5c8a664bfb1a52bd781e0/docs/evidence/adaptive-evidence-2026-10-22.json)
