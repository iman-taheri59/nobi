# ADR-0007: Decision Engine — Signal Fusion, Composite Scoring, and Asset Selection

- **Status:** Accepted (implemented)
- **Date:** 2026-02-14
- **Deciders:** Technical Architect, Quant Lead
- **Related ADRs:** 0005, 0006, 0008, 0009

---

## 1. Context

We must turn many per-symbol TA signals and FA ratings into a **single ranked list** of executable trade candidates across the whole Nobitex universe — precise, deterministic, and auditable — then map top candidates to orders the risk engine can size safely.

## 2. Decision

### 2.1 Composite Score
```
composite = clamp( ( w_ta · TAsignal.tscore_regime_adjusted
                   + w_fa · FA.fa_score
                   + w_liq · LiquidityScore )
                 − penalties, 0..100 )
```
- Default weights (v1): `w_ta=0.55, w_fa=0.25, w_liq=0.20` (versioned YAML, per-regime overrides).
- **Hard gates first:** FA vetoes, symbol suspension, data staleness, spread > max, depth < min, daily-loss kill active (ADR-0008).
- LiquidityScore from spread/depth/imbalance features — thin books get penalized even when signals look great.

### 2.2 Candidate Pipeline (per cycle, e.g., every candle close)
1. Universe (ACTIVE) → data-quality filter (ADR-0004)
2. Compute/refresh TA signals per timeframe + MTA bias (ADR-0005)
3. Join FA ratings & vetoes (ADR-0006)
4. Composite score → rank descending
5. **Selection rules (portfolio construction):**
   - Take top-N (configurable, e.g., N=3–5 concurrent positions).
   - **Correlation clustering:** max 1–2 positions per correlation cluster (e.g., L1s, memecoins) using rolling-return correlation — prevents disguised concentration.
   - **Sector/cap diversity:** at most 30% portfolio exposure to one bucket.
   - Cooldown after stop-out per symbol (e.g., 24h) to avoid revenge re-entries.
   - IRT vs USDT leg choice: prefer better expected net return **after premium + fees**; if IRT premium is stretched beyond threshold, prefer USDT leg or defer.
6. Produce `TradePlan {symbol, side, entry mode, stop, TPs, size_pct_cap, score_ttl}` → Risk Engine.

### 2.3 Sell/Exit Policy
- Signal reversal below exit threshold, trailing stop (ATR- or Supertrend-based), staged take-profits (R1/R2/R3), time-stop (no progress after N candles), and FA-veto forced exit (news-driven).
- Portfolio-level de-risking on regime shift (e.g., market regime classifier flips bearish + Fear&Greed extreme).

### 2.4 Assumptions-of-Record (anti-hype)
Backtests include fees & modeled slippage; composite weights are disclosed in reports; every selected trade stores full score breakdown (why-this-trade audit).

## 3. Alternatives Considered
| Option | Verdict |
|---|---|
| RL agent for selection | Rejected v1: data hunger + non-determinism; revisit Phase 4+ |
| Simple "top-1 momentum" | Rejected: concentration, no risk/FA awareness |
| Fully manual curation | Contradicts autonomy requirement |

## 4. Consequences

**Positive:** explainable ranking; diversification by construction; local-market awareness (premium/liquidity).

**Negative:** composite-weight tuning risks overfit (mitigation: fixed defaults, walk-forward evidence, change-freeze windows, change log with hypotheses).
