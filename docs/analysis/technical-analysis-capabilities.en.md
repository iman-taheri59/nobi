# nobitrader technical-analysis capabilities catalogue

[فارسی](technical-analysis-capabilities.md) · **English**

> **This document is a catalogue, not a list of implemented features.** The sections below describe the possible scope; what is actually computed today is listed in §0. Read §0 before citing a capability: several items in this catalogue do not exist in the code, and describing them as “available analysis” would be misleading.
>
> English translation added on 2026-11-02. The implementation snapshot remains dated 2026-10-20; translating this document does not constitute a new implementation audit. Section numbering follows the Persian source.

## 0. Implementation status (reconciled with `server/ta_engine.py`)

Last reconciled: 2026-10-20, HEAD after the veto bug fixes.

| Catalogue section | Status | Code details |
|---|---|---|
| 1. Trend | **Partial** | EMA 9/21/50/200, MACD(12,26,9), and ADX/DI14 are implemented. Parabolic SAR, Ichimoku, Supertrend, and VWAP are not in the code (grep = 0). |
| 2. Momentum | **Partial** | Only RSI14 and the MACD histogram. Stochastic, CCI, ROC, Williams %R, and Awesome Oscillator are absent. |
| 3. Volatility | **Partial** | Bollinger(20,2σ) with %B and ATR14 are implemented. Keltner and Donchian channels are absent; the daily volatility ceiling is enforced by the risk module (ADR-0008), not by TA. |
| 4. Volume and order book | **Partial** | A 30-candle volume average plus the momentum volume condition, a 20-candle high breakout, and Nobitex order-book depth/spread (route `/api/orderbook/{symbol}`) are implemented. OBV, MFI, CMF, Volume Profile, and the trades feed (`/v2/trades`) are absent. |
| 5. Candlestick patterns and structure | **No** | No candlestick pattern, fractal pivot, support/resistance level, Fibonacci level, trend line, or harmonic pattern exists in the code. The only substitute is the 20-candle high breakout in the `structure` score. |
| 6. Divergence | **No** | There is no dedicated module. |
| 7. Market regime | **Partial** | Only two states, `TREND`/`RANGE`, based on ADX>25; outside a trend, the multiplier is `RANGE_GATE=0.85`. Hurst, Squeeze, and ATR percentiles are absent. |
| 8. Multi-timeframe analysis | **No** | Each request evaluates one timeframe; the MTA agreement/conflict matrix and IRT/USDT alignment are not implemented. Allowed timeframes: `15m, 1h, 4h, D`. |
| 9. Scoring | **Yes, with different numbers** | `COMPONENT_WEIGHTS = {trend:0.40, momentum:0.25, volume:0.15, structure:0.20}` and thresholds `LONG_SCORE_MIN=68`, `NEUTRAL_SCORE_MIN=55` — not the 35/25/15/15/10 weights and 70/40 thresholds stated in the original §9 draft. At least 60 candles are required. |
| 10. Backtesting | **Yes** | Event-driven engine with fees and slippage, `WalkForwardRunner` with training/evaluation windows, parameter grid, and promotion gate (`/api/backtest/*`). Sensitivity matrices and correlated-signal pruning are absent. |
| 12. Signal analytics | **Yes** | Immutable journal, H1/H2/H3 horizons + R, IC, calibration, family attribution, MSI, VOID, CSV/JSON export, and Telegram digest. |

**Maintenance rule:** Change a “No” or “Partial” row to “Yes” only when the indicator name can be found in `server/ta_engine.py` (or its JS mirror) and has a test.

**Data inputs:** OHLCV candles from `/market/udf/history` at 1, 5, and 15 minutes, 1 and 4 hours, and daily; order book from `/v3/orderbook`; trades feed from `/v2/trades`; toman premium series.

---

## 1. Trend indicators

| Indicator | Default parameters | Derived signal |
|---|---|---|
| Simple/exponential moving averages (SMA/EMA) | 9, 20, 50, 200 | Aligned moving-average stack, golden/death cross, price distance from the averages |
| MACD | (12, 26, 9) | Signal-line cross, histogram jump, divergence |
| ADX + DI | 14 | Trend strength (ADX>25), +DI/−DI direction |
| Parabolic SAR | 0.02 / 0.2 | Dot reversal, natural trailing stop |
| Ichimoku cloud | (9, 26, 52) | Price position relative to the cloud, Tenkan/Kijun cross, cloud thickness |
| Supertrend | (10, 3×ATR) | Trend flip, dynamic trailing stop |
| VWAP | Session/weekly | Price above/below VWAP, mean reversion toward VWAP |

## 2. Momentum indicators

| Indicator | Parameters | Signal |
|---|---|---|
| RSI | 14 | Overbought/oversold relative to market regime, break of the 50 level |
| Stochastic | (14, 3, 3) | %K/%D cross in extreme zones |
| CCI | 20 | Exit from the ±100 zones |
| ROC | 12/25 | Price-change acceleration, momentum pivots |
| Williams %R | 14 | −20 / −80 zones |
| Awesome Oscillator | (5, 34) | Twin peaks, zero-line cross |

## 3. Volatility indicators

- **Bollinger Bands (20, 2σ):** %b, bandwidth (Bollinger Squeeze → possible precursor to an explosive breakout), and band walking during a trend.
- **ATR (14):** Basis for stop/take-profit dimensions and position sizing (direct link to ADR-0008).
- **Keltner Channel (20, 1.5×ATR):** Band crosses together with trend.
- **Donchian Channel (20/55):** Classic breakout strategy with a volume filter.
- **Daily volatility metric:** Compliance with maximum slippage and risk limits.

## 4. Volume and order-flow analysis

- **OBV (On-Balance Volume):** Confirmation/rejection of price movement; volume-price divergence.
- **MFI (Money Flow Index):** Volume-weighted RSI for money-flow extremes.
- **CMF (Chaikin Money Flow):** Balance of buying/selling pressure over the latest 20 candles.
- **Volume Profile:** Accumulated volume at each price level → POC (highest volume) and VA (Value Area); more realistic support/resistance.
- **Nobitex-specific order-book metrics:** Spread percentage, depth at ±1%/±2%, buyer/seller imbalance ratio, and absence of artificial walls (first-level spoofing detection).
- **Raw trade histogram (`/v2/trades`):** Buyer/seller initiative intensity (Trade Imbalance).

## 5. Candlestick patterns and price structure

- **Reversal patterns:** Bullish/bearish Engulfing, Hammer/Inverted Hammer, Morning/Evening Star, Harami, Three White Soldiers/Three Black Crows.
- **Pivots:** Automatic Swing High/Low detection with a fractal window → mapping the HH/HL and LH/LL chain (trend map).
- **Support/resistance zones:** Level clustering by touch count and strength (Tested-vs-Rejected).
- **Fibonacci retracement and extension:** Levels 0.382 / 0.5 / 0.618 and targets 1.272 / 1.618 — used to set TPs and re-entry zones.
- **Trend lines and channels:** Automatic polyline fitting across swings.
- **Harmonic patterns (advanced/phase 2):** Gartley, Bat, and Butterfly with ratio-match validation.

## 6. Divergence — dedicated module

- Classic and hidden bullish/bearish divergence between price and RSI, MACD histogram, or OBV.
- Output: divergence type, magnitude (depth), and confidence score (interval length and number of swings).
- Anti-spam protection: at least N candles between signals and at most one active signal per symbol.

## 7. Market-regime detection

The input governing which signal families are enabled or disabled:

| Regime | Identifiers | Bot behavior |
|---|---|---|
| Trending | ADX>25, Hurst>0.55, wide Ichimoku cloud | Trend strategies enabled; mean-reversion strategies disabled |
| Ranging | ADX<20, narrow Bollinger Bands, Hurst near 0.5 | Mean reversion (such as RSI+Bollinger) enabled |
| Compressed / awaiting breakout | Squeeze (Bollinger inside Keltner) | Start a breakout monitor with a volume filter |
| High volatility | ATR volatility above the historical 90th percentile | Reduce size, widen the stop, or defer |

## 8. Multi-timeframe analysis (MTA)

| Role | Timeframe | Purpose |
|---|---|---|
| Direction | Daily / 4-hour | Determine the permitted direction (buy only in an upstream trend) and key levels |
| Entry timing | 1-hour / 15-minute | Time entries in the upstream trend direction |
| Trade management | 15 / 5-minute | Trailing stop, TPs, and exits |

- MTA agreement is scored as a matrix: full agreement → multiply the final score; directional conflict → block entry.
- Toman/tether base-role check: examine trend alignment in the USDT and IRT bases to control the premium effect.

## 9. TA scoring and signal structure

Each symbol receives a score in each cycle:

```
TA_Score = Σ (family_weight × family_score(0..100)) × regime_factor × MTA_factor × liquidity_factor
```

- Default family weights in the code: **trend 40%, momentum 25%, volume 15%, structure 20%** (there is no “divergence” family). The 35/25/15/15/10 figures were an early draft and were never applied in the code.
- Auditable output: each family’s score plus evidence (indicator values at the decision moment).
- Thresholds in the code: `LONG` for a score ≥ 68, `NEUTRAL` for ≥ 55, otherwise `EXIT`. The values 70/40 are not in the code.

## 10. TA backtesting and evaluation tools

- Event-driven simulation with Nobitex fee tiers and slippage modeled from order-book depth.
- Walk-Forward Analysis: forward optimization/test windows; parameter-stability reporting.
- Sensitivity matrix for each parameter; pruning redundant signals by response correlation.
- Standard output: PF, Sharpe/Sortino, MDD, CAGR, win rate, average R, and breakdowns by symbol/regime/timeframe.

## 12. Predicted-signal performance reporting (Signal Analytics)

Under ADR-0012, every signal emitted by this engine is fully tracked and evaluated later:

- **Immutable log:** Every prediction is recorded with its score, rank, regime, input features at the decision moment, and TP/SL levels (`signal_log`).
- **Outcome horizons:** Results are recorded at four horizons — H1 (+1 hour), H2 (+1 day), H3 (+7 days), and R-Multiple relative to the ATR stop.
- **Quality metrics:** Hit rate, expectancy (average R), virtual profit factor, calibration curve, and information coefficient (IC) between score and realized return.
- **Success attribution:** Average R broken down by TA family (trend, momentum, volume, pattern, divergence), so each family’s scoring can be reweighted.
- **Signal states:** PENDING → IN_PROGRESS → RESOLVED (+VOID for halted symbols).
- **Health sentinel:** If realized IC falls below half the backtest value, emit a “signal decay” warning and stop promotion to live mode.
- **UI output:** The dashboard’s Performance Report page with symbol/timeframe/family/date filters, CSV/JSON export, and a weekly Telegram summary.

This feedback loop is the main basis for retuning signal-family weights (§9) and validating strategy packs.

## 11. Limitations and validity boundaries (transparency)

- TA is a probabilistic tool, not a predictor; news shocks can break a regime suddenly → the FA veto layer and emergency kill switch provide coverage.
- For thin Nobitex markets, extreme TA signals receive a stronger penalty through the liquidity factor.
- All indicators are recomputed; only current values at candle close are considered valid.
