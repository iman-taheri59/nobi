# ADR-0003: Nobitex API Integration and Exchange Client Design

[فارسی](../fa/ADR-0003-اتصال-به-API-نوبیتکس.md) · **English**

- **Status:** Accepted (read-only path implemented)
- **Date:** 2026-02-14
- **Deciders:** Technical Architect
- **Related ADRs:** 0002, 0008, 0009
- **Reference:** Official docs https://apidocs.nobitex.ir — base URL `https://apiv2.nobitex.ir`

---

## 1. Context

The bot integrates with the **official Nobitex API only**. Current production endpoints (per official docs) include:

### 1.1 Public market data (no auth)
| Purpose | Endpoint | Notes |
|---|---|---|
| 24h market stats (universe) | `GET /market/stats` | Full pair list, volume, last price |
| Orderbook v3 | `GET /v3/orderbook/{SYMBOL}` or symbol=`all` | `[price, amount]` levels, `lastTradePrice`; ~300 req/min |
| Recent trades | `GET /v2/trades/{SYMBOL}` | Tick flow for volume signals |
| OHLC candles (UDF) | `GET /market/udf/history?symbol=BTCIRT&resolution={1,5,15,60,240,D}&from=<unix>&to=<unix>` | TradingView UDF format (`t,o,h,l,c,v`) |
| Market options/precision | system options endpoints | Amount/price step per market for order validation |

### 1.2 Authenticated account & trading
| Purpose | Endpoint | Notes |
|---|---|---|
| Wallets / balances | `GET /v2/wallets` (legacy: `/users/wallets/balance`) | free/locked amounts |
| Place order | `POST /market/orders/add` | `type=buy\|sell`, `execution=limit\|market` (+ stop/OCO supported), **`clientOrderId` for idempotency** |
| Order status | `POST /market/orders/status` / `GET /market/orders/list` | Fill reconciliation |
| Cancel | `POST /market/orders/update-status` (cancel) / `cancel-batch` | Lifecycle control |

### 1.3 Authentication
Two supported schemes:
1. **Token:** header `Authorization: Token <token>` (long-lived session token).
2. **API Key (preferred for the bot):** headers `Nobitex-Key`, `Nobitex-Signature`, `Nobitex-Timestamp`; payload `timestamp + METHOD + full_path + raw_body` signed with **Ed25519**. Keys are **permission-scoped** (READ / TRADE scopes) — the bot will never request withdrawal rights.

## 2. Decision

Build a thin, owned adapter — `exchange/NobitexClient`:

1. **Dual auth support**, default **Ed25519 API key** with READ+TRADE scope only. Secrets from env vars (`NOBITEX_API_KEY`, `NOBITEX_API_SECRET`), never committed.
2. **Canonical internal models** (`Symbol, Candle, OrderBook, Order, Trade, Balance`) so strategy code never touches vendor JSON.
3. **Reliability policy:** connect/read timeouts (3s/10s), retries with exponential backoff + jitter for **GETs only**; **orders are never blindly retried** — idempotency via `clientOrderId` + status reconciliation instead. Circuit breaker opens after N consecutive failures (no trading while open).
4. **Rate limiting:** client-side token bucket per endpoint class; burst protection; cached stats; multi-symbol batching via `/v3/orderbook/all`.
5. **Clock safety:** server-side `Nobitex-Timestamp` skew check; drift logged.
6. **Contract tests:** recorded JSON fixtures for every endpoint; CI runs adapter tests against fixtures + optional sandbox smoke.
7. **Adapter interface `ExchangePort`** allows a second exchange connector later (international exchange for global data such as funding/OI for FA).

## 3. Alternatives Considered

| Option | Verdict |
|---|---|
| Unofficial community SDKs | Rejected: trust & maintenance risk; consult only |
| Scraping the website | Rejected: ToS violation, fragile |
| Polling CDNs/global exchanges for signals, Nobitex only for execution (Phase 3+) | Accepted as optional upgrade for majors (BTC/ETH liquidity drives), with premium correction |

## 4. Consequences

**Positive:** vendor isolation, idempotent order flow, honest rate budgets, security-aligned auth.

**Negative:** we own upgrade maintenance when Nobitex ships API changes (mitigation: contract tests, version pinning, changelog watch).

## 5. Operational Notes

- Logged but redacted: request/response metadata (never signatures/secrets).
- Every order carries our `clientOrderId` = deterministic hash of (signal_id, symbol, side, ts-bucket) → safe re-attempts without duplicates.
- On startup, reconcile: fetch open orders + balances; treat exchange as source of truth before scheduling new signals.

---

## Implementation amendment — 2026-10-20

The §2 table defines the full API contract, not what the client implements. Today:

| Group | Status |
|---|---|
| `/market/stats`, `/v3/orderbook/{symbol}`, `/market/udf/history` | implemented (`server/nobitex_client.py`) |
| `POST /market/orders/*` (place/cancel/status) | **not implemented** — the client has no order-submitting method |
| API-key auth (`NOBITEX_API_KEY/SECRET`) | reserved in `.env.example` as phase 3 only |

So the dashboard is **read-only** today, which agrees with the promotion gates in
ADR-0009 rather than violating them.
