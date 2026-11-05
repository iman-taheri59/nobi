# Nobi / Nobitex Market Analyzer — Comprehensive Technical & Fundamental Audit

> **Archive note (2026-11-05):** snapshot review of commit `2b5ea13` (2.2.2); never rewritten. The current status of every finding lives in [`README.en.md`](README.en.md) in this folder.


**Repository:** `https://github.com/iman-taheri59/nobi`  
**Audited version:** `2.2.2`  
**Commit:** `2b5ea130f53959d357c5c8a664bfb1a52bd781e0` (2026-11-03)  
**Audit date:** 2026-11-04  
**Persian version:** `nobi-audit-report.fa.md`  
**Evidence bundle:** `nobi-audit-evidence.zip`

> This is a code, formula, data-contract, and test-evidence audit. It is not investment advice, not a profitability certification, and not a live-trading authorization. No exchange order was placed and no production account was accessed.

---

## 1. Executive summary

Nobi is substantially more than a candle dashboard. It contains a FastAPI backend, a browser/standalone engine, technical scoring, a limited fundamental engine, liquidity scoring, a signal journal, walk-forward backtesting, paper trading, risk preflight, shadow gates, adaptive-threshold measurement, signal-intelligence analytics, and a gated live-execution boundary. The repository has an unusually strong culture of explicit provenance, “no data is not zero” contracts, bilingual documentation, and regression tests.

The local verification result is strong:

| Check | Result |
|---|---:|
| Backend pytest suite, exact `requirements-dev.txt` pins | **1742 passed**, 1 deprecation warning |
| JavaScript/package test suites | **23/23 passed** |
| Ruff | passed |
| mypy | passed, 160 source files |
| ESLint + dashboard lint | passed |
| `pip-audit` against runtime requirements | no known vulnerabilities reported |
| `npm audit --audit-level=high` | 0 high/critical; 5 moderate dev-tool findings |
| Independent offline reproductions | **14 finding groups reproduced** |

However, passing tests does not mean the signal engine is economically validated. Several important behaviors are either not covered by the current tests or are covered only at the unit level while the end-to-end contract remains inconsistent.

### Overall verdict

| Area | Assessment |
|---|---|
| Engineering discipline | **Strong**: typed core, many tests, explicit provenance, shadow-only gates, cautious live boundary. |
| Technical-analysis primitives | **Mostly sound**, but warm-up, edge-case RSI, regime cliff, and Python/JS parity need correction. |
| Fundamental analysis | **Partial and fragile**: useful provider ratios, but not a full FA engine; fallback/live/provenance contracts need repair. |
| Liquidity and ranking | **Material defect**: raw quote-currency turnover is scored as if comparable across IRT and USDT. |
| Signal evaluation | **Not yet decision-grade**: journal, backtest, paper, and live-adjacent policies do not share one exit/cost/horizon contract. |
| Risk/execution safety | **Good scaffolding, incomplete enforcement**: daily-loss halt is warning-only in the shared evaluator; live preflight uses paper-account context; promotion evidence counts ledger rows, not qualified outcomes. |
| Live readiness | **Not recommended** until P0/P1 items below are closed and a fresh forward paper/shadow evidence window is collected. |

### Highest-priority findings

1. **Liquidity unit bias:** equal USD turnover can receive very different liquidity scores because IRT turnover is not converted to USD before scoring/ranking.
2. **Decision-contract ambiguity:** `direction` is derived from TA, while `composite` is used for ranking and veto caps; a row can be `LONG` with composite below the nominal promotion threshold.
3. **History pagination gap:** the client requests up to 1200/2000/3000 candles through a single UDF call, while current official docs cap a page at 500. This breaks 15m H3 journal resolution and limits long backtests/baselines.
4. **FA provenance and fallback discontinuity:** partial provider failure can still be labeled `live`/`fresh`; live and fallback paths use different formulas; depth adjustment is asymmetric and effectively inert for normal scan liquidity scores.
5. **Outcome/exit inconsistency:** journal `return_pct` is not the partial-exit strategy return; paper trading does not exit at TP2; gap fills are idealized; missing candles can change outcomes silently.
6. **Adaptive evidence mismatch:** the recommended threshold uses an anchored transform, but the paired experiment tests a multiplicative threshold.
7. **Live risk gates:** daily-loss halt is not enforced by the shared evaluator; live promotion counts all execution-ledger rows, including rejected/prepared/unknown states, rather than qualified filled samples.

---

## 2. Scope, method, and limitations

### 2.1 What was inspected

Primary source of truth was the cloned repository at the commit above. The audit covered:

- `server/ta_engine.py`, `server/fa_engine.py`, `server/main.py`
- `server/journal.py`, `server/backtest.py`, `server/risk_engine.py`
- `server/gates.py`, `server/adaptive.py`, `server/adaptive_evidence.py`
- `server/signal_intelligence.py`, `server/signal_evidence_view.py`, `server/signal_drift.py`, `server/signal_cycles.py`
- `server/market_overview.py`, `server/benchmark.py`, `server/portfolio.py`
- `server/nobitex_client.py`, `server/candle_cache.py`, `server/queue_policy.py`, `server/fees.py`, `server/units.py`
- `server/paper_trading.py`, `server/live_execution.py`, `server/live_api.py`, selected security/auth defaults
- `nbx-core.js`, `nbx-app.js`, parity/test scripts, CI, requirements, package metadata
- capability catalogues, ADRs, changelog, and the historical `2.1.8` review only as context, not as current evidence

### 2.2 Verification performed

- Installed exact Python dependencies from `server/requirements-dev.txt` in an isolated virtual environment.
- Ran the full backend suite offline: external HTTP was blocked by proxy settings and the exchange base URL was pointed at an unused local port. Result: `1742 passed, 1 warning in 96.08s`.
- Ran all package `test:*` scripts under Node `v24.22.2`: 23/23 passed.
- Ran Ruff, mypy, ESLint, dashboard lint, `pip-audit`, and `npm audit`.
- Wrote independent offline reproductions: `reproduce_findings.py` and `ta_parity.cjs`. These use synthetic candles, mock HTTP transports, temporary SQLite/state files, and no production credentials.
- Fetched current official Nobitex API/pricing documentation for data-limit and fee context.

### 2.3 Limitations

- Python runtime was `3.13.16`, while CI targets Python 3.12. Package pins were exact, but this is not a claim about the project’s hosted CI run.
- No live Nobitex account, private API key, order placement, or production database was used.
- No multi-month live forward performance dataset was supplied. Therefore the audit cannot estimate real profitability, slippage distribution, capacity, or live signal decay.
- Official documentation was read as current public contract; live endpoint behavior was not probed under authenticated production conditions.
- The repository’s historical `2.1.8` review was not reused as proof of current defects. Every current finding below is tied to current code, a reproduction, or an explicit documentation contract.

---

## 3. Architecture and actual decision flow

### 3.1 Implemented pipeline

A simplified scan path is:

```text
/market/stats
  -> normalize symbols and Rial->Toman
  -> top 60 by raw quote turnover
  -> exclude USDTIRT from TA
  -> fetch UDF candles
  -> drop unfinished candles
  -> TA score
  -> liquidity score from raw 24h quote turnover
  -> composite without FA
  -> FA enrichment
  -> composite with FA + veto caps
  -> rank by composite
  -> journal rows
  -> prefetch top order books
  -> shadow gate ledger
  -> scan-cycle evidence
```

Important references:

- Universe loading and raw turnover sort: `server/main.py:1078–1134`
- Scan and composite: `server/main.py:1172–1353`
- Closed-candle filter: `server/queue_policy.py:19–30`
- TA scoring: `server/ta_engine.py:123–232`
- FA scoring: `server/fa_engine.py:189–373`
- Shadow gates: `server/gates.py`, `server/main.py:4761–4836`
- Journal scan: `server/main.py:141–180`

### 3.2 What is deliberately not applied

Two modules are measurement-only and should not be described as live filters:

- `server/gates.py` returns `applied: false`; symbol/data/book gates are shadow verdicts.
- `server/adaptive.py` returns `applied: false`; adaptive thresholds/stops are recommendations, not production policy.

This is a strength, not a defect: the code avoids silently promoting unvalidated rules. The risk is documentation/UI language that implies these gates already protect live ranking or execution.

---

## 4. Technical-analysis process and formula assessment

### 4.1 Implemented TA components

| Component | Weight | Implemented logic | Assessment |
|---|---:|---|---|
| Trend | 0.40 | Close vs EMA21, EMA9>EMA21, EMA21>EMA50, close vs EMA200, ADX>22 with +DI>−DI | Reasonable trend checklist; EMA200 immature with only 60 bars. |
| Momentum | 0.25 | RSI bands and MACD histogram slope/sign | Useful, but RSI zero-loss edge returns missing/neutral in Python. |
| Volume | 0.15 | Current volume vs prior 30-bar average; bonus only for rising close with volume | Long-only confirmation; high volume on falling price is not symmetrically penalized. |
| Structure | 0.20 | Bollinger %B and 20-bar close breakout | Simple and interpretable; Python sample std vs JS population std differs. |
| Regime gate | multiplier | ADX>25 => TREND, else score ×0.85 | Hard cliff at 25; no hysteresis. |
| Direction | thresholds | LONG ≥68, NEUTRAL ≥55, else EXIT | Fixed across symbols/timeframes; adaptive module measures but does not apply. |
| Risk geometry | stop | `2 × ATR(14)` | Standard ATR stop, but not calibrated per symbol/timeframe/cost. |

Core formulas:

```text
EMA_n(t) = α·C(t) + (1−α)·EMA_n(t−1), α = 2/(n+1)
Wilder-style smoothing in Python: EWM α = 1/n, adjust=False
RSI = 100 − 100/(1 + avg_gain/avg_loss)
MACD = EMA12 − EMA26; signal = EMA9(MACD); hist = MACD − signal
TR = max(H−L, |H−C_prev|, |L−C_prev|)
ATR = Wilder-style mean(TR, 14)
BB mid = SMA20; Python sd = sample std(ddof=1); JS sd = population std(ddof=0)
TA_raw = 0.40·trend + 0.25·momentum + 0.15·volume + 0.20·structure
TA_score = TA_raw × (1.0 if ADX>25 else 0.85)
```

### 4.2 What is correct or strong

- Scalar and vectorized Python scoring matched on the independent fixtures: max score difference `0`, max prefix-causality difference `0`.
- The scan drops unfinished candles before scoring, reducing partial-bar relabeling.
- Indicator primitives are conventional and readable.
- The code exposes diagnostics and component scores, which makes auditability possible.
- The repository tests many edge cases around units, timestamps, and no-data states.

### 4.3 TA issues and corrections

#### T1. EMA200 is used before it has 200 bars

The minimum history is 60 candles, but EMA200 is initialized from the first close and updated recursively. This is causal, but it is not a mature 200-period EMA. With 60 bars, the “200 EMA” is mostly a short-history recursive average.

**Correction:** either require at least 200 closed bars for the EMA200 component, or mark it immature and renormalize the trend component. Example:

```text
if bars < 200:
    trend_score = scored_trend_points / available_trend_points × 100
    evidence.ema200_mature = false
```

#### T2. Python RSI edge behavior is wrong for monotonic windows

`rsi()` divides by `loss.replace(0, np.nan)`. A monotonically rising window has zero average loss, so RSI becomes NaN/None instead of 100. In the independent rising fixture, Python reported `rsi: null`, while JS reported `99`.

**Correction:**

```text
if avg_loss == 0 and avg_gain > 0: RSI = 100
if avg_gain == 0 and avg_loss > 0: RSI = 0
if avg_gain == 0 and avg_loss == 0: RSI = 50 or explicitly flat/undefined
```

Do not silently convert a strong trend edge into neutral momentum.

#### T3. Python/JS TA parity is not exact

Independent 303-case comparison:

- max absolute score difference: **12.9**
- mean absolute score difference: **0.3188**
- direction flips: **1** (`random-60-34`: Python LONG 68.0, JS NEUTRAL 61.2)
- rising RSI: Python `null`, JS `99`

Sources include RSI zero-loss handling, Wilder initialization differences, Bollinger sample vs population std, and flat/zero-range handling. Existing parity tests focus heavily on broker/preflight/intrabar rules, not full TA score parity.

**Correction:** add a cross-engine TA golden-fixture suite covering rising, falling, flat, gaps, short history, threshold boundaries, and random walks. Define one canonical initialization and std convention.

#### T4. ADX regime gate is a cliff

ADX 24.99 and 25.01 produce a 15% score multiplier difference. This can create threshold chatter.

**Correction:** use a continuous gate or hysteresis:

```text
gate = 0.85 + 0.15·sigmoid((ADX−25)/2)
```

or require ADX>25 to enter TREND and ADX<22 to exit. Validate out-of-sample before applying.

#### T5. Volume confirmation is long-only asymmetric

High volume with rising price adds confirmation; high volume with falling price does not subtract equivalent risk. For a long-only engine this is defensible as a bullish filter, but it should not be described as symmetric market-volume analysis.

**Correction:** either document it as long-confirmation only, or add a bearish distribution penalty:

```text
if close < close_prev and volume > 1.4·avg_volume: volume_score −= 20
```

#### T6. Fixed thresholds are not calibrated

The same 68/55 thresholds apply to BTC 15m, alt 4h, and daily. The adaptive module correctly measures this, but production still uses fixed thresholds.

**Correction:** keep fixed thresholds until a paired, cost-aware, out-of-sample experiment supports a change. Then apply per-symbol/timeframe thresholds with versioned config and rollback.

---

## 5. Fundamental-analysis process and formula assessment

### 5.1 What is actually implemented

The FA engine is a provider-ratio scorer, not a full fundamental research system.

| Factor | Weight | Current implementation | Missing versus catalogue |
|---|---:|---|---|
| Market/tokenomics | 0.25 | CoinGecko market cap, FDV, volume/mcap, supply ratio | Exchange distribution, verified listing age, holder concentration, real unlocks |
| Tokenomics | 0.25 | MCap/FDV ratio, dilution penalty | Vesting calendar, inflation, value accrual, staking economics |
| On-chain | 0.20 | DefiLlama TVL match, fixed PoW bonus | Active addresses, NVT/MVRV, exchange flows, hash-rate security budget |
| Sentiment | 0.20 | Global Fear & Greed | News NLP, event taxonomy, funding/OI, social dominance |
| Macro | 0.10 | USDT/IRT premium adjustment | DXY, rates, gold, liquidity, domestic policy calendar |

References: `server/fa_engine.py:189–373`, `docs/analysis/fundamental-analysis-capabilities.en.md`.

The capability catalogue is honest that many items are not implemented. The audit agrees with that disclaimer. The problem is not that the project lacks a full FA system; the problem is that the implemented FA score can still carry 25% of the composite while its provenance and fallback contracts are not strong enough.

### 5.2 FA formula

```text
FA_raw = 0.25·market + 0.25·tokenomics + 0.20·onchain
       + 0.20·sentiment + 0.10·macro
FA_score = clamp(round(min(FA_raw, FA_raw + (depth−50)·0.2), 1), 0, 100)
```

Vetoes can cap the final composite:

- normal veto cap: 67
- severe veto cap: 45
- severe examples: thin TVL, extreme fear, demo unlock

### 5.3 FA issues and corrections

#### F1. Live and fallback paths are not formula-equivalent

With identical BTC inputs in a mock provider test:

- live FA score: **77.2**
- fallback FA score: **75.0**
- delta: **2.2 points**

Differences include:

- live tokenomics multiplier: `ratio × 130`
- fallback tokenomics multiplier: `ratio × 95`
- live TVL scale: `40 + min(60, log10(TVL+1)×18)`
- fallback TVL scale: `40 + min(55, log10(TVL+1)×5.2)`
- fallback market penalty lacks the live `vmc > 0.5` penalty
- fallback profiles are static and undated

**Correction:** one scoring function should consume a normalized factor vector. Provider failure should change coverage/provenance, not silently change the formula. If fallback is allowed to trade, it must be dated, versioned, and explicitly capped or excluded from promotion evidence.

#### F2. Depth adjustment is asymmetric and mostly inert in scans

The formula is:

```text
score = min(score, score + (depth−50)·0.2)
```

Good depth cannot improve the score; poor depth can penalize it. In the reproduction, depth 0 gave 67.2 and depth 100 gave 77.2, while the unadjusted live score was 77.2. In the actual scan, liquidity score is `50 + 4·log10(vol+1)` and is therefore ≥50 for nonnegative volume, so the FA depth adjustment normally does nothing.

**Correction:** decide whether depth is a penalty-only gate or a symmetric factor. If symmetric:

```text
FA_score = clamp(FA_raw + β·(depth−50), 0, 100)
```

If penalty-only, rename it and document that it cannot reward depth. Better: use order-book spread/impact directly in execution scoring rather than a log-turnover proxy inside FA.

#### F3. Partial provider failure can still be labeled live/fresh

In the mock test, CoinGecko succeeded, DefiLlama returned no usable TVL, and Fear & Greed failed with 503. The result still had:

- `profile: live`
- `fresh: true`
- sentiment default 60 from FGI 50

`fresh` currently means “the mapped provider path was used,” not “all factors were observed.”

**Correction:** emit factor-level provenance:

```json
{
  "market": {"source":"coingecko", "observed_at":"...", "status":"ok"},
  "onchain": {"source":"defillama", "status":"missing"},
  "sentiment": {"source":"alternative.me", "status":"failed", "default_used":true}
}
```

Then compute coverage and renormalize or cap the score. A score with 2/5 observed factors should not be presented as equivalent to 5/5.

#### F4. Global circuit breaker couples unrelated providers

A Fear & Greed failure can trip the shared FA circuit, causing subsequent CoinGecko reads to be skipped and fallback profiles to be used. Provider health should be scoped per provider, not global.

**Correction:** separate circuit state for CoinGecko, DefiLlama, and FNG. A sentiment outage should not invalidate market-cap data.

#### F5. Cache key omits depth input

The FA cache key includes symbol and premium bucket, but not `nobil_depth_score`. Because the current depth adjustment is mostly inert for scan liquidity ≥50, this is not currently a large numeric defect, but it becomes one if depth is made symmetric.

**Correction:** include all score-affecting inputs in the cache key, or separate factor cache from final scoring.

#### F6. Genesis age is not listing age

CoinGecko `genesis_date` is used as `days` and can trigger `new-listing<90d`. Genesis age is not Nobitex listing age, and not necessarily token launch age.

**Correction:** rename to `genesis_age_days`; use a verified exchange listing date for listing-risk vetoes.

#### F7. TVL matching is heuristic

DefiLlama matching uses name/symbol similarity. Chain TVL is not automatically token fundamentals, and symbol collisions are possible.

**Correction:** maintain a verified mapping table: `base asset -> protocol slug(s) -> relevance class`. For non-DeFi assets, mark TVL as not applicable rather than neutral/penalized.

#### F8. Global sentiment is applied to every asset

Fear & Greed is a market-wide crypto sentiment proxy. It is not asset-specific sentiment.

**Correction:** keep it as a macro/sentiment overlay with explicit scope, and do not let it masquerade as per-token news sentiment.

---

## 6. Composite score, liquidity, ranking, and vetoes

### 6.1 Current composite formula

```text
if FA is present:
    composite = 0.55·TA + 0.25·FA + 0.20·liquidity
else:
    composite = (0.55·TA + 0.20·liquidity) / 0.75
```

Vetoes then cap composite and may change direction:

```text
normal veto: composite ≤ 67
severe veto: composite ≤ 45
```

References: `server/main.py:1194–1280`, `server/main.py:584–606` tests.

### 6.2 Liquidity unit bias — reproduced

Current liquidity score:

```text
liquidity = round(clamp(50 + 4·log10(vol24 + 1), 0, 100))
```

`vol24` comes from `volumeDst`, which is quote-currency turnover. IRT and USDT are not comparable units. Official Nobitex market stats document `volumeDst` as 24h transaction value in the destination currency.

Reproduction with equal USD turnover:

| Market | Raw quote turnover | Liquidity score | TA | Composite without FA |
|---|---:|---:|---:|---:|
| USDT | 1,000,000 USDT | 74 | 70 | 71.1 |
| IRT | 100,000,000,000 IRT at 100,000 IRT/USDT | 94 | 66 | 73.5 |

The lower-TA IRT row ranks higher purely because of quote-unit scale. The universe is also sorted by raw `vol24`, so IRT markets can dominate the top-60 selection before scoring.

**Correction:** normalize turnover to USD before sorting/scoring:

```text
turnover_usd = vol24 / fx_quote_usd
liquidity = f(log10(turnover_usd + 1), spread_bps, depth_usd, impact_bps)
```

`market_overview.py` already performs a USD conversion for overview statistics; the scan/ranking path should use the same discipline.

### 6.3 Composite is not the final direction threshold

`direction` is produced by TA thresholds. Composite is used for ranking and veto caps. Reproduction:

```text
TA = 70, FA = 30, liquidity = 50
composite = 56.0
direction remains LONG
```

This may be intentional if TA alone is the trade trigger and FA/liquidity only rank candidates. But the code comments and veto language imply composite promotion thresholds. The system needs one explicit decision contract.

**Correction:** choose and document one of:

1. **TA-triggered, composite-ranked:** direction from TA; composite only ranks; veto can block. Then do not describe 67/68 as composite promotion thresholds.
2. **Composite-triggered:** direction from final capped composite; TA is a component. Then recompute direction after FA/veto and update journal/backtest/live gates consistently.

Until this is resolved, signal intelligence and promotion evidence are ambiguous.

### 6.4 Missing FA versus unmapped FA

When FA is unavailable, the composite renormalizes TA+liquidity. When a symbol is unmapped, FA can still return a default-based score with `profile: none`, and the scan may use that score in the full three-component composite. The UI coverage states are improved, but the numeric treatment can still differ.

**Correction:** if `profile == none` or coverage is below a threshold, treat FA as missing for composite purposes, or apply an explicit uncertainty penalty. Do not let “no provider mapping” behave like a measured neutral fundamental score.

### 6.5 Premium calculation

The USDT/IRT premium is computed from BTC legs:

```text
implied_USDT_IRT = BTCIRT / BTCUSDT
premium_pct = (USDTIRT / implied_USDT_IRT − 1) × 100
```

Per-coin premium uses each coin’s IRT/USDT legs. This is a reasonable cross-rate basis measure, but it depends on fresh, liquid legs. The code rejects absurd premiums and outliers, which is good.

**Correction:** add leg-age and minimum-turnover checks; report premium as a basis/risk overlay, not as a precise arbitrage signal unless executable quotes and fees are included.

---

## 7. Signal evaluation, journal, backtest, and drift

### 7.1 Journal model

The journal records one row per symbol/timeframe/bar close and resolves LONG rows over H1/H2/H3 using a gross OHLC R model:

```text
stop = entry − 2·ATR
TP1 = entry + 1.2·R
TP2 = entry + 2.0·R
after TP1: half off, stop moves to entry × 1.002
```

The module explicitly labels outcomes as `modeled-ohlc-gross-r`, not net account PnL. That honesty is a strength.

### 7.2 History cap breaks 15m H3 and long baselines

Current official UDF docs state:

- maximum 500 candles per request;
- `countback > 500` is capped at 500;
- larger ranges require `page`;
- 15m candles are available for the last 7 days;
- rate limit is 60 requests/minute.

The client’s `get_candles_ex()` makes one UDF call and does not paginate. Reproduction with a mock enforcing the documented cap:

```text
requested: 1200
received: 500
15m H3 required: 672 bars
```

Consequences:

- 15m H3 cannot resolve from the normal resolver path.
- Backtests requesting 600/1200/2000 bars may silently run on ≤500 bars.
- MSI baseline requests up to 3000 bars but may receive only 500.
- The repository has a paginated downloader script, but the main client/resolver/backtest routes do not use it.

**Correction:** implement paginated history in the client or a shared history service; enforce requested coverage; report `bars_requested`, `bars_received`, `pages`, `gaps`, and `coverage_ok`. Do not run promotion-grade backtests on truncated history without a visible refusal.

### 7.3 Journal `return_pct` is not strategy return after partial exits

Reproduction:

```text
R multiple: 0.7
stored return_pct: 0.4%
partial-exit strategy return: 1.4%
```

`return_pct` is computed from final `exit_price / entry − 1`, ignoring that half the position was closed at TP1. If this field is used for IC or calibration, it measures a different quantity than the R model.

**Correction:** store separate fields:

```text
final_leg_return_pct
strategy_return_pct_gross
strategy_return_pct_net
r_multiple_gross
r_multiple_net
```

Use the strategy return for IC/calibration if the question is “did the signal’s trade policy work?”

### 7.4 Missing candles can silently change outcomes

Reproduction:

```text
full path with a stop-touch candle: R = −1.0, hit_sl = true
same path with that candle removed: R = 0.0, hit_sl = false
```

The simulator walks the rows it receives; it does not require a continuous timestamp grid. A missing candle can erase a stop/target touch.

**Correction:** validate continuity before outcome resolution. If gaps exist, either fetch missing pages, mark the outcome `invalid_gap`, or use a conservative gap model. Do not silently score a gapped path as complete.

### 7.5 Adverse gap fills are idealized

Reproduction:

```text
entry = 100, stop = 98, next open = 90
modeled R = −1.0
open-aware gross R = −5.0
```

The journal/backtest stop model fills at the stop level even when the next candle opens far beyond it. This understates tail risk.

**Correction:** for long stops:

```text
if open < stop: fill = open
else if low <= stop: fill = stop
```

For targets, use symmetric open-aware logic. Add slippage/fees and report gross/net separately.

### 7.6 Paper, journal, and backtest exit policies differ

| Engine | TP1 | TP2 | After TP1 | Time stop | Costs |
|---|---|---|---|---|---|
| Journal | half at 1.2R | stored but not used as exit | trail to entry×1.002, horizon close | horizon | gross R |
| Python backtest | half at configured TP1 | closes remainder at TP2 | break-even stop | window edge | fee + slippage |
| Paper broker | half at TP1 | **not used as exit** | trail to entry×1.002 | 48h if <1% move | fee + book execution |

Reproduction: paper position with mid 105 and TP2 104 remained `PARTIAL` with 0.5 quantity after TP1.

**Correction:** define one canonical exit policy object and use it in journal, backtest, paper, and live preview. If engines intentionally differ, label them as different strategies and do not compare their R/PF as if they were the same.

### 7.7 Backtest is TA-only, not live-composite

`backtest.py` explicitly declares `BACKTEST_COMPONENTS = ("ta",)` and `matches_live: false`. This is honest. But a PASS certifies only TA rules, not the live composite with FA/liquidity/vetoes.

**Correction:** keep the caveat, and add a point-in-time composite replay once historical FA/liquidity snapshots exist. Until then, do not use TA-only backtest PASS as full-strategy promotion evidence.

### 7.8 Grid search selects on out-of-sample aggregates

`grid_search()` runs multiple parameter sets through walk-forward windows and chooses the best by aggregate OOS metrics. The final “best” is therefore selected using the evaluation data. It is not an untouched holdout.

**Correction:** use nested walk-forward or a final holdout. Report selection bias and deflated Sharpe/probability of backtest overfitting where possible.

### 7.9 `sharpe_like` is not annualized Sharpe

The backtest computes:

```text
mean(R) / std(R) × sqrt(number_of_trades)
```

This is a trade-stream t-like statistic, not annualized Sharpe. The name `sharpe_like` is cautious, but users may still read it as Sharpe.

**Correction:** rename to `trade_t_stat` or compute time-based returns and annualized Sharpe separately.

### 7.10 MSI baseline and realized IC are not the same score

Realized IC uses journal `composite`. The baseline route computes `backtest_ic()` from `ta_score_series()` only. Therefore MSI compares live composite IC against TA-only historical IC.

**Correction:** baseline and realized IC must use the same score definition, horizon, filters, and cost/outcome model. If only TA baseline exists, label MSI as TA-component drift, not full-signal integrity.

### 7.11 Config hash is too narrow

Reproduced journal config hash:

```text
463c14c224 = sha1(risk_k|tp1|tp2|trail|long_min)
```

It omits TA weights, FA weights/provider policy, liquidity formula, composite weights, data contract, and exit-policy version.

**Correction:** hash a canonical config object including scoring weights, thresholds, provider policy, cost model, exit policy, data contract, and engine version. Store it on every signal, outcome, backtest, and drift run.

### 7.12 Adaptive recommendation and experiment use different transforms

Reproduction with factor 1.25:

```text
anchored recommendation: 40 + (68−40)×1.25 = 75.0
experiment scaled threshold: 68×1.25 = 85.0
```

The paired experiment does not test the threshold it recommends.

**Correction:** use the same transform in `adaptive.report()` and `adaptive_evidence.compare()`. If the anchored transform is intended, the experiment must pass anchored thresholds into `ta_score_series()`.

### 7.13 Statistical dependence and drift inference

Signal intelligence correctly warns that sample thresholds are count-only and independence is unverified. Still, drift tests use normal approximations, Wilson intervals, and BH correction over overlapping signal outcomes. Overlapping horizons and cross-sectional crypto correlation violate independence.

**Correction:** use block bootstrap by time/symbol, cluster-robust intervals, or pre-registered non-overlapping evaluation windows. Treat BH q-values as heuristic unless dependence is controlled.

---

## 8. Risk, execution, portfolio, and market statistics

### 8.1 Risk preflight is a strong foundation

`risk_engine.preflight()` centralizes halted, market-closed, concurrent-cap, cash, single-asset, min-notional, risk-cap, heat-cap, min-cash, and funds checks. Paper and what-if use it. This is good engineering.

### 8.2 Daily-loss halt is warning-only in the shared evaluator

Reproduction:

```text
daily_pnl = −5%
RiskEngine.evaluate() -> action=open, blocked=false, halted=false
warnings include daily-loss-halt
```

`RiskEngine.triggers()` can halt, but the shared evaluate/preflight path does not enforce daily loss as a gate.

**Correction:** add a daily-loss gate to preflight and wire a durable daily PnL source. A warning named `daily-loss-halt` must actually halt.

### 8.3 Live preflight uses paper-account context

`_live_preflight()` calls `risk_whatif()`, which uses `get_broker()` and paper equity/cash/open positions. Live placement then uses that projection. Unless the live account is deliberately mirrored, live risk is being checked against paper state.

**Correction:** live preflight must use live balances, live open orders/positions, live fees, live market state, and live exposure. Paper preview may remain separate.

### 8.4 Promotion evidence counts ledger rows, not qualified outcomes

`ExecutionLedger.promotion_evidence()` counts all `live_execution` rows and distinct creation dates. It checks ambiguous unknown states, but does not require filled, reconciled, non-rejected, costed, out-of-sample samples.

**Correction:** promotion should require qualified filled trades, reconciled exchange IDs, net PnL, fees/slippage, strategy/config hash, and a minimum number of independent evaluation days. Rejected/prepared rows should not count as evidence.

### 8.5 Fee model is not universally conservative

Project fee: fixed `0.2%` per leg for maker/taker and IRT/USDT.

Current official Nobitex pricing page lists, for the base tier:

- Toman markets: 0.25% maker/taker
- USDT markets: 0.10% maker, 0.13% taker

Therefore 0.2% underestimates base-tier IRT fees and overestimates base-tier USDT fees. Higher VIP tiers differ again.

**Correction:** use a fee schedule by market, tier, maker/taker, and effective date. Backtests should store the fee model version. Do not claim a single fixed fee is always conservative.

### 8.6 Portfolio ERC treats missing correlation as independence

Reproduction:

```text
corr = [[1, None], [None, 1]]
ERC weights = [0.5, 0.5]
```

The matrix reports missing pairs, but the allocator uses zero for missing correlations when computing weights. That can overstate diversification.

**Correction:** either exclude assets with missing correlation, use a conservative correlation floor, or require complete pairwise history. Report the assumption beside the allocation.

### 8.7 Benchmark/portfolio alignment compresses gaps

`aligned_closes()` intersects timestamps, then `log_returns()` treats consecutive common timestamps as one-bar returns. Reproduction:

```text
common stamps: 0, 900, 4500
second return spans 3600 seconds, nominal bar is 900 seconds
```

This mixes horizons in beta/correlation/alpha.

**Correction:** require a regular grid, mask gaps, or compute returns only between adjacent expected timestamps. Do not annualize mixed-horizon returns as if they were uniform.

### 8.8 Market overview is comparatively honest

`market_overview.py` deduplicates assets, excludes USDTIRT from breadth, converts turnover to USD, and labels weighted change as not an index. This is a good pattern. The scan/ranking path should adopt the same unit discipline.

---

## 9. Testing, quality, and security observations

### 9.1 Strengths

- 1742 backend tests and 23 JS suites passed locally.
- Static checks passed.
- Tests cover many no-data, unit, timestamp, veto, parity, and gate cases.
- Live mode is disabled by default and requires confirmation, credentials, operator role, CSRF, and promotion evidence.
- Security defaults avoid wildcard CORS and require token/auth for remote mutations.
- Dependency audit found no known runtime Python vulnerabilities and no high/critical npm vulnerabilities.

### 9.2 Test gaps exposed by this audit

Add regression tests for:

1. Equal USD turnover across IRT/USDT must produce equal liquidity inputs.
2. Final direction/composite contract must be explicit and tested.
3. UDF client must paginate or refuse when requested bars exceed one page.
4. 15m H3 must resolve only with ≥672 future bars and continuous coverage.
5. FA partial provider failure must not report full freshness.
6. Live and fallback FA scoring must be formula-equivalent or explicitly different.
7. Journal strategy return must include partial exits.
8. Gap-open stop fills must be open-aware.
9. Paper TP2 behavior must match the declared exit policy.
10. Daily-loss halt must block in preflight/evaluate.
11. Adaptive recommendation and experiment must use the same threshold transform.
12. Python/JS TA scores must match within a documented tolerance on golden fixtures.
13. Config hash must change when scoring weights/provider policy/exit policy change.
14. Live promotion must count qualified filled samples, not all ledger rows.

### 9.3 npm audit note

`npm audit` reported 5 moderate findings in the ESLint/minimatch dev-tool chain, with 0 high/critical. These are development-tool exposure, not runtime exchange execution vulnerabilities, but should be remediated in a dependency-update PR.

---

## 10. Prioritized remediation roadmap

### P0 — before any live-capital promotion

| Item | Acceptance criterion |
|---|---|
| Live preflight uses live account state | Live what-if and live order use live balances/exposure; paper state cannot satisfy live gates. |
| Daily-loss halt enforced | A daily PnL breach blocks new orders in evaluate/preflight/live route. |
| Promotion evidence qualified | Only reconciled filled samples with net costs and config hash count; rejected/prepared rows excluded. |
| Fee schedule realistic | Market/tier/maker/taker fee model stored and used in backtest/paper/live preview. |
| Live disabled until evidence | No live order path can bypass P0 gates; default remains disabled. |

### P1 — before trusting signal rankings or performance reports

| Item | Acceptance criterion |
|---|---|
| USD-normalized liquidity | Equal USD turnover produces equal liquidity score; universe sort uses USD turnover. |
| One decision contract | Direction, composite, veto, and promotion threshold are documented and tested end-to-end. |
| Paginated history | Requests >500 bars either paginate or refuse; H3 15m resolves with full coverage. |
| Unified exit policy | Journal/backtest/paper share one versioned exit policy or are explicitly labeled different strategies. |
| Correct outcome semantics | Strategy return, final-leg return, gross/net R are separate; IC uses the intended quantity. |
| FA provenance/coverage | Factor-level source/status/timestamp; partial failure cannot appear fully fresh. |
| FA fallback equivalence | Same normalized inputs produce same score, or fallback is capped/excluded from promotion. |
| Gap-aware simulation | Missing candles and adverse opens cannot silently improve outcomes. |
| Adaptive transform consistency | Recommended and tested thresholds are identical. |

### P2 — quality and research improvements

| Item | Acceptance criterion |
|---|---|
| TA/JS parity suite | Golden fixtures pass within tolerance; RSI/BB/ADX conventions canonical. |
| EMA200 maturity | Immature EMA200 is flagged or excluded/renormalized. |
| Regime hysteresis | ADX gate no longer flips on 0.01 noise without validation. |
| Config hash expansion | Hash includes scoring, data, cost, exit, and provider policy. |
| Nested validation | Grid search uses nested WF or untouched holdout. |
| Dependence-aware stats | Drift/CI methods account for overlapping horizons and cross-asset correlation. |
| ERC missing-correlation policy | Missing correlation cannot silently imply independence. |
| Regular-grid benchmarking | Beta/alpha/correlation refuse or mask non-adjacent timestamp gaps. |

---

## 11. Independent reproduction evidence

The file `evidence/reproductions.json` contains machine-readable outputs. Summary:

| ID | Observation |
|---|---|
| F01/F02 | Equal USD turnover: USDT liq 74 vs IRT liq 94; TA66 IRT composite 73.5 outranks TA70 USDT composite 71.1; TA70/FA30/liq50 composite 56 remains LONG. |
| F03 | Mock BTC live FA 77.2 vs fallback 75.0; depth100 equals live, depth0 penalizes; partial provider failure still `fresh/live`. |
| F04 | Mock 500-bar UDF cap: requested 1200, received 500; 15m H3 needs 672. |
| F05 | Removing a stop-touch candle changes R from −1.0 to 0.0 and removes `hit_sl`. |
| F06 | Partial-exit example: R 0.7, stored return 0.4%, strategy return 1.4%. |
| F07 | Stop 98 with next open 90 models R −1.0 instead of open-aware −5.0. |
| F08 | Paper mid 105 > TP2 104 remains PARTIAL with 0.5 qty after TP1. |
| F09 | Daily PnL −5% still evaluates `open`, not halted. |
| F10 | Factor 1.25: anchored recommendation 75.0 vs experiment threshold 85.0. |
| F11 | 303 TA parity cases: max diff 12.9, mean diff 0.3188, 1 direction flip; rising RSI Python null vs JS 99. |
| F12 | Python scalar/vectorized and prefix-causality differences: 0. |
| F13 | Missing correlation matrix produces ERC weights [0.5, 0.5]. |
| F14 | Common timestamps 0/900/4500 produce a 3600s return treated as one bar. |
| F15 | Journal config hash matches geometry-only SHA1 `463c14c224`. |

These are synthetic reproductions of code behavior, not measurements of live market profitability.

---

## 12. Recommended target signal-evaluation design

A more decision-grade architecture would be:

```text
1. Data contract
   - symbol registry, quote units, USD normalization
   - closed candles, continuity, pagination, source timestamps
   - factor-level provenance and coverage

2. Feature layer
   - mature TA features
   - asset-specific FA factors where available
   - market-wide overlays explicitly scoped
   - execution liquidity: spread, depth, impact, fees

3. Decision layer
   - one versioned policy: trigger, rank, veto, block
   - uncertainty-aware composite
   - no silent fallback formula changes

4. Evaluation layer
   - one canonical exit/cost policy
   - gross/net R, strategy return, final-leg return
   - open-aware fills, gap invalidation
   - point-in-time replay when possible

5. Validation layer
   - nested walk-forward / holdout
   - dependence-aware confidence intervals
   - config-hash cohorts
   - shadow gates before enforcement
   - paper/live reconciliation before promotion
```

The project already has many of these pieces. The main work is not adding more indicators; it is making the contracts between data, score, outcome, risk, and promotion consistent.

---

## 13. Sources and references

### Repository

- Commit: `2b5ea130f53959d357c5c8a664bfb1a52bd781e0`
- Key files: `server/main.py`, `server/ta_engine.py`, `server/fa_engine.py`, `server/journal.py`, `server/backtest.py`, `server/risk_engine.py`, `server/paper_trading.py`, `server/live_execution.py`, `server/adaptive.py`, `server/adaptive_evidence.py`, `server/signal_drift.py`, `server/portfolio.py`, `server/benchmark.py`, `nbx-core.js`.

### Official Nobitex documentation consulted

- Nobitex OHLC UDF documentation: `https://apidocs.nobitex.ir/market_data/دریافت-داده-های-ohlc` — 500-candle page cap, `countback` cap, pagination, 15m 7-day availability, 60 requests/minute.
- Nobitex pricing: `https://nobitex.ir/pricing/` — current tiered maker/taker fees by Toman/USDT market.
- Nobitex market stats API documentation: `https://old-apidocs.nobitex.ir/` and `https://apidocs.nobitex.ir/websocket/websocket-market-stats-channel` — `volumeDst` as destination-currency 24h transaction value.

### Local evidence files

- `evidence/pytest.log`
- `evidence/js-summary.txt`
- `evidence/ruff.log`, `evidence/mypy.log`, `evidence/eslint.log`, `evidence/eslint-dashboard.log`
- `evidence/pip-audit.json`, `evidence/npm-audit.json`
- `evidence/reproductions.json`, `evidence/reproductions.log`
- `evidence/ta-parity-js.json`
- `reproduce_findings.py`, `ta_parity.cjs`

---

## 14. Final opinion

Nobi is a serious, well-instrumented research and trading-dashboard codebase. Its strongest qualities are explicit provenance, extensive tests, cautious shadow-only gates, and a clear attempt to avoid fabricated data. Those are rare and valuable.

The current weak point is not the absence of indicators. It is the lack of a single enforced contract from raw data units to final signal, outcome, risk, and promotion. The most important fixes are:

1. normalize liquidity to USD;
2. define one direction/composite/veto contract;
3. paginate history and validate coverage;
4. unify exit/cost/outcome semantics;
5. make FA provenance and fallback honest and formula-stable;
6. enforce live risk gates against live account state;
7. validate with nested, dependence-aware, forward paper evidence.

After those corrections, the project would be much better positioned to answer the only question that ultimately matters: **does this signal, after real costs and real execution constraints, produce a repeatable out-of-sample edge?** The current code can help measure that question, but it should not yet be treated as having answered it.
