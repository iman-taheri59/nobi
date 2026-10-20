"""A7 — the market is the benchmark, not zero.

The math is short enough to check by hand, so these tests do: a series that is
exactly twice the benchmark must produce beta 2, alpha 0 and 200 % capture. The
rest check that the code refuses to answer when the overlap is too short, and
that the route stays read-only.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmark import MIN_OBS, PERIODS_PER_YEAR, relative, summarise


# --------------------------------------------------------------------------- #
# the arithmetic
# --------------------------------------------------------------------------- #
def test_beta_two_and_no_alpha_when_the_asset_is_double_the_benchmark():
    bench = [0.01, -0.02, 0.03, -0.01] * 10
    asset = [2 * x for x in bench]
    r = relative(asset, bench, 365)
    assert r["n"] == 40
    assert r["beta"] == pytest.approx(2.0)
    assert r["alpha_annual_pct"] == pytest.approx(0.0, abs=1e-9)
    assert r["up_capture_pct"] == pytest.approx(200.0)
    assert r["down_capture_pct"] == pytest.approx(200.0)
    assert r["corr"] == pytest.approx(1.0)


def test_a_constant_edge_shows_up_as_positive_alpha():
    bench = [0.005, -0.005] * 20
    asset = [x + 0.001 for x in bench]
    r = relative(asset, bench, 365)
    assert r["beta"] == pytest.approx(1.0)
    # 0.001 per bar × 365 bars per year = 36.5 % a year
    assert r["alpha_annual_pct"] == pytest.approx(36.5, rel=1e-6)


def test_a_defensive_asset_has_beta_below_one_and_captures_less_downside():
    rnd = random.Random(3)
    bench = [rnd.gauss(0, 0.02) for _ in range(120)]
    asset = [0.5 * x + rnd.gauss(0, 0.001) for x in bench]
    r = relative(asset, bench, 365)
    assert 0.45 < r["beta"] < 0.55
    assert r["down_capture_pct"] < 70
    assert r["tracking_error_pct"] > 0


def test_information_ratio_is_the_excess_over_tracking_error():
    rnd = random.Random(4)
    bench = [rnd.gauss(0, 0.01) for _ in range(200)]
    asset = [x + 0.0004 + rnd.gauss(0, 0.002) for x in bench]
    r = relative(asset, bench, 365)
    assert r["info_ratio"] == pytest.approx(
        r["excess_annual_pct"] / r["tracking_error_pct"], rel=1e-6)
    assert r["info_ratio"] > 0


def test_relative_return_is_the_difference_of_compounded_returns():
    # A flat benchmark has no variance, so beta is undefined — vary it.
    bench = [0.01, -0.004, 0.007, -0.002] * 10
    asset = [x + 0.001 for x in bench]
    r = relative(asset, bench, 365)
    assert r["asset_return_pct"] == pytest.approx((math.exp(sum(asset)) - 1) * 100)
    assert r["bench_return_pct"] == pytest.approx((math.exp(sum(bench)) - 1) * 100)
    assert r["relative_return_pct"] == pytest.approx(
        r["asset_return_pct"] - r["bench_return_pct"])


def test_refuses_a_sample_it_cannot_support():
    assert relative([0.01] * 5, [0.01] * 5, 365) is None
    # a benchmark that never moves has no variance to divide by
    assert relative([0.01, 0.02] * 40, [0.0] * 80, 365) is None


def test_periods_per_year_are_crypto_days_not_stock_days():
    assert PERIODS_PER_YEAR["D"] == 365
    assert PERIODS_PER_YEAR["1h"] == 24 * 365
    assert all(v > 252 for v in PERIODS_PER_YEAR.values()), \
        "using 252 trading days for a 24/7 market overstates alpha"


def test_summary_counts_what_is_beating_the_market():
    rows = [
        {"beta": 1.0, "alpha_annual_pct": 10.0, "relative_return_pct": 5.0},
        {"beta": 1.2, "alpha_annual_pct": -4.0, "relative_return_pct": -2.0},
        {"beta": None, "alpha_annual_pct": 0.0, "relative_return_pct": 0.0},
    ]
    s = summarise(rows)
    assert s["symbols"] == 2
    assert s["beating_benchmark"] == 1
    assert s["beta"] == pytest.approx(1.1)
    assert summarise([]) is None


# --------------------------------------------------------------------------- #
# the route
# --------------------------------------------------------------------------- #
@pytest.fixture()
def bench_board(monkeypatch):
    import main

    board = {}
    for i, sym in enumerate(["AAAIRT", "BBBIRT", "CCCIRT"]):
        board[sym] = {"symbol": sym, "direction": "LONG", "composite": 80 - i}
    board["BTCIRT"] = {"symbol": "BTCIRT", "direction": "NEUTRAL", "composite": 60}
    main._cached["signals_by_tf"]["1h"] = board

    rnd = random.Random(11)
    steps = [rnd.gauss(0.0002, 0.012) for _ in range(220)]

    def rows_for(mult, extra=0.0):
        px = 1_000.0
        out = []
        for i, s in enumerate(steps):
            px *= math.exp(mult * s + extra)
            out.append({"t": 1_700_000_000 + i * 3600, "o": px, "h": px * 1.001,
                        "l": px * 0.999, "c": px, "v": 1.0})
        return out

    feeds = {
        "BTCIRT": rows_for(1.0),
        "AAAIRT": rows_for(1.5, 0.0004),     # geared and better
        "BBBIRT": rows_for(0.5),             # defensive
        "CCCIRT": rows_for(1.0, -0.0006),    # same gear, worse
        "SHORTIRT": rows_for(1.0)[:10],      # not enough bars
    }

    async def fake(symbol, tf="1h", count=240):
        if symbol not in feeds:
            from nobitex_client import UpstreamUnavailable
            raise UpstreamUnavailable(f"no candles for {symbol}")
        return feeds[symbol], "cache"

    monkeypatch.setattr(main.client, "get_candles_ex", fake)
    return board


def test_route_reports_a_beta_per_symbol(bench_board):
    from fastapi.testclient import TestClient

    import main

    d = TestClient(main.app).post("/api/benchmark/relative",
                                  json={"tf": "1h", "bench": "BTCIRT"}).json()
    assert d["ok"] is True, d
    by = {r["symbol"]: r for r in d["rows"]}
    assert set(by) == {"AAAIRT", "BBBIRT", "CCCIRT"}
    assert by["AAAIRT"]["beta"] == pytest.approx(1.5, abs=0.05)
    assert by["BBBIRT"]["beta"] == pytest.approx(0.5, abs=0.05)
    assert by["AAAIRT"]["alpha_annual_pct"] > by["CCCIRT"]["alpha_annual_pct"]
    assert all(r["n"] >= MIN_OBS for r in d["rows"])
    assert d["periods_per_year"] == 24 * 365
    assert d["summary"]["symbols"] == 3


def test_route_does_not_accept_itself_as_the_benchmark(bench_board):
    from fastapi.testclient import TestClient

    import main

    d = TestClient(main.app).post("/api/benchmark/relative",
                                  json={"tf": "1h", "bench": "AAAIRT"}).json()
    assert d["ok"] is True
    assert "AAAIRT" not in [r["symbol"] for r in d["rows"]]


def test_route_drops_a_symbol_without_enough_bars(bench_board):
    from fastapi.testclient import TestClient

    import main

    d = TestClient(main.app).post(
        "/api/benchmark/relative",
        json={"tf": "1h", "bench": "BTCIRT", "symbols": ["AAAIRT", "SHORTIRT"]}).json()
    assert d["ok"] is True
    assert [r["symbol"] for r in d["rows"]] == ["AAAIRT"]
    assert any(x["symbol"] == "SHORTIRT" for x in d["dropped"])


def test_route_skips_the_same_asset_in_another_quote(bench_board, monkeypatch):
    """BTC/USDT vs a BTC/IRT benchmark measures the basis, not performance."""
    from fastapi.testclient import TestClient

    import main

    board = main._cached["signals_by_tf"]["1h"]
    board["BTCUSDT"] = {"symbol": "BTCUSDT", "direction": "NEUTRAL", "composite": 50}
    board["USDTIRT"] = {"symbol": "USDTIRT", "direction": "NEUTRAL", "composite": 50}
    original = main.client.get_candles_ex

    async def fake(symbol, tf="1h", count=240):
        if symbol in ("BTCUSDT", "USDTIRT"):
            return await original("BTCIRT", tf, count)
        return await original(symbol, tf, count)

    monkeypatch.setattr(main.client, "get_candles_ex", fake)
    d = TestClient(main.app).post("/api/benchmark/relative",
                                  json={"tf": "1h", "bench": "BTCIRT"}).json()
    syms = [r["symbol"] for r in d["rows"]]
    assert "BTCUSDT" not in syms and "USDTIRT" not in syms
    # ...but asking for it by name is still allowed
    d2 = TestClient(main.app).post(
        "/api/benchmark/relative",
        json={"tf": "1h", "bench": "BTCIRT", "symbols": ["BTCUSDT"]}).json()
    assert [r["symbol"] for r in d2["rows"]] == ["BTCUSDT"]


def test_route_refuses_an_unknown_benchmark(bench_board):
    from fastapi.testclient import TestClient

    import main

    d = TestClient(main.app).post("/api/benchmark/relative",
                                  json={"tf": "1h", "bench": "NOPEIRT"}).json()
    assert d["ok"] is False
    assert "NOPEIRT" in d["error"]
