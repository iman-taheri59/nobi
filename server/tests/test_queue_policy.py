# mypy: check_untyped_defs = False
"""No browser clock, cache fallback, replay or late approval can revive a bar."""

from __future__ import annotations

import asyncio
import copy
import json
import time

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
    # 2.5.2 — the queue also checks how old the *scan* is; a real scan stamps it.
    monkeypatch.setitem(main._cached, "signals_ts", {"15m": BAR + 950})
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
    execution = data.json()["execution"]
    assert execution["executable"] is True
    assert execution["plan_entry"] == 100
    assert execution["execution_entry"] > 100  # book walk + slip, not the stale close
    assert execution["execution_rr"] >= execution["min_rr"]
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
    # 2.5.2 — the refusal itself is recorded (item 7); nothing else may change.
    assert not broker.positions and broker.cash == 100000
    assert [e.kind for e in broker.orders] == ["reject"]
    assert broker.orders[0].extra["reason"] == "queue-plan-levels-required" and broker.orders[0].extra["pre_broker"]


# ---- 2.5.2: a plan whose scan is too old is shown with its age but not placeable


def test_queue_plan_reports_scan_age_and_fresh_plan_is_not_stale(wired):
    data = TestClient(main.app).get("/api/risk/plan?symbol=BTCUSDT&tf=15m&approval=true").json()
    ctx = data["approval"]
    assert ctx["stale"] is False and ctx["stale_reason"] is None
    assert ctx["scan_age_s"] == 50.0 and ctx["scanned_at"] == BAR + 950
    assert ctx["max_scan_age_s"] == main.QUEUE_SCAN_MAX_AGE_S >= main.SIGNALS_TTL_S


def test_stale_scan_plan_is_previewed_with_age_but_refused_at_placement(wired, monkeypatch):
    now, _, broker = wired
    now[0] = BAR + 950 + main.QUEUE_SCAN_MAX_AGE_S + 1          # still inside the candle window
    assert now[0] < BAR + 1800
    data = TestClient(main.app).get("/api/risk/plan?symbol=BTCUSDT&tf=15m&approval=true").json()
    assert data.get("ok") is not False and "size_quote" in data, "the preview is still shown, with its age"
    ctx = data["approval"]
    assert ctx["stale"] is True and ctx["stale_reason"] == "scan-too-old"
    assert ctx["scan_age_s"] == pytest.approx(main.QUEUE_SCAN_MAX_AGE_S + 1, abs=0.2)
    body = payload()
    out = TestClient(main.app).post("/api/paper/order", json=body).json()
    assert out["error"] == "queue-signal-stale" and out["blocked"] is True
    assert out["scan_age_s"] == ctx["scan_age_s"]
    assert not broker.positions, "nothing was placed"
    # A fresh scan of the same candle (same signal_key) unlocks it.
    monkeypatch.setitem(main._cached, "signals_ts", {"15m": now[0] - 10})
    assert main._queue_context("BTCUSDT", "15m")["stale"] is False
    assert main._queue_order_context(body)["ok"] is True


def test_missing_scan_clock_fails_closed(wired, monkeypatch):
    monkeypatch.setitem(main._cached, "signals_ts", {})
    ctx = main._queue_context("BTCUSDT", "15m")
    assert ctx["ok"] is True and ctx["stale"] is True and ctx["stale_reason"] == "scan-clock-unavailable"
    assert ctx["scan_age_s"] is None
    assert main._queue_order_context(payload())["error"] == "queue-signal-stale"


def test_scan_going_stale_during_book_fetch_is_refused_by_the_guard(wired, monkeypatch):
    now, feed, broker = wired
    body = payload()

    class LateFeed:
        async def get_orderbook(self, symbol):
            # The scan clock does not move; the wall clock does, past the budget.
            now[0] = BAR + 950 + main.QUEUE_SCAN_MAX_AGE_S + 1
            return await feed.get_orderbook(symbol)

    broker.client = LateFeed()
    out = TestClient(main.app).post("/api/paper/order", json=body).json()
    assert out.get("error") == "queue-signal-stale" or "queue-signal-stale" in str(out)
    assert not broker.positions


# ---- 2.5.2: the market moved away from the plan entry ------------------------


def test_fresh_plan_reports_drift_within_limits(wired):
    data = TestClient(main.app).get("/api/risk/plan?symbol=BTCUSDT&tf=15m&approval=true").json()
    d = data["drift"]
    assert d["exceeded"] is False and d["reason"] is None
    assert d["mid"] == 100.0 and d["drift_pct"] == 0.0
    assert d["limit_pct"] == main.QUEUE_DRIFT_PCT and d["limit_atr"] == main.QUEUE_DRIFT_ATR


@pytest.mark.parametrize("ask,bid,reason", [
    ("102.01", "101.99", "price-moved-pct"),   # +2.0 % > 1.5 %
    ("97.99", "97.99", "price-moved-pct"),     # −2.0 % (fell toward the stop)
])
def test_moved_market_is_previewed_as_drifted_and_refused_at_placement(wired, ask, bid, reason):
    _now, feed, broker = wired

    async def moved(symbol):
        return {"bids": [[bid, "1000"]], "asks": [[ask, "1000"]], "lastTradePrice": ask}

    feed.get_orderbook = moved
    data = TestClient(main.app).get("/api/risk/plan?symbol=BTCUSDT&tf=15m&approval=true").json()
    assert "size_quote" in data, "preview still shown"
    assert data["drift"]["exceeded"] is True and data["drift"]["reason"] == reason
    out = TestClient(main.app).post("/api/paper/order", json=payload()).json()
    assert out["error"] == "queue-price-moved" and out["blocked"] is True
    assert out["drift"]["reason"] == reason
    assert not broker.positions


def test_atr_yardstick_trips_before_the_percent_one_in_a_flat_market():
    # ATR 1 on a price of 100: 0.6 % is nothing in % terms but 0.6×ATR.
    d = queue_policy.price_drift(100, 100.6, 1.0, pct_limit=1.5, atr_limit=0.5)
    assert d["exceeded"] is True and d["reason"] == "price-moved-atr" and d["drift_atr"] == 0.6
    d = queue_policy.price_drift(100, 100.4, 1.0, pct_limit=1.5, atr_limit=0.5)
    assert d["exceeded"] is False
    # No ATR: only the % yardstick applies; no mid: locked, not invented.
    assert queue_policy.price_drift(100, 101, None, pct_limit=1.5, atr_limit=0.5)["exceeded"] is False
    assert queue_policy.price_drift(100, None, 1.0, pct_limit=1.5, atr_limit=0.5)["reason"] == "mid-unavailable"


def test_signals_refresh_flag_forces_a_bounded_background_rescan(monkeypatch):
    calls = []

    async def fake_rescan(tf):
        calls.append(tf)

    monkeypatch.setattr(main, "rescan", fake_rescan)
    monkeypatch.setattr(main, "_spawn", lambda coro: __import__("asyncio").get_event_loop().create_task(coro))
    monkeypatch.setitem(main._cached, "signals_by_tf", {"15m": {"BTCUSDT": copy.deepcopy(SIGNAL)}})
    monkeypatch.setitem(main._cached, "refreshing", {})
    client = TestClient(main.app)
    # inside the TTL, cache 30 s old: plain GET does nothing, refresh=true rescans
    monkeypatch.setitem(main._cached, "signals_ts", {"15m": time.time() - 30})
    assert client.get("/api/signals?tf=15m").json()["refreshing"] is False
    r = client.get("/api/signals?tf=15m&refresh=true").json()
    assert r["refreshing"] is True
    # too young (< SIGNALS_FORCE_MIN_AGE_S): the button cannot hammer the exchange
    monkeypatch.setitem(main._cached, "refreshing", {})
    monkeypatch.setitem(main._cached, "signals_ts", {"15m": time.time() - 2})
    assert client.get("/api/signals?tf=15m&refresh=true").json()["refreshing"] is False


# ---- 2.5.2 item 7: refusals leave a trace in the paper book, counted by reason


def test_pre_broker_queue_refusals_are_recorded_as_rejects_with_source(wired, monkeypatch):
    now, feed, broker = wired
    import diagnostics
    # 1) stale scan → refused before the broker, recorded
    monkeypatch.setitem(main._cached, "signals_ts", {"15m": now[0] - main.QUEUE_SCAN_MAX_AGE_S - 1})
    out = TestClient(main.app).post("/api/paper/order", json=payload()).json()
    assert out["error"] == "queue-signal-stale"
    # 2) moved price → refused before place_limit, recorded with drift
    monkeypatch.setitem(main._cached, "signals_ts", {"15m": now[0] - 5})

    async def moved(symbol):
        return {"bids": [["103", "1000"]], "asks": [["103.02", "1000"]], "lastTradePrice": "103"}

    feed.get_orderbook = moved
    out = TestClient(main.app).post("/api/paper/order", json=payload()).json()
    assert out["error"] == "queue-price-moved"
    rejects = [e for e in broker.orders if e.kind == "reject"]
    assert [e.extra["reason"] for e in rejects] == ["queue-signal-stale", "queue-price-moved"]
    assert all(e.extra["source"] == "QUEUE" and e.extra["pre_broker"] is True for e in rejects)
    assert rejects[1].extra["drift_pct"] == pytest.approx(3.01, abs=0.01)
    assert not broker.positions
    # 3) the same events, counted the way the daily diagnostic reads them
    state = json.loads((paper_trading.STATE_FILE).read_text(encoding="utf-8"))
    counts = diagnostics.reject_counts(state["orders"])
    assert counts["total"] == 2 and counts["by_source"] == {"QUEUE": 2}
    assert counts["by_reason"] == {"queue-price-moved": 1, "queue-signal-stale": 1}
    assert counts["by_reason_source"]["queue-signal-stale"] == {"QUEUE": 1}
    # a window excludes old events; a reject without source counts under "?"
    assert diagnostics.reject_counts(state["orders"], since=now[0] + 10 ** 6)["total"] == 0
    legacy = [{"kind": "reject", "ts": 1.0, "extra": {"reason": "halted"}}]
    assert diagnostics.reject_counts(legacy)["by_source"] == {"?": 1}


def test_broker_rejects_now_carry_their_source(wired):
    _now, _feed, broker = wired
    broker.halted = True
    body = {**payload(), "source": "SEMI"}
    body.pop("approval")
    assert TestClient(main.app).post("/api/paper/order", json=body).json().get("ok") is not True
    ev = [e for e in broker.orders if e.kind == "reject"][-1]
    assert ev.extra == {"reason": "halted", "source": "SEMI"}
