# ADR-0005: Technical Analysis Engine — Architecture and Design

- **Status:** Proposed
- **Date:** 2026-02-14
- **Deciders:** Technical Architect, Quant Lead
- **Related ADRs:** 0004, 0007, 0008
- **Detailed indicator catalogue:** `docs/analysis/technical-analysis-capabilities.md`

---

## 1. Context

The TA engine turns stored OHLCV (+ orderbook features) into normalized, comparable trading signals for every Nobitex symbol and monitored timeframe. Requirements: deterministic, configurable, back-testable, and composable with fundamentals.

## 2. Decision

### 2.1 Indicator Registry
A declarative registry (name → function, params, warmup bars, category):
- **Trend:** SMA/EMA (9/20/50/200), MACD, ADX+DI, Parabolic SAR, Ichimoku, Supertrend, VWAP
- **Momentum:** RSI, Stochastic %K/%D, CCI, ROC, Williams %R
- **Volatility:** Bollinger Bands, ATR, Keltner Channels, Donchian
- **Volume:** OBV, MFI, CMF, Volume Profile levels
- **Structure:** pivot highs/lows, S/R zones, Fibonacci retracement levels, candlestick patterns (engulfing, hammer, doji, morning/evening star), divergence detection (price vs RSI/MACD)

Computation vectorized with `pandas-ta`/`TA-Lib`; warmup-window guard prevents look-ahead and NaN leakage.

### 2.2 Signal Layer (per symbol × timeframe)
Each signal family emits a normalized contribution in `[-100, +100]`:
- Trend continuation (MA stack, ADX>25, price vs VWAP)
- Mean reversion (RSI extremes vs regime, BB %b z-scores)
- Breakout (Donchian/Supertrend flips with volume filter)
- Pattern/divergence events (bounded contributions, anti-spam cooldown)
- Liquidity-adjusted entries (spread/depth penalties from ADR-0004 features)

**Regime gating:** trend-following signals active only when ADX/Hurst indicate trend; mean reversion only in ranging regimes. This prevents the classic failure of applying trend logic in chop.

### 2.3 Multi-Timeframe Alignment (MTA)
- Higher timeframe (e.g., 4h/1D) defines directional bias; execution timeframe (15m/1h) times entries (fractal alignment).
- MTA agreement multiplies score; disagreement hard-caps the score.

### 2.4 Pluggable Strategy Packs
YAML-defined packs (weights per signal family, per regime) → hot-swappable without code edits; versioned; each pack is individually back-testable and shadow-evaluable.

### 2.5 Optional ML Extension (Phase 4+)
Feature table (indicators + regime + premium) → gradient-boosted classifier predicting forward returns; used as a meta-filter multiplier, never a stand-alone trigger. Purged walk-forward validation only.

## 3. Output Contract
```
TASignal {
  symbol, timeframe, ts,
  direction: LONG | EXIT | NEUTRAL,
  ta_score: 0–100,
  components: {...auditable breakdown per family...},
  suggested: {entry_type, stop_atr_mult, tp_rr, validity_secs}
}
```

## 4. Consequences

**Positive:** auditable signals; regime awareness; independent pack evaluation; MTA reduces whipsaws.

**Negative:** parameter overfit risk (mitigation in ADR-0009: walk-forward + out-of-sample gates); indicator redundancy (mitigation: correlation pruning in research phase).
