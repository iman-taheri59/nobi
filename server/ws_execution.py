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
import contextlib
import json
import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from nobitex_client import resolve_ua
from units import market_symbol, price_scale, scale_stat_row

log = logging.getLogger("market-stream")

WS_URL = os.getenv("NBX_WS_URL", "").strip()
POLL_S = max(1.0, float(os.getenv("NBX_STREAM_POLL_S", "5")))
#: Public order-book channels are `public:orderbook-BTCIRT` (docs §websocket).
#: This used to default to `market:`, a prefix the exchange never publishes, so
#: an opted-in WebSocket could connect and then receive nothing forever.
WS_CHANNEL_PREFIX = os.getenv("NBX_WS_CHANNEL_PREFIX", "public:orderbook-")

#: One channel carries every market's 24h statistics (docs §websocket, "کانال
#: وضعیت تمامی بازارها"). Subscribing to it once beats N per-symbol channels and
#: is what keeps the *closed* flag and the last price current between REST polls.
WS_STATS_CHANNEL = "public:market-stats-all"
WS_STATS_PREFIX = "public:market-stats-"
WS_CANDLE_PREFIX = "public:candle-"

#: `15m` → `15`: the candle channel takes the resolution, not the app's label
#: (docs list 1/5/15/30/60/180/240/360/720/D/2D/3D).
TF_RESOLUTION = {"1m": "1", "5m": "5", "15m": "15", "30m": "30",
                 "1h": "60", "3h": "180", "4h": "240", "6h": "360",
                 "12h": "720", "D": "D", "2D": "2D", "3D": "3D"}
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
              "3h": 10800, "4h": 14400, "6h": 21600, "12h": 43200,
              "D": 86400, "2D": 172800, "3D": 259200}


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


@dataclass
class MarketStat:
    """One row of `public:market-stats-*`, already in the app's Toman scale."""
    latest: float = 0.0
    mark: float = 0.0
    day_change: float = 0.0
    best_bid: float = 0.0
    best_ask: float = 0.0
    volume_dst: float = 0.0
    closed: bool = False
    updated: float = 0.0

    @property
    def fresh(self) -> bool:
        return self.updated > 0 and (time.time() - self.updated) < 120.0


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
        #: (symbol, callback) pairs. The symbol is part of the registration on
        #: purpose: `streams_start` registers one callback per open position, and
        #: a flat list delivered every price to every position — a BTCIRT tick
        #: overwrote the `last_price` of an ETHIRT position, which marked a
        #: 0.3 ETH holding at 8.97 billion and multiplied equity by ~6 orders of
        #: magnitude. A callback registered with `None` still sees every tick.
        self.on_tick: list[tuple[str | None, Callable[[str, float], Any]]] = []
        self.enabled = True
        self.ticks = 0
        self.consecutive_errors = 0
        self.last_error: str | None = None
        self.last_tick_at: float | None = None
        self.ws_messages = 0
        #: Market-wide statistics from `public:market-stats-all`, keyed by the
        #: app's symbol (`BTCIRT`), not the channel's (`btc-irt`).
        self.markets: dict[str, MarketStat] = {}
        self.on_market: list[Callable[[str, MarketStat], Any]] = []
        self.stats_frames = 0
        self.last_stats_at: float | None = None
        #: Last candle seen per (symbol, timeframe). Used to make a REST series
        #: end where the market is, instead of where the last poll left it.
        self.candles: dict[tuple[str, str], dict] = {}
        self.candle_frames = 0
        self.candle_price_rejections = 0
        self.last_candle_rejection: dict[str, Any] | None = None
        #: Subscribe to the market-wide channels even with no position open.
        self.want_stats = False
        self.pongs = 0
        self.pong_echoes = 0
        self._last_pong_at = 0.0
        #: The live socket and the channels already asked for on it. A position
        #: opened after the handshake used to be invisible to the feed: the
        #: `subscribe` frames were only ever sent at connect time, so the new
        #: symbol was "subscribed" in memory and never on the wire.
        self._ws_conn: Any = None
        #: asyncio holds only a weak reference to a task; without this set a
        #: fire-and-forget task can be collected mid-flight.
        self._bg_tasks: set[asyncio.Task] = set()
        self._sent_channels: set[str] = set()
        self._next_sub_id = 2
        self.candle_tf = os.getenv("NBX_WS_CANDLE_TF", "15m")

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
            # A socket that connects and then subscribes to nothing is not a
            # feed. `channels` is what was actually sent on the wire, not what
            # the code would like to send: with no socket configured the two
            # lists differ, and printing the wish-list looked like a feed.
            "channels": sorted(self._sent_channels) if self._ws_connected else [],
            "channels_wanted": self.ws_channels(),
            "stats_frames": self.stats_frames,
            "last_stats_at": self.last_stats_at,
            "markets": len(self.markets),
            "candle_frames": self.candle_frames,
            "candle_price_rejections": self.candle_price_rejections,
            "last_candle_rejection": self.last_candle_rejection,
            "candle_source_unit": os.getenv("NBX_WS_CANDLE_PRICE_UNIT", "toman"),
            "candles": len(self.candles),
            "pongs": self.pongs,
            "pong_echoes": self.pong_echoes,
        }

    def ws_channels(self) -> list[str]:
        """Channel names the socket asked for (stats first, then candles/books)."""
        out = []
        if self.want_stats:
            out.append(WS_STATS_CHANNEL)
        for sym in sorted(self._subs):
            res = TF_RESOLUTION.get(self.candle_tf)
            if res:
                out.append(f"{WS_CANDLE_PREFIX}{sym}-{res}")
            out.append(f"{WS_CHANNEL_PREFIX}{sym}")
        return out

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=os.getenv("NBX_BASE_URL", "https://apiv2.nobitex.ir").rstrip("/"),
                timeout=8,
                headers={"User-Agent": resolve_ua()},
            )
        return self._client

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    async def subscribe(self, symbol: str, on_tick: Callable[[str, float], Any] | None = None) -> None:
        symbol = symbol.upper()
        if on_tick and (symbol, on_tick) not in self.on_tick:
            self.on_tick.append((symbol, on_tick))
        if symbol in self._subs:
            return
        self._subs.add(symbol)
        self.books.setdefault(symbol, HotBook())
        log.info("stream subscribe %s (transport=%s)", symbol, self.transport)
        if not self._tasks.get(symbol):
            self._tasks[symbol] = asyncio.create_task(self._poll_loop(symbol))
        self.ensure_ws()
        if self._ws_conn is not None:
            self._spawn(self._send_missing_subs())

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    def ensure_ws(self) -> None:
        """Open the socket when it is configured and not already running.

        Called by `subscribe()` and by the lifespan startup, because the
        market-wide statistics channel is worth having even with no position
        open — that is the channel that keeps `isClosed` and the last price
        current for the whole board.
        """
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
            except Exception as e:  # the loop must survive
                self.consecutive_errors += 1
                self.last_error = f"{type(e).__name__}: {e}"
                log.debug("poll error %s: %s", symbol, e)
            await asyncio.sleep(POLL_S)

    # ------------------------------------------------------------------ #
    # WebSocket (opt-in, Centrifugo protocol)
    # ------------------------------------------------------------------ #
    async def _ws_loop(self) -> None:
        try:
            import websockets
        except Exception as e:
            self.last_error = f"websockets unavailable: {e}"
            log.warning("WS requested but library missing: %s", e)
            return

        backoff = 1.0
        while self._subs or self.want_stats:
            try:
                async with websockets.connect(
                    WS_URL, ping_interval=20, ping_timeout=20,
                    additional_headers={"User-Agent": resolve_ua()},
                ) as ws:
                    # Anonymous connection: the protocol still wants a
                    # `connect` frame. `name` is not a field the server reads.
                    await ws.send(json.dumps({"id": 1, "connect": {}}))
                    hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    if "connect" not in hello:
                        raise RuntimeError(f"unexpected handshake: {hello}")
                    self._ws_connected = True
                    self._ws_conn = ws
                    self._sent_channels = set()
                    self.last_error = None
                    backoff = 1.0
                    subs = self.ws_channels()
                    for channel in subs:
                        await self._send_subscribe(ws, channel)
                    log.info("WS connected (%s), %d subscriptions", WS_URL, len(subs))
                    async for raw in ws:
                        # Centrifugo's application-level keep-alive is an empty
                        # object, not a protocol ping: the docs are explicit
                        # that the client must answer `{}` within 25 s or be
                        # disconnected. `websockets`' own ping is a different
                        # layer, so a socket that ignored this died and
                        # reconnected every 25 seconds, forever.
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", "ignore")
                        if await self._handle_keepalive(ws, raw):
                            continue
                        self._on_ws_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # reconnect forever
                self._ws_connected = False
                self._ws_conn = None
                self.last_error = f"ws: {type(e).__name__}: {e}"
                log.warning("WS error (%s), reconnecting in %.0fs", self.last_error, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        self._ws_connected = False
        self._ws_conn = None

    @staticmethod
    def _decode(data):
        """`pub.data` is a JSON string in the raw protocol and a dict via an SDK."""
        if isinstance(data, (str, bytes)):
            try:
                return json.loads(data)
            except Exception:
                return None
        return data

    async def _handle_keepalive(self, ws, raw: str) -> bool:
        """Answer Centrifugo's application-level ping with the same empty object.

        Returns True when the frame was a keep-alive and has been handled. The
        answer is rate-limited to one per second: a peer that echoes the pong
        back would otherwise ping-pong forever, which is exactly what happened
        the first time this was wired against a stand-in server.
        """
        if not isinstance(raw, str) or raw.strip() != "{}":
            return False
        now = time.time()
        if now - self._last_pong_at >= 1.0:
            self._last_pong_at = now
            await ws.send("{}")
            self.pongs += 1
        else:
            self.pong_echoes += 1
        return True

    async def _send_subscribe(self, ws, channel: str) -> None:
        await ws.send(json.dumps({"id": self._next_sub_id,
                                  "subscribe": {"channel": channel}}))
        self._next_sub_id += 1
        self._sent_channels.add(channel)

    async def _send_missing_subs(self) -> None:
        """Subscribe channels that appeared after the handshake."""
        ws = self._ws_conn
        if ws is None:
            return
        for channel in self.ws_channels():
            if channel in self._sent_channels:
                continue
            try:
                await self._send_subscribe(ws, channel)
                log.info("WS subscribed late: %s", channel)
            except Exception as e:  # reconnect will resend
                log.debug("late subscribe failed (%s): %s", channel, e)
                return

    def _on_ws_message(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            return
        push = (msg or {}).get("push") or {}
        if not push:
            return
        channel = str(push.get("channel") or "")
        data = self._decode((push.get("pub") or {}).get("data"))
        if not isinstance(data, dict):
            return
        if channel.startswith(WS_STATS_PREFIX):
            self._on_stats_frame(channel, data)
        elif channel.startswith(WS_CANDLE_PREFIX):
            self._on_candle_frame(channel, data)
        elif channel.startswith(WS_CHANNEL_PREFIX):
            self._on_orderbook_frame(channel, data)

    # ------------------------------------------------------------------ #
    # market-wide statistics: `public:market-stats-all`
    # ------------------------------------------------------------------ #
    def _on_stats_frame(self, channel: str, data: dict) -> None:
        """One channel, two shapes: all markets keyed by `btc-irt`, or one row."""
        if channel == WS_STATS_CHANNEL:
            rows = {k: v for k, v in data.items() if isinstance(v, dict)}
        else:
            sym = market_symbol(channel.split(WS_STATS_PREFIX, 1)[-1])
            rows = {sym: data} if sym else {}
        now = time.time()
        touched = False
        for key, row in rows.items():
            sym = market_symbol(key)
            if not sym:
                continue                       # deprecated `global` block
            # Same conversion as the REST route (`scale_stats`): Rial → Toman
            # for `-rls`/`-irt` markets. A private copy of this rule would have
            # quoted the whole board ten times too high.
            r = scale_stat_row(sym, row)
            st = self.markets.get(sym) or MarketStat()
            try:
                if r.get("latest") is not None:
                    st.latest = float(r["latest"])
                if r.get("mark") is not None:
                    st.mark = float(r["mark"])
                if r.get("dayChange") is not None:
                    st.day_change = float(r["dayChange"])
                if r.get("bestBuy") is not None:
                    st.best_bid = float(r["bestBuy"])
                if r.get("bestSell") is not None:
                    st.best_ask = float(r["bestSell"])
                if r.get("volumeDst") is not None:
                    st.volume_dst = float(r["volumeDst"])
            except (TypeError, ValueError):
                continue
            if "isClosed" in r:
                st.closed = bool(r["isClosed"])
            st.updated = now
            self.markets[sym] = st
            touched = True
            for fn in list(self.on_market):
                with contextlib.suppress(Exception):
                    fn(sym, st)
        if touched:
            self.stats_frames += 1
            self.last_stats_at = now
            self.ws_messages += 1

    # ------------------------------------------------------------------ #
    # candles: `public:candle-{SYMBOL}-{resolution}`
    # ------------------------------------------------------------------ #
    def _on_candle_frame(self, channel: str, data: dict) -> None:
        tail = channel.split(WS_CANDLE_PREFIX, 1)[-1]
        sym, _, res = tail.rpartition("-")
        if not sym or not res:
            return
        tf = next((k for k, v in TF_RESOLUTION.items() if v == res.upper()), None)
        if tf is None:
            return
        try:
            row = {"t": int(data["t"]), "o": float(data["o"]), "h": float(data["h"]),
                   "l": float(data["l"]), "c": float(data["c"]), "v": float(data.get("v") or 0)}
        except (KeyError, TypeError, ValueError, OverflowError):
            return
        # Public same-bar capture on 2026-10-28 shows DOGEIRT WS OHLCV
        # exactly matches Toman REST UDF; BTCUSDT stays USDT. Do not apply
        # the Rial orderbook scale to candles. Keep an explicit alternate-source
        # override, never infer/rescale from magnitude; merge guards REST.
        unit = os.getenv("NBX_WS_CANDLE_PRICE_UNIT", "toman").strip().lower()
        if unit not in ("rial", "toman"):
            self._reject_candle(sym, tf, "invalid-source-unit")
            return
        k = price_scale(unit) if sym.upper().endswith("IRT") else 1.0
        row.update({f: row[f] * k for f in ("o", "h", "l", "c")})
        if (row["t"] <= 0 or any(not math.isfinite(row[f]) or row[f] <= 0
                                 for f in ("o", "h", "l", "c"))
                or not math.isfinite(row["v"]) or row["v"] < 0
                or row["l"] > min(row["o"], row["c"])
                or row["h"] < max(row["o"], row["c"])):
            self._reject_candle(sym, tf, "invalid-ohlcv")
            return
        self.candles[(sym.upper(), tf)] = {**row, "_at": time.time()}
        self.candle_frames += 1
        self.ws_messages += 1

    def _reject_candle(self, symbol: str, tf: str, reason: str,
                       ratio: float | None = None) -> None:
        self.candle_price_rejections += 1
        self.last_candle_rejection = {"symbol": symbol.upper(), "tf": tf,
                                      "reason": reason,
                                      "ratio": ratio if ratio is not None and math.isfinite(ratio) else None,
                                      "at": time.time()}

    def merge_last_candle(self, symbol: str, tf: str, rows: list[dict]) -> list[dict]:
        """Let a series end on the candle the socket just published.

        Used only to *refresh* a REST series: the socket frame replaces the last
        row when it is the same candle, appends when it is a newer one, and is
        ignored when it is older than what REST already returned. A frame older
        than two candles is treated as stale and ignored entirely.
        """
        c = self.candles.get((symbol.upper(), tf))
        if not c or not rows:
            return rows
        ttl = TF_SECONDS.get(tf, 900) * 2
        if time.time() - c.get("_at", 0) > ttl:
            return rows
        row = {k: c[k] for k in ("t", "o", "h", "l", "c", "v")}
        last_t = int(rows[-1].get("t") or 0)
        if row["t"] < last_t:
            return rows
        try:
            reference = float(rows[-1].get("c") or 0)
        except (TypeError, ValueError, OverflowError):
            reference = 0.0
        ratio = row["c"] / reference if math.isfinite(reference) and reference > 0 else None
        consistent = (ratio is not None and all(0.2 < row[field] / reference < 5.0
                                               for field in ("o", "h", "l", "c")))
        # A gross jump is not a unit inference. Keep the REST series unchanged,
        # make the rejection visible and require investigation instead of guessing
        # a factor of ten. This is deliberately not a per-trade risk tolerance.
        if not consistent:
            self._reject_candle(symbol, tf, "rest-ws-price-mismatch", ratio)
            return rows
        if row["t"] == last_t:
            return [*rows[:-1], {**rows[-1], **{k: row[k] for k in ("o", "h", "l", "c", "v")}}]
        return [*rows, row]

    # ------------------------------------------------------------------ #
    # order book: `public:orderbook-{SYMBOL}`
    # ------------------------------------------------------------------ #
    def _on_orderbook_frame(self, channel: str, data: dict) -> None:
        symbol = channel.split(WS_CHANNEL_PREFIX, 1)[-1].upper()
        if not symbol:
            return
        book = self.books.setdefault(symbol, HotBook())
        # The documented payload is a full L2 snapshot: arrays of
        # [price, amount] strings, Rial-quoted for IRT markets.
        bids, asks = data.get("bids") or [], data.get("asks") or []
        scale = price_scale() if symbol.endswith("IRT") else 1.0

        def _top(rows, want_max):
            best = None
            for row in rows:
                try:
                    p = float(row[0]) * scale
                except (TypeError, ValueError, IndexError):
                    continue
                if p <= 0:
                    continue
                best = p if best is None else (max(best, p) if want_max else min(best, p))
            return best

        bid = _top(bids, want_max=True)
        ask = _top(asks, want_max=False)
        last = data.get("lastTradePrice")
        try:
            if bid:
                book.best_bid = bid
            if ask:
                book.best_ask = ask
            if not (bid or ask) and last:
                book.best_bid = book.best_ask = float(last) * scale
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
        for want, fn in list(self.on_tick):
            if want is not None and want != symbol:
                continue                      # another market's tick
            try:
                result = fn(symbol, mid)
                if asyncio.iscoroutine(result):
                    self._spawn(result)
            except Exception:  # a bad callback must not stop the feed
                pass

    def mid(self, symbol: str) -> float:
        return self.books.get(symbol.upper(), HotBook()).mid


STREAM_EXECUTOR = StreamExecutor()


async def streams_start(broker, client) -> None:
    """Subscribe every open position so its `last_price` tracks the market.

    Also turns on the market-wide statistics channel: it is one subscription
    that covers every market, and it is the only thing that notices a market
    closing between two REST polls.
    """
    STREAM_EXECUTOR.nbx_client = client
    STREAM_EXECUTOR.want_stats = True
    STREAM_EXECUTOR.ensure_ws()
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


__all__ = ["STREAM_EXECUTOR", "HotBook", "StreamExecutor", "streams_start"]
