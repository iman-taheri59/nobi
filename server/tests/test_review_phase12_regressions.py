"""Phase 12 offline regressions: Overview Market State, allocation donut, positions header/refresh."""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from market_overview import overview


def _row(
    sym: str,
    change: object = 1.0,
    vol: object = 100.0,
    fx: object = 100_000.0,
    last: object = 100.0,
) -> dict:
    return {"symbol": sym, "last": last, "change24": change, "vol24": vol, "fx_usd": fx}


@pytest.mark.parametrize(
    "bad_change", [True, False, "", "   ", float("nan"), float("inf"), float("-inf"), "bad"]
)
def test_boolean_blank_or_nonfinite_change_never_counts_as_priced_flat_or_up(
    bad_change: object,
) -> None:
    rows = [
        _row("BTCIRT", 2.0, 100.0),
        _row("ETHIRT", -1.0, 100.0),
        _row("SOLIRT", 0.5, 100.0),
        _row("BADIRT", bad_change, 100.0),
    ]
    out = overview(rows)
    assert out["ok"] is True
    assert out["universe_size"] == 4
    assert out["priced"] == 3
    assert out["excluded"]["no_change"] == 1
    assert out["breadth"]["up"] == 2
    assert out["breadth"]["down"] == 1
    assert out["breadth"]["flat"] == 0


@pytest.mark.parametrize("bad_vol", [-100.0, True, False, "", "   ", float("nan"), float("inf")])
def test_negative_boolean_or_nonfinite_volume_cannot_enter_turnover_weights(
    bad_vol: object,
) -> None:
    rows = [
        _row("BTCIRT", 4.0, 200.0, fx=1.0),
        _row("ETHIRT", -2.0, 200.0, fx=1.0),
        _row("SOLIRT", 1.0, bad_vol, fx=1.0),
    ]
    out = overview(rows)
    assert out["ok"] is True
    assert out["priced"] == 3
    assert out["excluded"]["no_volume"] == 1
    assert out["excluded"]["no_fx"] == 1
    assert out["turnover"]["total_usd"] == pytest.approx(400.0)
    assert out["change"]["vol_weighted_24h"] == pytest.approx(1.0)
    bad_top = next(r for r in out["turnover"]["top"] if r["symbol"] == "SOLIRT")
    assert bad_top["vol_usd"] is None


def test_signal_mix_ignores_invalid_directions_and_out_of_range_or_boolean_composites() -> None:
    rows = [_row("BTCIRT", 2.0, 100.0), _row("ETHIRT", -1.0, 100.0), _row("SOLIRT", 1.0, 100.0)]
    signals = {
        "BTCIRT": {"direction": "LONG", "regime": "TREND", "composite": 80.0},
        "ETHIRT": {"direction": "BOGUS", "regime": "   ", "composite": True},
        "SOLIRT": {"direction": "NEUTRAL", "regime": "RANGE", "composite": 150.0},
    }
    out = overview(rows, signals=signals)
    assert out["signals"]["directions"] == {"LONG": 1, "NEUTRAL": 1}
    assert out["signals"]["regimes"] == {"TREND": 1, "RANGE": 1}
    assert out["signals"]["scored"] == 1
    assert out["signals"]["avg_composite"] == pytest.approx(80.0)


@pytest.mark.parametrize("bad_premium", [True, False, float("nan"), float("inf"), "bad"])
def test_nonfinite_or_boolean_premium_and_nonpositive_fx_last_normalize_to_none(
    bad_premium: object,
) -> None:
    rows = [
        _row("BTCIRT", 2.0, 100.0),
        _row("ETHIRT", -1.0, 100.0),
        _row("SOLIRT", 1.0, 100.0),
        _row("USDTIRT", 0.2, 500.0, last=0.0),
    ]
    out = overview(rows, premium_pct=bad_premium)  # type: ignore[arg-type]
    assert out["premium_pct"] is None
    assert out["fx"] == {"symbol": "USDTIRT", "last": None, "change24": pytest.approx(0.2)}


def test_route_reports_snapshot_as_of_distinct_from_response_time_and_stale_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    monkeypatch.setitem(
        main._cached,
        "universe",
        [
            {"symbol": "BTCIRT", "last": 9e9, "vol24": 8e11, "change24": 2.5},
            {"symbol": "ETHIRT", "last": 3e8, "vol24": 4e11, "change24": -1.5},
            {"symbol": "SOLIRT", "last": 1.5e7, "vol24": 6e11, "change24": 3.0},
            {"symbol": "USDTIRT", "last": 100_000.0, "vol24": 1e11, "change24": 0.1},
        ],
    )
    monkeypatch.setitem(main._cached, "universe_ts", now - 540.0)
    monkeypatch.setitem(main._cached, "premium_pct", 0.75)
    monkeypatch.setitem(
        main._cached,
        "signals_by_tf",
        {"15m": {"BTCIRT": {"direction": "LONG", "regime": "TREND", "composite": 72.0}}, "4h": {}},
    )
    monkeypatch.setitem(main._cached, "signals_ts", {"15m": now - 30.0})

    c = TestClient(main.app)
    r15 = c.get("/api/market/overview?tf=15m").json()
    assert r15["ok"] is True
    assert r15["tf"] == "15m"
    assert r15["as_of_basis"] == "universe_snapshot"
    expected_uni_iso = datetime.fromtimestamp(now - 540.0, UTC).isoformat()
    assert r15["as_of"] == expected_uni_iso
    assert r15["freshness"]["universe"]["as_of"] == expected_uni_iso
    assert r15["freshness"]["universe"]["age_s"] >= 539.0
    assert r15["freshness"]["universe"]["stale"] is True
    assert r15["freshness"]["signals"]["tf"] == "15m"
    assert r15["freshness"]["signals"]["available"] is True
    assert r15["freshness"]["signals"]["stale"] is False
    assert r15["freshness"]["response_generated_at"] != r15["as_of"]

    # Another timeframe without cached signals must not silently reuse 15m's signal mix.
    r4h = c.get("/api/market/overview?tf=4h").json()
    assert r4h["ok"] is True
    assert r4h["tf"] == "4h"
    assert "signals" not in r4h
    assert r4h["freshness"]["signals"]["tf"] == "4h"
    assert r4h["freshness"]["signals"]["available"] is False
    assert r4h["freshness"]["signals"]["stale"] is None


def test_route_does_not_convert_unknown_quote_currency_with_toman_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        main._cached,
        "universe",
        [
            {"symbol": "BTCIRT", "last": 9e9, "vol24": 100_000_000.0, "change24": 2.0},
            {"symbol": "ETHUSDT", "last": 3000.0, "vol24": 500.0, "change24": -1.0},
            {"symbol": "XRPBTC", "last": 0.00002, "vol24": 999_999_999.0, "change24": 4.0},
            {"symbol": "USDTIRT", "last": 100_000.0, "vol24": 1e11, "change24": 0.0},
        ],
    )
    monkeypatch.setitem(main._cached, "universe_ts", 0.0)
    d = TestClient(main.app).get("/api/market/overview?tf=15m").json()
    assert d["ok"] is True
    assert d["priced"] == 3
    assert d["excluded"]["no_fx"] == 1
    assert d["turnover"]["total_usd"] == pytest.approx(1500.0)
    assert d["as_of_basis"] == "response_generated_unverified_universe_ts"
    assert d["freshness"]["universe"]["as_of"] is None
    assert d["freshness"]["universe"]["stale"] is True


@pytest.mark.parametrize(
    "bad_last", [0.0, -10.0, None, False, True, "bad", float("nan"), float("inf")]
)
def test_route_refuses_missing_or_invalid_usdtirt_rate_and_includes_freshness(
    monkeypatch: pytest.MonkeyPatch, bad_last: object
) -> None:
    monkeypatch.setitem(
        main._cached,
        "universe",
        [
            {"symbol": "BTCIRT", "last": 9e9, "vol24": 1e11, "change24": 1.0},
            {"symbol": "ETHIRT", "last": 3e8, "vol24": 1e11, "change24": -1.0},
            {"symbol": "SOLIRT", "last": 1e7, "vol24": 1e11, "change24": 2.0},
            {"symbol": "USDTIRT", "last": bad_last, "vol24": 1e11, "change24": 0.1},
        ],
    )
    d = TestClient(main.app).get("/api/market/overview?tf=1h").json()
    assert d["ok"] is False
    assert d["tf"] == "1h"
    assert "toman rate" in d["reason"]
    assert "freshness" in d and d["freshness"]["signals"]["tf"] == "1h"


def test_route_rejects_unsupported_timeframe_query() -> None:
    r = TestClient(main.app).get("/api/market/overview?tf=5m")
    assert r.status_code == 422
