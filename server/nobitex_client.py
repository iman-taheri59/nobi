"""
Nobitex API client (ADR-0003) — async, resiliency-first.

Public endpoints used in Phase 1:
  GET /market/stats
  GET /v3/orderbook/{SYMBOL} (and symbol=all)
  GET /market/udf/history?symbol=...&resolution=...&from=...&to=...

Private endpoints (Phase 3+) — wired but gated:
  Auth via Ed25519 API key (headers Nobitex-Key/-Signature/-Timestamp)
  POST /market/orders/add  etc. — NEVER enabled in this repo state.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from collections.abc import Callable

from candle_cache import CANDLE_CACHE

log = logging.getLogger("nbx-client")
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx


class ClientError(Exception):
    """Typed error chain for the adapter."""

    def __init__(self, code: str, message: str, meta: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.meta = meta or {}


class RateLimitError(ClientError):
    pass


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
    UA = "TraderBot/1.0 (+https://github.com/your-repo)"

    #: Symbols the dashboard offers in its pickers. Names only — no price,
    #: volume or change is stored here, because a stale hard-coded number is
    #: indistinguishable from a live one once it reaches the screen.
    SYMBOLS: tuple[str, ...] = (
        "BTCIRT", "ETHIRT", "USDTIRT", "SOLIRT", "TONIRT", "TRXIRT", "DOGEIRT",
        "XRPIRT", "ADAIRT", "AVAXIRT", "LINKIRT", "DOTIRT", "LTCIRT", "SHIBIRT",
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "TONUSDT", "TRXUSDT", "DOGEUSDT",
        "XRPUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
    )

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
        self.degraded_mode: bool = False

    async def __aenter__(self) -> "NobitexClient":
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
                headers={"User-Agent": self.UA},
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
        return {"User-Agent": self.UA}

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
                raise ClientError("AUTH", f"Nobitex rejected request with {resp.status_code} — check endpoint visibility (public vs private)", {"status": resp.status_code})
            if resp.status_code == 429:
                raise RateLimitError("RATE", "rate limited by Nobitex", {"status": 429})
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException as e:
            self._emit_stats(False, (time.perf_counter() - t0) * 1000)
            raise ClientError("TIMEOUT", "Nobitex timeout", {"error": str(e)})
        except httpx.HTTPStatusError as e:
            raise ClientError("HTTP", f"bad status {e.response.status_code}", {"status": e.response.status_code})
        except Exception as e:
            if isinstance(e, ClientError):
                raise
            self._emit_stats(False, (time.perf_counter() - t0) * 1000)
            raise ClientError("NETWORK", "network failure", {"error": repr(e)})

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
                    await asyncio.sleep(1.2 * attempt)
            except ClientError as e:
                if e.code not in ("NETWORK", "TIMEOUT", "HTTP", "RATE"):
                    log.error("client error: %s", e)
                    raise
                last_err = e
                if attempt < attempts:
                    backoff = min(4.0, 0.4 * (2 ** (attempt - 1)))
                    await asyncio.sleep(backoff + random.uniform(0, 0.3))
            except Exception as e:  # noqa: BLE001 - normalize unexpected errors
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
                return res
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
                return res
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
        self, symbol: str, resolution: str, from_ts: int, to_ts: int
    ) -> dict:
        return await self.get(
            "/market/udf/history",
            params={"symbol": symbol.upper(), "resolution": resolution, "from": from_ts, "to": to_ts},
            attempts=1,
        )

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
        if not self._is_circuit_open():
            try:
                raw = await self.get_udf_history(symbol, res, from_ts, now)
                if raw and raw.get("s") == "ok" and raw.get("c"):
                    rows = [
                        {"t": raw["t"][i], "o": raw["o"][i], "h": raw["h"][i], "l": raw["l"][i], "c": raw["c"][i], "v": raw["v"][i]}
                        for i in range(len(raw["t"]))
                    ]
                    rows.sort(key=lambda r: r["t"])
                    CANDLE_CACHE.store(symbol.upper(), res, from_ts, now, rows, synthetic=False)
                    self.degraded_mode = False
                    return rows, "live"
            except ClientError as e:
                log.warning("Nobitex UDF %s/%s unreachable (%s)", symbol, tf, e)
                self._trip_circuit()
                raise UpstreamUnavailable(
                    f"candles {symbol} {tf} unavailable: {e}", {"symbol": symbol, "tf": tf}) from e
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


__all__ = ["NobitexClient", "ClientError", "UpstreamUnavailable", "RateLimitError",
           "ClientStats", "TokenBucketer"]
