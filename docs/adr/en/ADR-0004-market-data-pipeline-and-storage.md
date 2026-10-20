# ADR-0004: Market Data Pipeline, Storage, and Data Quality

- **Status:** Accepted (partially implemented)
- **Date:** 2026-02-14
- **Deciders:** Technical Architect
- **Related ADRs:** 0002, 0003, 0005

---

## 1. Context

Accurate, gap-free historical candles drive both TA signals and backtests. Nobitex exposes UDF-compatible OHLC (`/market/udf/history`), orderbook v3, trades, and 24h stats. The universe is dynamic (new listings/delistings), with two quote currencies: **IRT** (toman) and **USDT**.

## 2. Decision

### 2.1 Universe Management
- Nightly job refreshes the tradable universe from `/market/stats` + market options (precision, min amounts). Statuses: `ACTIVE`, `HALTED`, `DELISTED`.
- Dual-quote view: every asset is tracked on both IRT and USDT books when available; a computed **IRT-premium** series (`IRT_price / (USDT_price × USDTIRT)` × 100 vs fair rate) is stored as a native feature — a materially important local factor.

### 2.2 Ingestion Scheduling (rate-budget aware)
| Tier | Symbols | Timeframes | Cadence |
|---|---|---|---|
| Tier-1 (majors: BTC, ETH, USDT-pairs with top volume) | ~10–20 | 5m, 15m, 1h, 4h, 1D | every candle close |
| Tier-2 (mid caps) | ~50 | 15m, 1h, 4h, 1D | every candle close |
| Tier-3 (rest of universe) | all remaining | 1h, 1D | every candle close |
- Historical backfill: chunked `from/to` windows respect response size limits; idempotent upserts.

### 2.3 Storage
- **TimescaleDB hypertable `candles`** `(symbol, timeframe, ts, o,h,l,c,v)` with unique index — the single source of truth.
- Derived tables: `orderbook_snapshots` (sampled best bid/ask, spread, depth-at-N), `trades_agg` (1m rollups), `market_stats_daily`.
- `parquet` research exports per symbol — **backtests consume the same candles loader as live**, eliminating train/serve skew.

### 2.4 Data Quality Gates (blocking)
1. **Gap detection:** expected vs actual candle timestamps; auto-refetch gaps; mark `bad_data` if unresolvable → symbol excluded from signals.
2. **Sanity:** OHLC invariants (`low ≤ o,c ≤ h`), volume ≥ 0, no duplicate ts.
3. **Staleness:** if latest candle age > 3× timeframe → symbol suspended from new entries.
4. **Split/wrong-token guard:** symbol alias table for renames/migrations.

### 2.5 Orderbook & Liquidity Features
- On candidate symbols: spread %, depth within ±1%/±2% of mid, imbalance ratio, recent trade intensity — feeds liquidity filter (ADR-0008) and paper-fill simulator (ADR-0009).

## 3. Consequences

**Positive:** gap-free, self-healing store; premium-aware features; reproducible research.

**Negative:** DB ops overhead; backfill burst needs patient rate budgeting (mitigation: rate-limited background backfiller with priority queue).

## 4. Retention & Ops
- Raw candles: indefinite. Orderbook snapshots: 180 days (compressed). Trades: 90 days raw, then aggregations.
- Nightly VACUUM/compression policy; backup encrypted to local storage.

---

## Implementation amendment — 2026-10-20

- **Implemented:** on-disk candle cache (`server/candle_cache.py`) with TTL and coverage;
  candles come from `/market/udf/history`.
- **Implemented (2026-10-20):** `GET /api/orderbook/{symbol}` exposes depth as
  normalised `{p, q}` levels plus spread, ±1% depth and imbalance. Before this the client
  read the book for the paper broker while no route published it, so the depth panel could
  only apologise.
- **Not implemented:** the 180-day snapshot archive. The route serves the current book
  only; nothing is persisted, so historical depth analysis is not possible.

## Second amendment — WebSocket (2026-10-20)

Until now only `public:orderbook-*` was consumed, and only for symbols with an open
position. Now:

| Channel | Consumer | Real effect |
|---|---|---|
| `public:market-stats-all` | `_apply_stream_stat` in `main.py` | the board's price and `dayChange` refresh between REST polls; the `market-closed` gate sees a market that closed *between* polls |
| `public:candle-{SYM}-{tf}` | `StreamExecutor.merge_last_candle` | `GET /api/candles` ends the series on the socket's last candle (`last_candle: "ws"`), so the chart and the scan run on the market's clock, not the poll's |
| `public:orderbook-{SYM}` | `HotBook` → position mids | unchanged, but a late subscription now actually reaches the wire |

Rules followed on the way:

- **keep-alive:** Centrifugo sends `{}` and expects `{}` back; the `websockets`
  protocol ping is a different layer. Answered at most once per second.
- **one unit rule:** Rial markets send both stats and candles in Rial; both go
  through `scale_stat_row`/`price_scale`, not through a second copy of the rule.
- **REST first, always:** as the documentation recommends; the socket only refreshes.
- **no socket means zero, not a wish:** `channels` in `/api/ws/status` lists only
  what really went on the wire; the wish-list is `channels_wanted`.
