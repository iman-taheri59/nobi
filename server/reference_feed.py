"""A second market-data source, for development and cross-checks (ADR-0014).

Nobitex is unreachable from outside Iran, which makes two ordinary things
impossible: developing against real candles, and checking whether the numbers on
screen are plausible. This module adds a **reference** feed for exactly those
two jobs — and for nothing else.

What it is *not*: a substitute for the venue. Every number it produces carries
`source="reference"`, the promotion gate stamps it `REFERENCE_DATA` and can
never return `PASS`, because a strategy validated on another exchange's prices
has not been validated on the venue it trades. That distinction is the whole
point of the feature; a reference feed that quietly filled in for Nobitex would
be the most expensive bug in this repository.

Provider notes (measured from this project's own sandbox, 2026-10-22):

    api.binance.com       451  — geo-blocked, the same class of problem
    www.okx.com           200
    api.kucoin.com        200  ← default
    api.exchange.coinbase 200
    apiv2.nobitex.ir    timeout

Binance is the obvious first thought and the wrong default: it is exactly as
unreachable from a blocked network as the exchange we are trying to work around.
KuCoin is the default here because it answered, its klines endpoint needs no API
key, and its candle shape is the same six-field row the rest of the code uses.

Configuration
-------------
``NBX_REFERENCE``            ``kucoin`` (default) or ``none`` to disable
``NBX_REFERENCE_BASE``       override the base URL (a mirror, or a test server)
``NBX_REFERENCE_TIMEOUT``    seconds, default 6
``NBX_TOMAN_PER_USDT``       optional fixed FX rate for deriving IRT prices

Deriving an IRT series
----------------------
``BTCIRT`` has no counterpart on a USD exchange. It can be *derived* as
``BTCUSDT_reference × toman_per_usdt``, but that number silently assumes the
rial premium is zero, which is precisely the thing the rial market exists to
price. So derivation is opt-in (a rate must be supplied), the result is labelled
``derived``, and the FX assumption is written into the report.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("nbx-reference")

#: Nobitex timeframe string → KuCoin candle type.
TF_MAP: dict[str, str] = {
    "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1hour", "4h": "4hour", "D": "1day",
}

#: Seconds per timeframe, used to sanity-check spacing in what came back.
TF_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "D": 86400,
}

#: KuCoin answers `/market/candles` with a window, not a count: asking without
#: `startAt`/`endAt` returned 100 rows here regardless of the timeframe, so a
#: request for 700 bars used to arrive as 100 and nothing said so. One page is
#: sized in seconds and pages walk backwards until the caller has what it asked
#: for — with a hard stop, because "fetch until it stops returning data" is how a
#: background worker ends up in an unbounded loop.
PAGE_BARS = 300
MAX_PAGES = 8

QUOTE_SUFFIXES = ("USDT", "USDC", "USD", "BTC", "ETH")
IRT_SUFFIXES = ("IRT", "IRR", "TMN")


class ReferenceError(Exception):
    """The reference feed could not answer, and said why."""

    def __init__(self, code: str, message: str, meta: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.meta = meta or {}


@dataclass
class Provider:
    name: str
    base: str
    timeout: float = 6.0
    enabled: bool = True
    #: last price seen per symbol, and when — a cheap staleness signal
    prices: dict[str, tuple[float, float]] = field(default_factory=dict)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def active_provider() -> Provider:
    """Read the configuration at call time, not at import.

    The running process may be told to use a mirror, and a test may point it at
    a fake server; neither should need a module reload.
    """
    name = (_env("NBX_REFERENCE", "kucoin") or "kucoin").lower()
    base = _env("NBX_REFERENCE_BASE", "https://api.kucoin.com").rstrip("/")
    try:
        timeout = float(_env("NBX_REFERENCE_TIMEOUT", "6") or 6)
    except ValueError:
        timeout = 6.0
    return Provider(name=name, base=base, timeout=timeout, enabled=name not in ("", "none", "off"))


# --------------------------------------------------------------------------- #
# Symbol handling
# --------------------------------------------------------------------------- #
def provider_pair(symbol: str) -> tuple[str, bool]:
    """`BTCUSDT` → `("BTC-USDT", False)`; `BTCIRT` → `("BTC-USDT", True)`.

    An IRT market is mapped to its USDT counterpart and marked *derived*: the
    caller still has to supply a toman rate, because the rial premium is exactly
    what a USD exchange cannot tell us.
    """
    s = (symbol or "").upper().strip()
    for q in IRT_SUFFIXES:
        if s.endswith(q) and len(s) > len(q):
            return f"{s[: -len(q)]}-USDT", True
    for q in QUOTE_SUFFIXES:
        if s.endswith(q) and len(s) > len(q):
            return f"{s[: -len(q)]}-{q}", False
    raise ReferenceError("bad-symbol", f"{s!r} is not a BASE/QUOTE pair this feed can map",
                         {"symbol": s})


def toman_rate(explicit: float | None = None) -> float | None:
    """The FX rate to use for derivation, if the operator supplied one."""
    if explicit is not None and explicit > 0:
        return float(explicit)
    raw = _env("NBX_TOMAN_PER_USDT")
    try:
        v = float(raw) if raw else 0.0
    except ValueError:
        return None
    return v if v > 0 else None


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
async def _get_json(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    try:
        r = await client.get(url)
    except httpx.TimeoutException as e:
        raise ReferenceError("timeout", f"reference feed timed out: {url}") from e
    except httpx.HTTPError as e:
        raise ReferenceError("network", f"reference feed unreachable: {e}") from e
    if r.status_code == 451:
        raise ReferenceError(
            "geo-blocked",
            "the reference provider answered 451 (unavailable for legal reasons) — "
            "it is as blocked as the venue this was meant to work around",
            {"status": 451},
        )
    if r.status_code != 200:
        raise ReferenceError("http", f"reference feed returned {r.status_code}",
                             {"status": r.status_code})
    try:
        return r.json()
    except ValueError as e:
        raise ReferenceError("bad-json", "reference feed returned non-JSON") from e


def _client(provider: Provider) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(provider.timeout),
                             headers={"User-Agent": "TraderBot/nobi"})


async def fetch_candles(
    symbol: str,
    tf: str = "1h",
    limit: int = 500,
    fx: float | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """Return ``(rows, meta)`` in the same candle shape the rest of the repo uses.

    ``rows`` are ascending by time, ``t`` in epoch **seconds**, exactly like
    `nobitex_client.get_candles`. ``meta`` records where they came from and
    whether IRT prices were derived.
    """
    provider = active_provider()
    if not provider.enabled:
        raise ReferenceError("disabled", "reference feed is disabled (NBX_REFERENCE=none)")
    if tf not in TF_MAP:
        raise ReferenceError("bad-timeframe", f"unsupported timeframe {tf!r}",
                             {"timeframes": sorted(TF_MAP)})
    pair, derived = provider_pair(symbol)
    # All normalized IRT candles are Toman, including derived reference rows.
    # Reference provenance stays separate; units do not justify mixing venues.
    rate = 1.0
    toman = None
    if derived:
        toman = toman_rate(fx)
        if toman is None:
            raise ReferenceError(
                "no-fx",
                "an IRT series can only be derived with a toman rate "
                "(pass toman_per_usdt or set NBX_TOMAN_PER_USDT)",
                {"symbol": symbol},
            )
        rate = toman

    wanted = max(1, int(limit))
    step = TF_SECONDS[tf] * PAGE_BARS
    now = int(time.time())
    seen: dict[int, dict] = {}
    pages = 0
    end = now
    async with _client(provider) as client:
        while len(seen) < wanted and pages < MAX_PAGES:
            url = (f"{provider.base}/api/v1/market/candles?type={TF_MAP[tf]}&symbol={pair}"
                   f"&startAt={end - step}&endAt={end}")
            payload = await _get_json(client, url)
            pages += 1
            if str(payload.get("code")) != "200000" or not isinstance(payload.get("data"), list):
                raise ReferenceError("provider-error", f"unexpected payload from {provider.name}",
                                     {"code": payload.get("code"), "page": pages})
            fresh = 0
            for item in payload["data"]:
                try:
                    # [start, open, close, high, low, volume, turnover] — close comes
                    # *third* in KuCoin's row, which is a silent way to swap high/low.
                    t, o, c, h, lo, v = item[0], item[1], item[2], item[3], item[4], item[5]
                    stamp = int(float(t))
                except (TypeError, ValueError, IndexError):
                    continue
                if stamp in seen:
                    continue          # the window overlaps the previous page
                seen[stamp] = {
                    "t": stamp,
                    "o": float(o) * rate,
                    "h": float(h) * rate,
                    "l": float(lo) * rate,
                    "c": float(c) * rate,
                    "v": float(v),
                }
                fresh += 1
            if fresh == 0:
                break                  # the venue has nothing older to give
            end = min(seen) - 1

    rows = [seen[t] for t in sorted(seen)][-wanted:]
    if not rows:
        raise ReferenceError("empty", f"{provider.name} returned no candles for {pair} {tf}",
                             {"pair": pair, "tf": tf})
    meta = {
        "provider": provider.name,
        "pair": pair,
        "tf": tf,
        "derived": derived,
        "quote_unit": "toman" if derived else "usdt",
        "toman_per_usdt": toman,
        "rows": len(rows),
        "first": rows[0]["t"],
        "last": rows[-1]["t"],
        # A short answer is stated, never implied: a caller that asked for 700
        # bars and got 240 must not believe it backtested 700.
        "requested": wanted,
        "pages": pages,
        "truncated": len(rows) < wanted,
    }
    return rows, meta


async def fetch_price(symbol: str, fx: float | None = None) -> tuple[float, dict[str, Any]]:
    """Last traded price, for a cheap liveness/cross-check call."""
    provider = active_provider()
    if not provider.enabled:
        raise ReferenceError("disabled", "reference feed is disabled (NBX_REFERENCE=none)")
    pair, derived = provider_pair(symbol)
    rate = 1.0
    if derived:
        toman = toman_rate(fx)
        if toman is None:
            raise ReferenceError("no-fx", "a toman rate is needed to derive an IRT price",
                                 {"symbol": symbol})
        rate = toman
    url = f"{provider.base}/api/v1/market/orderbook/level1?symbol={pair}"
    async with _client(provider) as client:
        payload = await _get_json(client, url)
    data = payload.get("data") or {}
    try:
        price = float(data["price"]) * rate
    except (KeyError, TypeError, ValueError) as e:
        raise ReferenceError("provider-error", f"no price in the {provider.name} payload") from e
    provider.prices[symbol.upper()] = (price, time.time())
    return price, {"provider": provider.name, "pair": pair, "derived": derived,
                   "quote_unit": "toman" if derived else "usdt",
                   "as_of": int(time.time())}


async def status() -> dict[str, Any]:
    """One call the dashboard can make to answer "is the fallback feed alive?"."""
    provider = active_provider()
    out: dict[str, Any] = {"provider": provider.name, "base": provider.base,
                           "enabled": provider.enabled}
    if not provider.enabled:
        out.update({"reachable": False, "reason": "disabled"})
        return out
    started = time.perf_counter()
    try:
        price, meta = await fetch_price("BTCUSDT")
    except ReferenceError as e:
        out.update({"reachable": False, "reason": e.code, "message": e.message})
        return out
    out.update({
        "reachable": True,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "sample": {"symbol": "BTCUSDT", "price": price},
        "paired_with": meta["pair"],
        "note": "read-only reference data; never a substitute for the venue",
    })
    return out
