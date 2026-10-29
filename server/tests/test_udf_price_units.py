# mypy: check_untyped_defs = False
"""REST UDF IRT prices are already Toman; orderbook prices are Rial.

Regression fixture: DOGEIRT 15m candle open 1793185200, fetched from the
public UDF endpoint on 2026-10-28. Production approval showed 2165.52,
while the upstream close was 21655.2. Never infer units from price magnitude.
"""

from __future__ import annotations

import pytest

from units import scale_orderbook, scale_udf


@pytest.fixture(autouse=True)
def clean_units(monkeypatch):
    monkeypatch.delenv("NBX_PRICE_UNIT", raising=False)
    monkeypatch.delenv("NBX_UDF_PRICE_UNIT", raising=False)


def udf():
    return {"s": "ok", "t": [1793185200], "o": [21733.2],
            "h": [21754.8], "l": [21646.1], "c": [21655.2], "v": [42109.47731935]}


def test_captured_udf_candle_is_not_divided_again():
    raw = udf()
    out = scale_udf(raw, "DOGEIRT")
    assert out["c"] == [21655.2]
    assert out["o"] == [21733.2]
    assert out["v"] == raw["v"]
    assert out["t"] == raw["t"]
    assert raw["c"] == [21655.2]


def test_book_and_udf_have_separate_source_units(monkeypatch):
    monkeypatch.setenv("NBX_PRICE_UNIT", "rial")
    book = scale_orderbook({"bids": [["216550", "1"]],
                            "asks": [["216560", "2"]]}, "DOGEIRT")
    assert book["bids"][0][0] == 21655
    assert scale_udf(udf(), "DOGEIRT")["c"] == [21655.2]


def test_rial_udf_source_can_be_overridden_without_changing_book(monkeypatch):
    monkeypatch.setenv("NBX_UDF_PRICE_UNIT", "rial")
    assert scale_udf({"c": [216552]}, "DOGEIRT")["c"] == pytest.approx([21655.2])
    assert scale_orderbook({"bids": [["216550", "1"]]}, "DOGEIRT")["bids"][0][0] == 21655


def test_explicit_unit_wins_over_udf_environment(monkeypatch):
    monkeypatch.setenv("NBX_UDF_PRICE_UNIT", "rial")
    assert scale_udf(udf(), "DOGEIRT", unit="toman")["c"] == [21655.2]


def test_usdt_candles_do_not_change(monkeypatch):
    monkeypatch.setenv("NBX_UDF_PRICE_UNIT", "rial")
    raw = {"c": [0.08], "v": [100]}
    assert scale_udf(raw, "DOGEUSDT") == raw


def test_invalid_udf_unit_fails_closed(monkeypatch):
    monkeypatch.setenv("NBX_UDF_PRICE_UNIT", "unknown")
    with pytest.raises(ValueError, match="NBX_UDF_PRICE_UNIT"):
        scale_udf(udf(), "DOGEIRT")


def test_legacy_price_cache_is_not_replayed_or_deleted(tmp_path, monkeypatch):
    import hashlib

    import candle_cache

    monkeypatch.setattr(candle_cache, "CACHE_ROOT", tmp_path)
    old_hash = hashlib.md5(b"DOGEIRT-15-0-10").hexdigest()[:16]
    legacy = tmp_path / f"DOGEIRT-15-{old_hash}.parquet"
    legacy.write_bytes(b"legacy rows with ambiguous price units")
    assert candle_cache._key("DOGEIRT", "15", 0, 10) != legacy
    assert candle_cache.CandleCache().load("DOGEIRT", "15", 0, 10) is None
    assert legacy.read_bytes() == b"legacy rows with ambiguous price units"


def test_cache_namespace_tracks_udf_override_not_orderbook_unit(monkeypatch):
    import candle_cache

    normal = candle_cache._key("DOGEIRT", "15", 0, 10)
    monkeypatch.setenv("NBX_PRICE_UNIT", "toman")
    assert candle_cache._key("DOGEIRT", "15", 0, 10) == normal
    monkeypatch.setenv("NBX_UDF_PRICE_UNIT", "rial")
    assert candle_cache._key("DOGEIRT", "15", 0, 10) != normal


def test_captured_book_rejects_wrong_units_and_rechecks_correct_unit_rr(tmp_path, monkeypatch):
    """Offline broker replay, NOT a production order or historical reconstruction.

    Book fetched at lastUpdate1793187248465, later than the user's plan.
    This proves the mechanism with captured depth, not exact historical fills.
    """
    import asyncio
    import time

    import paper_trading
    from risk_engine import RiskConfig, RiskEngine

    asks = [[217310, 2668.1], [217438, 251], [217469, 998], [217548, 1637.1],
            [217700, 951.4], [217779, 500], [217820, 1271.7], [217957, 339.8],
            [217960, 392], [218000, 9092.8], [218047, 2586.6], [218100, 60],
            [218200, 85], [218250, 4], [218490, 4], [218500, 124.6],
            [218518, 30], [218580, 9.2], [218700, 30], [218750, 4],
            [218762, 5], [218800, 378.3], [218850, 40], [218960, 73.8]]
    book = scale_orderbook({"bids": [[217300, 7092.1]], "asks": asks}, "DOGEIRT")

    class CapturedFeed:
        async def get_orderbook(self, symbol):
            assert symbol == "DOGEIRT"
            return book

    monkeypatch.setattr(paper_trading, "hot_mid_hook", None)
    monkeypatch.setattr(paper_trading, "market_closed_hook", None)
    for old_scale in (True, False):
        monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / f"paper-{old_scale}.json")
        engine = RiskEngine(RiskConfig(equity=100000))
        broker = paper_trading.PaperBroker(100000, CapturedFeed(), engine)
        broker.usdt_irt = 270700
        broker.usdt_irt_at = time.time()
        entry = scale_udf(udf(), "DOGEIRT", unit="rial" if old_scale else "toman")["c"][0]
        # Preserve the relative stop distance of the captured plan.
        atr = 9.045 if old_scale else 90.45
        plan = engine.evaluate("DOGEIRT", "buy", entry, atr,
                               equity_quote=27070000000, preview=True)
        pos = asyncio.run(broker.place_limit("DOGEIRT", "buy", plan.size_quote / entry,
                                            stop=plan.stop, tp1=plan.tp1, tp2=plan.tp2,
                                            source="QUEUE"))
        # Correct units alone do not make this captured market fill safe:
        # walking the thin book reduces the original TP2 R:R below policy.
        # Keep the captured book and original targets unchanged, and reject.
        assert pos is None
        assert not broker.positions
        assert broker.cash == 100000
        event = broker.orders[-1]
        assert event.price == 21798
        if old_scale:
            assert broker.last_error.startswith("execution-plan-invalid:")
            assert plan.tp1 < event.price
        else:
            assert broker.last_error.startswith("execution-rr-too-low:")
            assert plan.tp1 > event.price
            assert event.extra["rr"] < 1.5
