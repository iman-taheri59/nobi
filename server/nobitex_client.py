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
import hashlib
import logging
import math
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
from tenacity import (AsyncRetrying, retry_if_exception_type,
                      stop_after_attempt, wait_exponential, wait_random)


class ClientError(Exception):
    """Typed error chain for the adapter."""

    def __init__(self, code: str, message: str, meta: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.meta = code, self, {}
        self.message = message  # type: ignore[assignment]
        self.meta = meta or {}


class RateLimitError(ClientError):
    pass


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

    SEED_MARKETS: dict[str, tuple[float, float, float]] = {
        "BTCIRT": (4985000000.0, 950000000000.0, 2.4),
        "ETHIRT": (229500000.0, 410000000000.0, 3.1),
        "USDTIRT": (68250.0, 1250000000000.0, 0.4),
        "SOLIRT": (11850000.0, 185000000000.0, 5.2),
        "TONIRT": (385000.0, 142000000000.0, 1.8),
        "TRXIRT": (16200.0, 98000000000.0, -0.7),
        "DOGEIRT": (14800.0, 88000000000.0, 4.3),
        "XRPIRT": (152000.0, 76000000000.0, 1.9),
        "ADAIRT": (48500.0, 54000000000.0, -1.2),
        "AVAXIRT": (2150000.0, 42000000000.0, 2.8),
        "LINKIRT": (1240000.0, 35000000000.0, 3.5),
        "DOTIRT": (445000.0, 28000000000.0, -0.5),
        "LTCIRT": (6450000.0, 25000000000.0, 1.1),
        "SHIBIRT": (1.42, 22000000000.0, 6.0),
        "MATICIRT": (31500.0, 19000000000.0, -1.8),
        "BTCUSDT": (73040.0, 48000000.0, 2.3),
        "ETHUSDT": (3362.0, 29000000.0, 3.0),
        "SOLUSDT": (173.6, 16500000.0, 5.1),
        "TONUSDT": (5.64, 9800000.0, 1.7),
        "TRXUSDT": (0.237, 7400000.0, -0.8),
        "DOGEUSDT": (0.217, 6900000.0, 4.2),
        "XRPUSDT": (2.23, 6100000.0, 1.8),
        "ADAUSDT": (0.71, 4300000.0, -1.1),
        "AVAXUSDT": (31.5, 3500000.0, 2.7),
        "LINKUSDT": (18.17, 2900000.0, 3.4),
    }

    def __init__(
        self,
        base_url: str | None = None,
        timeout_s: float = 10.0,
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

    async def get(self, path: str, params: dict | None = None) -> Any:
        attempts = 3
        last_err: ClientError | None = None
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential(min=0.4, max=4.0) + wait_random(0, 0.3),
            retry=retry_if_exception_type((httpx.TimeoutException, ClientError)),
            reraise=True,
        ):
            with attempt:
                try:
                    return await self._request("GET", path + ("?" + urlencode(params or {}) if params else ""))
                except RateLimitError as e:
                    last_err = e
                    await asyncio.sleep(1.2 * (attempt.retry_state.attempt_number or 1))
                except ClientError as e:
                    if e.code in ("NETWORK", "TIMEOUT", "HTTP", "RATE"):
                        last_err = e
                        continue
                    log.error("client error: %s", e)
                    raise
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

    def _synth_market_stats(self) -> dict:
        stats_map = {}
        for sym, (price, vol, chg) in self.SEED_MARKETS.items():
            stats_map[sym.lower()] = {
                "latest": str(price),
                "last": str(price),
                "valueDst": str(vol),
                "dayChange": str(chg),
            }
        return {"status": "ok", "stats": stats_map, "simulated": True}

    def _synth_orderbook(self, symbol: str) -> dict:
        sym = symbol.upper()
        base_px = self.SEED_MARKETS.get(sym, (100000.0, 1e9, 0.0))[0]
        bids = []
        asks = []
        for lvl in range(1, 12):
            step = 0.0006 * lvl
            bp = round(base_px * (1.0 - step), 4 if base_px < 1000 else 0)
            ap = round(base_px * (1.0 + step), 4 if base_px < 1000 else 0)
            qty = round(15.0 / (lvl * 0.7), 4)
            bids.append([str(bp), str(qty)])
            asks.append([str(ap), str(qty)])
        return {
            "status": "ok",
            "lastUpdate": int(time.time() * 1000),
            "lastTradePrice": str(base_px),
            "bids": bids,
            "asks": asks,
            "simulated": True,
        }

    def _synth_candles(self, symbol: str, tf: str, count: int) -> list[dict]:
        sym = symbol.upper()
        tf_s = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "D": 86400}.get(tf, 900)
        target_px, base_vol, chg24 = self.SEED_MARKETS.get(sym, (100000.0, 5e8, 1.0))
        seed_int = int(hashlib.md5(f"{sym}:{tf}".encode()).hexdigest()[:8], 16)
        rng = random.Random(seed_int)
        now = (int(time.time()) // tf_s) * tf_s
        # Build forward price path with realistic trend regimes & pullbacks, then scale to target_px
        raw_closes = [100.0]
        vols = []
        bias = 0.0014 if chg24 >= 0 else -0.0004
        for i in range(1, count):
            cycle = math.sin(i / 21.0 + (seed_int % 5)) * 0.0085 + math.cos(i / 11.0) * 0.0045
            shock = rng.gauss(bias + cycle, 0.0085)
            nxt = max(5.0, raw_closes[-1] * (1.0 + shock))
            raw_closes.append(nxt)
            vol_mult = 1.9 if (cycle > 0.002 and shock > 0.003) else rng.uniform(0.6, 1.1)
            vols.append(max(1.0, (base_vol / max(target_px, 1e-6) / 96.0) * vol_mult))
        vols.append(vols[-1] if vols else 100.0)
        scale = target_px / raw_closes[-1]
        rows = []
        for i in range(count):
            t_i = now - (count - 1 - i) * tf_s
            c_i = raw_closes[i] * scale
            o_i = (raw_closes[i - 1] * scale) if i > 0 else c_i * 0.994
            wick_up = abs(rng.gauss(0, 0.005)) + 0.002
            wick_dn = abs(rng.gauss(0, 0.004)) + 0.0015
            h_i = max(o_i, c_i) * (1.0 + wick_up)
            l_i = min(o_i, c_i) * (1.0 - wick_dn)
            prec = 4 if target_px < 100 else (2 if target_px < 10000 else 0)
            rows.append({
                "t": t_i,
                "o": round(o_i, prec),
                "h": round(h_i, prec),
                "l": round(l_i, prec),
                "c": round(c_i, prec),
                "v": round(vols[i], 2),
            })
        return rows

    async def get_market_stats(self) -> dict:
        if not self._is_circuit_open():
            try:
                res = await self.get("/market/stats")
                self.degraded_mode = False
                return res
            except ClientError as e:
                log.warning("Nobitex /market/stats unreachable (%s), activating circuit-breaker fallback", e)
                self._trip_circuit()
        return self._synth_market_stats()

    async def get_orderbook(self, symbol: str) -> dict:
        if not self._is_circuit_open():
            try:
                res = await self.get(f"/v3/orderbook/{symbol.upper()}")
                self.degraded_mode = False
                return res
            except ClientError as e:
                log.warning("Nobitex orderbook %s unreachable (%s), activating circuit-breaker fallback", symbol, e)
                self._trip_circuit()
        return self._synth_orderbook(symbol)

    async def get_orderbook_all(self) -> dict:
        if not self._is_circuit_open():
            try:
                return await self.get("/v3/orderbook/all")
            except ClientError:
                self._trip_circuit()
        return {sym: self._synth_orderbook(sym) for sym in self.SEED_MARKETS}

    async def get_udf_history(
        self, symbol: str, resolution: str, from_ts: int, to_ts: int
    ) -> dict:
        return await self.get(
            "/market/udf/history",
            params={"symbol": symbol.upper(), "resolution": resolution, "from": from_ts, "to": to_ts},
        )

    async def get_candles(
        self, symbol: str, tf: str = "15m", count: int = 260
    ) -> list[dict]:
        tf_s = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "D": 86400}[tf]
        res = {"15m": "15", "1h": "60", "4h": "240", "D": "D"}.get(tf, str(tf_s // 60 if tf_s >= 60 else 1))
        now = (int(time.time()) // tf_s) * tf_s
        from_ts = now - count * tf_s
        # use cache when present
        cached = CANDLE_CACHE.load(symbol.upper(), res, from_ts, now)
        if cached:
            return cached
        if not self._is_circuit_open():
            try:
                raw = await self.get_udf_history(symbol, res, from_ts, now)
                if raw and raw.get("s") == "ok" and raw.get("c"):
                    rows = [
                        {"t": raw["t"][i], "o": raw["o"][i], "h": raw["h"][i], "l": raw["l"][i], "c": raw["c"][i], "v": raw["v"][i]}
                        for i in range(len(raw["t"]))
                    ]
                    rows.sort(key=lambda r: r["t"])
                    CANDLE_CACHE.store(symbol.upper(), res, from_ts, now, rows)
                    self.degraded_mode = False
                    return rows
            except ClientError as e:
                log.warning("Nobitex UDF %s/%s unreachable (%s), using fallback candles", symbol, tf, e)
                self._trip_circuit()
        rows = self._synth_candles(symbol, tf, count)
        CANDLE_CACHE.store(symbol.upper(), res, from_ts, now, rows)
        return rows

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


__all__ = ["NobitexClient", "ClientError", "RateLimitError", "ClientStats", "TokenBucketer"]
