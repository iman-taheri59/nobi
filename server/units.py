"""Exchange units, market symbols, minimums and tick sizes.

Everything here comes from the Nobitex API documentation
(https://apidocs.nobitex.ir) and exists because the app and the exchange do not
speak the same dialect:

* ``/market/stats`` keys markets ``btc-rls`` / ``btc-usdt``, while
  ``/v3/orderbook`` and ``/market/udf/history`` take ``BTCIRT`` / ``BTCUSDT``.
  Passing the stats key straight through to the order book asks for a market
  that does not exist.
* Rial markets are quoted **in Rial, not Toman** ("واحد قیمت در بازارهای ریالی
  به ریال (و نه تومان) می‌باشد", docs §market/orders/add). The UI labels its
  numbers in Toman, so one conversion has to happen somewhere; it happens here,
  once, at the API boundary.
* The exchange rejects orders below a documented minimum (3,000,000 Rial or
  11 USDT) and rounds amounts/prices to per-market precision. A preview that
  ignores those rules promises a fill the exchange will refuse.
"""

from __future__ import annotations

import math
import os

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
        r = dict(row or {})
        k = price_scale(unit) if is_rial_market(sym) else 1.0
        for f in ("latest", "bestSell", "bestBuy", "dayLow", "dayHigh",
                  "dayOpen", "dayClose", "mark", "volumeSrc", "volumeDst",
                  "valueDst", "dayChange"):
            if r.get(f) is None:
                continue
            try:
                v = float(r[f])
            except (TypeError, ValueError):
                continue
            # `dayChange` is a percentage and `volumeSrc` is denominated in the
            # coin: neither follows the quote currency, so neither is scaled.
            r[f] = v if f in ("dayChange", "volumeSrc") else v * k
        if "isClosed" in r:
            r["isClosed"] = bool(r["isClosed"])
        stats[sym] = r
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
        try:
            out["lastTradePrice"] = float(out["lastTradePrice"]) * k
        except (TypeError, ValueError):
            pass
    return out


def scale_udf(body: dict, symbol: str, unit: str | None = None) -> dict:
    """Convert OHLC of a rial market to Toman. Volume stays in the coin."""
    if not is_rial_market(symbol):
        return dict(body or {})
    k = price_scale(unit)
    out = dict(body or {})
    for f in ("o", "h", "l", "c"):
        seq = out.get(f)
        if isinstance(seq, list):
            try:
                out[f] = [float(x) * k for x in seq]
            except (TypeError, ValueError):
                pass
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

_PRICE_STEP = {"rls": 10.0, "usdt": 0.01}


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
            _PRICE_STEP[market_symbol(sym) or str(sym)] = float(step)
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
    if step <= 0 or not math.isfinite(price):
        return 0.0
    return math.floor(price / step + 0.5) * step


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
