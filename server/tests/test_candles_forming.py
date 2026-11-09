# mypy: check_untyped_defs = False
"""2.5.4 — the chart route asks for the forming bar; the scan does not.

9 Nov 2026, 22:39 local: the signal chart's last candle was 22:15 although the
browser now refetched every 45 s. The server answered every call from the same
cached, closed-bar series (`to` = start of the current bar, TTL 30 min). Three
properties:

1. `forming=True` sends the wall clock as `to`, `forming=False` the bar start;
2. the two answers never share a cache entry, and the forming one expires fast;
3. `/api/candles` is the only caller that asks for the forming bar.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nobitex_client as nc

MAIN = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")


class Feed:
    def __init__(self):
        self.calls: list[tuple[str, str, int, int]] = []

    async def __call__(self, symbol, res, from_ts, to_ts, count=260):
        self.calls.append((symbol.upper(), res, from_ts, to_ts))
        return {"s": "ok", "t": [from_ts, to_ts], "o": [1, 1], "h": [1, 1], "l": [1, 1],
                "c": [1, 1], "v": [1, 1]}


class Cache:
    """In-memory stand-in that records the namespace and TTL of every read."""

    def __init__(self):
        self.rows: dict[tuple, list] = {}
        self.reads: list[tuple] = []

    def load(self, symbol, resolution, from_ts, to_ts, ttl=None):
        self.reads.append((resolution, ttl))
        return self.rows.get((symbol, resolution, from_ts, to_ts))

    def store(self, symbol, resolution, from_ts, to_ts, rows, synthetic=False):
        self.rows[(symbol, resolution, from_ts, to_ts)] = rows


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _client(monkeypatch, cache, feed):
    c = nc.NobitexClient(base_url="http://127.0.0.1:1")
    monkeypatch.setattr(nc, "CANDLE_CACHE", cache)
    monkeypatch.setattr(c, "get_udf_history", feed)
    return c


def test_forming_sends_wall_clock_and_closed_sends_bar_start(monkeypatch):
    cache, feed = Cache(), Feed()
    c = _client(monkeypatch, cache, feed)
    before = int(time.time())
    run(c.get_candles_ex("BTCIRT", "15m", 100))
    run(c.get_candles_ex("BTCIRT", "15m", 100, forming=True))
    closed_to, forming_to = feed.calls[0][3], feed.calls[1][3]
    assert closed_to % 900 == 0 and closed_to <= before
    assert forming_to >= before and forming_to >= closed_to
    # same history window either way
    assert feed.calls[0][2] == feed.calls[1][2]


def test_forming_and_closed_answers_do_not_share_a_cache_entry(monkeypatch):
    cache, feed = Cache(), Feed()
    c = _client(monkeypatch, cache, feed)
    _rows, src = run(c.get_candles_ex("ETHIRT", "15m", 100))
    assert src == "live"
    _rows, src = run(c.get_candles_ex("ETHIRT", "15m", 100, forming=True))
    assert src == "live", "a closed-bar series must not be served to the chart as current"
    assert len(feed.calls) == 2
    # second asks hit their own entries
    assert run(c.get_candles_ex("ETHIRT", "15m", 100))[1] == "cache"
    assert run(c.get_candles_ex("ETHIRT", "15m", 100, forming=True))[1] == "cache"
    assert len(feed.calls) == 2
    namespaces = {r[0] for r in cache.reads}
    assert namespaces == {"15", "15-forming"}
    ttls = {r[0]: r[1] for r in cache.reads}
    assert ttls["15"] is None and ttls["15-forming"] == nc.LIVE_CANDLE_TTL_S
    assert nc.LIVE_CANDLE_TTL_S <= 120


def test_forming_ttl_is_honoured_by_the_real_cache(tmp_path, monkeypatch):
    import candle_cache as cc
    monkeypatch.setattr(cc, "CACHE_ROOT", tmp_path)
    cache = cc.CandleCache(ttl_s=1800)
    rows = [{"t": 1, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]
    cache.store("X", "15-forming", 0, 900, rows)
    assert cache.load("X", "15-forming", 0, 900, ttl=60)
    assert cache.load("X", "15-forming", 0, 900, ttl=0) is None, "ttl override must expire the entry"
    assert cache.load("X", "15-forming", 0, 900), "cache-wide TTL still applies without an override"


def test_only_the_chart_route_asks_for_the_forming_bar():
    forming_calls = [m.start() for m in re.finditer(r", forming=True\)", MAIN)]
    assert len(forming_calls) == 1, "exactly one caller may ask for the forming bar"
    route = MAIN.rfind('@app.get("/api/candles/{symbol}")', 0, forming_calls[0])
    assert route != -1 and MAIN.find("\n@app.", route + 1) > forming_calls[0], \
        "forming=True must live inside the /api/candles route and nowhere else"
