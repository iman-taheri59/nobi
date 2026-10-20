#!/usr/bin/env python3
"""A minimal Centrifugo stand-in, for testing the socket path offline.

This is **not** a product component — same shelf as `mock_upstream.py`. It speaks
exactly the frames the Nobitex documentation shows for a raw client:

    -> {"connect": {}, "id": 1}                     <- {"id": 1, "connect": {}}
    -> {"subscribe": {"channel": "..."}, "id": 2}   <- {"id": 2, "subscribe": {}}
    -> {}                                           <- {}          (keep-alive)
    <- {"push": {"channel": "...", "pub": {"data": "<json string>"}}}

The point is to prove the *client* works: subscription names, payload parsing,
Rial→Toman scaling, the keep-alive answer, and the merge of a socket candle into
a REST series. None of the numbers here are meant to be seen by a user; they
only move.

    python3 scripts/mock_ws.py --port 8099
    NBX_WS_URL=ws://127.0.0.1:8099/connection/websocket \\
        python3 scripts/run_with_mock_exchange.py --port 8033 --exchange http://127.0.0.1:8010
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

import websockets

#: Base quotes in Rial for the Rial markets and in dollars for the USDT pairs —
#: the same units the exchange publishes, so the client's scaling is exercised.
#: The three legs have to agree with each other, or the app's own premium check
#: (BTCIRT / BTCUSDT vs USDTIRT) reads ±50 % and refuses to print a number — the
#: first version of this fixture priced BTCIRT at 35 bn Rial next to a 73k dollar
#: BTCUSDT, i.e. a 110 % premium, and the market panel went blank.
MARKETS = {
    "btc-irt": 89_700_000_000.0,
    "eth-irt": 3_931_395_000.0,
    "usdt-irt": 1_008_050.0,
    "btc-usdt": 73_000.0,
}

#: Rial-quoted candle base, chosen so the Toman value lands near the fixture's
#: BTCIRT quote (89.7 bn Rial = 8.97 bn Toman). The candle channel sends the
#: market's own unit, and the client must scale it exactly like the REST series.
CANDLE_BASE = 89_700_000_000.0


def _stats(step: int) -> dict:
    drift = 1 + (step % 20 - 10) * 0.0002
    out = {}
    for key, base in MARKETS.items():
        px = base * drift
        out[key] = {
            "isClosed": False,
            "bestSell": f"{px * 1.0002:.0f}",
            "bestBuy": f"{px * 0.9998:.0f}",
            "volumeSrc": "12.5",
            "volumeDst": "21590044.0",
            "latest": f"{px:.0f}",
            "mark": f"{px:.0f}",
            "dayLow": f"{base * 0.97:.0f}",
            "dayHigh": f"{base * 1.03:.0f}",
            "dayOpen": f"{base:.0f}",
            "dayClose": f"{px:.0f}",
            "dayChange": "-1.25",
        }
    return out


def _candle(step: int) -> dict:
    now = int(time.time()) // 900 * 900
    close = CANDLE_BASE * (1 + (step % 30 - 15) * 0.0001)
    return {"t": now, "o": CANDLE_BASE, "h": close * 1.0005,
            "l": close * 0.9995, "c": close, "v": 1.25}


def _book(step: int) -> dict:
    px = CANDLE_BASE * (1 + (step % 10) * 0.00005)
    return {"asks": [[f"{px * 1.0001:.0f}", "0.01"]],
            "bids": [[f"{px * 0.9999:.0f}", "0.02"]],
            "lastTradePrice": f"{px:.0f}", "lastUpdate": int(time.time() * 1000)}


async def _serve(ws, _path=None) -> None:
    channels: set[str] = set()
    sender: asyncio.Task | None = None

    async def pusher() -> None:
        step = 0
        while True:
            await asyncio.sleep(1.0)
            step += 1
            frames = []
            if "public:market-stats-all" in channels:
                frames.append(("public:market-stats-all", _stats(step)))
            for ch in sorted(channels):
                if ch.startswith("public:candle-BTCIRT-"):
                    frames.append((ch, _candle(step)))
                elif ch.startswith("public:orderbook-BTCIRT"):
                    frames.append((ch, _book(step)))
            for channel, payload in frames:
                await ws.send(json.dumps({"push": {
                    "channel": channel,
                    "pub": {"data": json.dumps(payload), "offset": step},
                }}))

    try:
        async for raw in ws:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "ignore")
            if raw.strip() == "{}":
                # A pong from the client. Centrifugo does not answer a pong;
                # echoing it started an endless ping-pong in an earlier version
                # of this stand-in.
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if "connect" in msg:
                await ws.send(json.dumps({"id": msg.get("id"), "connect": {}}))
            elif "subscribe" in msg:
                ch = (msg.get("subscribe") or {}).get("channel") or ""
                channels.add(ch)
                await ws.send(json.dumps({"id": msg.get("id"), "subscribe": {}}))
                if sender is None:
                    sender = asyncio.create_task(pusher())
                # The application-level keep-alive: the client must answer with
                # the same empty object, or Centrifugo drops it after 25 s.
                async def keepalive():
                    while True:
                        await asyncio.sleep(20)
                        await ws.send("{}")
                asyncio.create_task(keepalive())
    finally:
        if sender:
            sender.cancel()


async def main_async(port: int) -> None:
    async with websockets.serve(_serve, "127.0.0.1", port):
        print(f"mock centrifugo on ws://127.0.0.1:{port}/connection/websocket", flush=True)
        await asyncio.Future()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    args = ap.parse_args()
    try:
        asyncio.run(main_async(args.port))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
