# mypy: check_untyped_defs = False
# Test doubles stand in for objects on purpose; their bodies answer to pytest.
"""One refused symbol must not blind the scan.

Observed on a real run (2026-10-20, live Nobitex):

    GET /market/udf/history?symbol=USDTIRT...       403 Forbidden
    WARNING  scan USDTIRT: candles USDTIRT 15m unavailable: 403
    WARNING  scan TRXIRT:  ... circuit breaker is open
    ... 38 more of the same ...
    rescan 15m: 5 symbols, FA 5/5

One pair that Nobitex does not serve publicly switched off candle data for
every other pair for two minutes, and the journal resolution for the whole
scan was skipped with it. The breaker exists for outages; a 403 on one symbol
is not an outage.
"""

from __future__ import annotations

import asyncio

import pytest

from nobitex_client import ClientError, NobitexClient, UpstreamUnavailable

ROWS = {"s": "ok", "t": [1, 2], "o": [1.0, 1.1], "h": [1.2, 1.3],
        "l": [0.9, 1.0], "c": [1.1, 1.2], "v": [10.0, 11.0]}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    import candle_cache

    monkeypatch.setattr(candle_cache, "CACHE_ROOT", tmp_path)
    c = NobitexClient()
    return c


def _serve(monkeypatch, client, behaviour: dict):
    async def fake_udf(symbol, res, from_ts, to_ts, count=260):
        action = behaviour.get(symbol.upper(), "ok")
        if isinstance(action, Exception):
            raise action
        return ROWS

    monkeypatch.setattr(client, "get_udf_history", fake_udf)


def test_a_forbidden_symbol_does_not_open_the_breaker(monkeypatch, client):
    _serve(monkeypatch, client, {"USDTIRT": ClientError("AUTH", "403", {"status": 403})})

    with pytest.raises(UpstreamUnavailable) as first:
        asyncio.run(client.get_candles_ex("USDTIRT", "15m", 10))
    assert "403" in str(first.value)

    # The breaker must still be closed: another symbol has to work.
    rows, src = asyncio.run(client.get_candles_ex("BTCIRT", "15m", 10))
    assert src == "live"
    assert len(rows) == 2


def test_a_forbidden_symbol_is_named_as_a_symbol_problem(monkeypatch, client):
    """The message has to say what happened. "circuit breaker is open" sent
    the reader looking at the network for a per-symbol refusal."""
    _serve(monkeypatch, client, {"XYZIRT": ClientError("AUTH", "403", {"status": 403})})

    with pytest.raises(UpstreamUnavailable) as e:
        asyncio.run(client.get_candles_ex("XYZIRT", "15m", 10))
    assert "circuit breaker" not in str(e.value)


def test_a_timeout_still_opens_the_breaker(monkeypatch, client):
    """The protection has to survive the fix: a transport failure is an outage."""
    _serve(monkeypatch, client, {"BTCIRT": ClientError("TIMEOUT", "Nobitex timeout")})

    with pytest.raises(UpstreamUnavailable):
        asyncio.run(client.get_candles_ex("BTCIRT", "15m", 10))

    with pytest.raises(UpstreamUnavailable) as e:
        asyncio.run(client.get_candles_ex("ETHIRT", "15m", 10))
    assert "circuit breaker" in str(e.value)


def test_a_server_error_is_an_outage(monkeypatch, client):
    _serve(monkeypatch, client, {"BTCIRT": ClientError("HTTP", "bad status 503", {"status": 503})})

    with pytest.raises(UpstreamUnavailable):
        asyncio.run(client.get_candles_ex("BTCIRT", "15m", 10))

    with pytest.raises(UpstreamUnavailable) as e:
        asyncio.run(client.get_candles_ex("ETHIRT", "15m", 10))
    assert "circuit breaker" in str(e.value)


def test_a_client_error_in_the_four_hundreds_is_not_an_outage(monkeypatch, client):
    _serve(monkeypatch, client, {"BTCIRT": ClientError("HTTP", "bad status 404", {"status": 404})})
    with pytest.raises(UpstreamUnavailable):
        asyncio.run(client.get_candles_ex("BTCIRT", "15m", 10))
    _rows, src = asyncio.run(client.get_candles_ex("ETHIRT", "15m", 10))
    assert src == "live"


# --------------------------------------------------------------------------- #
def test_the_stable_pair_is_not_scanned_for_technical_analysis(monkeypatch):
    """USDTIRT has no public UDF history (403) and no meaningful trend. Asking
    for it spent one request per scan to collect a rejection."""
    import main

    scanned: list[str] = []
    monkeypatch.setitem(main._cached, "universe", [
        {"symbol": "BTCIRT", "last": 1.0, "vol24": 1e9, "change24": 0.0, "closed": False},
        {"symbol": "USDTIRT", "last": 1.0, "vol24": 1e9, "change24": 0.0, "closed": False},
    ])
    # `rescan()` writes into these three caches in place. Without swapping them
    # for fresh dicts, the snapshot a fake candle produced for BTCIRT survived
    # this test — and `_signal_lookup()` prefers `signals_by_tf` over
    # `signals`, so a later test that set only `signals` got no price at all.
    # It passed in the full suite purely because another file replaced the dict
    # wholesale on the way past: a test-order dependency is not a passing test.
    for key in ("signals", "signals_by_tf", "signals_ts"):
        monkeypatch.setitem(main._cached, key, {})

    async def fake_candles(symbol, tf, count):
        scanned.append(symbol)
        return ([{"t": i, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.0, "v": 1.0}
                 for i in range(30)], "live")

    monkeypatch.setattr(main.client, "get_candles_ex", fake_candles)
    monkeypatch.setenv("NBX_JOURNAL", "0")
    monkeypatch.setattr(main, "journal_scan", lambda *a, **k: _noop())

    asyncio.run(main.rescan("15m", top=10))

    assert "BTCIRT" in scanned
    assert "USDTIRT" not in scanned


async def _noop():
    return 0
