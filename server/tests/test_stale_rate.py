# mypy: check_untyped_defs = False
# Test doubles stand in for objects on purpose; their bodies answer to pytest.
"""What the app says when the exchange stops answering.

Nobitex refuses most non-Iranian networks, so "no upstream" is not an exotic
state here — it is the normal state on a laptop outside Iran. Three rules
follow from the project's own contract:

1. a route that cannot price something says so in a sentence, with the gate's
   name, and answers 200 — a 500 is a stack trace shown to a user;
2. the refusal is the *same* refusal everywhere: if the preview will not price
   an IRT order, the broker must not book it either;
3. a number computed from a rate that is an hour old is not "the value".

The bug these tests pin down: `/api/risk/whatif` returned
`money.NoRate: rate from usdt-irt is 1935s old` as an HTTP 500, because a stale
rate is still a `Rate` object and every caller assumed a non-None rate was a
usable one.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from paper_trading import RATE_TTL_S, PaperBroker
from risk_engine import RiskConfig, RiskEngine


class Wire:
    """The exchange, answering or not. No POST method, on purpose."""

    def __init__(self, stats_ok: bool = False, usdt_irt: float = 100_805.0):
        self.stats_ok = stats_ok
        self.usdt_irt = usdt_irt
        self.stat_calls = 0

    async def get_orderbook(self, symbol):
        return {"bids": [[str(8_860_000_000), "10"]],
                "asks": [[str(8_880_000_000), "10"]],
                "lastTradePrice": str(8_870_000_000)}

    async def get_market_stats(self):
        self.stat_calls += 1
        if not self.stats_ok:
            raise RuntimeError("market stats unavailable: circuit breaker is open")
        return {"stats": {"USDTIRT": {"latest": str(self.usdt_irt)}}}


def _broker(wire: Wire, *, age_s: float, strategy: str = "MANUAL") -> PaperBroker:
    b = PaperBroker(100_000.0, wire, RiskEngine(RiskConfig(equity=100_000.0)))  # type: ignore[arg-type]
    b.usdt_irt = 100_805.0
    b.usdt_irt_at = time.time() - age_s
    return b


@pytest.fixture()
def make(tmp_path, monkeypatch):
    import paper_trading

    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "market_closed_hook", None)
    monkeypatch.setattr(paper_trading, "hot_mid_hook", None)
    monkeypatch.setattr(paper_trading, "PAPER_BROKER", None)

    def build(*, age_s: float, stats_ok: bool = False, snapshot: dict | None = None):
        broker = _broker(Wire(stats_ok=stats_ok), age_s=age_s)
        from fastapi.testclient import TestClient

        import main

        monkeypatch.setattr(main, "get_broker", lambda *a, **k: broker)
        # The what-if route needs a price and an ATR before it ever looks at
        # the rate; without a snapshot it answers "no price", which is a
        # different refusal and would hide the one under test.
        monkeypatch.setitem(main._cached, "signals", {"BTCIRT": snapshot or {}})
        # The other rate source: a stale exchange also means no fresh universe,
        # so the fallback must not hand the route a price either.
        monkeypatch.setitem(main._cached, "universe", [])
        monkeypatch.setitem(main._cached, "universe_ts", 0.0)
        return broker, TestClient(main.app)

    return build


# --------------------------------------------------------------------------- #
# the source: one answer to "is there a rate?"
def test_the_broker_returns_none_when_it_cannot_refresh_a_stale_rate(make):
    broker, _ = make(age_s=RATE_TTL_S + 1_635)     # 32 minutes, as reported
    assert asyncio.run(broker._fx()) is None


def test_a_stale_rate_becomes_usable_again_once_the_exchange_answers(make):
    broker, _ = make(age_s=RATE_TTL_S + 1_635, stats_ok=True)
    rate = asyncio.run(broker._fx())
    assert rate is not None
    assert not rate.is_stale
    assert rate.toman_per_usdt == pytest.approx(100_805.0)


def test_a_fresh_cached_rate_does_not_hit_the_exchange_at_all(make):
    broker, _ = make(age_s=10)
    wire = broker.client
    assert asyncio.run(broker._fx()) is not None
    assert wire.stat_calls == 0


# --------------------------------------------------------------------------- #
# the routes: a sentence, not a 500
def test_whatif_refuses_with_the_gate_name_when_the_rate_is_stale(make):
    _, client = make(age_s=RATE_TTL_S + 1_635, snapshot={"lastClose": 8_870_000_000,
                                                         "risk": {"atr": 40_000_000}})
    r = client.post("/api/risk/whatif", json={"symbol": "BTCIRT", "side": "buy"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["blocked_by"] == "no-fx-rate"
    assert "rate" in body["message"].lower()


def test_the_plan_route_refuses_the_same_way(make):
    _, client = make(age_s=RATE_TTL_S + 1_635, snapshot={"lastClose": 8_870_000_000,
                                                         "risk": {"atr": 40_000_000}})
    r = client.get("/api/risk/plan", params={"symbol": "BTCIRT", "side": "buy",
                                             "tf": "15m"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is not True
    assert body.get("error") == "no-fx-rate"


def test_the_risk_state_route_still_reports_caps_without_a_rate(make):
    _, client = make(age_s=RATE_TTL_S + 1_635)
    r = client.get("/api/risk/state")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["caps"]["riskPct"] == 0.75        # policy is not gated on the feed
    assert body["equity_irt"] is None             # …but the Toman figure is unknown
    assert body["fx"] is None


def test_an_irt_position_does_not_report_a_dollar_value_from_an_old_rate(make):
    """`value_usd` was computed from `broker.usdt_irt` with `as_of=now`, which
    dressed an hour-old price as a current one."""
    broker, client = make(age_s=RATE_TTL_S + 1_635)
    pos = client.post("/api/paper/order", json={
        "symbol": "BTCIRT", "qty": 0.001, "price": 8_870_000_000, "stop": 8_700_000_000,
    }).json()
    # The order itself must be refused for the same reason, so the position
    # cannot exist; this is the second half of "one answer everywhere".
    assert pos["ok"] is False
    assert str(pos.get("error")).startswith("no-fx-rate")
    assert broker.positions == {}


def test_a_missing_rate_never_becomes_a_paper_fill(make):
    broker, client = make(age_s=RATE_TTL_S * 10)
    r = client.post("/api/paper/order", json={
        "symbol": "BTCIRT", "qty": 0.001, "price": 8_870_000_000, "stop": 8_700_000_000,
    }).json()
    assert r["ok"] is False
    assert r["error"].startswith("no-fx-rate")
    assert not broker.positions


def test_an_existing_irt_position_reports_no_dollar_value_while_the_rate_is_stale(make):
    """The row used to build its own `Rate(..., as_of=now)` from the cached
    number, which dressed an old price as a fresh one. The Toman value stays
    exact; only the dollar figure is unknown — and it says so."""
    import time as _time

    broker, _ = make(age_s=RATE_TTL_S + 1_635)
    from paper_trading import Position

    pos = Position(id="p1", symbol="BTCIRT", side="buy", entry_price=8_800_000_000.0,
                   qty=0.001, size_quote=8_800_000.0, stop=8_600_000_000.0,
                   tp1=9_100_000_000.0, tp2=9_400_000_000.0, stop_pct=2.3,
                   opened_at=_time.time(), state="OPEN", filled_qty=0.001)
    broker.positions["p1"] = pos

    import main

    row = main._position_payload(pos)
    assert row["value_quote"] > 0          # Toman is exact
    assert row["value_usd"] is None        # dollars need a rate we do not have
    assert row["fx"] is None


# --------------------------------------------------------------------------- #
# the other rate source: the universe snapshot
def test_the_universe_fallback_is_dated_by_its_snapshot_not_by_reading_it(make):
    """`/api/risk/*` falls back to the USDTIRT row when the broker has no rate.
    It used to date that fallback `as_of=now`, so a universe loaded an hour ago
    became a fresh rate the moment someone read it."""
    import asyncio

    import main

    make(age_s=RATE_TTL_S + 1_000)          # broker: nothing usable
    main._cached["universe"] = [{"symbol": "USDTIRT", "last": 100_000.0}]
    main._cached["universe_ts"] = time.time() - 3_600   # …and the snapshot is an hour old

    assert asyncio.run(main._fx("BTCIRT")) is None


def test_a_fresh_snapshot_is_still_allowed_as_a_fallback(make):
    import asyncio

    import main

    make(age_s=RATE_TTL_S + 1_000)
    main._cached["universe"] = [{"symbol": "USDTIRT", "last": 100_000.0}]
    main._cached["universe_ts"] = time.time()

    rate = asyncio.run(main._fx("BTCIRT"))
    assert rate is not None
    assert rate.source == "universe"
    assert rate.age_s < 5
