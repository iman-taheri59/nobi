# ADR-0001: System Vision, Goals, and Scope

[فارسی](../fa/ADR-0001-چشم‌انداز-اهداف-و-دامنه-سیستم.md) · **English**

- **Status:** Accepted (charter)
- **Date:** 2026-02-14
- **Deciders:** Project Owner, Technical Architect
- **Related ADRs:** 0002, 0007, 0008

---

## 1. Context

We need a professional, precise, and complete automated trading bot for the **Nobitex** exchange (the largest Iranian crypto exchange, base URL `https://apiv2.nobitex.ir`). The bot must:

1. Analyze **all crypto assets listed on Nobitex** (both `IRT` = Iranian Toman pairs and `USDT` pairs) using **Technical Analysis (TA)** and **Fundamental Analysis (FA)**.
2. Use the **official Nobitex API** to place buy/sell orders.
3. Rank and select the **best trade candidates** automatically.
4. Execute profitable trades while protecting capital under strict, configurable risk rules.

Important: no system can guarantee profit. This ADR defines the vision; succeeding ADRs define the engineering that maximizes the probability of positive expectancy while bounding downside.

---

## 2. Goals

| ID | Goal | Success Criterion |
|----|------|-------------------|
| G1 | Cover the full Nobitex tradable universe (IRT + USDT pairs) | 100% of active markets scanned each cycle |
| G2 | Hybrid TA + FA signal generation with a single composite score (0–100) per asset | Deterministic, back-testable scoring |
| G3 | Fully automated execution via Nobitex official API (spot) | Order lifecycle reconciled against exchange state |
| G4 | Capital protection first: kill switches, drawdown halts, per-trade and portfolio risk caps | Configurable limits, enforced in code before any order is sent |
| G5 | Safe rollout: Backtest → Paper trading → Live micro-size → Full size | Promotion gates with measurable thresholds |
| G6 | Full observability & alerting | Every decision and order logged, notified, and auditable |

## 3. Non-Goals (Out of Scope)

- High-frequency / market-making strategies (Nobitex is a REST-polling API; latency-sensitive HFT is not viable).
- Margin/futures/leveraged trading (Phase 1 is spot-only; margin endpoints exist but are postponed).
- Withdrawal automation (bot will **never** hold withdrawal permissions).
- Cross-exchange arbitrage (future phase).
- Fiat automatic on/off-ramping.

## 4. Constraints

| Constraint | Impact |
|---|---|
| Nobitex rate limits (e.g., orderbook v3 ~300 req/min) | Polling scheduler must budget requests via token bucket |
| IRT pairs have local price premium vs. USDT pairs | Signal comparisons must be premium-aware |
| Semi-thin orderbooks for small-cap alts on Nobitex | Liquidity filters + slippage modeling mandatory |
| Geo/regulatory context (Iran + exchange ToS) | Must respect Nobitex terms; keys must be trade-only |
| Sporadic API maintenance windows | Circuit breaker + graceful degradation |

## 5. Decision

Adopt a **4-phase delivery plan**:

- **Phase 0 – Foundations:** exchange client, data ingestion, candle store, backtester.
- **Phase 1 – TA Engine + Paper Trading:** indicator/signal engine, paper execution, dashboards.
- **Phase 2 – FA Engine + Signal Fusion:** fundamental scorers, composite score, asset ranking.
- **Phase 3 – Live (micro-size):** trade-only API key, ≤1% of capital per trade, kill switch armed.
- **Phase 4 – Full Operation:** risk-scaled sizing within caps, continuous evaluation & re-tuning.

**Default mode is paper trading.** Live trading requires an explicit config flag plus a checklist (see ADR-0009).

## 6. Consequences

**Positive:** incremental risk; every phase produces a testable deliverable; capital is never at risk before evidence exists.

**Negative:** slower time-to-live; requires discipline to respect promotion gates.

## 7. Ethical & Legal Disclaimer

The software is a decision-support and execution tool, not a promise of profit. Users must comply with Nobitex Terms of Service and applicable regulations. All credentials stay local; withdrawal permissions are out of scope by design.

## 8. Success Metrics (global)

- Backtest: Profit Factor > 1.3, Max Drawdown < 15%, ≥ 100 trades sample.
- Paper: ≥ 30 days, live-data PF > 1.2.
- Operations: ≥ 99% job success rate, < 5s average signal-to-order latency at live.
