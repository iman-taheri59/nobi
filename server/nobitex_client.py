"""
Nobitex API client (ADR-0003) — async, resiliency-first.

Public endpoints used in Phase 1:
  GET /market/stats
  GET /v3/orderbook/{SYMBOL} (and symbol=all)
  GET /market/udf/history?symbol=...&resolution=...&from=...&to=...

Private endpoints (Phase 3+) — wired but gated:
  Auth via Ed25519 API key (headers Nobitex-Key/-Signature/-Timestamp)
  Trading is isolated in live_execution.py behind owner/promotion interlocks.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from candle_cache import CANDLE_CACHE
from units import scale_orderbook, scale_stats, scale_udf

log = logging.getLogger("nbx-client")

#: The one identity every request carries. Nobitex asks for `TraderBot/XXXXX`
#: where XXXXX is a unique *name* — not a URL (it rots: repos get renamed or
#: made private, and then the header is a dead claim in someone else's log)
#: and not a version number (nothing in this repo could keep it honest).
DEFAULT_UA = "TraderBot/nobi"
_UA_PREFIX = "TraderBot/"
_UA_MAX = 150


def resolve_ua(raw: str | None = None) -> str:
    """`TraderBot/` + a name, or the default when what was given is unusable.

    Read at call time, not at import, so a running process can be named and a
    test can rename it without reloading modules.
    """
    ua = (raw or os.getenv("NBX_UA") or DEFAULT_UA).strip()
    if ua.lower().startswith(_UA_PREFIX.lower()):
        ua = _UA_PREFIX + ua.split("/", 1)[1]
    else:
        ua = _UA_PREFIX + ua
    if not ua.isascii() or len(ua) <= len(_UA_PREFIX) or len(ua) > _UA_MAX:
        log.warning("NBX_UA=%r is not a usable header value; using %s", raw, DEFAULT_UA)
        return DEFAULT_UA
    return ua


def safe_request_context(path: str) -> str:
    """Only known public route names and bounded market fields may reach logs.

    Never echo hosts/userinfo, arbitrary path segments, query parameters,
    response bodies, headers or credentials, including on malformed URLs.
    """
    try:
        url = urlsplit(path)
        query = parse_qs(url.query, max_num_fields=100)
    except ValueError:
        return "[unrecognized endpoint]"
    if url.path in ("/market/stats", "/market/udf/history"):
        endpoint = url.path
    elif re.fullmatch(r"/v3/orderbook/(?:[A-Z0-9]{1,24}|all)", url.path):
        symbol = url.path.rsplit("/", 1)[-1]
        return f"/v3/orderbook/{{symbol}} symbol={symbol}"
    else:
        return "[unrecognized endpoint]"
    fields = [endpoint]
    if endpoint == "/market/udf/history":
        for key, pattern in (("symbol", r"[A-Z0-9]{1,24}"),
                             ("resolution", r"(?:[0-9]{1,5}|D|W|M)")):
            values = query.get(key, [])
            if values:
                value = values[0] if len(values) == 1 and re.fullmatch(pattern, values[0]) else "[invalid]"
                fields.append(f"{key}={value}")
    return " ".join(fields)


class ClientError(Exception):
    """Typed error chain for the adapter."""

    def __init__(self, code: str, message: str, meta: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.meta = meta or {}


class RateLimitError(ClientError):
    pass


def is_outage(e: ClientError) -> bool:
    """Does this failure mean "the exchange is down", or "this request is wrong"?

    The client-wide circuit breaker is for outages. A per-symbol refusal —
    401/403 (endpoint or pair not public), 404, any other 4xx — must stay
    local: one pair the exchange does not serve turned into "circuit breaker
    is open" for every other pair in the same scan.

    429 counts as an outage: the exchange is telling this client to slow down,
    which is a statement about the client, not about one symbol.
    """
    if e.code in ("TIMEOUT", "NETWORK", "RATE"):
        return True
    status = (e.meta or {}).get("status")
    if isinstance(status, int):
        return status == 429 or status >= 500
    return False




#: How long a per-pair UDF refusal is remembered. Nobitex answers 403 for some
#: pairs' public history (USDTIRT is the usual one) and for delisted symbols;
#: asking again every five minutes produced the same red error line forever and
#: buried the errors that actually meant something. Six hours is long enough to
#: stop the noise and short enough that a pair that becomes public is picked up
#: the same day.
UDF_REFUSAL_TTL_S = float(os.getenv("NBX_UDF_REFUSAL_TTL_S", "21600"))


class UpstreamUnavailable(ClientError):
    """Nobitex could not be reached and no cached answer exists.

    Raising is the point: the dashboard must say "the exchange is unreachable",
    not draw a price chart invented by a random-number generator.
    """

    def __init__(self, message: str = "Nobitex is unreachable", meta: dict | None = None):
        super().__init__("UPSTREAM", message, meta or {})


class AuthRequired(ClientError):
    pass


class TokenBucketer:
    """Simple client-side token bucket for rate budgeting."""

    def __init__(self, per_minute: int):
        self.capacity = per_minute
        self.tokens = float(per_minute)
        self.refill = per_minute / 60.0
        self.updated = time.monotonic()

    def get(self, cost: float = 1) -> None:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.refill)
        self.updated = now
        self.tokens -= cost

    @property
    def healthy(self) -> bool:
        return self.tokens >= 0


@dataclass
class ClientStats:
    total: int = 0
    ok: int = 0
    fail: int = 0
    last_latency_ms: float | None = None

    def success_rate(self) -> float:
        return (self.ok / self.total * 100) if self.total else 0.0


class NobitexClient:
    """
    Usage:
        client = NobitexClient()
        await client.connect()  # for a persistent session (optional)
        data = await client.get_market_stats()
    """
    BASE_URL = "https://apiv2.nobitex.ir"

    #: There used to be a hard-coded `SYMBOLS` tuple here. Nothing read it —
    #: the market list comes from `/market/stats` — and it had already drifted
    #: from the exchange's own list (the docs ship MATICIRT/MATICUSDT; the
    #: tuple did not). A second list of "what exists" is a second thing to be
    #: wrong. `units.market_symbol` turns the stats payload into symbols; that
    #: is the only source.

    def __init__(
        self,
        base_url: str | None = None,
        timeout_s: float = 6.0,
        rate_per_min: int = 120,
        session_factory: Callable[[], httpx.AsyncClient] | None = None,
    ):
        self.base_url = (base_url or os.getenv("NBX_BASE_URL") or self.BASE_URL).rstrip("/")
        self.timeout = timeout_s
        self.bucket = TokenBucketer(rate_per_min)
        self.stats = ClientStats()
        self._client: httpx.AsyncClient | None = None
        self._session_factory = session_factory
        self._closed = False
        self._circuit_open_until: float = 0.0
        # symbol -> monotonic deadline: "the exchange refused this pair's history"
        self._udf_refused: dict[str, float] = {}
        self.degraded_mode: bool = False
        self.ua = resolve_ua()

    async def __aenter__(self) -> NobitexClient:
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.aclose()

    async def connect(self) -> None:
        if self._client is not None:
            return
        if self._session_factory:
            self._client = self._session_factory()
        else:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, read=self.timeout * 1.5),
                headers={"User-Agent": self.ua},
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=40),
            )

    async def aclose(self) -> None:
        self._closed = True
        if self._client:
            await self._client.aclose()
            self._client = None

    # ---------------- Auth (ADR-0003) ----------
    # Compatibility: this endpoint requires a READ-ONLY token or API key pair
    # only for private data; public endpoints stay unauthenticated.
    def _public_headers(self) -> dict:
        return {"User-Agent": self.ua}

    def _emit_stats(self, ok: bool, latency_ms: float) -> None:
        self.stats.total += 1
        if ok:
            self.stats.ok += 1
        else:
            self.stats.fail += 1
        self.stats.last_latency_ms = latency_ms

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        if self._client is None:
            await self.connect()
        assert self._client is not None
        if self._closed:
            raise ClientError("CLOSED", "client already closed")
        if not self.bucket.healthy:
            await asyncio.sleep(max(0.0, (self.bucket.tokens * -1) / (self.bucket.refill or 1)))
        self.bucket.get()
        t0 = time.perf_counter()
        try:
            resp = await self._client.request(method, path, **kwargs)
            latency = (time.perf_counter() - t0) * 1000
            self._emit_stats(resp.is_success, latency)
            if resp.status_code in (401, 403):
                raise ClientError(
                    "AUTH", f"Nobitex rejected request with {resp.status_code} — "
                    f"{safe_request_context(path)} (cause not determined)",
                    {"status": resp.status_code},
                )
            if resp.status_code == 429:
                # The docs ship `backOff` (seconds) and `limit` with the error;
                # sleeping a fixed amount can be shorter than asked for, and
                # repeating that gets the token blocked for two minutes.
                body: dict = {}
                try:
                    body = resp.json() or {}
                except Exception:  # body may be empty
                    body = {}
                raise RateLimitError(
                    "RATE", "rate limited by Nobitex",
                    {"status": 429,
                     "backoff_s": float(body.get("backOff") or 0.0),
                     "limit": body.get("limit")},
                )
            resp.raise_for_status()
            body = resp.json()
            # HTTP 200 does not mean the exchange did the thing. Every Nobitex
            # endpoint reports business errors with 200 and
            # {"status": "failed", "code": ..., "message": ...}; treating that
            # as success turned "the exchange refused" into "the market is
            # empty" one layer up.
            if isinstance(body, dict) and body.get("status") == "failed":
                raise ClientError(
                    str(body.get("code") or "FAILED"),
                    str(body.get("message") or "Nobitex refused the request"),
                    {"status": resp.status_code, "code": body.get("code")},
                )
            return body
        except httpx.TimeoutException as e:
            self._emit_stats(False, (time.perf_counter() - t0) * 1000)
            raise ClientError("TIMEOUT", "Nobitex timeout", {"error": str(e)}) from e
        except httpx.HTTPStatusError as e:
            raise ClientError("HTTP", f"bad status {e.response.status_code}",
                              {"status": e.response.status_code}) from e
        except Exception as e:
            if isinstance(e, ClientError):
                raise
            self._emit_stats(False, (time.perf_counter() - t0) * 1000)
            raise ClientError("NETWORK", "network failure", {"error": repr(e)}) from e

    async def get(self, path: str, params: dict | None = None, attempts: int | None = None) -> Any:
        """GET with bounded exponential backoff.

        Note: exceptions are handled *here* rather than inside a tenacity
        ``with attempt`` block — catching them there makes tenacity treat the
        attempt as successful and silently disables retries.
        """
        attempts = attempts or 2
        url = path + ("?" + urlencode(params or {}) if params else "")
        last_err: ClientError | None = None

        for attempt in range(1, attempts + 1):
            try:
                return await self._request("GET", url)
            except RateLimitError as e:
                last_err = e
                if attempt < attempts:
                    # Wait what the exchange asked for, not what we feel like.
                    wait = float((e.meta or {}).get("backoff_s") or 0) or 1.2 * attempt
                    await asyncio.sleep(min(60.0, wait))
            except ClientError as e:
                if e.code not in ("NETWORK", "TIMEOUT", "HTTP", "RATE"):
                    # Error text can contain upstream response bodies. Log only
                    # the allowlisted request context and bounded diagnostic fields.
                    code = e.code if re.fullmatch(r"[A-Z0-9_]{1,32}", e.code) else "[invalid]"
                    status = e.meta.get("status")
                    status = status if type(status) is int and 100 <= status <= 599 else "unknown"
                    log.error("client error: code=%s status=%s GET %s",
                              code, status, safe_request_context(url))
                    raise
                last_err = e
                if attempt < attempts:
                    backoff = min(4.0, 0.4 * (2 ** (attempt - 1)))
                    await asyncio.sleep(backoff + random.uniform(0, 0.3))
            except Exception as e:  # normalize unexpected errors
                last_err = ClientError("NETWORK", "network failure", {"error": repr(e)})
                if attempt < attempts:
                    await asyncio.sleep(0.4 * attempt)

        assert last_err is not None
        raise last_err

    # -------------
    # Public API
    # -------------
    def _is_circuit_open(self) -> bool:
        return time.monotonic() < self._circuit_open_until

    def _trip_circuit(self, duration_s: float = 120.0) -> None:
        self._circuit_open_until = time.monotonic() + duration_s
        self.degraded_mode = True

    async def get_market_stats(self) -> dict:
        if not self._is_circuit_open():
            try:
                res = await self.get("/market/stats", attempts=1)
                self.degraded_mode = False
                # Rial markets are quoted in Rial (docs); the app works in
                # Toman. One conversion, here, at the boundary.
                return scale_stats(res)
            except ClientError as e:
                log.warning("Nobitex /market/stats unreachable (%s)", e)
                self._trip_circuit()
                raise UpstreamUnavailable(f"market stats unavailable: {e}", {"cause": str(e)}) from e
        raise UpstreamUnavailable("market stats unavailable: circuit breaker is open")

    async def get_orderbook(self, symbol: str) -> dict:
        if not self._is_circuit_open():
            try:
                res = await self.get(f"/v3/orderbook/{symbol.upper()}", attempts=1)
                self.degraded_mode = False
                return scale_orderbook(res, symbol)
            except ClientError as e:
                log.warning("Nobitex orderbook %s unreachable (%s)", symbol, e)
                self._trip_circuit()
                raise UpstreamUnavailable(f"orderbook {symbol} unavailable: {e}") from e
        raise UpstreamUnavailable(f"orderbook {symbol} unavailable: circuit breaker is open")

    async def get_orderbook_all(self) -> dict:
        if not self._is_circuit_open():
            try:
                return await self.get("/v3/orderbook/all", attempts=1)
            except ClientError as e:
                self._trip_circuit()
                raise UpstreamUnavailable(f"orderbooks unavailable: {e}") from e
        raise UpstreamUnavailable("orderbooks unavailable: circuit breaker is open")

    async def get_udf_history(
        self, symbol: str, resolution: str, from_ts: int, to_ts: int,
        count: int | None = None,
    ) -> dict:
        """OHLC series. ``countback`` is the documented way to ask for the last
        ``count`` candles before ``to`` and takes priority over ``from``, so the
        request does not depend on the server's idea of how far back the range
        may start."""
        params = {"symbol": symbol.upper(), "resolution": resolution, "to": int(to_ts)}
        if count:
            params["countback"] = int(count)
        else:
            params["from"] = int(from_ts)
        res = await self.get("/market/udf/history", params=params, attempts=1)
        return scale_udf(res, symbol)

    async def get_candles(
        self, symbol: str, tf: str = "15m", count: int = 260
    ) -> list[dict]:
        rows, _ = await self.get_candles_ex(symbol, tf, count)
        return rows

    async def get_candles_ex(
        self, symbol: str, tf: str = "15m", count: int = 260
    ) -> tuple[list[dict], str]:
        """Return ``(rows, source)`` where source is ``live`` or ``cache``.

        If neither the exchange nor the cache can answer, raise
        `UpstreamUnavailable`. There is deliberately no third branch: a
        generated candle series looks exactly like a traded one on screen.
        """
        tf_s = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "D": 86400}[tf]
        res = {"15m": "15", "1h": "60", "4h": "240", "D": "D"}.get(tf, str(tf_s // 60 if tf_s >= 60 else 1))
        now = (int(time.time()) // tf_s) * tf_s
        from_ts = now - count * tf_s
        # use cache when present
        cached = CANDLE_CACHE.load(symbol.upper(), res, from_ts, now)
        if cached:
            return cached, "cache"
        until = self._udf_refused.get(symbol.upper(), 0.0)
        if time.monotonic() < until:
            # Already refused recently. Asking again would cost a request and a
            # red line in the dashboard for an answer that has not changed.
            raise UpstreamUnavailable(
                f"candles {symbol} {tf} unavailable: this pair's public history is "
                f"refused (remembered, retrying in {int(until - time.monotonic())}s)",
                {"symbol": symbol, "tf": tf, "refused": True},
            )
        if not self._is_circuit_open():
            try:
                raw = await self.get_udf_history(symbol, res, from_ts, now, count=count)
                if raw and raw.get("s") == "ok" and raw.get("c"):
                    rows = [
                        {"t": raw["t"][i], "o": raw["o"][i], "h": raw["h"][i], "l": raw["l"][i], "c": raw["c"][i], "v": raw["v"][i]}
                        for i in range(len(raw["t"]))
                    ]
                    rows.sort(key=lambda r: r["t"])
                    CANDLE_CACHE.store(symbol.upper(), res, from_ts, now, rows, synthetic=False)
                    self._udf_refused.pop(symbol.upper(), None)
                    self.degraded_mode = False
                    return rows, "live"
            except ClientError as e:
                if is_outage(e):
                    log.warning("Nobitex UDF %s/%s unreachable (%s)", symbol, tf, e)
                    self._trip_circuit()
                else:
                    # Nobitex serves some pairs (USDTIRT, delisted symbols)
                    # without public UDF history. That refusal says nothing
                    # about the other forty symbols — tripping the shared
                    # breaker here cost a whole scan its candles.
                    self._udf_refused[symbol.upper()] = time.monotonic() + UDF_REFUSAL_TTL_S
                    log.info("Nobitex UDF %s/%s refused (%s) — remembered for %.0f min, "
                             "other symbols untouched", symbol, tf, e, UDF_REFUSAL_TTL_S / 60)
                raise UpstreamUnavailable(
                    f"candles {symbol} {tf} unavailable: {e}",
                    {"symbol": symbol, "tf": tf, "refused": not is_outage(e)}) from e

            # The exchange answered — it just has no candles (or rejected the
            # resolution). That is not an outage, and saying "circuit breaker is
            # open" here sent readers looking at the network.
            state = (raw or {}).get("s")
            detail = (raw or {}).get("errmsg") or ""
            raise UpstreamUnavailable(
                f"candles {symbol} {tf} unavailable: {state or 'empty response'}"
                + (f" ({detail})" if detail else ""),
                {"symbol": symbol, "tf": tf, "state": state},
            )
        raise UpstreamUnavailable(f"candles {symbol} {tf} unavailable: circuit breaker is open",
                                  {"symbol": symbol, "tf": tf})

    # ----------------
    # Private shell (Phase 3+, gated)
    # ----------------
    def _auth_headers(self) -> dict:
        """
        ADR-0003/0009: keys are READ-ONLY by policy in this phase — we never
        build signed requests here. This method exists so that when the TRADE
        phase is reached, an injected signer replaces this stub without
        changing call sites.
        """
        raise AuthRequired("AUTH", "TRADE endpoints are intentionally disabled until ADR-0009 promotion gates pass.")


__all__ = [
    "ClientError",
    "ClientStats",
    "NobitexClient",
    "RateLimitError",
    "TokenBucketer",
    "UpstreamUnavailable",
]
