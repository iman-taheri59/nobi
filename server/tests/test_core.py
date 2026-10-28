# mypy: check_untyped_defs = False
# Test doubles stand in for objects on purpose; their bodies answer to pytest.
"""Core invariants for the Nobi backend.

These tests are intentionally dependency-light: they exercise the money/risk
math without touching the network. Run with:

    cd server && python -m pytest -q
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nobitex_client import ClientError, NobitexClient
from paper_trading import FEE_PCT, PaperBroker
from risk_engine import RiskConfig, RiskEngine


# --------------------------------------------------------------------------- #
# Risk engine
# --------------------------------------------------------------------------- #
def test_single_asset_cap_resizes_instead_of_warning():
    eng = RiskEngine(RiskConfig(equity=100_000))
    # A tiny ATR on a high price would otherwise produce a 37,500% notional.
    plan = eng.evaluate("XUSDT", "buy", entry=1000.0, atr=0.01)
    nominal_pct = plan.size_quote / 100_000 * 100
    assert plan.blocked is False
    assert nominal_pct <= 25.0001
    assert "single-asset-cap-resized" in plan.warnings


def test_heat_cap_shrinks_the_order():
    eng = RiskEngine(RiskConfig(equity=100_000, max_heat_pct=1.0))
    first = eng.evaluate("AUSDT", "buy", 100.0, 3.0)     # 0.75 % heat
    second = eng.evaluate("BUSDT", "buy", 100.0, 3.0)    # only 0.25 % left
    assert first.blocked is False and second.blocked is False
    assert second.risk_pct == pytest.approx(0.25, abs=0.01)
    assert second.size_quote < first.size_quote


def test_kill_switch_blocks_everything():
    eng = RiskEngine(RiskConfig(equity=100_000))
    eng.halted = True
    plan = eng.evaluate("BTCUSDT", "buy", 73_000, 500)
    assert plan.blocked is True
    assert plan.risk_pct == 0


def test_irt_sizing_uses_quote_currency_equity():
    """0.75 % of a 100k USD account is ~51.2M IRT, not 750 IRT."""
    eng = RiskEngine(RiskConfig(equity=100_000))
    usdt_irt = 68_250.0
    plan = eng.evaluate(
        "BTCIRT", "buy", entry=4_985_000_000, atr=87_000_000,
        equity_quote=100_000 * usdt_irt,
    )
    # risk in USD = notional * stop distance % / fx
    risk_usd = plan.size_quote * (abs(plan.entry - plan.stop) / plan.entry) / usdt_irt
    assert 600 < risk_usd < 900          # ~750 USD, not 0.31 USD
    assert plan.size_quote / usdt_irt < 25_000  # within the 25 % cap


# --------------------------------------------------------------------------- #
# Paper broker (fake exchange, no network)
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, price=73_040.0, usdt_irt=68_250.0):
        self.price = price
        self.usdt_irt = usdt_irt
        self.calls = 0

    async def get_orderbook(self, symbol):
        self.calls += 1
        p = self.price
        return {
            "status": "ok",
            "lastTradePrice": str(p),
            "bids": [[str(p * 0.999), "1000"]],
            "asks": [[str(p * 1.001), "1000"]],
        }

    async def get_market_stats(self):
        # `NobitexClient.get_market_stats` returns symbol-keyed stats in Toman;
        # the fake stands in for the client, so it must speak that dialect.
        return {"stats": {"USDTIRT": {"latest": str(self.usdt_irt), "last": str(self.usdt_irt)}}}


def run(coro):
    return asyncio.run(coro)


def make_broker(tmp_path, monkeypatch, equity=100_000, price=73_040.0):
    import paper_trading

    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    client = FakeClient(price=price)
    return PaperBroker(equity, client, RiskEngine(RiskConfig(equity=equity))), client


def test_paper_irt_order_consumes_usd_not_toman(tmp_path, monkeypatch):
    """Prices in the app are Toman; cash is USD. One order proves both.

    0.001 BTC at 498,500,000 Toman = 498,500 Toman; at USDTIRT 68,250 that is
    ~7.30 USD. Consuming 498,500 (Toman) or 4,985,000,000 (Rial) of a 100,000
    USD balance would have been the same bug with a different factor.
    """
    broker, _ = make_broker(tmp_path, monkeypatch)
    pos = run(broker.place_limit(
        "BTCIRT", "buy", 0.001, price=498_500_000,
        stop=480_975_523, tp1=519_529_371, source="TEST",
    ))
    assert pos is not None, broker.last_error
    assert pos.size_usd == pytest.approx(498_500 / 68_250, abs=0.01)
    assert broker.cash == pytest.approx(100_000 - pos.size_usd * (1 + FEE_PCT), abs=0.02)


def test_paper_rejects_oversized_single_asset(tmp_path, monkeypatch):
    broker, _ = make_broker(tmp_path, monkeypatch)
    pos = run(broker.place_limit("BTCUSDT", "buy", 1.0, price=73_040, stop=70_000))
    assert pos is None
    assert broker.last_error and broker.last_error.startswith("single-asset-cap")


def test_paper_rejects_excess_risk(tmp_path, monkeypatch):
    broker, _ = make_broker(tmp_path, monkeypatch)
    # 0.2 BTC (~14.6k USD) with a 10 % stop risks ~1.46 % of equity.
    pos = run(broker.place_limit("BTCUSDT", "buy", 0.2, price=73_040, stop=65_736))
    assert pos is None
    assert broker.last_error and broker.last_error.startswith("risk-cap")


def test_paper_rejects_when_cash_floor_breached(tmp_path, monkeypatch):
    broker, _ = make_broker(tmp_path, monkeypatch)
    broker.cash = 11_000  # above the 10 % floor only until fees
    pos = run(broker.place_limit("BTCUSDT", "buy", 0.03, price=73_040, stop=68_000))
    assert pos is None
    assert broker.last_error is not None
    assert broker.last_error.split(":")[0] in {"min-cash", "risk-cap", "insufficient-funds"}


def test_equity_marks_open_position_to_market(tmp_path, monkeypatch):
    broker, client = make_broker(tmp_path, monkeypatch)
    pos = run(broker.place_limit("BTCUSDT", "buy", 0.1, price=73_040, stop=69_000))
    assert pos is not None
    before = broker.equity
    client.price = 73_040 * 1.01
    run(broker.evaluate_positions())
    assert broker.equity > before
    assert broker.equity == pytest.approx(broker.cash + pos.filled_qty * client.price, abs=0.05)


# --------------------------------------------------------------------------- #
# HTTP client resilience
# --------------------------------------------------------------------------- #
def test_client_retries_then_raises_typed_error():
    attempts = {"n": 0}

    class C(NobitexClient):
        async def _request(self, method, path, **kw):
            attempts["n"] += 1
            raise ClientError("TIMEOUT", "boom")

    c = C()
    with pytest.raises(ClientError) as exc:
        run(c.get("/market/stats", attempts=3))
    assert attempts["n"] == 3
    assert exc.value.code == "TIMEOUT"
    assert exc.value.message == "boom"  # not the exception object itself


# --------------------------------------------------------------------------- #
# TA: vectorised series must equal the scalar implementation bar-by-bar
# --------------------------------------------------------------------------- #
def _random_walk(n: int, seed: int = 7, drift: float = 0.0004, vol: float = 0.009):
    import random

    from ta_engine import candles_to_df

    rng = random.Random(seed)
    px, t, rows = 100.0, 1_700_000_000, []
    for _ in range(n):
        px *= 1 + rng.gauss(drift, vol)
        o = px * (1 + rng.gauss(0, 0.002))
        h = max(o, px) * (1 + abs(rng.gauss(0, 0.003)))
        low = min(o, px) * (1 - abs(rng.gauss(0, 0.003)))
        rows.append({"t": t, "o": o, "h": h, "l": low, "c": px, "v": rng.uniform(50, 300)})
        t += 3600
    return candles_to_df(rows)


def test_ta_series_matches_scalar():
    from ta_engine import ta_score, ta_score_series

    df = _random_walk(260)
    series = ta_score_series(df)
    assert series[59] is None
    for i in range(60, len(df)):
        scalar = ta_score(df.iloc[: i + 1])
        vec = series[i]
        assert vec is not None
        assert scalar["score"] == vec["score"]
        assert scalar["direction"] == vec["direction"]
        assert scalar["regime"] == vec["regime"]
        assert scalar["components"] == vec["components"]
        assert scalar["risk"]["atr"] == pytest.approx(vec["risk"]["atr"], rel=1e-12)


# --------------------------------------------------------------------------- #
# Backtester mechanics
# --------------------------------------------------------------------------- #
def _frame(prices):
    from ta_engine import candles_to_df

    rows = []
    for i, p in enumerate(prices):
        rows.append({"t": 1_700_000_000 + i * 3600, "o": p, "h": p, "l": p, "c": p, "v": 100})
    return candles_to_df(rows)


def _long_signal(entry_atr=1.0, idx=30):
    return [None] * idx + [
        {
            "score": 80,
            "direction": "LONG",
            "regime": "TREND",
            "components": {},
            "risk": {"atr": entry_atr, "stopDistance": 2 * entry_atr, "stopPct": 2.0},
            "lastClose": 100.0,
        }
    ] + [None] * (80 - idx)


def test_stop_wins_when_one_candle_touches_both_levels():
    from backtest import EventBacktester

    # 30 flat bars, entry bar, then a candle spanning stop (98) and TP1 (103).
    prices = [100.0] * 30 + [100.0, 100.0, 100.0]
    df = _frame(prices)
    df.loc[31, "h"] = 110.0
    df.loc[31, "l"] = 90.0
    # Target arithmetic only: slippage is declared off so the assertion is about
    # where TP1/TP2 sit, not about the cost model. The default (6 bps) is
    # asserted in test_backtest_costs.py.
    bt = EventBacktester({"initial_equity": 100_000, "risk_pct": 1.0, "atr_stop_k": 2.0,
                          "tp1_r": 1.5, "tp2_r": 2.5, "slippage_bps": 0})
    res = bt.run_symbol(df, "XUSDT", "1h", signals=_long_signal(), start=30, end=33)
    assert res.trades == 1
    assert bt.closed[0].reason == "sl-ambiguous"
    assert bt.closed[0].ambiguous is True
    assert res.final_equity < 100_000


def test_tp1_takes_half_then_tp2_closes_the_rest():
    from backtest import EventBacktester

    df = _frame([100.0] * 33)
    df.loc[31, "h"] = 104.0   # >= tp1 (103), above stop
    df.loc[31, "l"] = 99.5
    df.loc[32, "h"] = 110.0   # >= tp2 (105)
    df.loc[32, "l"] = 100.5
    # Target arithmetic only — costs are asserted separately (test_backtest_costs).
    bt = EventBacktester({"initial_equity": 100_000, "risk_pct": 1.0, "atr_stop_k": 2.0,
                          "tp1_r": 1.5, "tp2_r": 2.5, "slippage_bps": 0})
    res = bt.run_symbol(df, "XUSDT", "1h", signals=_long_signal(), start=30, end=33)
    assert res.trades == 1
    tr = bt.closed[0]
    assert tr.tp1_done is True
    # 50 % closed at TP1 (103), the rest at TP2 (105); entry 100.
    assert tr.exit == pytest.approx(105.0)
    assert res.final_equity > 100_000


def test_initial_equity_is_respected():
    from backtest import EventBacktester

    df = _frame([100.0] * 33)
    df.loc[31, "h"] = 104.0
    df.loc[31, "l"] = 99.5
    bt = EventBacktester({"initial_equity": 12_345, "risk_pct": 1.0})
    res = bt.run_symbol(df, "XUSDT", "1h", signals=_long_signal(), start=30, end=33)
    assert res.initial_equity == pytest.approx(12_345)
    # The first point is already marked after paying the entry fee, so it is
    # slightly below the starting balance — but nowhere near the 100k default.
    assert 12_000 < res.equity_curve[0][1] < 12_345


# --------------------------------------------------------------------------- #
# Walk-forward: optimisation happens on train, numbers come from eval
# --------------------------------------------------------------------------- #
def test_walk_forward_is_out_of_sample_and_picks_params_on_train():
    from backtest import run_and_save

    rows = [
        {"t": 1_700_000_000 + i * 3600, "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "v": r["v"]}
        for i, r in enumerate(_random_walk(700, seed=3).to_dict("records"))
    ]
    rep = run_and_save("TUSDT", "1h", rows, {"initial_equity": 50_000}, mode="wf", auto_window=True)
    assert rep["aggregate"]["windows"] >= 3
    for w in rep["details"]:
        assert w["eval_start"] > w["train_end"]          # strictly after training
        assert set(w["params"]) == {"atr_stop_k", "tp1_r", "tp2_r"}
        assert w["oos"]["trades"] == w["trades"]          # flattened fields are OOS
    assert rep["aggregate"]["trades"] == sum(w["oos"]["trades"] for w in rep["details"])
    assert "is" in rep["details"][0] and "degradation" in rep


def test_gate_reports_insufficient_data_for_short_history():
    from backtest import run_and_save

    rows = [
        {"t": 1_700_000_000 + i * 3600, "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "v": r["v"]}
        for i, r in enumerate(_random_walk(320, seed=5).to_dict("records"))
    ]
    rep = run_and_save("TUSDT", "1h", rows, {"initial_equity": 50_000}, mode="wf", auto_window=True)
    g = rep["gate"]
    assert g["state"] in {"INSUFFICIENT_DATA", "FAIL"}
    if rep["aggregate"]["trades"] < 30:
        assert g["state"] == "INSUFFICIENT_DATA"
        assert g["bars_needed"] > rep["bars"]


def test_gate_does_not_pass_on_random_noise():
    """A promotion gate that random data can pass is worthless."""
    from backtest import run_and_save

    rows = [
        {"t": 1_700_000_000 + i * 3600, "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "v": r["v"]}
        for i, r in enumerate(_random_walk(1200, seed=11).to_dict("records"))
    ]
    rep = run_and_save("TUSDT", "1h", rows, {"initial_equity": 50_000}, mode="wf", auto_window=True)
    assert rep["gate"]["state"] != "PASS"


def test_gate_never_passes_on_synthetic_data():
    """Simulated candles may look perfect (the offline fallback is a seeded
    random walk), so the gate must refuse to certify them."""
    from backtest import run_and_save

    rows = [
        {"t": 1_700_000_000 + i * 3600, "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "v": r["v"]}
        for i, r in enumerate(_random_walk(700, seed=3).to_dict("records"))
    ]
    rep = run_and_save("TUSDT", "1h", rows, {"initial_equity": 50_000}, mode="wf",
                       auto_window=True, source="synthetic")
    g = rep["gate"]
    assert g["state"] == "SYNTHETIC_DATA"
    assert g["would_be"] in {"PASS", "FAIL", "INSUFFICIENT_DATA"}
    assert g["data_source"] == "synthetic"
    assert rep["source"] == "synthetic"


# --------------------------------------------------------------------------- #
# Sprint 3 — access control
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _isolate_journal_db(tmp_path, monkeypatch):
    """Every test gets its own journal DB.

    Control endpoints now write audit rows; without this they would land in the
    developer's real `journal.db` and pollute the dashboard's audit page.
    """
    import journal as J

    monkeypatch.setattr(J, "DB_PATH", tmp_path / "journal.db")
    yield


def _client():
    """A TestClient bound to the FastAPI app without triggering startup I/O."""
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app, raise_server_exceptions=False)


def test_mutations_are_loopback_only_without_a_token(monkeypatch):
    import security

    monkeypatch.setattr(security, "API_TOKEN", "")
    c = _client()
    # TestClient reports host "testclient", which counts as loopback.
    #
    # The target used to be /api/paper/halt, which left the global kill switch
    # armed for every later test in the session — a silent cross-test leak that
    # only showed up as "some other test says the account is halted". Any
    # guarded mutation exercises the same guard; pick one with no residue.
    r = c.post("/api/errors/clear")
    assert r.status_code == 401  # troubleshooting controls require an owner session


def test_remote_mutation_is_rejected_without_a_token(monkeypatch):
    import security

    monkeypatch.setattr(security, "API_TOKEN", "")
    c = _client()
    # Same reasoning as the test above: exercise the guard on a mutation that
    # leaves no residue. The handler is expected to reject this request; the
    # point is that the guard runs first.
    c.post("/api/errors/clear", headers={"x-forwarded-for": "8.8.8.8"},
           # spoof the peer address the app sees
           )
    # TestClient always reports 127.0.0.1/testclient; exercise the guard directly.
    from fastapi import HTTPException

    class FakeClient:
        host = "8.8.8.8"

    class FakeURL:
        path = "/api/paper/halt"

    class FakeReq:
        client = FakeClient()
        url = FakeURL()
        headers = {}

    with pytest.raises(HTTPException) as exc:
        asyncio.run(security.require_client(FakeReq()))
    assert exc.value.status_code == 403


def test_token_is_required_when_configured(monkeypatch):
    from fastapi import HTTPException

    import security

    monkeypatch.setattr(security, "API_TOKEN", "s3cret")

    class FakeURL:
        path = "/api/paper/order"

    class FakeReq:
        class client:
            host = "8.8.8.8"

        url = FakeURL()

        def __init__(self, headers):
            self.headers = headers

    with pytest.raises(HTTPException) as exc:
        asyncio.run(security.require_client(FakeReq({})))
    assert exc.value.status_code == 401

    # correct token passes, wrong token does not
    asyncio.run(security.require_client(FakeReq({"x-api-token": "s3cret"})))
    with pytest.raises(HTTPException):
        asyncio.run(security.require_client(FakeReq({"x-api-token": "nope"})))

    # bearer form is accepted too
    asyncio.run(security.require_client(FakeReq({"authorization": "Bearer s3cret"})))


def test_rate_limit_blocks_bursts(monkeypatch):
    from fastapi import HTTPException

    import security

    monkeypatch.setattr(security, "_RATE_LIMIT", 3)
    security.reset_rate_limits()

    class FakeURL:
        path = "/api/paper/order"

    class FakeReq:
        class client:
            host = "4.4.4.4"

        url = FakeURL()
        headers = {}

    monkeypatch.setattr(security, "API_TOKEN", "t")
    fake = FakeReq()
    fake.headers = {"x-api-token": "t"}
    for _ in range(3):
        asyncio.run(security.require_client(fake))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(security.require_client(fake))
    assert exc.value.status_code == 429
    security.reset_rate_limits()


def test_cors_is_not_wildcard_by_default(monkeypatch):
    import security

    monkeypatch.delenv("NBX_CORS_ORIGINS", raising=False)
    assert security.cors_origins() == []
    monkeypatch.setenv("NBX_CORS_ORIGINS", "https://desk.example.com, http://localhost:3000")
    assert security.cors_origins() == ["https://desk.example.com", "http://localhost:3000"]


# --------------------------------------------------------------------------- #
# Sprint 3 — simulated data must not leak into the cache
# --------------------------------------------------------------------------- #
def test_synthetic_candles_are_never_cached(tmp_path, monkeypatch):
    import candle_cache

    monkeypatch.setattr(candle_cache, "CACHE_ROOT", tmp_path)
    cache = candle_cache.CandleCache()

    rows = [{"t": 1_700_000_000 + i * 3600, "o": 1, "h": 2, "l": 1, "c": 1.5, "v": 10} for i in range(5)]
    cache.store("BTCIRT", "60", 0, 10, rows, synthetic=True)
    assert list(tmp_path.glob("*.parquet")) == []
    assert cache.stats()["synthetic_skipped"] == 1
    assert cache.load("BTCIRT", "60", 0, 10) is None

    cache.store("BTCIRT", "60", 0, 10, rows, synthetic=False)
    assert cache.load("BTCIRT", "60", 0, 10) is not None


def test_client_refuses_to_invent_candles_when_upstream_is_down(monkeypatch, tmp_path):
    """The old fallback generated 120 plausible candles and labelled them
    "synthetic". On screen that is indistinguishable from a real chart, so the
    client now refuses and the API says the exchange is unreachable."""
    import candle_cache
    import nobitex_client

    monkeypatch.setattr(candle_cache, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(candle_cache, "CANDLE_CACHE", candle_cache.CandleCache())
    monkeypatch.setattr(nobitex_client, "CANDLE_CACHE", candle_cache.CANDLE_CACHE)

    c = nobitex_client.NobitexClient()
    c._trip_circuit(60)  # force the "upstream is down" branch

    with pytest.raises(nobitex_client.UpstreamUnavailable):
        asyncio.run(c.get_candles_ex("BTCIRT", "1h", 120))
    # nothing was written, and nothing was invented
    assert list(tmp_path.glob("*.parquet")) == []


def test_upstream_endpoints_say_so_instead_of_returning_empty_lists():
    """An empty list reads as "no signals today"; the payload must name the wall."""

    client = _client()
    for path in ("/api/candles/BTCIRT?tf=1h", "/api/universe", "/api/signals?tf=15m"):
        body = client.get(path).json()
        if body.get("count", 0) or body.get("scored"):
            continue                     # a live/cached answer is fine too
        assert body["ok"] is False, path
        assert body["unavailable"] is True, path
        assert "نوبیتکس" in body["message"], path

    candles = client.get("/api/candles/BTCIRT?tf=1h").json()
    if candles.get("ok") is False:
        assert candles["rows"] == [] and candles["source"] == "unavailable"


def test_no_response_invents_prices():
    """No endpoint may return a fabricated number when the exchange is down."""

    body = _client().get("/api/candles/NOPEIRT?tf=1h").json()
    if body.get("ok") is False:
        assert body["rows"] == []
    # A symbol the exchange would never return must not gain rows from a generator.
    assert all(r.get("o") for r in body.get("rows") or [])


def test_legacy_synthetic_cache_file_is_rejected(tmp_path, monkeypatch):
    import json

    import candle_cache

    monkeypatch.setattr(candle_cache, "CACHE_ROOT", tmp_path)
    cache = candle_cache.CandleCache()
    fp = candle_cache._key("BTCIRT", "60", 0, 10)
    import pandas as pd

    pd.DataFrame([{"t": 1, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]).to_parquet(fp, index=False)
    fp.with_suffix(".meta.json").write_text(json.dumps({"synthetic": True}), encoding="utf-8")
    assert cache.load("BTCIRT", "60", 0, 10) is None


# --------------------------------------------------------------------------- #
# Sprint 3 — composite must not use a fabricated FA score
# --------------------------------------------------------------------------- #
def test_composite_without_fa_renormalises_weights():
    """Mirror of main._composite: no FA => TA and liquidity absorb its weight."""
    TA_W, FA_W, LIQ_W = 0.55, 0.25, 0.20

    def composite(ta, liq, fa):
        if fa is None:
            w = TA_W + LIQ_W
            return round((TA_W * ta + LIQ_W * liq) / w, 1), "ta+liq"
        return round(TA_W * ta + FA_W * fa + LIQ_W * liq, 1), "ta+fa+liq"

    with_fa, basis = composite(80, 70, 60)
    assert basis == "ta+fa+liq"
    assert with_fa == pytest.approx(0.55 * 80 + 0.25 * 60 + 0.20 * 70, abs=0.05)

    no_fa, basis2 = composite(80, 70, None)
    assert basis2 == "ta+liq"
    # A fabricated 50 would drag the score down; renormalising keeps it honest.
    assert no_fa == pytest.approx((0.55 * 80 + 0.20 * 70) / 0.75, abs=0.05)
    assert no_fa != composite(80, 70, 50)[0]


# --------------------------------------------------------------------------- #
# Sprint 3 — streaming must describe itself honestly
# --------------------------------------------------------------------------- #
def test_stream_status_reports_rest_transport_by_default():
    from ws_execution import StreamExecutor

    ex = StreamExecutor()
    st = ex.status()
    assert st["transport"] == "rest-poll"
    assert st["enabled"] is True
    assert st["ws_connected"] is False
    assert st["poll_interval_s"] >= 1


def test_book_freshness_and_mid():

    from ws_execution import HotBook

    b = HotBook(best_bid=100.0, best_ask=102.0, last_update=time.time())
    assert b.mid == pytest.approx(101.0)
    assert b.fresh is True
    b.last_update = time.time() - 60
    assert b.fresh is False


def test_tick_loop_exists_and_is_scheduled(monkeypatch):
    import main

    assert hasattr(main, "_position_tick_loop")
    assert main.TICK_S >= 1


def test_tick_loop_action_closes_a_breached_position(tmp_path, monkeypatch):
    """SL must fire from the scheduler, not only from a dashboard request."""
    import main

    broker, client = make_broker(tmp_path, monkeypatch, price=73_040.0)
    pos = run(broker.place_limit("BTCUSDT", "buy", 0.1, price=73_040, stop=70_000))
    assert pos is not None
    assert pos.state != "CLOSED"

    # Price falls through the stop; no HTTP request is made to trigger evaluation.
    client.price = 69_500.0
    closed = run(main.tick_once(broker))
    assert len(closed) == 1
    assert closed[0].state == "CLOSED"
    assert closed[0].close_reason in {"sl-hit", "sl"}
    assert broker.risk.open_heat == pytest.approx(0.0, abs=1e-9)


def test_tick_once_is_a_noop_without_positions(tmp_path, monkeypatch):
    import main

    broker, _ = make_broker(tmp_path, monkeypatch)
    assert run(main.tick_once(broker)) == []


def test_halt_then_resume_round_trip(monkeypatch):
    """Halt must be sticky, documented, and reversible via an explicit call."""
    import security

    monkeypatch.setattr(security, "API_TOKEN", "")
    c = _client()
    if True:
        halted = c.post("/api/paper/halt").json()
        assert halted["halted"] is True and halted["resumed"] is False
        # The response must state what actually happened, not what we wish did.
        assert halted["audit_id"] is not None
        assert halted["telegram"]["sent"] is False
        assert halted["telegram"]["reason"] == "not-configured"
        state = c.get("/api/risk/state").json()
        assert state["halted"] is True

        block = c.post("/api/paper/order", json={"symbol": "BTCUSDT", "side": "buy", "qty": 0.01}).json()
        assert block.get("ok") is False

        resumed = c.post("/api/paper/resume").json()
        assert resumed["halted"] is False and resumed["resumed"] is True
        assert c.get("/api/risk/state").json()["halted"] is False
        again = c.post("/api/paper/resume").json()
        assert again["halted"] is False and again["resumed"] is False


def test_fa_result_marks_profile_fallback_as_not_fresh():
    from fa_engine import FAResult

    live = FAResult(score=70.0, evidence={"profile": "live"}, fresh=True)
    fb = FAResult(score=50.0, evidence={"profile": "fallback"}, fresh=False)
    assert live.fresh and live.evidence["profile"] == "live"
    assert not fb.fresh and fb.evidence["profile"] == "fallback"


def test_risk_preview_does_not_reserve_heat():
    from risk_engine import RiskConfig, RiskEngine

    r = RiskEngine(RiskConfig(equity=100_000.0))
    before = r.open_heat
    plan = r.evaluate("BTCUSDT", "buy", 73_040.0, 500.0, "15m", preview=True)
    assert plan.action == "open"
    assert r.open_heat == before

    real = r.evaluate("BTCUSDT", "buy", 73_040.0, 500.0, "15m")
    assert r.open_heat == pytest.approx(before + real.risk_pct)


def test_signal_lookup_prefers_requested_timeframe():
    import main

    main._cached["signals"] = {"BTCUSDT": {"lastClose": 1.0, "tag": "15m"}}
    main._cached["signals_by_tf"] = {"1h": {"BTCUSDT": {"lastClose": 2.0, "tag": "1h"}}}
    assert main._signal_lookup("btcusdt", "1h")["tag"] == "1h"
    assert main._signal_lookup("BTCUSDT", "4h")["tag"] == "15m"   # fallback
    assert main._signal_lookup("NOPE", "1h") is None


def test_paper_order_accepts_size_quote_alone(monkeypatch, tmp_path):
    import main

    monkeypatch.setattr(main, "_cached", {"signals": {"BTCUSDT": {"lastClose": 73_040.0}},
                                          "signals_by_tf": {}, "universe": []})
    broker, _ = make_broker(tmp_path, monkeypatch, price=73_040.0)
    monkeypatch.setattr(main, "get_broker", lambda *a, **k: broker)

    c = _client()
    r = c.post("/api/paper/order", json={"symbol": "BTCUSDT", "side": "buy", "size_quote": 500.0,
                                         "stop": 70_000})
    body = r.json()
    assert r.status_code == 200, body
    assert body.get("ok") is True, body
    # Amounts are truncated to the market's precision (BTC: 6 decimals), so the
    # filled quantity is 500/73,040 floored at 1e-6 — not the exact quotient.
    assert body["position"]["qty"] == pytest.approx(0.006845, abs=1e-9)
    # size_usd includes the taker fee, so compare the notional with a small band.
    assert body["position"]["size_usd"] == pytest.approx(500.0, abs=2.0)


def test_max_risk_pct_caps_a_misconfigured_sizing():
    from risk_engine import RiskConfig, RiskEngine

    r = RiskEngine(RiskConfig(equity=100_000.0, risk_pct=5.0, max_risk_pct=2.0))
    plan = r.evaluate("BTCUSDT", "buy", 73_040.0, 500.0, "15m", preview=True)
    assert plan.risk_budget_pct == pytest.approx(2.0)
    actual = abs(plan.entry - plan.stop) * plan.size_quote / plan.entry / 100000 * 100
    assert plan.risk_pct == pytest.approx(actual, abs=1e-6)
    assert plan.risk_pct <= plan.risk_budget_pct
    assert "risk-pct-clamped" in plan.warnings


# --------------------------------------------------------------------------- #
# Audit / export / notifier honesty (ADR-0011 §5, ADR-0012 §2.6)
# --------------------------------------------------------------------------- #
def test_audit_endpoint_records_control_actions(monkeypatch, tmp_path):
    import auth

    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    auth.create_user("audit-operator", "A-strong-operator-password!", role="operator")
    c = _client()
    login = c.post("/api/auth/login", json={
        "username": "audit-operator", "password": "A-strong-operator-password!"})
    csrf = login.cookies.get("nbx_csrf") or c.cookies.get("nbx_csrf") or ""
    c.post("/api/paper/halt", headers={"X-CSRF-Token": csrf})
    try:
        body = c.get("/api/audit?limit=10").json()
    finally:
        # This test needs a real control action, so it halts. Leaving it armed
        # poisoned every later test in the session.
        c.post("/api/paper/resume", headers={"X-CSRF-Token": csrf})
    assert body["counts"].get("paper.halt") == 1
    event = body["events"][0]
    assert event["action"] == "paper.halt"
    assert event["result"] == "ok"
    # TestClient reports "testclient" as the peer; the column must be populated.
    assert event["client"] == "testclient"


def test_notify_status_reports_disabled_not_sent(monkeypatch, tmp_path):
    """Operators can see honest delivery state without seeing credentials."""
    import auth

    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    auth.create_user("notify-operator", "A-strong-operator-password!", role="operator")
    client = _client()
    client.post("/api/auth/login", json={
        "username": "notify-operator", "password": "A-strong-operator-password!"})
    body = client.get("/api/notify/status").json()
    assert body["configured"] is False
    assert body["transport"] == "disabled"
    assert body["sent"] == 0


def test_export_endpoint_sets_headers_and_never_paginates():
    import journal as J

    c = _client()
    conn = J.connect()
    try:
        for i in range(150):
            conn.execute(
                """INSERT INTO signal_log(ts, symbol, timeframe, direction, composite,
                                          source, origin, config_hash)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (1_700_000_000 + i * 900, "BTCIRT", "15m", "LONG", 60.0,
                 "synthetic", "backfill", "abc"))
        conn.commit()
    finally:
        conn.close()

    r = c.get("/api/journal/export?format=csv")
    assert r.status_code == 200
    assert r.text.split("\n")[0] == ",".join(J.EXPORT_COLUMNS)
    assert len(r.text.strip().split("\n")) == 151          # header + 150 rows
    assert r.headers["x-rows"] == "150"
    assert "attachment" in r.headers["content-disposition"]

    j = c.get("/api/journal/export?format=json").json()
    assert j["rows"] == 150 and j["data"][0]["symbol"] == "BTCIRT"

    # Filters apply, and an empty result is still a valid file.
    assert c.get("/api/journal/export?tf=1h").text.strip().count("\n") == 0


def test_signal_detail_endpoint_returns_levels_and_path(monkeypatch):
    """The row detail must survive a candle outage: 200 with an honest reason."""
    import journal as J
    import main

    conn = J.connect()
    try:
        conn.execute(
            """INSERT INTO signal_log(ts, symbol, timeframe, direction, composite,
                                      entry, stop, tp1, tp2, atr, source, origin,
                                      config_hash, features)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (1_700_000_000, "BTCIRT", "15m", "LONG", 71.0, 100.0, 98.0, 103.0,
             106.0, 1.0, "synthetic", "backfill", "abc",
             '{"components":{"trend":100,"volume":40}}'))
        sid = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        conn.execute(
            """INSERT INTO signal_outcomes(signal_id, horizon_key, resolved_at,
                                           return_pct, r_multiple, hit_tp1, hit_sl)
               VALUES (?,?,?,?,?,?,?)""", (sid, "H2", 1_700_086_400, 1.0, 0.8, 1, 0))
        conn.commit()
    finally:
        conn.close()

    async def fake_candles(symbol, tf="15m", count=260):
        # 30 bars of context must exist *before* the entry bar, which opens at
        # ts - one step; otherwise signal_path clamps and the index shifts.
        base = (1_700_000_000 - 900) - 30 * 900
        return ([{"t": base + i * 900, "o": 100.0, "h": 101.0, "l": 99.0,
                  "c": 100.5, "v": 5.0} for i in range(60)], "synthetic")

    monkeypatch.setattr(main.client, "get_candles_ex", fake_candles)
    c = _client()
    r = c.get(f"/api/journal/signal/{sid}")
    assert r.status_code == 200
    d = r.json()
    assert d["signal"]["symbol"] == "BTCIRT"
    assert d["signal"]["components"]["trend"] == 100
    assert d["outcomes"]["H2"]["r_multiple"] == 0.8
    assert d["path_reason"] == "ok" and d["candle_source"] == "synthetic"
    # Simulated candles are regenerated per request, so the path is not the one
    # that produced the outcome: the API must say it is not usable for drawing.
    assert d["path_usable"] is False
    assert d["entry_index"] == 30

    # Unknown id is a 404, not an empty 200.
    assert c.get("/api/journal/signal/999999").status_code == 404


def test_signal_detail_marks_live_path_as_usable(monkeypatch):
    import journal as J
    import main

    conn = J.connect()
    try:
        conn.execute(
            """INSERT INTO signal_log(ts, symbol, timeframe, direction, composite,
                                      entry, stop, tp1, tp2, atr, source, origin)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (1_700_000_000, "ETHIRT", "15m", "LONG", 66.0, 100.0, 98.0, 103.0,
             106.0, 1.0, "live", "live"))
        sid = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        conn.commit()
    finally:
        conn.close()

    async def fake_candles(symbol, tf="15m", count=260):
        base = (1_700_000_000 - 900) - 30 * 900
        return ([{"t": base + i * 900, "o": 100.0, "h": 101.0, "l": 99.0,
                  "c": 100.5, "v": 5.0} for i in range(60)], "live")

    monkeypatch.setattr(main.client, "get_candles_ex", fake_candles)
    d = _client().get(f"/api/journal/signal/{sid}").json()
    assert d["candle_source"] == "live" and d["path_usable"] is True


# --------------------------------------------------------------------------- #
# Settings page must not be able to lie (ADR-0011 page 7)
# --------------------------------------------------------------------------- #
def test_published_weights_reproduce_the_score_the_engine_computes():
    """Tie the *published* weights to the *actual* arithmetic.

    Asserting `snapshot == ta_engine.COMPONENT_WEIGHTS` would be a tautology —
    both sides are one object. The real risk is someone editing the formula in
    `ta_score` and leaving the constants behind, so this recomputes the score
    from the constants the settings page publishes and compares it to what the
    engine returned for the same bar.
    """

    import numpy as np

    import settings_view
    import ta_engine
    from ta_engine import candles_to_df

    rng = np.random.default_rng(11)
    n = 400
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    rows = [{"t": 1_700_000_000 + i * 900, "o": float(closes[i]),
             "h": float(closes[i] * 1.004), "l": float(closes[i] * 0.996),
             "c": float(closes[i]), "v": float(rng.uniform(1, 5))} for i in range(n)]
    out = ta_engine.ta_score(candles_to_df(rows))

    snap = settings_view.snapshot()
    weights = snap["weights"]["ta_components"]["values"]
    raw = sum(weights[k] * out["components"][k] for k in weights)
    gate = snap["thresholds"]["range_gate"]["value"] if out["regime"] == "RANGE" else 1.0
    reported = raw * gate

    assert abs(reported - out["score"]) < 0.2, (reported, out["score"], out["components"])


def test_published_composite_weights_reproduce_the_composite():
    import settings_view

    snap = settings_view.snapshot()
    w = snap["weights"]["composite"]["values"]
    import main

    composite, basis = main.composite_score(ta=70.0, liq=40.0, fa=60.0)
    assert basis == "ta+fa+liq"
    assert abs(composite - (w["ta"] * 70 + w["fa"] * 60 + w["liq"] * 40)) < 0.06

    composite2, basis2 = main.composite_score(ta=70.0, liq=40.0, fa=None)
    assert basis2 == "ta+liq"
    assert abs(composite2 - (w["ta"] * 70 + w["liq"] * 40) / (w["ta"] + w["liq"])) < 0.06


def test_js_mirror_uses_the_same_weights_as_python():
    """`nbx-core.js` duplicates the weights; the settings page only reads Python.

    A drift between the two engines would make the dashboard's own scores
    disagree with the journal's, and no Python test would notice.
    """
    import re
    from pathlib import Path

    import settings_view
    import ta_engine

    js = (Path(__file__).resolve().parents[2] / "nbx-core.js").read_text(encoding="utf-8")
    snap = settings_view.snapshot()
    w = snap["weights"]["ta_components"]["values"]

    m = re.search(
        r"([\d.]+) \* comp\.trend \+ ([\d.]+) \* comp\.momentum \+ "
        r"([\d.]+) \* comp\.volume \+ ([\d.]+) \* comp\.structure", js)
    assert m, "the JS weight expression moved; update this guard"
    assert [float(x) for x in m.groups()] == [w["trend"], w["momentum"], w["volume"], w["structure"]]

    # 1.0.16 made the two bars *names* on both sides (so a caller can ask "what
    # if the bar were different?"), which means the guard has to compare values
    # rather than rediscover literals in an expression.
    d = re.search(r"const LONG_SCORE_MIN = ([\d.]+);\s*\n\s*const NEUTRAL_SCORE_MIN = ([\d.]+);", js)
    assert d, "the JS direction thresholds moved; update this guard"
    assert float(d.group(1)) == ta_engine.LONG_SCORE_MIN
    assert float(d.group(2)) == ta_engine.NEUTRAL_SCORE_MIN
    assert re.search(r"score >= longMin \? 'LONG' : score >= neutralMin \?", js), \
        "the JS label no longer uses the named bars"

    # The browser engine now runs the "no fundamentals" branch of the same
    # formula (TA + liquidity, renormalised). The FA term must not reappear with
    # a fabricated score; check the renormalised expression instead.
    c = re.search(r"\(([\d.]+) \* s\.score \+ ([\d.]+) \* liq\) / ([\d.]+)", js)
    assert c, "the JS composite expression moved; update this guard"
    cw = snap["weights"]["composite"]["values"]
    ta_w, liq_w = cw["ta"], cw["liq"]
    got = [float(x) for x in c.groups()[:2]]
    # The JS keeps the published weights and divides by their sum, so the
    # coefficients must equal ta/liq and the divisor must equal ta+liq.
    assert abs(got[0] - ta_w) < 1e-9 and abs(got[1] - liq_w) < 1e-9, (
        f"JS composite weights {got} drift from python ({ta_w}, {liq_w})")
    assert abs(float(c.group(3)) - (ta_w + liq_w)) < 1e-6, "JS divisor must be the weight sum"
    assert not re.search(r"\* fa \+", js), "the FA term came back with no FA source"


def test_settings_weights_actually_sum_to_one():
    import settings_view

    snap = settings_view.snapshot()
    assert abs(sum(snap["weights"]["ta_components"]["values"].values()) - 1.0) < 1e-9
    assert abs(sum(snap["weights"]["composite"]["values"].values()) - 1.0) < 1e-9
    assert abs(sum(snap["weights"]["fa_subweights"]["values"].values()) - 1.0) < 1e-9


def test_settings_never_renders_a_secret(monkeypatch):
    import importlib

    import settings_view

    monkeypatch.setenv("NBX_API_TOKEN", "sk-live-supersecret-value-1234")
    importlib.reload(settings_view)
    body = str(settings_view.snapshot())
    assert "supersecret" not in body
    token = next(s for s in settings_view.snapshot()["secrets"] if s["name"] == "NBX_API_TOKEN")
    assert token["present"] is True and token["hint"].endswith("1234")
    importlib.reload(settings_view)


def test_env_file_is_loaded_without_overriding_the_real_environment(tmp_path, monkeypatch):
    """`.env.example` told users to create `.env`; nothing ever read it.

    A token placed there was silently ignored and the UI said "not configured".
    The loader must fill gaps but never win against a real env var.
    """
    import _env

    f = tmp_path / ".env"
    f.write_text(
        "NBX_TELEGRAM_TOKEN=from-file # comment\n"
        "NBX_ENV_ONLY_TEST=applied\n"
        "QUOTED='two words'\n"
        "# a comment\n"
        "export EXPORTED=yes\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NBX_TELEGRAM_TOKEN", "from-shell")
    monkeypatch.delenv("NBX_ENV_ONLY_TEST", raising=False)

    st = _env.load(f)

    assert st["loaded"] is True and st["exists"] is True and st["error"] is None
    assert __import__("os").environ["NBX_TELEGRAM_TOKEN"] == "from-shell", "file must not override shell"
    assert __import__("os").environ["NBX_ENV_ONLY_TEST"] == "applied"
    assert __import__("os").environ["QUOTED"] == "two words"
    assert __import__("os").environ["EXPORTED"] == "yes"
    assert st["vars"] == 3, "only the three variables the shell did not already define"


def test_missing_env_file_is_reported_not_fatal(tmp_path):
    import _env

    st = _env.load(tmp_path / "nope.env")
    assert st["exists"] is False and st["loaded"] is False and st["error"] is None


def test_settings_reports_the_env_file_and_never_its_absolute_path(tmp_path, monkeypatch):
    import settings_view

    monkeypatch.setattr(settings_view, "env_file", lambda: {"path": "server/.env",
                                                            "exists": True, "loaded": True,
                                                            "vars": 2, "error": None})
    snap = settings_view.snapshot()
    assert snap["env"]["path"] == "server/.env"
    rel = snap["env"]["path"].removeprefix("server/").strip("/")
    assert "/" not in rel, f"the env path is not relative to the repo: {snap['env']['path']}"
    for s in snap["secrets"]:
        assert s["where"] == "server/.env"


def test_telegram_probe_never_returns_the_token():
    """The probe answers yes/no; a rejection must not be an httpx URL echo."""
    import main

    src = __import__("inspect").getsource(main.settings_telegram_test)
    assert "str(exc)" not in src, "an exception string can contain the request URL, which contains the token"
    assert "type(exc).__name__" in src
    assert "getMe" in src
    assert 'body.get("description")' in src


def test_a_token_bearing_request_never_writes_the_token_to_the_log(caplog):
    """`httpx` logs full URLs at INFO; the Telegram token is part of the URL.

    This is a leak that never touches the browser: it goes to stdout, journald
    or the container's log, where it outlives the process.
    """
    import logging

    from logsafe import quiet_httpx, redact

    secret = "123456789:AAFakeTokenThatMustNotAppear"
    caplog.set_level(logging.INFO)

    with quiet_httpx():
        logging.getLogger("httpx").info("HTTP Request: GET https://api.telegram.org/bot%s/getMe", secret)

    assert secret not in caplog.text
    # and the logger's level is restored afterwards
    assert logging.getLogger("httpx").level == logging.NOTSET or \
        logging.getLogger("httpx").isEnabledFor(logging.INFO)

    assert redact(f"https://api.telegram.org/bot{secret}/getMe") == \
        "https://api.telegram.org/bot***/getMe"


def test_notify_does_not_leak_the_token_through_exception_text(monkeypatch):
    """A transport error's message may embed the URL, so only its type is kept."""
    import inspect

    import notify

    monkeypatch.setattr(notify, "TOKEN", "t0ken")
    monkeypatch.setattr(notify, "CHAT_ID", "-100")

    src = inspect.getsource(notify.send)
    assert "quiet_httpx()" in src, "telegram send must run with httpx logging quieted"
    assert "type(e).__name__" in src
    assert 'last_error=str(e)' not in src


def test_root_serves_a_self_contained_page():
    """`/` must not depend on subresources.

    A sandboxed preview iframe, a phone opening the file from a download
    folder and an air-gapped host all block subresource requests. When `/`
    served `index.html`, the browser received the markup and *nothing else*:
    no stylesheet, no JavaScript, no charts. The page must arrive complete.
    """
    body = _client().get("/").text

    assert "<!DOCTYPE html>" in body
    # stylesheets are inlined, not linked
    assert 'href="assets/css/' not in body, "a local stylesheet is still linked"
    # scripts are inlined, not linked
    assert 'src="nbx-core.js"' not in body and 'src="nbx-app.js"' not in body
    # the compiled Tailwind utilities travel with the page (no CDN)
    assert "@layer utilities" in body or ".flex{display:flex}" in body
    assert "cdn.jsdelivr.net/npm/@tailwindcss" not in body
    # enough to actually render something
    assert len(body) > 200_000


def test_root_page_needs_no_external_hosts():
    """External <script src> is a hard dependency; a *stylesheet* link is not.

    The only remaining external references may be the two optional `<script>`
    tags for the chart library and the icon set — both have a local fallback
    (`lightweight-charts` shim + the inline OFFLINE_ICONS table), and both carry
    an onerror hook. Anything else, especially a stylesheet, would be fatal.
    """
    import re

    body = _client().get("/").text
    assert "fonts.googleapis.com" not in body
    assert "cdn.jsdelivr.net" not in body, "a stylesheet still points at a CDN"

    allowed = ("lightweight-charts", "lucide")
    external = re.findall(r'<script[^>]+src="https://([^/"]+)', body)
    for host in external:
        assert host in ("unpkg.com",), f"unexpected external script host {host}"
    for tag in re.findall(r'<script[^>]+src="https://unpkg[^"]+"[^>]*>', body):
        assert any(a in tag for a in allowed), f"unknown external script {tag[:80]}"
        assert "onerror=" in tag, "an external script must declare a fallback"


def test_root_never_serves_the_un_inlined_page_when_the_build_cannot_run():
    """Stale-but-complete beats fresh-but-empty.

    `build_standalone.py` refuses to run when `tailwind.css` is older than the
    markup (new classes would be missing). If that refusal fell back to
    `index.html`, the browser would get markup with no CSS and no JS — the
    exact failure this whole path exists to prevent. It must keep serving the
    last good self-contained build.
    """
    import os

    import main

    page = main._self_contained_page()
    assert page.name == "nobitex-mobile.html"

    src = main.REPO_ROOT / "nbx-app.js"
    now = src.stat().st_mtime
    try:
        os.utime(src, (now + 5, now + 5))          # makes the build refuse
        page = main._self_contained_page()
        assert page.name == "nobitex-mobile.html", "fell back to the un-inlined index.html"
        body = page.read_text(encoding="utf-8")
        assert 'href="assets/css/' not in body
    finally:
        os.utime(src, (now, now))


# --------------------------------------------------------------------------- #
# Error log
# --------------------------------------------------------------------------- #
def test_errorlog_never_stores_a_secret():
    """Tokens travel in URLs, headers and exception text. All three are scrubbed."""
    import errorlog

    # Split so the literal never matches a bot-token scanner: this fixture is
    # synthetic and must not raise a secret-scanning alarm on every push.
    token = "123456789:" + "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    for raw in (
        f"GET https://api.telegram.org/bot{token}/getMe failed",
        "Authorization: Bearer sk-live-supersecret-1234",
        "NBX_API_TOKEN=sk-live-supersecret-1234 rejected",
        f"token was {token}",
    ):
        cleaned = errorlog.scrub(raw)
        assert "AAHdqTcv" not in cleaned and "supersecret" not in cleaned, cleaned
        assert "[redacted]" in cleaned


def test_errorlog_captures_logging_and_survives_a_failing_store(monkeypatch):
    import logging

    import errorlog

    errorlog.clear()
    recorded = []
    real_capture = errorlog.capture

    def spy(**kw):
        recorded.append(kw)
        return real_capture(**kw)

    monkeypatch.setattr(errorlog, "capture", spy)
    h = errorlog.Handler()
    h.emit(logging.LogRecord("nbx-app.test", logging.ERROR, __file__, 1,
                             "upstream exploded", None, None))
    assert recorded and recorded[0]["message"] == "upstream exploded"

    # A broken database must not propagate: the log is diagnostic, not load-bearing.
    import journal
    monkeypatch.setattr(journal, "connect", lambda *a, **k: (_ for _ in ()).throw(OSError("db gone")))
    row = errorlog.capture(source="test", message="still works")
    assert row["message"] == "still works"
    assert errorlog.recent(limit=5), "the in-memory ring must still answer"


def test_errorlog_endpoints_are_wired_and_bounded(monkeypatch, tmp_path):
    import auth

    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    auth.create_user("error-owner", "A-strong-owner-password!", role="owner")
    client = _client()
    login = client.post("/api/auth/login", json={
        "username": "error-owner", "password": "A-strong-owner-password!"})
    csrf = login.cookies.get("nbx_csrf") or client.cookies.get("nbx_csrf") or ""
    r = client.get("/api/errors?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"errors", "n", "counts", "filters"}
    assert isinstance(body["errors"], list) and body["n"] <= 5

    # a bad level is rejected rather than silently ignored
    assert client.get("/api/errors?level=nonsense").status_code == 422

    st = client.post("/api/errors/selftest", headers={"X-CSRF-Token": csrf}).json()
    assert st["readable"] is True and st["total_after"] >= st["total_before"]

    reported = client.post("/api/errors", json={"message": "chart threw",
                                                "source": "js", "stack": "at x"},
                           headers={"X-CSRF-Token": csrf}).json()
    assert reported["ok"] and reported["id"]
    names = {e["source"] for e in client.get("/api/errors?limit=50").json()["errors"]}
    assert any(n.startswith("client.") for n in names)


def test_grid_failure_is_logged_even_though_the_endpoint_catches_it():
    """`return {"ok": False}` is not an exception, so the global handler never
    saw it: the button showed a red toast and the log stayed empty."""
    import errorlog

    before = {row["id"] for row in errorlog.recent(limit=50)}
    r = _client().post("/api/backtest/grid",
                       json={"symbol": "BTCIRT", "tf": "1h", "bars": "oops"})
    assert r.status_code == 200, "rejections stay 200 so the UI can render them"
    body = r.json()
    assert body["ok"] is False and body.get("log_id")

    rows = {row["id"]: row for row in errorlog.recent(limit=50)}
    assert body["log_id"] in rows, "the returned id must point at a real row"
    assert body["log_id"] not in before, "this failure must have written a new row"
    row = rows[body["log_id"]]
    assert row["level"] == "error" and row["source"] == "api.backtest.grid"
    assert "BTCIRT" in row["message"]
    assert row["detail"], "the traceback belongs in the row, not only on stdout"


def test_logging_bridge_keeps_warnings_visible():
    """Upstream timeouts are warnings on the server and failures to the user;
    with an ERROR-only bridge they never reached the dashboard."""
    import logging

    import errorlog

    errorlog.install()
    logging.getLogger("upstream.test").warning("nobitex timed out for BTCIRT")
    rows = [r for r in errorlog.recent(limit=30, q="nobitex timed out") if r["source"] == "upstream.test"]
    assert rows, "a warning must land in the log"
    assert rows[0]["level"] == "warning"


def test_unhandled_exception_is_logged_and_returns_the_log_id():
    """The browser gets a 500 *and* a pointer to the row that explains it."""
    import main

    @main.app.get("/api/__boom__")
    async def _boom():                      # pragma: no cover - raised on purpose
        raise RuntimeError("deliberate failure for the error-log test")

    try:
        r = _client().get("/api/__boom__")
        assert r.status_code == 500
        body = r.json()
        assert body["log_id"] and body["error"] == "internal"
        import errorlog
        rows = errorlog.recent(limit=20)
        assert any(row["id"] == body["log_id"] for row in rows)
        assert any("deliberate failure" in (row["detail"] or "") or
                   "deliberate failure" in row["message"] for row in rows)
    finally:
        main.app.router.routes = [rt for rt in main.app.router.routes
                                  if getattr(rt, "path", "") != "/api/__boom__"]


def test_settings_endpoint_is_read_only(monkeypatch, tmp_path):
    import auth

    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    auth.create_user("settings-owner", "A-strong-owner-password!", role="owner")
    client = _client()
    client.post("/api/auth/login", json={
        "username": "settings-owner", "password": "A-strong-owner-password!"})
    body = client.get("/api/settings").json()
    assert body["read_only"] is True
    assert body["weights"]["ta_components"]["source"].startswith("ta_engine.")
    assert isinstance(body["flags"], list) and body["flags"]
    # There is no mutating counterpart: the router must not expose one.
    import main

    routes = {(r.path, m) for r in main.app.routes for m in getattr(r, "methods", set())}
    assert ("/api/settings", "GET") in routes

    # The invariant is "no endpoint here writes configuration", not "only GET
    # exists". The single POST is the Telegram probe: it cannot mutate anything,
    # it is guarded by owner+CSRF, and it never sends a message.
    writes = {(p, m) for (p, m) in routes
              if p.startswith("/api/settings") and m in {"PUT", "PATCH", "DELETE"}}
    assert not writes, f"settings must expose no write verb, found {writes}"
    posts = {p for (p, m) in routes if p.startswith("/api/settings") and m == "POST"}
    assert posts == {"/api/settings/telegram/test"}, posts


# --------------------------------------------------------------------------
# No fabricated market data may live in the shipped UI (2026-10-20)
# --------------------------------------------------------------------------
def _ui_sources():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    return {name: (root / name).read_text(encoding="utf-8")
            for name in ("index.html", "nbx-core.js", "nbx-app.js")}


def test_no_synthetic_generators_survive_in_the_ui():
    """A price, candle or equity curve must come from the exchange or not exist.

    The project shipped several helpers that produced convincing numbers out of
    nothing (`symPx`, `genWalk`, `SEED_MARKETS`, `synthCandlesFor`). Each was
    removed by hand; this guard makes the removal durable.
    """
    banned = ("symPx", "genWalk", "SEED_MARKETS", "synthCandlesFor",
              "FA_PROFILES", "function rand(")
    for name, src in _ui_sources().items():
        for token in banned:
            assert token not in src, f"{token} came back in {name}"


def test_desk_ticket_has_no_baked_prices():
    src = _ui_sources()["index.html"]
    # The manual ticket starts empty and is filled from window.livePrice().
    assert "const manual = { sym:'BTCIRT', side:'buy', price:null" in src
    assert "window.livePrice" in src or "livePx(" in src
    # A hard-coded 4985000000 (or any 4e9+ literal) would be a quoted BTC price.
    import re

    for m in re.finditer(r"\b(\d{9,})\b", src):
        assert int(m.group(1)) < 4_000_000_000 or int(m.group(1)) in {451, 429}, m.group()


def test_missing_data_is_always_explained_in_the_ui():
    ui = _ui_sources()
    src = ui["index.html"] + ui["nbx-app.js"]
    # Every empty panel must carry the sentence, not just an em-dash.
    assert "up.short" in ui["index.html"]
    assert "data-unavailable" in src
    # ... and the sandboxed-preview crash on localStorage must stay guarded.
    assert "localStorage.getItem" in ui["nbx-core.js"]
    import re

    block = re.search(r"apiToken: \(function \(\) \{(.*?)\}\)\(\)", ui["nbx-core.js"], re.S)
    assert block and "catch" in block.group(1), "localStorage read must not throw in a sandbox"


# --------------------------------------------------------------------------
# A regression list for values that were *fabricated* and then removed.
# The layout test can only see a browser; these strings live in the markup.
# --------------------------------------------------------------------------
def test_known_fabricated_values_stay_deleted():
    """Every entry here was a real, shipped lie found by reading the UI.

    Hard-coded market numbers are indistinguishable from real ones on screen —
    `ATR(14) = 1.9%` looked exactly like a computed ATR. A denylist is ugly but
    it is the only thing that survives a refactor, and each entry carries the
    reason it must not return.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    src = "\n".join((root / n).read_text(encoding="utf-8")
                    for n in ("index.html", "nbx-app.js", "nbx-core.js"))
    banned = {
        "'تکنیکال (۷۸)'": "fabricated TA score for candidate #1",
        "'فاندامنتال (۶۴)'": "fabricated FA score for candidate #1",
        "'نقدشوندگی (۵۵)'": "fabricated liquidity score for candidate #1",
        "ADX=۲۹": "fabricated ADX reading",
        "%b = ۰٫۷۴": "fabricated Bollinger %b",
        "'۱ آنلاک بزرگ نزدیک": "fabricated unlock/regulatory claims",
        "ATR(14) · BTCIRT 15m</div><div class=\"num font-bold\">1.9%":
            "hard-coded ATR percentage",
        "v.replace('{v}', fmtNum(124))": "fake API latency in the header",
        "genWalk": "random-walk equity curve",
        "symPx": "hard-coded price table for the manual ticket",
        "SEED_MARKETS": "seed market table",
        "synthCandlesFor": "synthetic candle generator",
    }
    found = [(token, why) for token, why in banned.items() if token in src]
    assert not found, "fabricated value(s) came back: " + "; ".join(
        f"{t} ({w})" for t, w in found)


def test_every_data_panel_can_explain_itself():
    """`data-unavailable` is the contract the layout test looks for.

    A panel that can be empty with no error branch is a bug waiting for a
    network outage; this asserts the eight panels that need live exchange data
    all have one.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    html = (root / "index.html").read_text(encoding="utf-8")
    app = (root / "nbx-app.js").read_text(encoding="utf-8")
    ui = html + app
    # Places that show exchange-derived values:
    for panel, marker in {
        "candle chart": "candleUp",
        "allocation donut": "allocUnavailable",
        "order book": "obUnavailable",
        "signals list": "data-unavailable",
        "fundamentals": "faUnavailableHTML",
        "backtest slot": "btUnavailableHTML",
        "equity curve": "drawEquity",
        "empty table rows": "emptyRow",
    }.items():
        assert marker in ui, f"{panel}: no unavailable branch ({marker})"


# --------------------------------------------------------------------------
# The live-data probe: its decision table is the thing that has to be right,
# because nobody can run the "exchange is up" half in CI.
# --------------------------------------------------------------------------
def _probe():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "probe_live.py"
    spec = importlib.util.spec_from_file_location("probe_live", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_probe_judges_both_upstream_states():
    """A number shown while the exchange is down is a failure, not a pass."""
    p = _probe()

    # (kind, verdict, live) -> should it pass?
    cases = [
        # exchange up
        ("market", "DATA", True, True),
        ("market", "EXPLAINED", True, False),      # data was there, UI dropped it
        ("market", "BLANK", True, False),
        ("server", "DATA", True, True),
        ("config", "TEXT", True, True),
        ("optional", "EXPLAINED", True, False),     # leftover outage banner
        ("optional", "EMPTY", True, True),          # "no trade has closed yet"
        ("server", "EMPTY", True, True),
        ("market", "EMPTY", True, False),           # says nothing useful
        # exchange down
        ("market", "EXPLAINED", False, True),
        ("market", "DATA", False, False),          # fabricated / stale and unlabelled
        ("market", "EMPTY", False, False),         # hides the outage
        ("server", "EMPTY", False, True),
        ("market", "BLANK", False, False),
        ("server", "DATA", False, True),           # the paper book needs no exchange
        ("optional", "BLANK", False, False),
        ("market", "MISSING", False, False),
    ]
    for kind, verdict, live, want in cases:
        got, why = p.judge(kind, verdict, live)
        assert got is want, f"{kind}/{verdict} live={live}: got {got} ({why})"


def test_probe_covers_every_tab_and_every_panel_is_reachable():
    p = _probe()
    assert set(p.PANELS) == {"overview", "signals", "fa", "orders", "performance",
                             "desk", "risk", "backtest", "errors", "settings"}
    # an empty panel list would make the probe pass by doing nothing
    assert sum(len(v) for v in p.PANELS.values()) >= 35
    for tab, panels in p.PANELS.items():
        for pid, kind, label in panels:
            assert kind in {"market", "server", "config", "optional"}, (tab, pid, kind)
            assert pid and label


def test_probe_parses_persian_numbers():
    p = _probe()
    assert p.as_number("۸۸٬۴۱۲٬۰۰۰") == 88_412_000
    assert p.as_number("−۱٫۰۰R") == -1.0     # U+2212 minus, used in the R column
    assert p.as_number("+۰٫۰۰%") == 0.0
    assert p.as_number("—") is None


def test_probe_classifier_separates_empty_state_from_outage():
    """'No trade has closed yet' must not be read as 'the exchange is down'.

    The two used to be the same verdict, which is how a stale outage banner
    survived a recovery and how "Nobitex unreachable" ended up printed over an
    empty desk table that had nothing to do with the exchange.
    """
    p = _probe()

    def rec(txt, digits=None, **kw):
        import re
        d = len(re.findall(r"[0-9\u06F0-\u06F9]", txt)) if digits is None else digits
        base = {"visible": True, "txt": txt, "digits": d, "len": len(txt),
                "dashOnly": bool(txt) and bool(re.match(r"^[\s—–\-\u2212٪%/۰-۹]+$", txt)),
                "un": 0, "inCard": 0, "tabUn": 0, "drawn": False}
        base.update(kw)
        return base

    assert p.classify(rec("هیچ خطایی ثبت نشده است"), "server")[0] == "EMPTY"
    assert p.classify(rec("سرور نوبیتکس در دسترس نیست"), "market")[0] == "EXPLAINED"
    assert p.classify(rec("بدون داده"), "optional")[0] == "EMPTY"
    # a number is a number even with a single digit
    assert p.classify(rec("۰"), "server")[0] == "DATA"
    # a chart canvas with pixels is data; an em-dash is not
    assert p.classify(rec("", drawn=True), "market")[0] == "DATA"
    assert p.classify(rec("—"), "market")[0] == "BLANK"
    # an outage marker still wins over a drawn canvas (empty chart with axes)
    assert p.classify(rec("", drawn=True, un=1), "market")[0] == "EXPLAINED"
    # a hidden marker is not a reader-visible claim
    assert p.classify(rec("۸۸٬۴۱۲", un=0, inCard=0), "market")[0] == "DATA"


# --------------------------------------------------------------------------- #
# Order book route (the gap that left the depth panel apologising forever)
# --------------------------------------------------------------------------- #
def test_book_levels_accepts_both_shapes_and_drops_junk():
    import main

    raw = [[100, 2], ["101.5", "0.5"], [0, 5], [-1, 2], ["x", 1], [102]]
    out = main._book_levels(raw, 10)
    assert out == [{"p": 100.0, "q": 2.0}, {"p": 101.5, "q": 0.5}]

    # dict rows are the other shape the API has used
    assert main._book_levels([{"price": 5, "amount": 1}], 3) == [{"p": 5.0, "q": 1.0}]
    # the limit is honoured
    assert len(main._book_levels([[i + 1, 1] for i in range(50)], 4)) == 4


def test_orderbook_route_computes_spread_depth_and_imbalance(monkeypatch):
    """The panel shows numbers, so the numbers have to agree with the rows."""
    import asyncio

    import main

    book = {
        "status": "ok",
        "lastUpdate": 1700000000000,
        "bids": [[100.0, 2.0], [99.5, 1.0], [90.0, 5.0]],
        "asks": [[101.0, 1.0], [101.5, 3.0]],
    }

    async def fake(symbol):
        return book

    monkeypatch.setattr(main.client, "get_orderbook", fake)
    out = asyncio.run(main.orderbook("BTCIRT", 8))

    assert out["ok"] is True
    assert out["symbol"] == "BTCIRT"
    assert out["best_bid"] == 100.0 and out["best_ask"] == 101.0
    assert out["mid"] == 100.5
    assert round(out["spread"], 6) == 1.0
    assert round(out["spread_pct"], 4) == round(1.0 / 100.5 * 100, 4)
    # ±1% of mid = ±1.005 around 100.5: 99.5 and 101.5 are inside, 90.0 is not.
    assert out["bid_depth"] == 100.0 * 2.0 + 99.5 * 1.0
    assert out["ask_depth"] == 101.0 * 1.0 + 101.5 * 3.0
    assert round(out["imbalance"], 2) == round(
        (out["bid_depth"] - out["ask_depth"]) / (out["bid_depth"] + out["ask_depth"]) * 100, 2)
    assert out["levels"] == 5


def test_orderbook_route_reports_unavailable_instead_of_raising(monkeypatch):
    """A 500 would render as a generic error; the UI contract reads the flag."""
    import asyncio

    import main
    from nobitex_client import UpstreamUnavailable

    async def boom(symbol):
        raise UpstreamUnavailable("circuit breaker is open")

    monkeypatch.setattr(main.client, "get_orderbook", boom)
    out = asyncio.run(main.orderbook("BTCIRT", 8))
    assert out["ok"] is False
    assert out["unavailable"] is True
    assert out["bids"] == [] and out["asks"] == []
    assert out["message"]          # the sentence the UI shows


def test_orderbook_route_treats_an_empty_book_as_unavailable(monkeypatch):
    import asyncio

    import main

    async def empty(symbol):
        return {"status": "ok", "bids": [], "asks": []}

    monkeypatch.setattr(main.client, "get_orderbook", empty)
    out = asyncio.run(main.orderbook("BTCIRT", 8))
    assert out["ok"] is False and out["unavailable"] is True


# --------------------------------------------------------------------------- #
# Fundamental vetoes have to change the outcome, not just decorate it
# --------------------------------------------------------------------------- #
def test_veto_caps_a_long_below_the_promotion_threshold():
    import main
    from ta_engine import LONG_SCORE_MIN

    score, direction, meta = main.apply_vetoes(91.0, "LONG", ["premium-stretched"])
    assert score < LONG_SCORE_MIN, "a capped score must not still qualify as LONG"
    assert direction == "NEUTRAL"
    assert meta["uncapped_composite"] == 91.0
    assert meta["uncapped_direction"] == "LONG"
    assert meta["veto_cap"] == main.VETO_CAP


def test_severe_veto_goes_further_than_a_mild_one():
    import main

    mild, d_mild, _ = main.apply_vetoes(90.0, "LONG", ["premium-stretched"])
    severe, d_severe, meta = main.apply_vetoes(90.0, "LONG", ["thin-tvl"])
    assert severe < mild
    assert d_severe == "EXIT" and d_mild == "NEUTRAL"
    assert meta["veto_severe"] == ["thin-tvl"]


def test_no_vetoes_leaves_the_score_alone():
    import main

    assert main.apply_vetoes(72.5, "LONG", []) == (72.5, "LONG", {})


def test_veto_never_raises_a_score():
    """Capping is a ceiling: a weak symbol must not be pushed up to it."""
    import main

    score, direction, _ = main.apply_vetoes(12.0, "EXIT", ["thin-tvl"])
    assert score == 12.0 and direction == "EXIT"


def test_fa_route_reports_the_fundamental_source_not_the_exchange(monkeypatch):
    """`source` on /api/fa used to be Nobitex provenance, so a live CoinGecko
    read came back as source=unavailable while profile=live.

    The doubles go through `monkeypatch`, not straight onto the module singleton:
    assigning `main.FA_ENGINE.score` left the fake installed for every test that
    ran afterwards, and a later test on an unmapped symbol was then told it was
    `live` (1.1.3, ADR-0025). A test that leaks its double makes the *next* test
    lie, which is worse than the thing it was written to catch.
    """
    import asyncio

    import main
    from fa_engine import FAResult

    async def fake_score(symbol, **kw):
        return FAResult(score=70.0, market=70, tokenomics=70, onchain=70,
                        sentiment=70, macro=70, vetoes=[], evidence={"profile": "live"},
                        fresh=True)

    async def fake_fallback(symbol, **kw):
        return FAResult(score=64.0, market=60, tokenomics=60, onchain=60,
                        sentiment=60, macro=60, vetoes=[], evidence={"profile": "fallback"},
                        fresh=False)

    monkeypatch.setattr(main.FA_ENGINE, "score", fake_score)
    live = asyncio.run(main.fa("BTC"))
    assert live["source"] == "live" and live["profile"] == "live"

    monkeypatch.setattr(main.FA_ENGINE, "score", fake_fallback)
    fb = asyncio.run(main.fa("BTC"))
    assert fb["source"] == "fallback", "a hard-coded profile must never be labelled live"

    async def fake_cached(symbol, **kw):
        return FAResult(score=71.0, market=70, tokenomics=70, onchain=70,
                        sentiment=70, macro=70, vetoes=[], evidence={"profile": "live"},
                        fresh=False, cached=True)

    monkeypatch.setattr(main.FA_ENGINE, "score", fake_cached)
    cached = asyncio.run(main.fa("BTC"))
    assert cached["source"] == "cached", "a cached live read is not a static profile"
    assert cached["profile"] == "live"


def test_journal_row_keeps_the_veto_explanation():
    """A row that stores NEUTRAL must also store why it is not LONG."""
    import journal

    snap = {
        "symbol": "BTCIRT", "tf": "15m", "direction": "NEUTRAL", "direction_raw": "LONG",
        "score": 80.0, "composite": 67.0, "lastClose": 100.0,
        "risk": {"atr": 2.0}, "fa_score": 60.0, "liquidity_score": 55.0,
        "fa": {"vetoes": ["premium-stretched"]},
        "veto_cap": 67.0, "veto_severe": [], "uncapped_composite": 80.0,
    }
    row = journal.make_row(snap, bar_ts=1_700_000_000)
    assert row is not None
    assert row.direction == "NEUTRAL"
    assert row.features["direction_raw"] == "LONG"
    assert row.features["vetoes"] == ["premium-stretched"]
    assert row.features["veto_cap"] == 67.0
    assert row.features["uncapped_composite"] == 80.0


def test_premium_veto_can_actually_fire():
    """Passing a literal 0 made `premium-stretched` unreachable in production.

    This drives FAEngine.score through an injected provider so the rule is
    exercised, not just present.
    """
    import asyncio

    import fa_engine

    eng = fa_engine.FAEngine()

    async def overview(_cg_id):
        return {"market_data": {"market_cap": {"usd": 1e12}, "fully_diluted_valuation": {"usd": 1e12},
                                "total_volume": {"usd": 1e11}, "total_supply": 1, "max_supply": 1},
                "genesis_date": "2009-01-03"}

    async def llama():
        return [{"name": "bitcoin", "symbol": "BTC", "tvl": 5e9}]

    async def fng():
        return {"fg_value": 55}

    eng._cg_overview = overview
    eng._llama_protocols = llama
    eng._listen_prices = fng
    fa_engine.FA_CACHE.clear()

    calm = asyncio.run(eng.score("BTC", usdt_premium_dev=0.01))
    hot = asyncio.run(eng.score("BTC", usdt_premium_dev=0.09))
    assert "premium-stretched" not in calm.vetoes
    assert "premium-stretched" in hot.vetoes
    assert hot.macro < calm.macro, "a stretched premium must lower the macro score"


def test_failed_build_reports_the_reason_not_just_the_exit_code():
    """The runtime rebuild used to log "exit status 1" and nothing else."""
    import subprocess

    import main

    exc = subprocess.CalledProcessError(1, ["python", "build.py"],
                                        stderr=b"assets/css/tailwind.css is older than the markup it styles.\nRun: npm run css\n")
    msg = main._short_proc_error(exc)
    assert "npm run css" in msg
    assert msg.startswith("Run:")


def test_implausible_premium_is_treated_as_missing_data():
    """±50 % is a broken quote, not a premium; it must not reach the veto."""
    import main

    rows = [{"symbol": "BTCIRT", "last": 100.0}, {"symbol": "BTCUSDT", "last": 1.0},
            {"symbol": "USDTIRT", "last": 100.0}]
    assert main._premium_pct(rows) == 0.0
    rows[2]["last"] = 40.0          # -60 % implied premium: a data error
    assert main._premium_pct(rows) is None


# --------------------------------------------------------------------------- #
# What-if (A2): the preview must agree with the executor
# --------------------------------------------------------------------------- #
def _pf(**kw):
    import risk_engine
    base = {"equity": 100_000.0, "cash": 100_000.0, "open_heat": 0.0,
            "open_positions": 0, "concurrent_limit": 5, "max_heat_pct": 6.0,
            "min_cash_pct": 10.0, "max_single_asset_pct": 25.0, "risk_pct_cap": 0.75,
            "halted": False, "notional_usd": 10_000.0, "fee_usd": 20.0, "risk_usd": 750.0}
    base.update(kw)
    return risk_engine.preflight(**base)


def test_whatif_projects_heat_cash_and_worst_case():
    r = _pf(open_heat=1.5, open_positions=2)
    s = r.summary()
    assert s["heat_before"] == 1.5
    assert s["heat_after"] == 2.25          # +0.75 % risk
    assert s["heat_headroom_pct"] == 3.75   # 6.0 - 2.25
    assert s["cash_after"] == 89_980.0
    assert s["slots_left"] == 2
    # Worst case is the stop distance *plus* the fee, not the stop alone.
    assert s["loss_at_stop_usd"] == 770.0
    assert not s["blocked"]


def test_whatif_blocks_on_the_same_gates_as_the_broker():
    assert _pf(open_heat=5.5).summary()["blocked_by"] == "heat-cap"
    assert _pf(open_positions=5).summary()["blocked_by"] == "concurrent-cap"
    assert _pf(halted=True).summary()["blocked_by"] == "halted"
    assert _pf(risk_usd=900.0).summary()["blocked_by"] == "risk-cap"
    # An empty account is its own gate, not a generic "not enough funds".
    assert _pf(cash=0.0).summary()["blocked_by"] == "cash-present"
    # 25 % of equity is the ceiling for one position
    assert _pf(notional_usd=26_000.0).summary()["blocked_by"] == "single-asset-cap"


def test_preflight_allows_the_broker_s_five_percent_slack():
    """place_limit accepts risk_pct * 1.05; the preview must not be stricter."""
    assert not _pf(risk_usd=780.0).summary()["blocked"]        # 0.78 % vs 0.75*1.05
    assert _pf(risk_usd=800.0).summary()["blocked_by"] == "risk-cap"


def test_a_rejected_order_reports_heat_it_would_have_added():
    s = _pf(open_heat=5.9).summary()
    assert s["blocked_by"] == "heat-cap"
    assert s["heat_after"] == 6.65, "the panel must show the overshoot it refused"
    assert s["heat_headroom_pct"] == 0.0


def test_whatif_route_reports_a_real_projection():
    """End-to-end through the route, with the signal cache primed."""
    from fastapi.testclient import TestClient

    import main

    sym = "BTCIRT"
    main._cached["signals_by_tf"].setdefault("15m", {})[sym] = {
        "symbol": sym, "lastClose": 4_000_000_000.0,
        "risk": {"atr": 40_000_000.0},
    }
    main._cached["universe"] = [{"symbol": "USDTIRT", "last": 100_000.0}]
    # A snapshot carries the time it was taken; without one it is not a rate
    # (`main._fx` dates its fallback by this, not by "now").
    main._cached["universe_ts"] = time.time()

    c = TestClient(main.app)
    r = c.post("/api/risk/whatif", json={"symbol": sym, "side": "buy", "tf": "15m"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True, d
    p = d["projection"]
    assert {"heat_before", "heat_after", "loss_at_stop_usd", "gates"} <= set(p)
    ids = {g["id"] for g in p["gates"]}
    # The two exchange-rule gates are part of the projection, not just the broker.
    assert {"halted", "concurrent-cap", "cash-present", "single-asset-cap",
            "min-notional", "market-closed"} <= ids
    # The suggested size must itself pass the gates it was sized against.
    if d["sized_by"] == "risk-engine" and not p["blocked"]:
        assert p["risk_pct"] <= 0.75 * 1.05 + 1e-9
    # And the stop really is two ATR away.
    assert abs(d["stop"] - (d["entry"] - 2 * 40_000_000.0)) < 1e-6


def test_whatif_refuses_to_guess_a_price():
    from fastapi.testclient import TestClient

    import main

    c = TestClient(main.app)
    r = c.post("/api/risk/whatif", json={"symbol": "NOPRICEIRT"})
    d = r.json()
    assert d["ok"] is False and "unavailable" in str(d).lower()


def test_whatif_does_not_move_the_account():
    """A projection that reserves heat is a silent trade."""
    from fastapi.testclient import TestClient

    import main

    sym = "ETHIRT"
    main._cached["signals_by_tf"].setdefault("15m", {})[sym] = {
        "symbol": sym, "lastClose": 3e11, "risk": {"atr": 3e9},
    }
    main._cached["universe"] = [{"symbol": "USDTIRT", "last": 100_000.0}]
    # A snapshot carries the time it was taken; without one it is not a rate
    # (`main._fx` dates its fallback by this, not by "now").
    main._cached["universe_ts"] = time.time()
    from paper_trading import get_broker
    br = get_broker(main.EQUITY, main.client, main.risk)
    heat_before = main.risk.open_heat
    cash_before = br.cash
    open_before = len(br.positions)
    TestClient(main.app).post("/api/risk/whatif", json={"symbol": sym})
    assert main.risk.open_heat == heat_before
    assert br.cash == cash_before
    assert len(br.positions) == open_before


def test_whatif_names_the_kill_switch_instead_of_blaming_sizing():
    from fastapi.testclient import TestClient

    import main

    main._cached["signals_by_tf"].setdefault("15m", {})["SOLIRT"] = {
        "symbol": "SOLIRT", "lastClose": 1e10, "risk": {"atr": 1e8}}
    main.risk.halted = True
    try:
        d = TestClient(main.app).post("/api/risk/whatif", json={"symbol": "SOLIRT"}).json()
    finally:
        main.risk.halted = False
    assert d["ok"] is False
    assert d["blocked_by"] == "halted", d
