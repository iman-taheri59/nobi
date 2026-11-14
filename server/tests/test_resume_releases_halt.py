# mypy: check_untyped_defs = False
"""A kill switch that cannot be released is a stop, not a safety feature.

The trigger inputs are *derived*: `refresh_risk_triggers` recomputes the daily
loss, the weekly loss and the stop streak from the retained exit events every
time it runs. Clearing `halted` and `trigger_reasons` therefore changed nothing
that the next scan reads — three losing closed positions were still there, the
streak was still 3, and the switch re-armed before a single order could reach
the preflight. The operator pressed "resume", the toast said resumed, and the
book stayed shut.

These tests pin the release: after a resume, the windows restart at the resume
instant, and a *new* stop can still arm the switch.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime

import pytest

from paper_trading import OrderEvent, PaperBroker, Position
from risk_engine import RiskConfig, RiskEngine


class _NoNetwork:
    async def get_orderbook(self, _symbol):
        raise AssertionError("a halted order reached the exchange")


def _broker(tmp_path, monkeypatch):
    import paper_trading

    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    return PaperBroker(100_000, _NoNetwork(), RiskEngine(RiskConfig(equity=100_000)))


def _closed(symbol: str, ts: float, pnl: float = 0.0) -> Position:
    return Position(
        id=f"closed-{symbol}-{ts}", symbol=symbol, side="buy", entry_price=100,
        qty=1, size_quote=100, stop=98, tp1=102, tp2=105, stop_pct=2,
        opened_at=ts - 60, state="CLOSED", closed_at=ts, close_reason="sl-hit",
        risk_usd=2, risk_pct=0.002,
    )


def _three_stops(broker, now: float, pnl_each: float = 0.0) -> None:
    broker.positions = {f"p{i}": _closed("BTCUSDT", now - 300 + i) for i in range(3)}
    broker.orders = [
        OrderEvent(now - 300 + i, "close", "BTCUSDT", "buy", 1, 98, {"pnl": pnl_each})
        for i in range(3)
    ]


def test_resume_releases_the_latch_instead_of_re_arming_it(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    now = datetime(2026, 11, 4, 12, tzinfo=UTC).timestamp()
    _three_stops(broker, now)

    armed = broker.refresh_risk_triggers(now)
    assert armed["consecutive_sl"] == 3
    assert broker.risk.halted is True
    assert "consecutive-stop-halt" in armed["latched_reasons"]

    # What the resume endpoint does, without the HTTP layer.
    broker.halted = False
    broker.risk.halted = False
    broker.risk.trigger_reasons = []
    broker.risk.resumed_at = now + 1

    later = broker.refresh_risk_triggers(now + 60)
    assert later["consecutive_sl"] == 0, "the same three stops were counted again"
    assert later["reasons"] == []
    assert broker.risk.halted is False, "resume was undone by the next scan"


def test_resume_restarts_the_realized_pnl_window(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    now = datetime(2026, 11, 4, 12, tzinfo=UTC).timestamp()
    _three_stops(broker, now, pnl_each=-1500)  # -4.5% of 100k in one day

    armed = broker.refresh_risk_triggers(now)
    assert armed["daily_pnl_pct"] == pytest.approx(-4.5)
    assert "daily-loss-halt" in armed["reasons"]

    broker.risk.halted = False
    broker.risk.trigger_reasons = []
    broker.risk.resumed_at = now + 1

    later = broker.refresh_risk_triggers(now + 60)
    assert later["daily_pnl_pct"] == pytest.approx(0.0)
    assert later["reasons"] == []
    assert broker.risk.halted is False


def test_a_new_stop_after_resume_still_arms_the_switch(tmp_path, monkeypatch):
    """The release must not be permanent immunity."""
    broker = _broker(tmp_path, monkeypatch)
    now = datetime(2026, 11, 4, 12, tzinfo=UTC).timestamp()
    _three_stops(broker, now)
    broker.refresh_risk_triggers(now)

    broker.risk.halted = False
    broker.risk.trigger_reasons = []
    broker.risk.resumed_at = now + 1

    for i in range(3):
        ts = now + 100 + i
        broker.positions[f"n{i}"] = _closed("ETHUSDT", ts)
        broker.orders.append(
            OrderEvent(ts, "close", "ETHUSDT", "buy", 1, 98, {"pnl": 0.0})
        )
    state = broker.refresh_risk_triggers(now + 200)
    assert state["consecutive_sl"] == 3
    assert "consecutive-stop-halt" in state["reasons"]


def test_resume_floor_survives_a_restart(tmp_path, monkeypatch):
    """A reboot must not be a back door around the operator's decision."""
    broker = _broker(tmp_path, monkeypatch)
    now = datetime(2026, 11, 4, 12, tzinfo=UTC).timestamp()
    _three_stops(broker, now)
    broker.refresh_risk_triggers(now)

    broker.risk.halted = False
    broker.risk.trigger_reasons = []
    broker.risk.resumed_at = now + 5
    broker.save()

    reloaded = _broker(tmp_path, monkeypatch)
    assert reloaded.risk.resumed_at == pytest.approx(now + 5)
    state = reloaded.refresh_risk_triggers(now + 60)
    assert state["consecutive_sl"] == 0
    assert reloaded.risk.halted is False


def test_resume_route_records_the_floor_and_reports_it(tmp_path, monkeypatch):
    """Whole-path check: the HTTP handler, not only the broker method."""
    from datetime import datetime as _dt

    from fastapi.testclient import TestClient

    import auth
    import main
    import paper_trading
    import security

    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    monkeypatch.setattr(security, "API_TOKEN", "")
    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "PAPER_BROKER", None)
    monkeypatch.setattr(main.notify, "send", _async_none, raising=False)
    auth.reset_caches()
    security.reset_rate_limits()

    user = auth.create_user("resumer", "a long enough password", role="owner")
    token, csrf, _ = auth.create_session(user, ip="10.0.0.1", ua="Resume Test")
    client = TestClient(main.app)
    client.cookies.set("nbx_session", token)
    client.cookies.set("nbx_csrf", csrf)

    broker = paper_trading.get_broker(100_000, main.client, main.risk)
    broker.halted = True
    main.risk.halted = True
    main.risk.trigger_reasons = ["manual-halt"]
    broker.save()

    before = _dt.now(UTC).timestamp()
    resp = client.post("/api/paper/resume", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resumed"] is True
    assert body["halted"] is False
    assert body["resumed_at"]
    assert main.risk.resumed_at >= before
    assert main.risk.trigger_reasons == []

    # And the release is durable: a fresh broker reads the floor back.
    monkeypatch.setattr(paper_trading, "PAPER_BROKER", None)
    reloaded = paper_trading.get_broker(100_000, main.client, main.risk)
    assert reloaded.risk.resumed_at == pytest.approx(main.risk.resumed_at)


async def _async_none(*_a, **_k):
    return {"sent": False}


# --------------------------------------------------------------- cache pruning
def test_the_cache_prunes_itself_without_a_cron_entry(tmp_path, monkeypatch):
    """Nothing ever deleted a cache file, and the volume fills in 62-73 days.

    Measured production growth was 0.27-0.32 GB/day on a 25 GB disk. A schedule
    entry would fix it until the next reinstall; a budget in `store()` cannot
    be forgotten.
    """
    import candle_cache

    cache_dir = tmp_path / "candles"
    cache_dir.mkdir()
    monkeypatch.setattr(candle_cache, "CACHE_ROOT", cache_dir)
    now = time.time()  # mtimes must be in the present, or age alone prunes them

    for i in range(6):
        fp = cache_dir / f"BTCUSDT-{i}.parquet"
        fp.write_bytes(b"x" * 1000)
        os.utime(fp, (now - i * 10, now - i * 10))

    cache = candle_cache.CandleCache()
    result = cache.prune(max_bytes=2_500, max_age_s=None)
    # Oldest first until under budget: 6000 -> 3000 -> 2000.
    assert result["removed"] == 4
    assert result["remaining_bytes"] == pytest.approx(2_000)
    assert len(list(cache_dir.glob("*.parquet"))) == 2

    # The two survivors are the newest ones.
    names = sorted(p.name for p in cache_dir.glob("*.parquet"))
    assert names == ["BTCUSDT-0.parquet", "BTCUSDT-1.parquet"]


def test_an_expired_cache_entry_goes_even_when_the_budget_is_fine(tmp_path, monkeypatch):
    import candle_cache

    cache_dir = tmp_path / "candles"
    cache_dir.mkdir()
    monkeypatch.setattr(candle_cache, "CACHE_ROOT", cache_dir)
    now = time.time()
    old = cache_dir / "old.parquet"
    old.write_bytes(b"x" * 10)
    os.utime(old, (now - 30 * 24 * 3600, now - 30 * 24 * 3600))
    fresh = cache_dir / "fresh.parquet"
    fresh.write_bytes(b"x" * 10)

    result = candle_cache.CandleCache().prune(max_bytes=10**9, max_age_s=7 * 24 * 3600)
    assert result["removed"] == 1
    assert not old.exists()
    assert fresh.exists()


def test_pruning_a_missing_cache_directory_is_not_an_error(tmp_path, monkeypatch):
    import candle_cache

    monkeypatch.setattr(candle_cache, "CACHE_ROOT", tmp_path / "does-not-exist")
    result = candle_cache.CandleCache().prune()
    assert result["removed"] == 0


# ------------------------------------------------------------ visible uptime
def test_health_reports_uptime_and_free_space():
    """The 13 Nov outage was invisible: nothing in the API said how long the
    process had been up or how much disk was left, so a suspended VM and a
    healthy one produced the same document."""
    from fastapi.testclient import TestClient

    import main

    body = TestClient(main.app).get("/api/health").json()
    assert body["process"]["uptime_s"] >= 0
    assert body["process"]["started_at"].endswith("+00:00")
    assert body["disk"]["free_gb"] > 0
    assert body["disk"]["total_gb"] >= body["disk"]["free_gb"]
    assert isinstance(body["disk"]["ok"], bool)
    assert 0 <= body["disk"]["used_pct"] <= 100


# ---------------------------------------------------- halt?flat actually flattens
def _book(bids, asks):
    class _B:
        usdt_irt = 1.0

        async def get_orderbook(self, _symbol):
            return {"bids": [[str(p), str(a)] for p, a in bids],
                    "asks": [[str(p), str(a)] for p, a in asks],
                    "lastTradePrice": str(asks[0][0])}

        async def get_market_stats(self):
            return {"stats": {"USDTIRT": {"latest": "1"}}}

    return _B()


def _open_position(broker, symbol: str, qty: float, entry: float = 100.0):
    from paper_trading import Position

    pid = f"pp-test-{symbol}-{qty}"
    broker.positions[pid] = Position(
        id=pid, symbol=symbol, side="buy", entry_price=entry, qty=qty,
        size_quote=entry * qty, stop=entry * 0.95, tp1=entry * 1.05,
        tp2=entry * 1.10, stop_pct=5.0, opened_at=time.time(), state="OPEN",
        filled_qty=qty, requested_qty=qty, risk_usd=1.0, risk_pct=0.01,
        size_usd=entry * qty, quote_usd=1.0, last_price=entry,
    )
    return pid


def test_flatten_closes_every_open_position(tmp_path, monkeypatch):
    import paper_trading

    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "PAPER_BROKER", None)
    broker = PaperBroker(100_000, _book([(100.0, 1000)], [(100.0, 1000)]),
                         RiskEngine(RiskConfig(equity=100_000)))
    _open_position(broker, "BTCUSDT", 1.0)
    _open_position(broker, "ETHUSDT", 2.0)

    result = asyncio.run(broker.flatten())
    assert result["requested"] == 2
    assert result["closed"] == 2
    assert result["failed"] == 0
    assert result["still_open"] == []
    assert not [p for p in broker.positions.values() if p.state != "CLOSED"]


def test_flatten_reports_what_a_thin_book_could_not_close(tmp_path, monkeypatch):
    """A partial fill is not a close, and the response must say so."""
    import paper_trading

    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "PAPER_BROKER", None)
    # Bids hold 0.4 of the 1.0 we need to sell.
    broker = PaperBroker(100_000, _book([(100.0, 0.4)], [(100.0, 1000)]),
                         RiskEngine(RiskConfig(equity=100_000)))
    pid = _open_position(broker, "BTCUSDT", 1.0)

    result = asyncio.run(broker.flatten())
    assert result["requested"] == 1
    assert result["closed"] == 0
    assert result["failed"] == 1
    assert result["still_open"][0]["id"] == pid
    assert result["still_open"][0]["remaining_qty"] < 1.0
    assert broker.positions[pid].state != "CLOSED"


def test_halt_with_flat_closes_the_book_and_says_how_many(tmp_path, monkeypatch):
    """The route, not only the broker: `flat=true` used to close nothing."""
    from fastapi.testclient import TestClient

    import auth
    import main
    import paper_trading
    import security

    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    monkeypatch.setattr(security, "API_TOKEN", "")
    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "PAPER_BROKER", None)
    monkeypatch.setattr(main.notify, "send", _async_none, raising=False)
    auth.reset_caches()
    security.reset_rate_limits()

    user = auth.create_user("halter", "a long enough password", role="owner")
    token, csrf, _ = auth.create_session(user, ip="10.0.0.1", ua="Halt Test")
    client = TestClient(main.app)
    client.cookies.set("nbx_session", token)
    client.cookies.set("nbx_csrf", csrf)

    broker = paper_trading.get_broker(100_000, main.client, main.risk)
    broker.client = _book([(100.0, 1000)], [(100.0, 1000)])
    _open_position(broker, "BTCUSDT", 1.0)

    try:
        resp = client.post("/api/paper/halt?flat=true", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["halted"] is True
        assert body["flat_requested"] is True
        assert body["flat_result"]["closed"] == 1
        assert body["open_positions"] == 0
    finally:
        # conftest fails a test that leaves the latch armed, on purpose.
        main.risk.halted = False
        broker.halted = False


def test_halt_without_flat_leaves_positions_alone(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import auth
    import main
    import paper_trading
    import security

    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    monkeypatch.setattr(security, "API_TOKEN", "")
    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "PAPER_BROKER", None)
    monkeypatch.setattr(main.notify, "send", _async_none, raising=False)
    auth.reset_caches()
    security.reset_rate_limits()

    user = auth.create_user("halter2", "a long enough password", role="owner")
    token, csrf, _ = auth.create_session(user, ip="10.0.0.1", ua="Halt Test 2")
    client = TestClient(main.app)
    client.cookies.set("nbx_session", token)
    client.cookies.set("nbx_csrf", csrf)

    broker = paper_trading.get_broker(100_000, main.client, main.risk)
    broker.client = _book([(100.0, 1000)], [(100.0, 1000)])
    _open_position(broker, "BTCUSDT", 1.0)

    try:
        body = client.post("/api/paper/halt", headers={"X-CSRF-Token": csrf}).json()
        assert body["flat_requested"] is False
        assert body["flat_result"] is None
        assert body["open_positions"] == 1
    finally:
        main.risk.halted = False
        broker.halted = False


# ------------------------------------------------------- execution-mode default
def test_the_built_in_mode_is_semi_and_says_where_it_came_from():
    """An unset host must not silently be `auto`."""
    import exec_mode

    snap = exec_mode.ModeStore().snapshot()
    assert snap["default"] == "semi"
    assert snap["default_source"] == "built-in"
    assert snap["built_in_default"] == "semi"
    # And the snapshot keeps saying this executor is paper-only.
    assert snap["executor"] == "paper"
    assert snap["sends_to_exchange"] is False


def test_auto_can_be_chosen_by_configuration_not_by_editing_code(monkeypatch):
    """`auto` has to be reachable without patching a constant."""
    import importlib

    monkeypatch.setenv("NBX_EXEC_MODE", "auto")
    import exec_mode
    reloaded = importlib.reload(exec_mode)
    try:
        assert reloaded.DEFAULT == "auto"
        assert reloaded.DEFAULT_SOURCE == "NBX_EXEC_MODE"
        assert reloaded.ModeStore().snapshot()["default"] == "auto"
    finally:
        monkeypatch.delenv("NBX_EXEC_MODE", raising=False)
        importlib.reload(exec_mode)


def test_an_invalid_exec_mode_refuses_to_start(monkeypatch):
    import importlib

    monkeypatch.setenv("NBX_EXEC_MODE", "fullauto")
    import exec_mode
    try:
        with pytest.raises(SystemExit) as exc:
            importlib.reload(exec_mode)
        assert "fullauto" in str(exc.value)
    finally:
        monkeypatch.delenv("NBX_EXEC_MODE", raising=False)
        importlib.reload(exec_mode)
