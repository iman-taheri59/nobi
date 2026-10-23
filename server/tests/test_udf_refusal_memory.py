# mypy: check_untyped_defs = False
# Test doubles stand in for the exchange on purpose; their bodies answer to pytest.
"""A pair the exchange refuses to publish history for is remembered, not retried.

From the errors export of a real install (2026-10-23 18:04):

    error  nbx-client   client error: Nobitex rejected request with 403
    warning nbx-app     resolve USDTIRT skipped: candles USDTIRT 15m unavailable: ...

every five minutes, forever. The 403 is real and expected — Nobitex serves some
pairs' public UDF history to nobody, and the client already knew that (it keeps
the shared breaker closed so the other forty symbols keep their candles). What
was missing is memory: the same doomed request went out again on every resolve
pass, and each one painted the errors tab red. A tab that is always red is a tab
nobody opens, which is how a real failure gets missed.

Three properties, three tests:

1. the first refusal is remembered and the next call does not touch the exchange;
2. the memory expires, so a pair that becomes public is picked up;
3. a success clears it — the memory must never outlive its own evidence.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nobitex_client as nc


class Feed:
    """An exchange that answers one symbol and refuses the other."""

    def __init__(self, refused: set[str] | None = None):
        self.calls: list[str] = []
        self.refused = refused if refused is not None else {"USDTIRT"}

    async def __call__(self, symbol, res, from_ts, now, count=260):
        self.calls.append(symbol.upper())
        if symbol.upper() in self.refused:
            raise nc.ClientError("HTTP", "Nobitex rejected request with 403", {"status": 403})
        return {"s": "ok", "t": [1, 2], "o": [1, 1], "h": [1, 1], "l": [1, 1],
                "c": [1, 1], "v": [1, 1]}


@pytest.fixture
def client(monkeypatch):
    c = nc.NobitexClient(base_url="http://127.0.0.1:1")
    monkeypatch.setattr(nc.CANDLE_CACHE, "load", lambda *a, **k: [])
    monkeypatch.setattr(nc.CANDLE_CACHE, "store", lambda *a, **k: None)
    return c


def run(coro):
    return asyncio.run(coro)


def test_a_refused_pair_is_not_asked_again(client, monkeypatch):
    feed = Feed()
    monkeypatch.setattr(client, "get_udf_history", feed)

    with pytest.raises(nc.UpstreamUnavailable) as first:
        run(client.get_candles_ex("USDTIRT", "15m", 100))
    assert first.value.meta.get("refused") is True
    assert feed.calls == ["USDTIRT"]

    for _ in range(5):
        with pytest.raises(nc.UpstreamUnavailable) as again:
            run(client.get_candles_ex("USDTIRT", "15m", 100))
        assert again.value.meta.get("refused") is True
    assert feed.calls == ["USDTIRT"], "a remembered refusal still cost a request"


def test_the_breaker_stays_closed_and_other_symbols_keep_working(client, monkeypatch):
    feed = Feed()
    monkeypatch.setattr(client, "get_udf_history", feed)
    with pytest.raises(nc.UpstreamUnavailable):
        run(client.get_candles_ex("USDTIRT", "15m", 100))
    rows, source = run(client.get_candles_ex("BTCIRT", "15m", 100))
    assert source == "live" and len(rows) == 2
    assert client.degraded_mode is False


def test_the_memory_expires(client, monkeypatch):
    feed = Feed()
    monkeypatch.setattr(client, "get_udf_history", feed)
    with pytest.raises(nc.UpstreamUnavailable):
        run(client.get_candles_ex("USDTIRT", "15m", 100))
    # rewind the deadline instead of sleeping for six hours
    client._udf_refused["USDTIRT"] = 0.0
    feed.refused = set()   # the pair is public now
    _rows, source = run(client.get_candles_ex("USDTIRT", "15m", 100))
    assert source == "live" and feed.calls == ["USDTIRT", "USDTIRT"]


def test_a_success_clears_the_memory(client, monkeypatch):
    feed = Feed()
    monkeypatch.setattr(client, "get_udf_history", feed)
    with pytest.raises(nc.UpstreamUnavailable):
        run(client.get_candles_ex("USDTIRT", "15m", 100))
    client._udf_refused["USDTIRT"] = 0.0
    feed.refused = set()
    run(client.get_candles_ex("USDTIRT", "15m", 100))
    assert "USDTIRT" not in client._udf_refused


def test_an_outage_is_not_remembered_as_a_refusal(client, monkeypatch):
    """A network failure says nothing about the pair, so it must not be cached."""

    async def timeout(symbol, res, from_ts, now, count=260):
        raise nc.ClientError("TIMEOUT", "read timed out", {})

    monkeypatch.setattr(client, "get_udf_history", timeout)
    with pytest.raises(nc.UpstreamUnavailable) as e:
        run(client.get_candles_ex("BTCIRT", "15m", 100))
    assert e.value.meta.get("refused") is False
    assert "BTCIRT" not in client._udf_refused
