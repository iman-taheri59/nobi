# nobitrader fundamental-analysis capabilities catalogue

[فارسی](fundamental-analysis-capabilities.md) · **English**

> **This document is a catalogue, not a list of implemented features.** §0 states exactly what is computed today; §§1–10 describe the full possible scope, and a substantial number of those items are not in the code.
>
> Actual engine output: `fa_score` (0–100) + a list of vetoes + auditable `evidence`, from three providers: CoinGecko, DefiLlama, and alternative.me. **CryptoPanic, news RSS, and macro overlays are not used by the code**, even though they appear in this document’s source list.
>
> English translation added on 2026-11-02. The implementation snapshot remains dated 2026-10-20; translating this document does not constitute a new implementation audit.

## 0. Implementation status (reconciled with `server/fa_engine.py`)

Last reconciled: 2026-10-20.

| Catalogue section | Status | Code details |
|---|---|---|
| 1. Market structure | **Partial** | Market cap, FDV, MCap/FDV ratio (and dilution using `total_supply/max_supply`), volume/market-cap ratio, listing age, and Nobitex depth are implemented. Volume distribution across exchanges is absent. |
| 2. Tokenomics | **No (ratios only)** | Only MCap/FDV and a fixed penalty when `circulating/max < 0.6`. Unlock calendars, inflation rates, holder concentration, value accrual, and PoS metrics are absent; the `unlock-7d>5%` veto fires only for the demo symbol `XYZ`. |
| 3. On-chain health | **Partial** | Only TVL from DefiLlama. Network activity, NVT/MVRV, and exchange flows are absent. `pow_bonus` is a fixed **+12** for BTC/LTC/DOGE, not a hash-rate calculation. |
| 4. Sentiment and NLP | **No (FGI only)** | Only Fear & Greed from alternative.me. News classification, event taxonomy, dominance, funding, and OI are absent. |
| 5. Macro overlay | **No** | The macro score defaults to 55 and moves only with the USDT/IRT premium; DXY, 10-year bonds, gold, and global liquidity are not read. |
| 6. Iran layer | **Partial** | The USDT/IRT premium is calculated and passed to the engine (since 2026-10-20; previously a fixed 0 was passed, so the `premium-stretched` veto effectively never fired). Domestic flow maps, local calendars, and policy filters are absent. |
| 7. Composite scoring | **Yes** | `WEIGHTS = {market:0.25, tokenomics:0.25, onchain:0.20, sentiment:0.20, macro:0.10}` — matches the document. |
| 8. Red flags | **5 of 8 rules** | Implemented: `new-listing<90d`, `thin-tvl`, `extreme-fear`, `premium-stretched`, and `unlock-7d>5%` (demo). Hack/delist/network downtime, regulatory events, and spoofing are absent. |
| 9. Sourcing and audit | **Partial** | `evidence`, a 10-minute cache, and a global provider Circuit Breaker are implemented. A nightly FA-factor report is absent; the cache is not refreshed every 60 seconds, and `source` is distinguished from `profile` (`live` / `cached` / `fallback`). |

**Maintenance rule:** Do not change a “No” row to “Yes” until a function and a test exist in `server/fa_engine.py`. A veto whose condition can never occur (such as a premium fed as a fixed 0) counts as an absent capability and must remain “No” in this table.

**Data sources (all free/low-cost):** CoinGecko API, DefiLlama API, CryptoPanic, news RSS, alternative.me (Fear & Greed), internal Nobitex exchange data (order book/statistics), and free macro overlays.

---

## 1. Market-structure and liquidity metrics (hourly/daily updates)

| Metric | How it is used |
|---|---|
| Market capitalization and rank (Market Cap) | Minimum-size filter; penalty for nano-caps |
| FDV and MCap/FDV ratio | Future-supply risk measure; a very low ratio warns of unlocks |
| Circulating/total supply | Complements the ratio above |
| Volume/market-cap ratio | Implausible volume relative to size → lower score (possible fake volume) |
| Native Nobitex depth and spread | Real tradable liquidity on Nobitex, not global liquidity |
| Project listing age | Less than X days = new-listing risk |
| Market distribution across exchanges (when available) | Volume concentrated on one exchange = fragility |

## 2. Tokenomics and supply risk (weekly/event-driven updates)

- **Unlock and vesting calendar:** Upcoming releases (amount, percentage of circulating supply, exact date). Rules: unlock > 1% of circulating supply in the next week → lower score; > 5% in the next month → hard veto on new positions.
- **Inflation/emission rate:** Annual supply expansion compared with approximate demand growth.
- **Holder concentration:** Share held by top addresses; team/issuer share, where a public proxy is available from CoinGecko/free explorers.
- **Value-accrual mechanism:** Buyback & Burn, fee distribution to holders, staking, and real yield (APR net of inflation).
- **PoS adaptation metrics:** Staked ratio, lock rate, and changes to network procedures.

## 3. On-chain health (daily updates; majors only in version 1)

| Category | Metrics | Free source |
|---|---|---|
| Network activity | Active-address trend, transaction count, fees | Public explorer data/DefiLlama |
| DeFi economics | TVL trend, TVL/MCap ratio (L1/L2), protocol revenue and fees, sector P/S | DefiLlama |
| PoW security | Hash rate and security-budget estimate for BTC/LTC/DOGE, etc. | Public feeds |
| Exchange flows | Net exchange inflow/outflow (selling pressure/accumulation) | Free subscription plans where available |
| Cycle valuation | NVT and MVRV percentiles where available | Calculated from free sources |

## 4. Sentiment analysis and news processing (NLP) — event-driven + hourly

### 4.1 Multilingual sentiment classification

- Sentiment analysis of EN/FA headlines and news using a crypto lexicon plus an optional model.
- Output per news item: polarity (−1..+1), intensity, source, and source-credibility weight.
- Time decay: news half-life to weight the impact of a news signal over time.

### 4.2 Event taxonomy and veto rules

| Event | Detectors | FA action |
|---|---|---|
| Hack/exploit | Keywords such as confirmed exploit/flash-loan/tvldrain | Veto + immediate exit |
| Delisting/exchange warning | Official delist / trading halted | Veto + sell at the first opportunity |
| Regulatory | Country/topic classification and impact severity | Lower score or veto |
| Partnership/new listing | Official source or the most credible sources | Limited score increase |
| Near-term unlock | Unlock calendar | Rules from §2 |
| Fundamental guidance / tokenomics change | Official announcement | Review the tokenomics score |

- Anti-shilling measures: remove repetition across outlets, weight source credibility, require ≥2 independent sources for a news veto (to prevent fabricated FUD), and apply a cooling-off window after severe news.

### 4.3 Macro-sentiment indicators

- **Fear & Greed Index** (alternative.me): overall-regime overlay; extreme greed plus a weak technical setup → reduce exposure.
- **BTC/USDT dominance:** Altcoin risk flow (altseason expansion or exhaustion).
- **Funding rate and Open Interest (if futures data becomes available):** Leverage-fragility overlay.

## 5. Macroeconomic overlay (daily)

| Metric | Impact logic |
|---|---|
| DXY (US dollar) | Usually an inverse medium-term correlation with crypto |
| US 10-year bonds and interest-rate expectations | Tightening liquidity → risk aversion |
| Gold and traditional risk indices | A map of global capital-market sentiment |
| Liquidity index (optional) | Co-movement with the medium-term crypto trend |

## 6. Iran-specific layer (local competitive advantage)

This is the most distinctive analytical advantage for a Nobitex trading bot:

- **USDTIRT premium:** Trend and volatility of the tether-toman rate on Nobitex itself versus reference rates. Pseudo-risk forecast: buying an altcoin against toman when the premium is high carries “premium compression” risk alongside asset risk → prefer a tether base or a shorter-term trade.
- **Pseudo-domestic-flow map:** Changes in demand intensity in toman order books relative to tether.
- **Traditional/local calendar (optional):** Account for local busy-hour behavior in the volume pipeline.
- **Domestic policy filter:** Iranian regulatory news, classified separately in the event taxonomy.

## 7. Composite fundamental scoring

```
fa_score = w1·market_structure + w2·tokenomics + w3·onchain_health + w4·sentiment_and_news + w5·macro_overlays − risk_penalties
```

- Default version-1 weights: market structure 25%, tokenomics 25%, on-chain 20%, sentiment/news 20%, macro 10% (YAML-configurable by asset class: major/L2/DeFi/meme).
- For meme coins: lower on-chain/tokenomics weights and higher sentiment weight, plus a low score floor → short-term trades only, with small size.
- Percentile normalization within categories for fair comparison.

## 8. Red-flag system (hard vetoes)

Any one of these blocks a new entry regardless of the technical setup and triggers an exit/risk-reduction process for an open position:

1. Confirmed hack/exploit affecting the project or bridge.
2. Official delisting announcement or market halt on Nobitex.
3. Large imminent unlock above the configured threshold.
4. Network reversal/operational halt (effective downtime).
5. High-severity regulatory news affecting the asset or crypto flows in Iran.
6. Data deprivation: persistent delay from critical providers → trade in an “information-risk-reduced mode”.
7. Volume/participant fraud: spoofing patterns or anomalous artificial volume in the Nobitex order book.
8. Immutable smart-contract change/emergency token migration without exchange support.

## 9. Sourcing, validation, and audit

- Every score/veto is stored with `evidence` (metric value, source, and recorded timestamp).
- Source resilience: cache + a Circuit Breaker per provider + a stale flag → reduce the weight of that metric group rather than setting it to zero.
- Nightly “FA ranking factors” report for decision-path transparency.

## 10. Limitations and future development paths

- Version 1 uses free sources → on-chain/flow accuracy is more limited than paid tools. Upgrade path: paid on-chain APIs such as Glassnode and Santiment.
- Code/contract review (technical security analysis and audits) belongs to version 2 or later.
- NLP upgrade: use a local language model for more accurate classification of Persian news and specialist phrases.
- Crypto-data aggregation: track top wallets and whales within the limits of free data.
