# ADR-0006: Fundamental Analysis Engine — Architecture and Data Sources

[فارسی](../fa/ADR-0006-موتور-تحلیل-فاندامنتال.md) · **English**

- **Status:** Accepted (implemented)
- **Date:** 2026-02-14
- **Deciders:** Technical Architect, Research Lead
- **Related ADRs:** 0002, 0007
- **Detailed metric catalogue:** `docs/analysis/fundamental-analysis-capabilities.md`

---

## 1. Context

TA alone is blind to token quality, unlock schedules, on-chain health, and news shocks. The FA engine produces a slow-moving `fa_score` (0–100) per asset plus **hard vetoes** (red flags) that gate trading regardless of technicals. All inputs are free/low-cost APIs (no paid SaaS required for v1), refreshed on slower cadences (hourly for market data, daily for on-chain, event-driven for news).

## 2. Decision — Data Providers & Metric Groups

### 2.1 Market Structure & Liquidity (hourly/daily)
| Metric | Source |
|---|---|
| Market cap, rank, FDV, circulating/total supply | CoinGecko free API (fallback: CoinMarketCap) |
| Volume/market-cap ratio (fake-volume filter) | CoinGecko + Nobitex own stats |
| Exchange availability & Nobitex-specific liquidity | `/v3/orderbook` depth features (ADR-0004) |
| Age of listing (new-listing risk) | CoinGecko genesis/listing date |

### 2.2 Tokenomics & Supply Risk (weekly)
- Inflation rate, emission schedule, upcoming **token unlocks / vesting cliffs** (TokenUnlocks/aggregated calendars).
- Holder concentration (top-10/top-50 share where available), insider/team reserves.
- Staking/yield dynamics for PoS assets.

### 2.3 On-Chain Health (daily) — majors only in v1
Free tiers: DefiLlama (TVL, fees, revenue), public RPC stats.
- Active addresses, transaction count growth, network fees trend
- TVL trend & TVL/MCap ratio (L1/L2, DeFi tokens)
- Stablecoin flows and exchange netflows (where available)
- Hash rate/security budget for PoW (BTC, LTC, DOGE...)
- NVT ratio as valuation sanity check

### 2.4 Sentiment & News NLP (event-driven + hourly)
| Source | Usage |
|---|---|
| CryptoPanic API (news aggregation) | headline stream, community votes |
| RSS feeds of major crypto/news outlets | event detection |
| Fear & Greed Index (alternative.me) | market-wide regime overlay |
| Fear-of-recency social metrics (CoinGecko tickers/social data; X API if later licensed) | hype/spam gauge |
Analysis pipeline: multilingual (EN/FA) sentiment classification (lexicon + optional local LLM), entity linking (BTC, ETH, symbol aliases), **event taxonomy** (hack, delist, regulation, partnership, exploit, unlock) with severity weights. Dedup + source-reputation weighting to resist shilling/manipulation.

### 2.5 Macro Overlay (daily)
- DXY, US yields, gold, BTC dominance; proxied via free feeds.
- **Iran-local overlay:** USDT/IRT free-market premium trend & volatility (from Nobitex itself: `USDTIRT` market) — regime classifier for local demand surges and premium squeeze risk.

## 3. Scoring Model
```
fa_score = w1·MarketQuality + w2·Tokenomics + w3·OnChainHealth + w4·Sentiment + w5·MacroFit − Penalties
```
- Weights versioned in YAML; normalized per-metric via robust z/percentile ranks (clipped).
- **Veto rules (hard):** exchange-wide delist announcement, critical exploit/hack news, imminent unlock > X% of mcap, stated supply collapse, Nobitex halt, data-stale providers.
- Output: `FARating { symbol, fa_score, vetoes: [...], evidence: {...auditable links/metric values...} }`

## 4. Alternatives Considered
| Option | Verdict |
|---|---|
| Paid on-chain suites (Glassnode etc.) v1 | Rejected (cost); optional upgrade path |
| LLM-only news scoring without guardrails | Rejected: hallucination risk; LLM optional behind deterministic rules |
| Ignoring FA for v1 | Rejected by requirement; implemented but as multiplier + vetoes, conserving TA as speed layer |

## 5. Consequences

**Positive:** filters junk-tier and event-risked assets; premium/local alpha exploited; fully auditable evidence trail.

**Negative:** provider rate limits/reliability (mitigation: caching, per-provider circuit breakers, staleness flags, graceful degrade of that metric group).
