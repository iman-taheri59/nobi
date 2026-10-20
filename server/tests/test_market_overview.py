"""A6 — breadth, dispersion and concentration, computed rather than asserted.

The failure mode this guards against is a tidy dashboard that says "market is
bullish" because somebody typed 68 % into the HTML. Every number here comes
from rows the test controls, and the unit trap — IRT volumes added to USDT
volumes — has its own test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_overview import overview


def _row(sym: str, change: float, vol: float, fx: float = 100_000.0) -> dict:
    return {"symbol": sym, "last": 100.0, "change24": change, "vol24": vol, "fx_usd": fx}


def test_breadth_counts_up_down_and_flat():
    rows = [_row("AIRT", 5.0, 100.0), _row("BIRT", -3.0, 100.0),
            _row("CIRT", 0.0, 100.0), _row("DIRT", 1.0, 100.0)]
    o = overview(rows)
    assert o["ok"] is True
    assert o["breadth"]["up"] == 2
    assert o["breadth"]["down"] == 1
    assert o["breadth"]["flat"] == 1
    assert o["breadth"]["pct_up"] == pytest.approx(50.0)


def test_turnover_is_converted_to_usd_before_it_is_summed():
    """100 units of an IRT pair at 100,000 IRT/USD is $100, not 100 units."""
    rows = [_row("IRT", 1.0, 10_000_000.0, fx=100_000.0),   # $100
            _row("USDT", 1.0, 300.0, fx=1.0)]                 # $300
    o = overview(rows)
    assert o["turnover"]["total_usd"] == pytest.approx(400.0)
    # the IRT pair is the small one despite the huge raw number
    assert [t["symbol"] for t in o["turnover"]["top"]] == ["USDT", "IRT"]


def test_volume_weighted_change_uses_dollar_turnover():
    rows = [_row("AIRT", 10.0, 900.0, fx=1.0), _row("BIRT", -10.0, 100.0, fx=1.0),
            _row("CIRT", 0.0, 100.0, fx=1.0)]
    o = overview(rows)
    # (10×900 + −10×100 + 0×100) / 1100 = 7.27
    assert o["change"]["vol_weighted_24h"] == pytest.approx(7.2727, abs=1e-3)
    assert o["breadth"]["pct_up_vol_weighted"] == pytest.approx(900 / 1100 * 100, abs=0.01)


def test_dispersion_separates_a_uniform_move_from_a_split_market():
    uniform = overview([_row(f"S{i}IRT", 3.0, 100.0) for i in range(4)])
    split = overview([_row("AIRT", 9.0, 100.0), _row("BIRT", -9.0, 100.0),
                      _row("CIRT", 0.0, 100.0), _row("DIRT", 4.0, 100.0)])
    assert uniform["change"]["dispersion_24h"] == 0.0
    assert split["change"]["dispersion_24h"] > 5


def test_concentration_names_the_top_three_share():
    rows = [_row("AIRT", 1.0, 700.0, fx=1.0), _row("BIRT", 1.0, 200.0, fx=1.0),
            _row("CIRT", 1.0, 50.0, fx=1.0), _row("DIRT", 1.0, 50.0, fx=1.0)]
    o = overview(rows)
    assert o["turnover"]["top3_share_pct"] == pytest.approx(95.0)
    assert o["turnover"]["top"][0]["symbol"] == "AIRT"


def test_leaders_and_laggards_are_the_actual_extremes():
    rows = [_row(f"S{i}IRT", i - 2.0, 100.0) for i in range(5)]
    o = overview(rows, top_n=2)
    assert [r["symbol"] for r in o["leaders"]] == ["S4IRT", "S3IRT"]
    assert [r["symbol"] for r in o["laggards"]] == ["S0IRT", "S1IRT"]


def test_a_short_board_does_not_put_a_loser_among_the_leaders():
    """With 3 gainers and 3 losers, top_n=5 must not pad leaders with a faller."""
    rows = [_row("AIRT", 2.0, 1.0), _row("BIRT", 1.0, 1.0), _row("CIRT", 0.5, 1.0),
            _row("DIRT", -0.5, 1.0), _row("EIRT", -1.0, 1.0), _row("FIRT", -2.0, 1.0)]
    o = overview(rows, top_n=5)
    assert [r["symbol"] for r in o["leaders"]] == ["AIRT", "BIRT", "CIRT"]
    assert [r["symbol"] for r in o["laggards"]] == ["FIRT", "EIRT", "DIRT"]
    assert all(r["change24"] > 0 for r in o["leaders"])
    assert all(r["change24"] < 0 for r in o["laggards"])


def test_a_thin_market_refuses_to_report_breadth():
    o = overview([_row("AIRT", 1.0, 100.0), _row("BIRT", -1.0, 100.0)])
    assert o["ok"] is False
    assert o["breadth"]["pct_up"] == pytest.approx(50.0)   # the count is still true
    assert o["change"]["median_24h"] is None               # the statistic is not
    assert "at least" in o["reason"]


def test_rows_without_a_change_are_excluded_and_counted():
    rows = [_row("AIRT", 1.0, 100.0), _row("BIRT", 2.0, 100.0), _row("CIRT", 3.0, 100.0),
            {"symbol": "D"}, {"symbol": "E", "change24": None, "vol24": 5.0}]
    o = overview(rows)
    assert o["priced"] == 3
    assert o["excluded"]["no_change"] == 2
    assert o["breadth"]["pct_up"] == pytest.approx(100.0)


def test_a_row_without_an_fx_rate_does_not_enter_the_usd_turnover():
    rows = [_row("AIRT", 1.0, 100.0, fx=1.0),
            {"symbol": "BIRT", "change24": 1.0, "vol24": 1e9}]     # no fx_usd
    o = overview(rows)
    assert o["turnover"]["total_usd"] == pytest.approx(100.0)
    assert o["excluded"]["no_fx"] == 1


def test_signal_mix_travels_with_the_market_view():
    signals = {
        "AIRT": {"direction": "LONG", "regime": "TREND", "composite": 80},
        "BIRT": {"direction": "NEUTRAL", "regime": "RANGE", "composite": 60},
        "CIRT": {"direction": "EXIT", "regime": "RANGE", "composite": 40},
    }
    rows = [_row(f"{k}", 1.0, 100.0) for k in signals]
    o = overview(rows, signals=signals)
    assert o["signals"]["directions"] == {"LONG": 1, "NEUTRAL": 1, "EXIT": 1}
    assert o["signals"]["regimes"] == {"TREND": 1, "RANGE": 2}
    assert o["signals"]["avg_composite"] == pytest.approx(60.0)


def test_the_same_coin_in_two_quotes_is_counted_once():
    """BTC/IRT and BTC/USDT are one asset; counting both double-weights it."""
    rows = [_row("BTCIRT", 5.0, 100.0), _row("BTCUSDT", 5.0, 50.0, fx=1.0),
            _row("ETHIRT", -1.0, 100.0), _row("SOLIRT", 2.0, 100.0)]
    o = overview(rows)
    assert o["universe_size"] == 3
    assert o["excluded"]["same_asset"] == 1
    assert o["breadth"]["up"] == 2          # BTC once, SOL once — not BTC twice
    assert {t["symbol"] for t in o["turnover"]["top"]} >= {"BTCIRT"}


def test_the_toman_rate_is_reported_as_fx_not_as_breadth():
    """USDT/IRT is the exchange rate; counting it as an up market is wrong."""
    rows = [_row("BTCIRT", 2.0, 100.0), _row("ETHIRT", -2.0, 100.0),
            _row("SOLIRT", 1.0, 100.0), _row("USDTIRT", 0.4, 500.0, fx=100_000.0)]
    o = overview(rows)
    assert [r["symbol"] for r in o["leaders"]] != ["USDTIRT"]
    assert "USDTIRT" not in [t["symbol"] for t in o["turnover"]["top"]]
    assert o["fx"]["symbol"] == "USDTIRT"
    assert o["fx"]["change24"] == pytest.approx(0.4)
    assert o["breadth"]["up"] == 2 and o["breadth"]["down"] == 1


def test_premium_passes_through_untouched_including_none():
    assert overview([_row(f"S{i}", 1.0, 10.0) for i in range(3)],
                    premium_pct=1.25)["premium_pct"] == 1.25
    assert overview([_row(f"S{i}", 1.0, 10.0) for i in range(3)])["premium_pct"] is None


# --------------------------------------------------------------------------- #
# the route
# --------------------------------------------------------------------------- #
def test_route_serves_the_computed_overview(monkeypatch):
    from fastapi.testclient import TestClient

    import main

    main._cached["universe"] = [
        {"symbol": "BTCIRT", "last": 9e9, "vol24": 8e11, "change24": 2.5},
        {"symbol": "ETHIRT", "last": 3e8, "vol24": 4e11, "change24": -1.5},
        {"symbol": "USDTIRT", "last": 101_200.0, "vol24": 1e11, "change24": 0.1},
        {"symbol": "BTCUSDT", "last": 88_000.0, "vol24": 5e6, "change24": 1.0},
        {"symbol": "SOLIRT", "last": 1.5e7, "vol24": 6e11, "change24": 3.0},
    ]
    main._cached["premium_pct"] = 0.0
    main._cached["signals_by_tf"]["15m"] = {
        "SOLIRT": {"direction": "LONG", "regime": "TREND", "composite": 74},
        "BTCIRT": {"direction": "LONG", "regime": "TREND", "composite": 78},
        "ETHIRT": {"direction": "NEUTRAL", "regime": "RANGE", "composite": 60},
        "USDTIRT": {"direction": "NEUTRAL", "regime": "RANGE", "composite": 50},
        "BTCUSDT": {"direction": "LONG", "regime": "TREND", "composite": 72},
    }
    d = TestClient(main.app).get("/api/market/overview?tf=15m").json()
    assert d["ok"] is True, d
    # USDTIRT is FX, and BTC/USDT is BTC/IRT quoted twice: three assets remain
    assert d["universe_size"] == 3 and d["fx"]["symbol"] == "USDTIRT"
    assert d["excluded"]["same_asset"] == 1
    # every volume must be in USD, so the toman pair cannot dominate by scale
    assert d["turnover"]["top"][0]["symbol"] == "BTCIRT"
    assert d["turnover"]["top"][0]["vol_usd"] == pytest.approx(8e11 / 101_200, rel=1e-6)
    # the mix counts assets, not listings: BTC appears once even though both
    # BTC/IRT and BTC/USDT were scored
    assert d["signals"]["directions"]["LONG"] == 2   # BTC (once) + SOL
    assert d["signals"]["scored"] == 3

    assert "index" in d["note"]


def test_route_says_so_when_there_is_no_universe(monkeypatch):
    from fastapi.testclient import TestClient

    import main

    main._cached["universe"] = []
    d = TestClient(main.app).get("/api/market/overview").json()
    assert d["ok"] is False
    assert "universe" in (d.get("error") or d.get("reason") or "").lower() or d["universe_size"] == 0
