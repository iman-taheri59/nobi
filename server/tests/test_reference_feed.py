"""The reference feed: a second venue, and the fences around it (1.0.10).

Nobitex is unreachable from outside Iran, so real candles cannot be fetched for
development and there is nothing to check the numbers against. `reference_feed`
fixes that with a different exchange's public API — and the entire value of the
feature depends on it never being mistaken for the venue.

These tests pin the fences rather than the network:

1. symbol/timeframe mapping, including the row shape KuCoin sends (open, **close**,
   high, low — the classic way to swap high and low without noticing);
2. IRT markets are refused without a toman rate, and the derived series is
   scaled to **rial** (a factor-of-ten bug would look plausible on screen);
3. the promotion gate stamps a reference run `REFERENCE_DATA` and never `PASS`;
4. the routes expose the feed without ever substituting it silently.

Every HTTP call is mocked: a test that needs the internet fails for reasons that
have nothing to do with the code.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reference_feed as rf  # noqa: E402

# --------------------------------------------------------------------------- #
# A fake venue
# --------------------------------------------------------------------------- #
CANDLES = {
    # deliberately NOT in time order, and with close in the third slot
    "data": [
        ["1792656000", "82232.6", "81842.4", "82267.7", "81663.2", "213.47", "17.5"],
        ["1792641600", "82928.4", "82244.7", "82984.5", "82147.2", "270.05", "22.3"],
    ],
    "code": "200000",
}
LEVEL1 = {"code": "200000", "data": {"price": "81842.4", "bestBid": "81840.0",
                                     "bestAsk": "81845.0"}}


def _install(monkeypatch, handler):
    """Point the module's client factory at a mock transport."""
    real_client = rf._client

    def factory(provider):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 timeout=httpx.Timeout(5.0))

    monkeypatch.setattr(rf, "_client", factory)
    monkeypatch.setenv("NBX_REFERENCE", "kucoin")
    monkeypatch.delenv("NBX_TOMAN_PER_USDT", raising=False)
    return real_client


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# 1. mapping and row shape
# --------------------------------------------------------------------------- #
def test_a_usd_pair_maps_to_the_provider_pair():
    assert rf.provider_pair("BTCUSDT") == ("BTC-USDT", False)
    assert rf.provider_pair("ETHUSDC") == ("ETH-USDC", False)
    assert rf.provider_pair("btcusdt") == ("BTC-USDT", False)


def test_an_unknown_symbol_is_refused_not_guessed():
    with pytest.raises(rf.ReferenceError) as e:
        rf.provider_pair("BTC")
    assert e.value.code == "bad-symbol"


def test_rows_come_back_ascending_with_the_right_ohlc(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "symbol=BTC-USDT" in str(request.url)
        return httpx.Response(200, json=CANDLES)

    _install(monkeypatch, handler)
    rows, meta = _run(rf.fetch_candles("BTCUSDT", "4h", limit=10))

    assert meta["pair"] == "BTC-USDT"
    assert meta["derived"] is False
    assert [r["t"] for r in rows] == sorted(r["t"] for r in rows)
    first = rows[0]
    assert first["o"] == pytest.approx(82928.4)
    assert first["c"] == pytest.approx(82244.7)   # third field, not second
    assert first["h"] == pytest.approx(82984.5)   # high is not low
    assert first["l"] == pytest.approx(82147.2)
    assert first["h"] > first["c"] > first["l"]


def test_an_unsupported_timeframe_says_which_ones_exist(monkeypatch):
    _install(monkeypatch, lambda r: httpx.Response(200, json=CANDLES))
    with pytest.raises(rf.ReferenceError) as e:
        _run(rf.fetch_candles("BTCUSDT", "7m"))
    assert e.value.code == "bad-timeframe"
    assert "4h" in e.value.meta["timeframes"]


def test_a_451_is_named_as_a_geoblock_not_as_a_generic_failure(monkeypatch):
    _install(monkeypatch, lambda r: httpx.Response(451, json={}))
    with pytest.raises(rf.ReferenceError) as e:
        _run(rf.fetch_candles("BTCUSDT", "1h"))
    assert e.value.code == "geo-blocked"
    assert "451" in e.value.message


def test_a_disabled_feed_refuses_before_touching_the_network(monkeypatch):
    called: list[str] = []

    def handler(request):
        called.append(str(request.url))
        return httpx.Response(200, json=CANDLES)

    _install(monkeypatch, handler)
    monkeypatch.setenv("NBX_REFERENCE", "none")
    with pytest.raises(rf.ReferenceError) as e:
        _run(rf.fetch_candles("BTCUSDT", "1h"))
    assert e.value.code == "disabled"
    assert called == []


# --------------------------------------------------------------------------- #
# 2. rial markets: refused by default, derived only on request
# --------------------------------------------------------------------------- #
def test_an_irt_series_needs_a_toman_rate(monkeypatch):
    _install(monkeypatch, lambda r: httpx.Response(200, json=CANDLES))
    with pytest.raises(rf.ReferenceError) as e:
        _run(rf.fetch_candles("BTCIRT", "4h"))
    assert e.value.code == "no-fx"


def test_a_derived_irt_series_is_scaled_to_rial(monkeypatch):
    from units import RIAL_PER_TOMAN

    _install(monkeypatch, lambda r: httpx.Response(200, json=CANDLES))
    rows, meta = _run(rf.fetch_candles("BTCIRT", "4h", limit=10, fx=100_000.0))

    assert meta["derived"] is True
    assert meta["quote_unit"] == "rial"
    assert meta["toman_per_usdt"] == pytest.approx(100_000.0)
    # 82 244.7 USD × 100 000 toman × 10 rial per toman
    assert rows[0]["c"] == pytest.approx(82244.7 * 100_000 * RIAL_PER_TOMAN)
    # and the unit is declared, not implied
    assert rf.provider_pair("BTCIRT") == ("BTC-USDT", True)


# --------------------------------------------------------------------------- #
# 3. the gate never certifies another venue's prices
# --------------------------------------------------------------------------- #
def _rows(n: int = 700, seed: int = 21):
    import random

    rnd = random.Random(seed)
    px = 100.0
    out = []
    for i in range(n):
        o = px
        px = max(1.0, px * (1 + rnd.gauss(0.0004, 0.009)))
        out.append({"t": 1_700_000_000 + i * 3600, "o": o, "h": max(o, px) * 1.004,
                    "l": min(o, px) * 0.996, "c": px, "v": 10.0})
    return out


def test_a_reference_run_is_stamped_and_can_never_pass():
    from backtest import run_and_save

    rep = run_and_save("TUSDT", "1h", _rows(), {"initial_equity": 50_000},
                       mode="wf", auto_window=True, source="reference")
    gate = rep["gate"]
    assert rep["source"] == "reference"
    assert gate["state"] == "REFERENCE_DATA"
    assert gate["would_be"] in {"PASS", "FAIL", "INSUFFICIENT_DATA"}
    assert "another exchange" in gate["message"]


def test_a_live_run_is_unaffected_by_the_new_state():
    from backtest import run_and_save

    rep = run_and_save("TUSDT", "1h", _rows(), {"initial_equity": 50_000},
                       mode="wf", auto_window=True, source="live")
    assert rep["gate"]["state"] in {"PASS", "FAIL", "INSUFFICIENT_DATA"}


def test_a_synthetic_run_keeps_its_own_state():
    from backtest import run_and_save

    rep = run_and_save("TUSDT", "1h", _rows(), {"initial_equity": 50_000},
                       mode="wf", auto_window=True, source="synthetic")
    assert rep["gate"]["state"] == "SYNTHETIC_DATA"


# --------------------------------------------------------------------------- #
# 4. the routes
# --------------------------------------------------------------------------- #
def test_the_backtest_route_labels_a_reference_run(monkeypatch):
    import main

    async def fake_candles(symbol, tf, limit, fx=None):
        return _rows(), {"provider": "kucoin", "pair": "BTC-USDT", "derived": False,
                         "rows": 700}

    monkeypatch.setattr(main.reference_feed, "fetch_candles", fake_candles)
    out = _run(main.backtest_run({"symbol": "BTCUSDT", "tf": "1h", "bars": 700,
                                  "candles_source": "reference"}))
    assert out["ok"] is True
    assert out["candles_source"] == "reference"
    # `report.source` is the candle origin; the body's `upstream` is liveness.
    assert out["report"]["source"] == "reference"
    assert "upstream" in out
    assert out["reference"]["provider"] == "kucoin"
    assert out["report"]["gate"]["state"] == "REFERENCE_DATA"


def test_the_backtest_route_rejects_an_unknown_source(monkeypatch):
    import main

    out = _run(main.backtest_run({"symbol": "BTCIRT", "candles_source": "binance"}))
    assert out["ok"] is False
    assert out["allowed"] == ["nobitex", "reference"]


def test_the_backtest_route_surfaces_a_reference_refusal(monkeypatch):
    import main

    async def boom(symbol, tf, limit, fx=None):
        raise rf.ReferenceError("no-fx", "a toman rate is needed")

    monkeypatch.setattr(main.reference_feed, "fetch_candles", boom)
    out = _run(main.backtest_run({"symbol": "BTCIRT", "candles_source": "reference"}))
    assert out["ok"] is False
    assert out["error"] == "no-fx"
    assert out["candles_source"] == "reference"


# --------------------------------------------------------------------------- #
# 5. paging — a request for N bars must not silently arrive as 100 (1.0.12)
# --------------------------------------------------------------------------- #
def _series(start: int, count: int, step: int = 3600) -> dict:
    """A run of KuCoin rows ending at `start`, oldest first."""
    data = []
    for i in range(count):
        t = start + i * step
        base = 80000 + i
        data.append([str(t), str(base), str(base + 20), str(base + 40), str(base - 40),
                     "12.5", "1.0"])
    return {"code": "200000", "data": data}


def test_paging_keeps_asking_until_the_caller_has_what_it_asked_for(monkeypatch):
    """The venue answers a *window*, so 400 bars need more than one request."""
    calls: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(pair.split("=") for pair in request.url.query.decode().split("&"))
        start, end = int(q["startAt"]), int(q["endAt"])
        calls.append((start, end))
        step = 3600
        count = min(rf.PAGE_BARS, max(0, (end - start) // step))
        return httpx.Response(200, json=_series(end - count * step, count, step))

    _install(monkeypatch, handler)
    rows, meta = _run(rf.fetch_candles("BTCUSDT", "1h", limit=400))

    assert len(rows) == 400
    assert meta["requested"] == 400
    assert meta["truncated"] is False
    assert meta["pages"] >= 2, "one page holds PAGE_BARS, so 400 must page"
    # Each page walks strictly backwards, and no timestamp is duplicated.
    assert len(calls) == meta["pages"]
    assert len({r["t"] for r in rows}) == len(rows)
    assert [r["t"] for r in rows] == sorted(r["t"] for r in rows)


def test_a_venue_that_has_nothing_older_stops_and_says_truncated(monkeypatch):
    """A short answer is reported as short, never padded and never implied full."""
    state = {"page": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["page"] += 1
        if state["page"] > 2:
            return httpx.Response(200, json={"code": "200000", "data": []})
        q = dict(pair.split("=") for pair in request.url.query.decode().split("&"))
        end = int(q["endAt"])
        return httpx.Response(200, json=_series(end - 100 * 3600, 100, 3600))

    _install(monkeypatch, handler)
    rows, meta = _run(rf.fetch_candles("BTCUSDT", "1h", limit=400))

    assert len(rows) < 400
    assert meta["truncated"] is True
    assert meta["rows"] == len(rows)
    assert state["page"] <= rf.MAX_PAGES + 1, "paging must terminate"


def test_paging_stops_at_the_hard_page_limit(monkeypatch):
    """A venue that always answers the same full page must not loop forever."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # A fixed window of rows, ignoring startAt: every page looks "fresh"
        # only because the timestamps are new — the loop is bounded by MAX_PAGES.
        return httpx.Response(200, json=_series(1_700_000_000 + calls["n"] * 10 ** 6,
                                                rf.PAGE_BARS))

    _install(monkeypatch, handler)
    rows, meta = _run(rf.fetch_candles("BTCUSDT", "1h", limit=10_000))
    assert meta["pages"] == rf.MAX_PAGES
    assert calls["n"] == rf.MAX_PAGES
    assert len(rows) <= rf.PAGE_BARS * rf.MAX_PAGES
