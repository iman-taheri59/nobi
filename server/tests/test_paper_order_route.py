# mypy: check_untyped_defs = False
# Test doubles stand in for objects on purpose; their bodies answer to pytest.
"""`POST /api/paper/order` — the route that turns a queue row into a position.

It is 25 lines with no test until now, and it sits on the boundary the whole
project is built around: the browser says "this is what I would send", the
broker decides whether that is allowed. Three things have to be true, and each
is a separate test below:

1. it never forwards anything to the exchange — a paper order is a paper order;
2. it never invents a fill the broker would refuse — refusals come back as
   `ok: false` with the gate's own name, not as a position;
3. it answers questions about its own inputs (missing field, `size_quote`
   without a price) with a sentence, not a `KeyError`.
"""

from __future__ import annotations

import pytest

from paper_trading import PaperBroker
from risk_engine import RiskConfig, RiskEngine


class Feed:
    """A book good enough to fill against, and nothing else.

    `place_order` is present on purpose: if the route ever tries to reach the
    exchange, the test fails loudly instead of quietly succeeding.
    """

    def __init__(self, price: float = 73_040.0, usdt_irt: float | None = 100_805.0):
        self.price = price
        self.usdt_irt = usdt_irt
        self.exchange_calls = 0

    async def get_orderbook(self, symbol):
        return {"bids": [[str(self.price * 0.999), "100"]],
                "asks": [[str(self.price * 1.001), "100"]],
                "lastTradePrice": str(self.price)}

    async def get_market_stats(self):
        if self.usdt_irt is None:
            raise RuntimeError("market stats unavailable")
        return {"stats": {"USDTIRT": {"latest": str(self.usdt_irt)}}}

    async def place_order(self, *_a, **_kw):
        self.exchange_calls += 1
        raise AssertionError("a paper order must never reach the exchange")


@pytest.fixture()
def broker(tmp_path, monkeypatch):
    import paper_trading

    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "market_closed_hook", None)
    monkeypatch.setattr(paper_trading, "hot_mid_hook", None)
    monkeypatch.setattr(paper_trading, "PAPER_BROKER", None)
    monkeypatch.setattr(paper_trading, "equity_hook", None, raising=False)
    b = PaperBroker(100_000.0, Feed(), RiskEngine(RiskConfig(equity=100_000.0)))
    return b


@pytest.fixture()
def client(monkeypatch, broker):
    from fastapi.testclient import TestClient

    import main

    monkeypatch.setattr(main, "get_broker", lambda *a, **k: broker)
    return TestClient(main.app)


# --------------------------------------------------------------------------- #
def test_a_valid_order_returns_the_position_the_broker_booked(client, broker):
    r = client.post("/api/paper/order", json={
        "symbol": "BTCUSDT", "side": "buy", "qty": 0.01,
        "price": 73_040.0, "stop": 72_000.0, "source": "MANUAL",
    }).json()

    assert r["ok"] is True, r.get("error")
    pos = r["position"]
    assert pos["symbol"] == "BTCUSDT"
    assert pos["qty"] == pytest.approx(0.01)
    assert pos["entry_price"] == pytest.approx(73_040.0)
    assert pos["execution"] == "limit"
    # The exchange-facing id is bounded and unique; the paper id is not reused.
    assert pos["client_order_id"].startswith("nobi-")
    assert len(pos["client_order_id"]) <= 32
    # And the broker really holds it: the route reports the broker, not a copy.
    assert pos["id"] in broker.positions
    # The metrics block is the account after the fill, not a per-order count:
    # cash went down by notional + fee, equity did not move by more than that.
    assert r["metrics"]["cash"] == pytest.approx(broker.cash)
    assert r["metrics"]["openPositions"] >= 1


def test_the_position_is_priced_in_both_currencies(client, broker):
    """`size_usd` and `risk_pct` are the numbers the risk caps compare. They
    must be in the account currency, not in the pair's quote currency."""
    r = client.post("/api/paper/order", json={
        "symbol": "BTCUSDT", "qty": 0.01, "price": 73_040.0, "stop": 72_000.0,
    }).json()
    pos = r["position"]
    assert pos["quote"] == "USDT"
    assert pos["size_usd"] == pytest.approx(730.40, rel=1e-6)      # 0.01 × 73,040
    assert pos["risk_usd"] == pytest.approx(abs(73_040 - 72_000) * 0.01, rel=1e-6)
    assert pos["risk_pct"] == pytest.approx(pos["risk_usd"] / 100_000 * 100, rel=1e-6)


def test_a_refused_order_comes_back_as_the_gate_that_refused_it(client, broker):
    """Below the exchange minimum the route must answer `ok: false` and name
    the gate. A position here would be a fill the exchange never prints."""
    r = client.post("/api/paper/order", json={
        "symbol": "BTCUSDT", "qty": 0.0001, "price": 73_040.0, "stop": 70_000.0,
    }).json()

    assert r["ok"] is False
    assert r["position"] is None
    assert r["error"].startswith("min-notional")
    assert broker.positions == {}


def test_size_quote_alone_is_converted_at_the_price_given(client, broker):
    r = client.post("/api/paper/order", json={
        "symbol": "BTCUSDT", "size_quote": 730.40, "price": 73_040.0, "stop": 72_000.0,
    }).json()

    assert r["ok"] is True, r.get("error")
    assert r["position"]["qty"] == pytest.approx(0.01, rel=1e-9)


def test_size_quote_without_a_price_is_a_sentence_not_a_guess(client, broker, monkeypatch):
    import main

    monkeypatch.setattr(main, "_cached", {**main._cached, "signals": {}})
    r = client.post("/api/paper/order", json={
        "symbol": "BTCUSDT", "size_quote": 730.40,
    }).json()

    assert r.get("error") == "price required to convert size_quote into qty"
    assert "ok" not in r or r["ok"] is False
    assert broker.positions == {}


def test_missing_symbol_or_size_is_refused_before_anything_is_booked(client, broker):
    r = client.post("/api/paper/order", json={"symbol": "BTCUSDT"}).json()
    assert r["error"] == "symbol and either qty or size_quote required"
    assert broker.positions == {}


def test_the_kill_switch_stops_the_route_at_the_broker(client, broker):
    broker.halted = True
    r = client.post("/api/paper/order", json={
        "symbol": "BTCUSDT", "qty": 0.01, "price": 73_040.0, "stop": 72_000.0,
    }).json()

    assert r["ok"] is False
    assert r["error"].startswith("halted")
    assert broker.positions == {}


def test_an_irt_pair_without_a_rate_is_refused_by_name(tmp_path, monkeypatch):
    """No USDTIRT price means no way to size an IRT pair in USD. The order must
    be refused with `no-fx-rate`, not booked at the raw Rial notional."""
    import paper_trading

    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "market_closed_hook", None)
    monkeypatch.setattr(paper_trading, "hot_mid_hook", None)
    b = PaperBroker(100_000.0, Feed(price=4_500_000_000.0, usdt_irt=None),
                    RiskEngine(RiskConfig(equity=100_000.0)))
    monkeypatch.setattr(paper_trading, "PAPER_BROKER", b)
    monkeypatch.setattr(paper_trading, "get_broker", lambda *a, **k: b)

    from fastapi.testclient import TestClient

    import main
    monkeypatch.setattr(main, "get_broker", lambda *a, **k: b)

    r = TestClient(main.app).post("/api/paper/order", json={
        "symbol": "BTCIRT", "qty": 0.001, "price": 4_500_000_000.0, "stop": 4_400_000_000.0,
    }).json()

    assert r["ok"] is False
    assert r["error"].startswith("no-fx-rate")
    assert b.positions == {}


def test_a_paper_order_never_reaches_the_exchange(client, broker):
    """The route's whole promise. The fake feed counts exchange calls."""
    feed = broker.client
    r = client.post("/api/paper/order", json={
        "symbol": "BTCUSDT", "qty": 0.01, "price": 73_040.0, "stop": 72_000.0,
    }).json()

    assert r["ok"] is True
    assert feed.exchange_calls == 0


def test_the_fill_is_tagged_with_who_placed_it(client, broker):
    """`source` travels with the order events, so the orders page can say
    whether a human or the automatic pass pulled the trigger."""
    client.post("/api/paper/order", json={
        "symbol": "BTCUSDT", "qty": 0.01, "price": 73_040.0, "stop": 72_000.0,
        "source": "AUTO",
    })

    fills = [e for e in broker.orders if e.kind == "fill"]
    assert fills and fills[-1].extra["source"] == "AUTO"


def test_a_risk_plan_from_the_queue_does_not_change_what_the_broker_decides(client, broker):
    """The queue sends the plan it previewed. The broker still runs its own
    gates — the hand-off is a hint, never an authorisation."""
    r = client.post("/api/paper/order", json={
        "symbol": "BTCUSDT", "qty": 0.0001, "price": 73_040.0, "stop": 70_000.0,
        "risk_plan": {"size_quote": 730.0, "approved": True},
    }).json()

    assert r["ok"] is False
    assert r["error"].startswith("min-notional")


def test_an_unexpected_broker_failure_is_a_sentence_not_a_500(client, broker, monkeypatch):
    async def boom(*_a, **_kw):
        raise RuntimeError("book vanished mid-order")

    monkeypatch.setattr(broker, "place_limit", boom)
    resp = client.post("/api/paper/order", json={
        "symbol": "BTCUSDT", "qty": 0.01, "price": 73_040.0, "stop": 72_000.0,
    })

    assert resp.status_code == 200          # the client gets an answer…
    body = resp.json()
    assert body["ok"] is False              # …and it is not a position
    assert "book vanished mid-order" in body["error"]
