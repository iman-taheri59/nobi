"""
Market price feed for the paper broker.

Honest naming
-------------
This module used to be called "WebSocket executor" while it polled REST every
two seconds. It now does what it says:

* ``transport = "ws"``        — a real WebSocket feed (Centrifugo) is connected
                                and has delivered at least one tick.
* ``transport = "rest-poll"`` — the default. ``GET /v3/orderbook/{symbol}`` on a
                                configurable cadence (``NBX_STREAM_POLL_S``).

There is no pretend mode: ``/api/ws/status`` reports the transport that is
actually in use, together with the last error and the tick count.

WebSocket is opt-in via ``NBX_WS_URL`` (e.g. ``wss://ws.nobitex.ir/connection/websocket``).
If the handshake fails for any reason the executor stays on REST polling and the
failure is visible in the status payload instead of being swallowed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

log = logging.getLogger("market-stream")

WS_URL = os.getenv("NBX_WS_URL", "").strip()
POLL_S = max(1.0, float(os.getenv("NBX_STREAM_POLL_S", "5")))
WS_CHANNEL_PREFIX = os.getenv("NBX_WS_CHANNEL_PREFIX", "market:")


@dataclass
class HotBook:
    best_bid: float = 0.0
    best_ask: float = 0.0
    last_update: float = 0.0
    latency_ms: float = 0.0

    @property
    def mid(self) -> float:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return 0.0

    @property
    def fresh(self) -> bool:
        return self.mid > 0 and (time.time() - self.last_update) < 15.0


class StreamExecutor:
    """Maintains a hot price per subscribed symbol and fires callbacks."""

    def __init__(self) -> None:
        self.books: dict[str, HotBook] = {}
        self._subs: set[str] = set()
        self._tasks: dict[str, asyncio.Task] = {}
        self._client: httpx.AsyncClient | None = None
        self._ws_task: asyncio.Task | None = None
        self._ws_connected = False
        self.nbx_client: Any = None
        self.on_tick: list[Callable[[str, float], Any]] = []
        self.enabled = True
        self.ticks = 0
        self.consecutive_errors = 0
        self.last_error: str | None = None
        self.last_tick_at: float | None = None
        self.ws_messages = 0

    # ------------------------------------------------------------------ #
    # transport
    # ------------------------------------------------------------------ #
    @property
    def transport(self) -> str:
        return "ws" if (WS_URL and self._ws_connected and self.ws_messages > 0) else "rest-poll"

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "transport": self.transport,
            "ws_url": WS_URL or None,
            "ws_connected": self._ws_connected,
            "ws_messages": self.ws_messages,
            "poll_interval_s": POLL_S,
            "subscriptions": sorted(self._subs),
            "ticks": self.ticks,
            "consecutive_errors": self.consecutive_errors,
            "last_error": self.last_error,
            "last_tick_at": self.last_tick_at,
        }

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=os.getenv("NBX_BASE_URL", "https://apiv2.nobitex.ir").rstrip("/"),
                timeout=8,
                headers={"User-Agent": "TraderBot/1.0"},
            )
        return self._client

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    async def subscribe(self, symbol: str, on_tick: Callable[[str, float], Any] | None = None) -> None:
        symbol = symbol.upper()
        if on_tick and on_tick not in self.on_tick:
            self.on_tick.append(on_tick)
        if symbol in self._subs:
            return
        self._subs.add(symbol)
        self.books.setdefault(symbol, HotBook())
        log.info("stream subscribe %s (transport=%s)", symbol, self.transport)
        if not self._tasks.get(symbol):
            self._tasks[symbol] = asyncio.create_task(self._poll_loop(symbol))
        if WS_URL and not self._ws_task:
            self._ws_task = asyncio.create_task(self._ws_loop())

    async def unsubscribe(self, symbol: str) -> None:
        symbol = symbol.upper()
        self._subs.discard(symbol)
        task = self._tasks.pop(symbol, None)
        if task:
            task.cancel()

    async def close(self) -> None:
        for sym in list(self._tasks):
            await self.unsubscribe(sym)
        if self._ws_task:
            self._ws_task.cancel()
            self._ws_task = None
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # REST polling (always available)
    # ------------------------------------------------------------------ #
    async def _fetch_book(self, symbol: str) -> dict:
        if self.nbx_client is not None:
            return await self.nbx_client.get_orderbook(symbol)
        c = await self._ensure_client()
        r = await c.get(f"/v3/orderbook/{symbol}")
        r.raise_for_status()
        return r.json()

    async def _poll_loop(self, symbol: str) -> None:
        # If a real socket is delivering, do not also hammer REST for the same
        # symbol: check the book freshness first and stay quiet.
        while True:
            try:
                if self.transport == "ws" and self.books.get(symbol, HotBook()).fresh:
                    await asyncio.sleep(POLL_S)
                    continue
                t0 = time.perf_counter()
                data = await self._fetch_book(symbol)
                latency = (time.perf_counter() - t0) * 1000
                book = self.books.setdefault(symbol, HotBook())
                bids = data.get("bids") or []
                asks = data.get("asks") or []
                if bids:
                    book.best_bid = float(bids[0][0])
                if asks:
                    book.best_ask = float(asks[0][0])
                if not bids and not asks and data.get("lastTradePrice"):
                    px = float(data["lastTradePrice"])
                    book.best_bid = book.best_ask = px
                book.last_update = time.time()
                book.latency_ms = latency
                self.consecutive_errors = 0
                self.last_error = None
                if book.mid > 0:
                    self._emit(symbol, book.mid)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - the loop must survive
                self.consecutive_errors += 1
                self.last_error = f"{type(e).__name__}: {e}"
                log.debug("poll error %s: %s", symbol, e)
            await asyncio.sleep(POLL_S)

    # ------------------------------------------------------------------ #
    # WebSocket (opt-in, Centrifugo protocol)
    # ------------------------------------------------------------------ #
    async def _ws_loop(self) -> None:
        try:
            import websockets  # type: ignore
        except Exception as e:  # noqa: BLE001
            self.last_error = f"websockets unavailable: {e}"
            log.warning("WS requested but library missing: %s", e)
            return

        backoff = 1.0
        while self._subs:
            try:
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(json.dumps({"id": 1, "connect": {"name": "nobi"}}))
                    hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    if "connect" not in hello:
                        raise RuntimeError(f"unexpected handshake: {hello}")
                    self._ws_connected = True
                    self.last_error = None
                    backoff = 1.0
                    subs = sorted(self._subs)
                    for i, sym in enumerate(subs, start=2):
                        await ws.send(json.dumps({
                            "id": i,
                            "subscribe": {"channel": f"{WS_CHANNEL_PREFIX}{sym}"},
                        }))
                    log.info("WS connected (%s), %d subscriptions", WS_URL, len(subs))
                    async for raw in ws:
                        self._on_ws_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - reconnect forever
                self._ws_connected = False
                self.last_error = f"ws: {type(e).__name__}: {e}"
                log.warning("WS error (%s), reconnecting in %.0fs", self.last_error, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        self._ws_connected = False

    def _on_ws_message(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            return
        push = (msg or {}).get("push") or {}
        channel = str(push.get("channel") or "")
        symbol = channel.split(WS_CHANNEL_PREFIX, 1)[-1].upper()
        data = (push.get("pub") or {}).get("data") or {}
        if not symbol or not isinstance(data, dict):
            return
        book = self.books.setdefault(symbol, HotBook())
        bid = data.get("bestBid") or data.get("bid") or data.get("b")
        ask = data.get("bestAsk") or data.get("ask") or data.get("a")
        last = data.get("lastTradePrice") or data.get("last")
        try:
            if bid:
                book.best_bid = float(bid)
            if ask:
                book.best_ask = float(ask)
            elif last and not book.best_ask:
                book.best_bid = book.best_ask = float(last)
        except (TypeError, ValueError):
            return
        if book.mid <= 0:
            return
        book.last_update = time.time()
        self.ws_messages += 1
        self._emit(symbol, book.mid)

    # ------------------------------------------------------------------ #
    def _emit(self, symbol: str, mid: float) -> None:
        self.ticks += 1
        self.last_tick_at = time.time()
        for fn in list(self.on_tick):
            try:
                result = fn(symbol, mid)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:  # noqa: BLE001 - a bad callback must not stop the feed
                pass

    def mid(self, symbol: str) -> float:
        return self.books.get(symbol.upper(), HotBook()).mid


STREAM_EXECUTOR = StreamExecutor()


async def streams_start(broker, client) -> None:
    """Subscribe every open position so its `last_price` tracks the market."""
    STREAM_EXECUTOR.nbx_client = client
    for pos in (getattr(broker, "positions", {}) or {}).values():
        if getattr(pos, "state", "OPEN") == "CLOSED":
            continue
        symbol = pos.symbol if hasattr(pos, "symbol") else pos.get("symbol")

        def tick(sym, mid, _pos=pos):
            # Synchronous by contract: `_emit` does not await callbacks.
            try:
                if isinstance(_pos, dict):
                    _pos["last_price"] = mid
                else:
                    _pos.last_price = mid
            except Exception:
                pass

        await STREAM_EXECUTOR.subscribe(symbol, tick)


__all__ = ["STREAM_EXECUTOR", "StreamExecutor", "streams_start", "HotBook"]
