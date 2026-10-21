# ADR-0015: Currency units, market symbols and exchange gates at the API boundary

[فارسی](../fa/ADR-0015-واحد-پول-نماد-و-دروازه‌های-صرافی.md) · **English**

- **Status:** Accepted and implemented
- **Date:** 2026-10-20
- **Decision owner:** technical architect
- **Related:** 0003 (API connection), 0004 (data pipeline), 0008 (risk), 0009 (execution), 0014 (data honesty)
- **Enforced by:** `server/units.py`, `server/tests/test_api_conformance.py`, `server/ws_execution.py`

---

## 1. Context

A full read of the official Nobitex documentation showed the app and the exchange
speaking different dialects. All three divergences met at one boundary:

1. **Market keys.** `/market/stats` keys markets `btc-rls` / `btc-usdt`; `/v3/orderbook`
   and `/market/udf/history` take `BTCIRT` / `BTCUSDT`. The client only upper-cased the
   key, producing `BTC-RLS` — a market that does not exist — and passed that string to
   the order book and the chart.
2. **Currency units.** The docs are explicit: rial markets are quoted in Rial, not Toman.
   The dashboard labels every figure in Toman. Neither side was wrong; the missing
   conversion was.
3. **Order rules.** The documented minimum order (`SmallOrder` = 3,000,000 Rial / 11 USDT),
   amount/price precision, and `isClosed` were not applied by the paper broker.

Result: the paper broker filled orders the exchange would reject, the dashboard called
Rial figures "Toman", and "unknown market" was displayed as "empty market".

## 2. Decision

### 2.1 One module, one boundary: `server/units.py`

Everything that changes a unit or a market name lives in one file and is called **only**
from the API client:

| Function | Input | Output |
|---|---|---|
| `market_symbol("btc-rls")` | documented stats key | `BTCIRT` |
| `scale_stats` | rial stats payload | symbol-keyed, Toman-valued payload |
| `scale_orderbook` / `scale_udf` | rial levels and OHLC | Toman; amounts stay in the base coin |
| `truncate_amount` / `round_price` | quantity, price | the market's precision |
| `min_notional_usd(sym, usdt_irt_toman)` | documented minimum | minimum in account currency |

Rule: **no layer above the client divides by ten or rewrites `-rls` into `IRT`.**
If a Toman-denominated source is ever used, `NBX_PRICE_UNIT=toman` is the only change.

### 2.2 HTTP 200 means "replied", not "done"

Business errors arrive as HTTP 200 with `{"status":"failed","code":...}`. `_request`
used to treat them as data. Every `failed` body now becomes `ClientError(code)`, and a
`429` keeps the documented `backOff` so retries wait as long as the exchange asked.

### 2.3 Exchange gates live inside the shared `preflight`

Two gates were added to the **shared function**, not to the broker and not to the preview:

- `min-notional` — order value ≥ the documented minimum.
- `market-closed` — the market is not reporting `isClosed`.

Both `/api/risk/whatif` and `PaperBroker.place_limit` read `preflight`, so the preview
and the executor changed together. A preview missing a gate is a lie.

### 2.4 Minimums come from `/v2/options`, not from memory

The 3,000,000 Rial figure is in the docs and in the `minOrders` block of `/v2/options`.
The app calls `apply_options` once at startup and keeps the documented defaults if the
call fails.

### 2.5 The WebSocket reads what is published

The channel is `public:orderbook-{SYMBOL}` (not `market:`), and `pub.data` is a **JSON
string** carrying a full `[[price, amount], ...]` snapshot — not a dict with `bestBid`.
Both were fixed.

## 3. Consequences

- **Positive:** dashboard numbers equal exchange numbers; an order below the minimum is
  refused with a clear Persian sentence; a business error no longer becomes "no data".
- **Negative:** the IRT path carries one extra division, and tests written against
  mixed units had to be corrected (two in `test_core`).
- **Residual risk:** the changelog announces a base-URL change in Khordad 1404 but never
  states the new URL. `NBX_BASE_URL` stays until an official source appears.

## 4. Evidence

- `server/tests/test_api_conformance.py` — one test per documented rule.
- `server/units.py` — the only conversion point.
- `docs/reviews/2026-10-20-nobitex-api-conformance-fa.md` — the full mismatch table.

---

## Implementation amendment — 2026-10-20

### The standalone bundle carries the same gates

`nbx-core.js` contains a port of `risk_engine.preflight` (`NBX.Risk.preflight`)
because the single-file bundle on a phone has no backend to ask. A second copy of
a rule is dangerous, so the copies are **tested** rather than assumed equal:

- `scripts/check_preflight_parity.py` runs the same ten gates over 28 scenarios
  in both implementations and reports the first divergence; precision tables,
  minimums and risk defaults are compared too.
- `scripts/js_broker_test.js` checks the browser broker's behaviour (FX, exchange
  minimum, closed market, precision, equity).
- Both run inside `pytest` (`server/tests/test_js_parity.py`).

### Consequences

- `NBX.Paper` converts through the `USDTIRT` rate and refuses IRT orders without
  one (`no-fx-rate`); it used to treat Toman as dollars.
- `NBX.Units` maps `btc-rls` → `BTCIRT` and Rial → Toman on the path where
  `MarketEngine.loadUniverse` talks to the exchange directly.
- The local risk gate in the manual ticket was removed; the server decides and
  names the gate.
