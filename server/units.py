"""Exchange units, market symbols, minimums and tick sizes.

Everything here comes from the Nobitex API documentation
(https://apidocs.nobitex.ir) and exists because the app and the exchange do not
speak the same dialect:

* ``/market/stats`` keys markets ``btc-rls`` / ``btc-usdt``, while
  ``/v3/orderbook`` and ``/market/udf/history`` take ``BTCIRT`` / ``BTCUSDT``.
  Passing the stats key straight through to the order book asks for a market
  that does not exist.
* Stats, orderbook and order-entry prices for Rial markets are in Rial.
  REST UDF history for IRT symbols is already in Toman (verified against the
  DOGEIRT source candle on 2026-10-28). These are separate source contracts;
  applying the orderbook scale to UDF makes plans ten times too low.
  All normalized prices inside the app are in Toman.
* The exchange rejects orders below a documented minimum (3,000,000 Rial or
  11 USDT) and rounds amounts/prices to per-market precision. A preview that
  ignores those rules promises a fill the exchange will refuse.
"""

from __future__ import annotations

import contextlib
import math
import os
from decimal import ROUND_HALF_UP, Decimal, localcontext

#: One Toman is ten Rial. Not a market quantity — a unit definition.
RIAL_PER_TOMAN = 10.0

#: ``rial`` (what the exchange sends) or ``toman`` (what a Toman-denominated
#: source sends). Set ``NBX_PRICE_UNIT`` to override.
DEFAULT_UNIT = "rial"


# --------------------------------------------------------------------------- #
# Symbols
# --------------------------------------------------------------------------- #
def market_symbol(key: str) -> str | None:
    """``btc-rls`` → ``BTCIRT``; ``btc-usdt`` → ``BTCUSDT``.

    Returns ``None`` for keys that do not name a market (the docs mark
    ``global`` as deprecated and say it will disappear). Accepts an already
    normalised symbol unchanged, so it is safe to call on both dialects.
    """
    s = (key or "").strip().lower()
    if not s or s == "global":
        return None
    if "-" in s:
        coin, _, dst = s.partition("-")
        if not coin or not dst:
            return None
        if dst in ("rls", "irt"):
            return f"{coin.upper()}IRT"
        return f"{coin.upper()}{dst.upper()}"
    return s.upper()


def is_rial_market(symbol: str) -> bool:
    s = (symbol or "").upper()
    return s.endswith("IRT")


def quote_currency(symbol: str) -> str:
    """``rls`` for a rial market, ``usdt`` for a dollar market (API spelling)."""
    return "rls" if is_rial_market(symbol) else "usdt"


# Normalized data contract, NOT the raw exchange source unit.
IRT_PRICE_CONTRACT = "irt-toman-v2"


def price_contract(symbol: str) -> str:
    return IRT_PRICE_CONTRACT if is_rial_market(symbol) else "usdt-v1"


# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #
def price_scale(unit: str | None = None) -> float:
    """Multiplier that turns a rial-market quote into Toman."""
    unit = (unit or os.getenv("NBX_PRICE_UNIT") or DEFAULT_UNIT).strip().lower()
    return 1.0 if unit == "toman" else 1.0 / RIAL_PER_TOMAN


def to_toman(value, unit: str | None = None) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v * price_scale(unit)


#: Fields of one `/market/stats` row that follow the quote currency.
_STAT_PRICE_FIELDS = ("latest", "bestSell", "bestBuy", "dayLow", "dayHigh",
                      "dayOpen", "dayClose", "mark", "volumeSrc", "volumeDst",
                      "valueDst", "dayChange")
#: ... of which these do not: a percentage and a coin-denominated volume.
_STAT_UNSCALED = ("dayChange", "volumeSrc")


def scale_stat_row(symbol: str, row: dict, unit: str | None = None) -> dict:
    """One stats row, Rial → Toman when the market is Rial-quoted.

    The REST route (`/market/stats`) and the WebSocket channel
    (`public:market-stats-all`) carry the same numbers in the same unit, so
    both must go through this function. A second scaling rule written for the
    socket would have quoted every Rial market ten times too high.
    """
    r = dict(row or {})
    k = price_scale(unit) if is_rial_market(symbol) else 1.0
    for f in _STAT_PRICE_FIELDS:
        if r.get(f) is None:
            continue
        try:
            v = float(r[f])
        except (TypeError, ValueError):
            continue
        r[f] = v if f in _STAT_UNSCALED else v * k
    if "isClosed" in r:
        r["isClosed"] = bool(r["isClosed"])
    return r


def scale_stats(body: dict, unit: str | None = None) -> dict:
    """Convert a ``/market/stats`` payload to Toman in place-ish (new dict).

    Only rial markets are scaled; a dollar market already quotes dollars.
    Volume is scaled too: ``volumeDst`` is denominated in the destination
    currency, which for a rial market is Rial.
    """
    out = dict(body or {})
    stats = {}
    for key, row in (out.get("stats") or {}).items():
        sym = market_symbol(key)
        if sym is None:
            continue                      # deprecated `global` block
        stats[sym] = scale_stat_row(sym, row, unit)
    out["stats"] = stats
    return out


def scale_orderbook(body: dict, symbol: str, unit: str | None = None) -> dict:
    """Convert ``[[price, amount], ...]`` levels of a rial market to Toman.

    Levels always come back as floats: the exchange sends strings, and every
    consumer downstream was doing its own `float()` (or forgetting to).
    """
    k = price_scale(unit) if is_rial_market(symbol) else 1.0
    out = dict(body or {})

    def _levels(rows):
        res = []
        for row in rows or []:
            try:
                p, q = float(row[0]), float(row[1])
            except (TypeError, ValueError, IndexError):
                res.append(row)
                continue
            res.append([p * k, q])
        return res

    out["bids"] = _levels(out.get("bids"))
    out["asks"] = _levels(out.get("asks"))
    if out.get("lastTradePrice") is not None:
        # A level the exchange mis-typed stays as it arrived rather than
        # becoming a zero; `_levels` above already dropped the unusable ones.
        with contextlib.suppress(TypeError, ValueError):
            out["lastTradePrice"] = float(out["lastTradePrice"]) * k
    return out


def udf_price_unit(unit: str | None = None) -> str:
    """REST UDF's source unit, independent of stats/book/order-entry units.

    UDF IRT candles are already Toman. A proxy serving Rial candles may opt in
    explicitly; changing NBX_PRICE_UNIT must never change this contract.
    """
    value = (unit or os.getenv("NBX_UDF_PRICE_UNIT") or "toman").strip().lower()
    if value not in ("rial", "toman"):
        raise ValueError("NBX_UDF_PRICE_UNIT must be rial or toman")
    return value


def scale_udf(body: dict, symbol: str, unit: str | None = None) -> dict:
    """Normalize REST UDF OHLC to Toman; volume stays in the coin."""
    if not is_rial_market(symbol):
        return dict(body or {})
    k = price_scale(udf_price_unit(unit))
    out = dict(body or {})
    for f in ("o", "h", "l", "c"):
        seq = out.get(f)
        if isinstance(seq, list):
            with contextlib.suppress(TypeError, ValueError):
                out[f] = [float(x) * k for x in seq]
    return out


# --------------------------------------------------------------------------- #
# Minimums and tick sizes
# --------------------------------------------------------------------------- #
#: Documented in §market/orders/add (SmallOrder) and returned by /v2/options
#: under `minOrders`. Quoted in the market's own quote currency.
MIN_NOTIONAL_QUOTE = {"rls": 3_000_000.0, "usdt": 11.0}

#: Fallback tick sizes for the markets this dashboard ships. `/v2/options`
#: (`amountPrecisions`, `pricePrecisions`) is authoritative and can overwrite
#: these through `apply_options()`; these values are the documented examples and
#: the exchange's market precision policy, not a guess about prices.
_AMOUNT_STEP = {
    "BTCIRT": 0.000001, "BTCUSDT": 0.000001,
    "ETHIRT": 0.00001, "ETHUSDT": 0.0001,
    "SOLIRT": 0.0001, "SOLUSDT": 0.0001,
    "XRPIRT": 1.0, "XRPUSDT": 0.1,
    "ADAIRT": 1.0, "ADAUSDT": 1.0,
    "DOGEIRT": 1.0, "DOGEUSDT": 1.0,
    "TONIRT": 0.001, "TONUSDT": 0.001,
    "TRXIRT": 1.0, "TRXUSDT": 1.0,
    "USDTIRT": 0.1,
}

# Internal price ticks are normalized Toman / USDT, not raw Rial.
_PRICE_STEP = {"rls": 1.0, "usdt": 0.01}

#: Frozen copy of what this file ships, so an override can be undone. `apply_options`
#: writes to the tables above, which are process globals: a dashboard that read the
#: exchange's real minimums once keeps them, and anything that expected the
#: documented SmallOrder afterwards is wrong. That is correct in production — the
#: exchange is authoritative — and a trap in a test process, where one test's
#: network call silently rewrote what three later tests asserted.
_DOCUMENTED = {
    "min": dict(MIN_NOTIONAL_QUOTE),
    "amount": dict(_AMOUNT_STEP),
    "price": dict(_PRICE_STEP),
}


def reset_documented_defaults() -> None:
    """Undo every `apply_options` override, back to what this module documents.

    Exists for the test suite (see `server/tests/conftest.py`) and for any caller
    that needs the shipped tables rather than the exchange's live ones.
    """
    MIN_NOTIONAL_QUOTE.clear()
    MIN_NOTIONAL_QUOTE.update(_DOCUMENTED["min"])
    _AMOUNT_STEP.clear()
    _AMOUNT_STEP.update(_DOCUMENTED["amount"])
    _PRICE_STEP.clear()
    _PRICE_STEP.update(_DOCUMENTED["price"])


def apply_options(payload: dict) -> int:
    """Load precision overrides from a ``/v2/options`` payload.

    Returns how many markets were updated. Silently ignores anything malformed:
    a broken options document must not stop the dashboard from trading with the
    documented defaults.
    """
    nb = (payload or {}).get("nobitex") or {}
    n = 0
    for quote, value in (nb.get("minOrders") or {}).items():
        try:
            MIN_NOTIONAL_QUOTE[str(quote).lower()] = float(value)
            n += 1
        except (TypeError, ValueError):
            continue
    for sym, step in (nb.get("amountPrecisions") or {}).items():
        try:
            _AMOUNT_STEP[market_symbol(sym) or str(sym)] = float(step)
            n += 1
        except (TypeError, ValueError):
            continue
    for sym, step in (nb.get("pricePrecisions") or {}).items():
        try:
            value = float(step)
            symbol = market_symbol(sym)
            if symbol is None or not math.isfinite(value) or value <= 0:
                continue
            # /v2/options pricePrecisions is in destination currency (Rial
            # for IRT). This contract is independent of the book override.
            normalized = (float(Decimal(str(value)) / Decimal(str(RIAL_PER_TOMAN)))
                          if is_rial_market(symbol) else value)
            if normalized <= 0:
                continue
            _PRICE_STEP[symbol] = normalized
            n += 1
        except (TypeError, ValueError):
            continue
    return n


def amount_step(symbol: str) -> float:
    return _AMOUNT_STEP.get((symbol or "").upper(), 0.00000001)


def price_step(symbol: str) -> float:
    return _PRICE_STEP.get((symbol or "").upper(), _PRICE_STEP[quote_currency(symbol)])


def truncate_amount(qty: float, symbol: str) -> float:
    """Amounts are truncated toward zero (docs §monetary precision)."""
    step = amount_step(symbol)
    if step <= 0 or not math.isfinite(qty):
        return 0.0
    return math.floor(max(0.0, qty) / step) * step


def round_price(price: float, symbol: str) -> float:
    """Prices are rounded half-up to the market's tick (banker's rounding is
    *not* what the exchange documents)."""
    step = price_step(symbol)
    if step <= 0 or not math.isfinite(step) or not math.isfinite(price):
        return 0.0
    value, quantum = Decimal(str(price)), Decimal(str(step))
    with localcontext() as ctx:
        # Enough digits for all finite float inputs, including tiny ticks.
        ctx.prec = 2 * max(28, len(str(value)) + len(str(quantum))
                           + abs(value.adjusted()) + abs(quantum.adjusted()))
        multiples = (value / quantum).to_integral_value(rounding=ROUND_HALF_UP)
        return float(multiples * quantum)


def min_notional_quote(symbol: str) -> float:
    return MIN_NOTIONAL_QUOTE[quote_currency(symbol)]


def min_notional_usd(symbol: str, usdt_irt_toman: float) -> float:
    """The documented minimum expressed in the account's USD currency.

    ``usdt_irt_toman`` is the USDT/IRT rate *in Toman* — the same number the
    dashboard shows. Passing a Rial rate here would make every rial market's
    minimum look ten times too small.
    """
    q = quote_currency(symbol)
    if q == "usdt":
        return MIN_NOTIONAL_QUOTE["usdt"]
    if usdt_irt_toman <= 0:
        return 0.0
    return MIN_NOTIONAL_QUOTE["rls"] / RIAL_PER_TOMAN / usdt_irt_toman
