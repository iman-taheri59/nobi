#!/usr/bin/env python3
"""Bounded PUBLIC market-data capture. No app imports, config, tokens or orders.

Run with the server's existing venv; stdout is the public JSON evidence file:
 /opt/nobi/.venv/bin/python nobitex_unit_probe.py --seconds 35 > nobitex-unit-evidence.json
This does not assert a unit from price magnitude or change any app setting.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from datetime import UTC, datetime

import httpx
import websockets

API = 'https://apiv2.nobitex.ir'
WS = 'wss://ws.nobitex.ir/connection/websocket'
ALLOWED = ('DOGEIRT', 'BTCIRT', 'BTCUSDT')


def compare_same_bar(symbol: str, candle: dict, udf: dict) -> dict | None:
    """Compare the stable OPEN of an identical bar; close may still be moving."""
    if udf.get('s') != 'ok':
        return None
    try:
        stamp = int(candle['t'])
        index = [int(t) for t in udf['t']].index(stamp)
        raw_open = float(candle['o'])
        rest_open = float(udf['o'][index])
        if not all(math.isfinite(x) and x > 0 for x in (raw_open, rest_open)):
            return None
        ratio = raw_open / rest_open
        if not math.isfinite(ratio):
            return None
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        return None
    return {'symbol': symbol, 'bar_t': stamp, 'raw_ws_open': candle['o'],
            'raw_udf_open': udf['o'][index], 'ws_open_div_udf_open': ratio,
            'interpretation': 'same-bar observation only; no setting changed'}


async def capture(symbols: list[str], seconds: int) -> dict:
    result: dict = {'started_utc': datetime.now(UTC).isoformat(),
                    'duration_limit_seconds': seconds, 'symbols': symbols,
                    'public_only': True, 'app_config_read': False,
                    'orders_sent': False, 'settings_changed': False,
                    'ws_connected': False, 'subscriptions_acknowledged': [],
                    'ws_candles': [], 'rest_samples': [], 'comparisons': [], 'errors': []}
    # Disable environment proxy credentials. No .env/config/application imports.
    async with httpx.AsyncClient(timeout=8, trust_env=False, follow_redirects=False) as client:
        async def get(path: str, params: dict | None = None) -> dict:
            # Hard allowlist: callers cannot turn this helper into a private API client.
            permitted = {'/v2/options', '/market/udf/history'} | {
                f'/v3/orderbook/{symbol}' for symbol in symbols}
            if path not in permitted:
                raise ValueError('non-public endpoint forbidden')
            response = await client.get(API + path, params=params)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError('unexpected public response')
            return body

        async def rest_snapshot(symbol: str) -> None:
            sample: dict = {'symbol': symbol, 'observed_utc': datetime.now(UTC).isoformat()}
            try:
                udf, book = await asyncio.gather(
                    get('/market/udf/history', {'symbol': symbol, 'resolution': '15',
                                               'to': int(time.time()) + 1, 'countback': 4}),
                    get(f'/v3/orderbook/{symbol}'))
                sample['udf'] = {k: udf[k] for k in ('s', 't', 'o', 'h', 'l', 'c', 'v') if k in udf}
                sample['orderbook'] = {k: book[k] for k in ('status', 'lastUpdate') if k in book}
                sample['orderbook'].update({k: (book.get(k) or [])[:2] for k in ('bids', 'asks')})
            except Exception as exc:
                sample['error_type'] = type(exc).__name__
            result['rest_samples'].append(sample)

        async def rest_loop() -> None:
            try:
                options = await get('/v2/options')
                nb = options.get('nobitex') or {}
                result['raw_options_subset'] = {
                    'pricePrecisions': {s: (nb.get('pricePrecisions') or {}).get(s) for s in symbols},
                    'amountPrecisions': {s: (nb.get('amountPrecisions') or {}).get(s) for s in symbols},
                    'minOrders': {s: (nb.get('minOrders') or {}).get(s) for s in ('rls', 'usdt')}}
            except Exception as exc:
                result['errors'].append({'stage': 'public-options', 'type': type(exc).__name__})
            while True:
                await asyncio.gather(*(rest_snapshot(s) for s in symbols))
                await asyncio.sleep(10)

        async def ws_capture() -> None:
            channels = {f'public:candle-{s}-15': s for s in symbols}
            async with websockets.connect(WS, open_timeout=10, close_timeout=2, max_size=1_000_000) as ws:
                await ws.send(json.dumps({'id': 1, 'connect': {}}))
                subscribed = False
                last_pong = 0.0
                async for raw in ws:
                    for line in raw.decode().splitlines() if isinstance(raw, bytes) else raw.splitlines():
                        try:
                            message = json.loads(line)
                        except (ValueError, TypeError):
                            continue
                        if message == {}:
                            if time.monotonic() - last_pong > 1:
                                await ws.send('{}')
                                last_pong = time.monotonic()
                            continue
                        if not isinstance(message, dict):
                            continue
                        if message.get('id') == 1 and 'connect' in message and not subscribed:
                            result['ws_connected'] = True
                            for i, channel in enumerate(channels, start=2):
                                await ws.send(json.dumps({'id': i, 'subscribe': {'channel': channel}}))
                            subscribed = True
                        if message.get('id', 0) in range(2, 2 + len(channels)) and 'subscribe' in message:
                            channel = list(channels)[message['id'] - 2]
                            result['subscriptions_acknowledged'].append(channel)
                        if 'error' in message:
                            result['errors'].append({'stage': 'ws-public-protocol',
                                                     'code': (message['error'] or {}).get('code')})
                        push = message.get('push') or {}
                        incoming_channel = push.get('channel')
                        if not isinstance(incoming_channel, str) or incoming_channel not in channels:
                            continue
                        data = (push.get('pub') or {}).get('data')
                        if isinstance(data, str):
                            try:
                                data = json.loads(data)
                            except ValueError:
                                continue
                        if isinstance(data, dict) and len(result['ws_candles']) < 30:
                            result['ws_candles'].append({
                                'symbol': channels[incoming_channel], 'channel': incoming_channel,
                                'observed_utc': datetime.now(UTC).isoformat(),
                                'raw': {k: data[k] for k in ('t', 'o', 'h', 'l', 'c', 'v') if k in data}})

        async def bounded_ws() -> None:
            try:
                await ws_capture()
            except Exception as exc:
                result['errors'].append({'stage': 'public-websocket', 'type': type(exc).__name__})

        tasks = [asyncio.create_task(rest_loop()), asyncio.create_task(bounded_ws())]
        try:
            await asyncio.sleep(seconds)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        # A final REST observation is bounded by its independent eight-second timeout.
        await asyncio.gather(*(rest_snapshot(s) for s in symbols))

    for frame in result['ws_candles']:
        for sample in result['rest_samples']:
            if sample['symbol'] == frame['symbol']:
                comparison = compare_same_bar(frame['symbol'], frame['raw'], sample.get('udf') or {})
                if comparison is not None and comparison not in result['comparisons']:
                    result['comparisons'].append(comparison)
    result['finished_utc'] = datetime.now(UTC).isoformat()
    result['evidence_status'] = 'same-bar-capture-available' if result['comparisons'] else 'undetermined'
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=35, help='5..60 seconds plus final REST timeout')
    parser.add_argument('--symbols', nargs='+', choices=ALLOWED, default=['DOGEIRT', 'BTCUSDT'])
    args = parser.parse_args()
    if not 5 <= args.seconds <= 60 or len(args.symbols) > 3:
        parser.error('duration must be 5..60 seconds; at most three symbols')
    result = asyncio.run(capture(list(dict.fromkeys(args.symbols)), args.seconds))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result['comparisons'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
