# mypy: check_untyped_defs = False
"""No browser clock, cache fallback, replay or late approval can revive a bar."""

from __future__ import annotations

import asyncio
import copy

import pytest
from fastapi.testclient import TestClient

import main
import paper_trading
import queue_policy
from exec_mode import ModeStore
from money import Rate
from paper_trading import PaperBroker
from risk_engine import RiskConfig, RiskEngine

BAR = 1793171700.0  # 2026-10-28 07:15 UTC
SIGNAL = {
    "symbol": "BTCUSDT",
    "lastClose": 100.0,
    "direction": "LONG",
    "composite": 80,
    "risk": {"atr": 2.0},
}


@pytest.mark.parametrize("tf,step", queue_policy.TF_SECONDS.items())
def test_deadline_is_one_candle_after_close_not_preview_creation(tf, step):
    first = queue_policy.describe("BTCUSDT", tf, SIGNAL, BAR, now=BAR + step + 1)
    second = queue_policy.describe("BTCUSDT", tf, SIGNAL, BAR, now=BAR + 2 * step - 1)
    assert first["ok"] and second["ok"]
    assert first["expires_at"] == second["expires_at"] == BAR + 2 * step
    assert first["signal_key"] == second["signal_key"]
    assert (
        queue_policy.describe("BTCUSDT", tf, SIGNAL, BAR, now=BAR + 2 * step)["error"]
        == "queue-signal-expired"
    )


@pytest.mark.parametrize("bar", [None, 0, -1, float("nan"), float("inf"), BAR * 1000])
def test_unusable_clock_never_gets_an_unlimited_deadline(bar):
    assert not queue_policy.describe("BTCUSDT", "15m", SIGNAL, bar, now=BAR + 1000)["ok"]


def test_open_candle_and_non_long_and_invalid_values_fail_closed():
    assert (
        queue_policy.describe("BTCUSDT", "15m", SIGNAL, BAR, now=BAR + 800)["error"]
        == "queue-signal-candle-open"
    )
    assert not queue_policy.describe(
        "BTCUSDT", "15m", {**SIGNAL, "direction": "EXIT"}, BAR, now=BAR + 1000
    )["ok"]
    for value in [None, float("nan"), float("inf"), -1]:
        assert not queue_policy.describe(
            "BTCUSDT", "15m", {**SIGNAL, "lastClose": value}, BAR, now=BAR + 1000
        )["ok"]


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    now = [BAR + 1000]
    monkeypatch.setattr(queue_policy.time, "time", lambda: now[0])
    monkeypatch.setattr(main, "EXEC_MODE", ModeStore())
    monkeypatch.setitem(main._cached, "signals", {"BTCUSDT": {**SIGNAL, "lastClose": 999}})
    monkeypatch.setitem(main._cached, "signals_by_tf", {"15m": {"BTCUSDT": copy.deepcopy(SIGNAL)}})
    monkeypatch.setitem(main._cached, "signals_bars", {"15m": {"BTCUSDT": BAR}})
    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "hot_mid_hook", None)
    monkeypatch.setattr(paper_trading, "market_closed_hook", None)

    class Feed:
        expire = False

        async def get_orderbook(self, symbol):
            if self.expire:
                now[0] = BAR + 1800
            return {
                "bids": [["99.99", "1000"]],
                "asks": [["100.01", "1000"]],
                "lastTradePrice": "100",
            }

    feed = Feed()
    broker = PaperBroker(100_000, feed, RiskEngine(RiskConfig(equity=100_000)))
    monkeypatch.setattr(main, "get_broker", lambda *a, **kw: broker)

    async def rate(sym):
        return Rate(toman_per_usdt=100000, as_of=now[0])

    monkeypatch.setattr(main, "_fx", rate)
    return now, feed, broker


def payload():
    context = main._queue_context("BTCUSDT", "15m")
    return {
        "symbol": "BTCUSDT",
        "side": "buy",
        "source": "QUEUE",
        "size_quote": 1000,
        "stop": 95,
        "tp1": 106,
        "tp2": 110,
        "approval": {"tf": "15m", "signal_key": context["signal_key"]},
    }


def test_plan_clock_matches_exact_tf_signal_not_last_scanned_cache(wired):
    data = TestClient(main.app).get("/api/risk/plan?symbol=BTCUSDT&tf=15m&approval=true")
    assert data.status_code == 200
    assert data.headers["cache-control"] == "private, no-store, max-age=0"
    assert data.json()["entry"] == 100
    assert data.json()["approval"]["expires_at"] == BAR + 1800
    assert (
        TestClient(main.app).get("/api/health").headers["cache-control"]
        == "private, no-store, max-age=0"
    )


def test_valid_queue_order_uses_exact_tf_price_and_dedup_survives_reload(wired):
    _now, _feed, broker = wired
    body = payload()
    result = asyncio.run(main.paper_order(body))
    assert result["ok"], result
    pos = next(iter(broker.positions.values()))
    assert pos.requested_qty == 10  # never divide by unrelated 999 price
    assert pos.client_order_id.startswith("nobi-q-")
    cash = broker.cash
    second = asyncio.run(main.paper_order(body))
    assert not second["ok"] and second["error"] == "queue-proposal-already-used"
    assert broker.cash == cash and len(broker.positions) == 1
    broker.load()
    assert not asyncio.run(main.paper_order(body))["ok"]
    assert len(broker.positions) == 1


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("expired", "queue-signal-expired"),
        ("mode", "queue-mode-or-side-changed"),
        ("entry", "queue-signal-changed"),
        ("direction", "queue-signal-not-LONG"),
        ("missing-bars", "queue-signal-clock-invalid"),
        ("tf", "queue-signal-unavailable"),
        ("limit", "queue-quote-sizing-required"),
        ("missing-metadata", "queue-metadata-required"),
    ],
)
def test_old_changed_or_unbound_approvals_are_refused_before_broker(wired, mutation, error):
    now, _feed, broker = wired
    body = payload()
    if mutation == "expired":
        now[0] = BAR + 1800
    elif mutation == "mode":
        main.EXEC_MODE.default = "manual"
    elif mutation == "entry":
        main._cached["signals_by_tf"]["15m"]["BTCUSDT"]["lastClose"] = 101
    elif mutation == "direction":
        main._cached["signals_by_tf"]["15m"]["BTCUSDT"]["direction"] = "EXIT"
    elif mutation == "missing-bars":
        main._cached["signals_bars"]["15m"] = {}
    elif mutation == "tf":
        body["approval"]["tf"] = "1h"
    elif mutation == "limit":
        body["price"] = 1
    elif mutation == "missing-metadata":
        body.pop("approval")
    result = asyncio.run(main.paper_order(body))
    assert not result["ok"] and result["error"] == error
    assert not broker.positions
    assert broker.cash == 100_000


def test_expiry_during_book_fetch_is_rechecked_before_cash_or_position_mutation(wired):
    _now, feed, broker = wired
    body = payload()
    feed.expire = True
    result = asyncio.run(main.paper_order(body))
    assert not result["ok"] and result["error"] == "queue-signal-expired"
    assert not broker.positions and broker.cash == 100_000


def test_stale_queue_plan_is_refused_without_inventing_prices(wired):
    now, _, _ = wired
    now[0] = BAR + 1900
    data = TestClient(main.app).get("/api/risk/plan?symbol=BTCUSDT&tf=15m&approval=true").json()
    assert data["error"] == "queue-signal-expired"
    assert "size_quote" not in data


def test_concurrent_double_approval_commits_only_once(wired):
    _now, feed, broker = wired
    original = feed.get_orderbook

    async def yielding_book(symbol):
        await asyncio.sleep(0)
        return await original(symbol)

    feed.get_orderbook = yielding_book
    body = payload()

    async def twice():
        return await asyncio.gather(main.paper_order(body), main.paper_order(body))

    results = asyncio.run(twice())
    assert sum(bool(result["ok"]) for result in results) == 1
    assert len(broker.positions) == 1


def test_changed_signal_while_fetching_book_is_refused(wired):
    _, feed, broker = wired
    body = payload()
    original = feed.get_orderbook

    async def changed_book(symbol):
        main._cached["signals_by_tf"]["15m"]["BTCUSDT"]["direction"] = "EXIT"
        return await original(symbol)

    feed.get_orderbook = changed_book
    result = asyncio.run(main.paper_order(body))
    assert not result["ok"] and result["error"] == "queue-signal-not-LONG"
    assert not broker.positions and broker.cash == 100_000


@pytest.mark.parametrize("tf,step", queue_policy.TF_SECONDS.items())
def test_scan_inputs_exclude_forming_candles_before_scoring(tf, step):
    rows = [{"t": BAR + step, "c": 999}, {"t": BAR, "c": 100}]
    closed = queue_policy.closed_rows(rows, tf, now=BAR + step + 1)
    assert closed == [{"t": BAR, "c": 100}]
    assert queue_policy.closed_rows(rows, tf, now=BAR + 2 * step) == list(reversed(rows))


def test_journal_uses_scored_bar_not_a_second_candle_fetch(wired, monkeypatch):
    seen = []

    def capture(snap, ts, rank, **kwargs):
        seen.append(ts)
        return None

    async def forbidden(*args, **kwargs):
        raise AssertionError("journal must not fetch an unrelated candle series")

    monkeypatch.setattr(main.signal_journal, "make_row", capture)
    monkeypatch.setattr(main.client, "get_candles_ex", forbidden)
    assert asyncio.run(main.journal_scan("15m")) == 0
    assert seen == [int(BAR + 900)]


@pytest.mark.parametrize("book_price,error", [
    (106.0, "execution-plan-invalid:"),  # TP1 crossed only at execution
    (102.0, "execution-rr-too-low:"),  # TP1 still above entry; TP2 R:R too low
])
def test_queue_market_fill_revalidates_unchanged_targets(wired, monkeypatch, book_price, error):
    _now, _feed, broker = wired
    body = payload()
    before = (broker.cash, broker.equity, broker.risk.open_heat)

    async def execution(*args):
        return book_price, 10

    monkeypatch.setattr(broker, "_execution_price", execution)
    result = TestClient(main.app).post("/api/paper/order", json=body).json()
    assert result["ok"] is False and result["position"] is None
    assert result["error"].startswith(error)
    assert (broker.cash, broker.equity, broker.risk.open_heat) == before
    assert not broker.positions
    assert [e.kind for e in broker.orders] == ["reject"]
    assert broker.orders[-1].extra["tp1"] == body["tp1"]
    assert broker.orders[-1].extra["tp2"] == body["tp2"]
    # Rejection is durable, and the Orders API carries the named gate.
    broker.load()
    row = TestClient(main.app).get("/api/paper/orders").json()["rows"][0]
    assert row["state"] == "REJECTED" and row["reason"] == error.rstrip(":")


@pytest.mark.parametrize("field", ["stop", "tp1", "tp2"])
def test_queue_explicit_zero_is_not_replaced_with_a_default(wired, field):
    _, _, broker = wired
    body = payload()
    body[field] = 0
    result = TestClient(main.app).post("/api/paper/order", json=body).json()
    assert not result["ok"]
    assert result["error"].startswith("execution-plan-invalid:")
    assert not broker.positions and broker.cash == 100000


def test_valid_queue_approval_preserves_the_displayed_plan(wired):
    body = payload()
    result = TestClient(main.app).post("/api/paper/order", json=body).json()
    assert result["ok"], result
    pos = result["position"]
    assert (pos["stop"], pos["tp1"], pos["tp2"]) == (body["stop"], body["tp1"], body["tp2"])


@pytest.mark.parametrize("field", ["stop", "tp1", "tp2"])
@pytest.mark.parametrize("missing", [True, False])
def test_queue_cannot_fall_back_to_unapproved_default_targets(wired, field, missing):
    _, _, broker = wired
    body = payload()
    if missing:
        body.pop(field)
    else:
        body[field] = None
    result = TestClient(main.app).post("/api/paper/order", json=body).json()
    assert not result["ok"] and result["error"] == "queue-plan-levels-required"
    assert not broker.positions and not broker.orders and broker.cash == 100000
