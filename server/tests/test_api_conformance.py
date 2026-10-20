"""Conformance tests against the Nobitex API documentation.

Each test here pins one documented rule the app used to ignore. The doc reference
is in the test name or docstring; the code under test is the one that decides
what the dashboard shows or what the paper broker places.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

import units
from nobitex_client import ClientError, NobitexClient, RateLimitError, UpstreamUnavailable


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Units: Rial in the API, Toman in the app
# --------------------------------------------------------------------------- #
def test_stats_quoted_in_rial_are_converted_to_toman():
    """`/market/stats` quotes rial markets in Rial (docs §market/stats example:
    ``"latest": "4900000000"`` for btc-rls). The dashboard labels its numbers in
    Toman, so the boundary must divide by ten — once."""
    body = {"stats": {
        "btc-rls": {"latest": "4_900_000_000".replace("_", ""), "bestSell": "4_901_000_000",
                    "volumeDst": "1200000000000", "dayChange": "1.5"},
        "btc-usdt": {"latest": "73000", "bestSell": "73001", "dayChange": "0.5"},
    }}
    out = units.scale_stats(body)
    assert out["stats"]["BTCIRT"]["latest"] == pytest.approx(490_000_000)
    assert out["stats"]["BTCIRT"]["bestSell"] == pytest.approx(490_100_000)
    assert out["stats"]["BTCIRT"]["volumeDst"] == pytest.approx(120_000_000_000)
    assert out["stats"]["BTCIRT"]["dayChange"] == 1.5          # a ratio: never scaled
    assert out["stats"]["BTCUSDT"]["latest"] == 73_000.0       # dollar market untouched


def test_stats_symbol_keys_become_the_symbols_the_other_endpoints_take():
    """`btc-rls` is the stats key; `/v3/orderbook` and `udf/history` want
    `BTCIRT`. Upper-casing the key gives "BTC-RLS", which is not a market."""
    out = units.scale_stats({"stats": {"btc-rls": {"latest": "1"},
                                       "usdt-rls": {"latest": "1"},
                                       "sol-usdt": {"latest": "1"},
                                       "global": {"binance-price": "1"}}})
    assert set(out["stats"]) == {"BTCIRT", "USDTIRT", "SOLUSDT"}
    assert units.market_symbol("btc-rls") == "BTCIRT"
    assert units.market_symbol("btc-usdt") == "BTCUSDT"
    assert units.market_symbol("global") is None


def test_orderbook_and_candles_of_a_rial_market_are_converted_too():
    ob = units.scale_orderbook({"bids": [["4900000000", "0.1"]],
                                "asks": [["4901000000", "0.2"]],
                                "lastTradePrice": "4900500000"}, "BTCIRT")
    assert ob["bids"][0] == [pytest.approx(490_000_000), pytest.approx(0.1)]
    assert ob["asks"][0][0] == pytest.approx(490_100_000)
    assert ob["lastTradePrice"] == pytest.approx(490_050_000)
    assert ob["asks"][0][1] == pytest.approx(0.2)              # amount is in coin

    udf = units.scale_udf({"s": "ok", "o": ["4900000000"], "h": ["4910000000"],
                           "l": ["4890000000"], "c": ["4905000000"], "v": ["12"]}, "BTCIRT")
    assert udf["c"] == [pytest.approx(490_500_000)]
    assert udf["v"] == ["12"]                                  # volume stays in BTC

    usdt = units.scale_orderbook({"bids": [["73000", "1"]]}, "BTCUSDT")
    assert usdt["bids"][0][0] == pytest.approx(73_000)


def test_switch_to_a_toman_denominated_source_would_only_change_the_scale(monkeypatch):
    body = {"stats": {"btc-rls": {"latest": "490000000"}}}
    assert units.scale_stats(body, unit="toman")["stats"]["BTCIRT"]["latest"] == pytest.approx(490_000_000)
    monkeypatch.setenv("NBX_PRICE_UNIT", "toman")
    assert units.to_toman(490_000_000) == pytest.approx(490_000_000)


# --------------------------------------------------------------------------- #
# Precision and the documented minimum order
# --------------------------------------------------------------------------- #
def test_amounts_truncate_and_prices_round_to_the_market_tick():
    assert units.truncate_amount(0.0000019999, "BTCIRT") == pytest.approx(0.000001)
    assert units.truncate_amount(12.987, "XRPIRT") == pytest.approx(12.0)   # whole coins
    assert units.round_price(4_900_004_999.0, "BTCIRT") == pytest.approx(4_900_005_000)
    assert units.round_price(73_000.004, "BTCUSDT") == pytest.approx(73_000.0)


def test_minimum_order_is_three_million_rial_not_toman():
    """SmallOrder = 3,000,000 Rial = 300,000 Toman. Reading it as 3 million
    Toman would let the broker promise 30x smaller orders than the exchange takes."""
    # USDT = 100,805 Toman ⇒ 300,000 Toman ≈ 2.98 USD
    assert units.min_notional_usd("BTCIRT", 100_805.0) == pytest.approx(2.976, abs=0.001)
    assert units.min_notional_usd("BTCUSDT", 100_805.0) == pytest.approx(11.0)


def test_options_endpoint_can_override_precision():
    n = units.apply_options({"nobitex": {"amountPrecisions": {"BTCIRT": "0.00001"},
                                         "pricePrecisions": {"BTCIRT": "100"}}})
    assert n == 2
    assert units.truncate_amount(0.0000199, "BTCIRT") == pytest.approx(0.00001)
    assert units.round_price(4_900_049.0, "BTCIRT") == pytest.approx(4_900_000)
    # restore for the rest of the suite
    units.apply_options({"nobitex": {"amountPrecisions": {"BTCIRT": "0.000001"},
                                     "pricePrecisions": {"BTCIRT": "10"}}})


# --------------------------------------------------------------------------- #
# Envelope: HTTP 200 is not "ok"
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    @property
    def is_success(self):
        return self.status_code < 400

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeHTTP:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    async def request(self, method, path, **kw):
        self.calls.append((method, path, kw))
        return self.resp

    async def aclose(self):
        return None


def _client_with(monkeypatch, resp):
    c = NobitexClient(session_factory=lambda: _FakeHTTP(resp))
    run(c.connect())
    return c, c._client


def test_a_200_with_status_failed_is_an_error_not_a_result(monkeypatch):
    """Docs: business errors arrive as HTTP 200 + {"status":"failed", "code"}.
    The old `_request` returned them as data, so "InsufficientBalance" became an
    empty order book and "InvalidMarket" became a market with no candles."""
    c, _ = _client_with(monkeypatch, _Resp(200, {
        "status": "failed", "code": "InsufficientBalance",
        "message": "موجودی حساب کافی نیست"}))
    with pytest.raises(ClientError) as ei:
        run(c.get("/market/orders/add", attempts=1))
    assert ei.value.code == "InsufficientBalance"
    assert "موجودی" in str(ei.value)


def test_a_200_with_status_ok_still_returns_the_body(monkeypatch):
    c, _ = _client_with(monkeypatch, _Resp(200, {"status": "ok", "stats": {}}))
    assert run(c.get("/market/stats", attempts=1))["status"] == "ok"


def test_429_exposes_the_backoff_the_exchange_asked_for(monkeypatch):
    """Docs §general: 429 carries `backOff` seconds and `limit`; ignoring them
    and retrying immediately gets the token blocked for two minutes."""
    c, _ = _client_with(monkeypatch, _Resp(429, {
        "status": "failed", "code": "TooManyRequests",
        "message": "requests limit exceeded", "backOff": 7, "limit": 20}))
    with pytest.raises(RateLimitError) as ei:
        run(c.get("/market/stats", attempts=1))
    assert ei.value.meta["backoff_s"] == 7.0
    assert ei.value.meta["limit"] == 20


def test_candle_requests_ask_for_countback_not_a_guessed_from():
    """`countback` has priority over `from` in the UDF docs; asking for N bars
    back from `to` is the documented request."""
    client = NobitexClient()
    seen = {}

    async def fake_get(path, params=None, attempts=1):
        seen["path"], seen["params"] = path, dict(params or {})
        return {"s": "ok", "t": [1], "o": [1], "h": [1], "l": [1], "c": [1], "v": [1]}

    client.get = fake_get
    run(client.get_udf_history("BTCIRT", "60", from_ts=0, to_ts=1_700_000_000, count=200))
    assert seen["path"] == "/market/udf/history"
    assert seen["params"]["countback"] == 200
    assert seen["params"]["symbol"] == "BTCIRT"
    assert "from" not in seen["params"]


def test_no_data_is_reported_as_no_data_not_as_an_outage():
    client = NobitexClient()

    async def fake_get(path, params=None, attempts=1):
        return {"s": "no_data", "nextTime": None}

    client.get = fake_get
    with pytest.raises(UpstreamUnavailable) as ei:
        run(client.get_candles_ex("NEWCOINIRT", "1h", count=10))
    msg = str(ei.value)
    assert "no_data" in msg
    assert "circuit breaker" not in msg      # that sent readers to the network


def test_error_state_from_the_exchange_is_surfaced_with_its_message():
    client = NobitexClient()

    async def fake_get(path, params=None, attempts=1):
        return {"s": "error", "errmsg": "unsupported resolution"}

    client.get = fake_get
    with pytest.raises(UpstreamUnavailable) as ei:
        run(client.get_candles_ex("BTCIRT", "1h", count=10))
    assert "unsupported resolution" in str(ei.value)


# --------------------------------------------------------------------------- #
# The universe: documented stats keys must become tradable symbols
# --------------------------------------------------------------------------- #
def test_universe_loads_the_documented_stats_keys(monkeypatch, tmp_path):
    """`btc-rls` → `BTCIRT`. Before this, every row was `BTC-RLS`: the order book
    said "unknown market", the premium had nothing to compare against, and FX
    conversion returned 0 for every symbol."""
    import main

    class C:
        async def get_market_stats(self):
            return {"stats": {
                "btc-rls": {"latest": "490000000", "volumeDst": "900", "dayChange": "2.0",
                            "isClosed": False},
                "usdt-rls": {"latest": "100805", "volumeDst": "1000", "dayChange": "0.1",
                             "isClosed": False},
                "doge-usdt": {"latest": "0.12", "volumeDst": "10", "dayChange": "-1.0",
                              "isClosed": True},
                "global": {"binance-price": "1"},
            }}

    monkeypatch.setattr(main, "client", C())
    monkeypatch.setattr(main, "_cached", {"universe": [], "universe_ts": 0.0,
                                          "closed_markets": [], "signals": {},
                                          "signals_by_tf": {}, "signals_ts": {},
                                          "premium": None, "premium_pct": None})
    monkeypatch.setattr(main, "_UNIVERSE_LOCK", None, raising=False)
    rows = run(main._load_universe_locked())
    syms = {r["symbol"] for r in rows}
    assert syms == {"BTCIRT", "USDTIRT", "DOGEUSDT"}
    assert "BTC-RLS" not in syms
    assert main._cached["closed_markets"] == ["DOGEUSDT"]
    assert next(r for r in rows if r["symbol"] == "USDTIRT")["last"] == pytest.approx(100_805)
    # volumeDst is the documented field; the old code read `valueDst` first.
    assert next(r for r in rows if r["symbol"] == "BTCIRT")["vol24"] == pytest.approx(900)


# --------------------------------------------------------------------------- #
# The broker: a preview that promises a refused order is a lie
# --------------------------------------------------------------------------- #
def _broker(tmp_path, monkeypatch, equity=100_000, price=73_040.0):
    import paper_trading
    from paper_trading import PaperBroker
    from risk_engine import RiskConfig, RiskEngine

    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "market_closed_hook", None)

    class FakeClient:
        def __init__(self):
            self.usdt_irt = 100_805.0

        async def get_orderbook(self, symbol):
            return {"bids": [[str(price * 0.999), "100"]],
                    "asks": [[str(price * 1.001), "100"]],
                    "lastTradePrice": str(price)}

        async def get_market_stats(self):
            return {"stats": {"USDTIRT": {"latest": "100805"}}}

    return PaperBroker(equity, FakeClient(), RiskEngine(RiskConfig(equity=equity)))


def test_broker_refuses_an_order_below_the_exchange_minimum(tmp_path, monkeypatch):
    b = _broker(tmp_path, monkeypatch)
    # 0.0001 BTC ≈ $7.3 — above 11 USDT? No: 7.3 < 11, so the exchange refuses it.
    pos = run(b.place_limit("BTCUSDT", "buy", 0.0001, price=73_040, stop=70_000))
    assert pos is None
    assert b.last_error and b.last_error.startswith("min-notional")


def test_broker_accepts_an_order_just_above_the_minimum(tmp_path, monkeypatch):
    b = _broker(tmp_path, monkeypatch)
    pos = run(b.place_limit("BTCUSDT", "buy", 0.0002, price=73_040, stop=70_000))
    assert pos is not None, b.last_error


def test_broker_refuses_a_closed_market(tmp_path, monkeypatch):
    import paper_trading

    b = _broker(tmp_path, monkeypatch)
    monkeypatch.setattr(paper_trading, "market_closed_hook", lambda s: s == "BTCIRT")
    pos = run(b.place_limit("BTCIRT", "buy", 0.001, price=498_500_000, stop=480_000_000))
    assert pos is None
    assert b.last_error and b.last_error.startswith("market-closed")


def test_broker_truncates_the_fill_to_the_market_precision(tmp_path, monkeypatch):
    b = _broker(tmp_path, monkeypatch)
    pos = run(b.place_limit("BTCUSDT", "buy", 0.00023456789, price=73_040, stop=70_000))
    assert pos is not None, b.last_error
    assert pos.filled_qty == pytest.approx(0.000234, abs=1e-12)
    assert pos.entry_price == pytest.approx(73_040.0)


def test_whatif_reports_the_same_two_exchange_gates(tmp_path, monkeypatch):
    """The what-if panel runs the broker's `preflight`; the gates it prints are
    the ones the broker enforces. A preview missing a gate is a preview that
    promises an order the broker (and the exchange) will reject."""
    from risk_engine import preflight

    pf = preflight(equity=100_000, cash=100_000, open_heat=0.0, open_positions=0,
                   concurrent_limit=5, max_heat_pct=6.0, min_cash_pct=10.0,
                   max_single_asset_pct=25.0, risk_pct_cap=0.75, halted=False,
                   notional_usd=0.5, fee_usd=0.001, risk_usd=0.02,
                   min_notional_usd=11.0, market_closed=True)
    ids = {g.id: g for g in pf.gates}
    assert not ids["min-notional"].ok
    assert "11.00" in ids["min-notional"].detail
    assert not ids["market-closed"].ok
    assert pf.blocked
    assert pf.first_block().id in ("min-notional", "market-closed")


def test_options_minimums_replace_the_shipped_defaults():
    units.apply_options({"nobitex": {"minOrders": {"rls": "5000000", "usdt": "12"}}})
    try:
        assert units.MIN_NOTIONAL_QUOTE["rls"] == 5_000_000.0
        assert units.MIN_NOTIONAL_QUOTE["usdt"] == 12.0
    finally:
        units.apply_options({"nobitex": {"minOrders": {"rls": "3000000", "usdt": "11"}}})


# --------------------------------------------------------------------------- #
# WebSocket: channel naming and the published payload
# --------------------------------------------------------------------------- #
def _stream():
    from ws_execution import StreamExecutor

    s = StreamExecutor()
    got = []
    s.on_tick.append((None, lambda sym, px: got.append((sym, px))))
    return s, got


def test_ws_subscribes_to_the_documented_public_channel_name():
    """The exchange publishes `public:orderbook-{symbol}`; a `market:` prefix
    connects and then delivers nothing, forever."""
    from ws_execution import WS_CHANNEL_PREFIX

    assert WS_CHANNEL_PREFIX == "public:orderbook-"


def test_ws_parses_the_published_orderbook_frame():
    """Docs §websocket: `pub.data` is a JSON string carrying full L2 snapshots in
    [price, amount] arrays; `bestBid`/`bestAsk` do not exist in the payload."""
    s, got = _stream()
    payload = json.dumps({"asks": [["35077909990", "0.009433"], ["35078000000", "0.000274"]],
                          "bids": [["35020080080", "0.185784"], ["35020070060", "0.086916"]],
                          "lastTradePrice": "35077909990", "lastUpdate": 1726581829816})
    frame = json.dumps({"push": {"channel": "public:orderbook-BTCIRT",
                                 "pub": {"data": payload, "offset": 989153}}})
    s._on_ws_message(frame)
    assert got, "the frame produced no tick"
    sym, px = got[-1]
    assert sym == "BTCIRT"
    # 35,077,909,990 Rial is 3,507,790,999 Toman; mid of best bid/ask.
    assert px == pytest.approx((3_502_008_008 + 3_507_790_999) / 2, rel=1e-9)


def test_ws_also_accepts_a_dict_payload_from_the_sdk():
    s, got = _stream()
    frame = {"push": {"channel": "public:orderbook-BTCUSDT",
                      "pub": {"data": {"bids": [["73000", "1"]], "asks": [["73010", "1"]]}}}}
    s._on_ws_message(json.dumps(frame))
    assert got and got[-1][0] == "BTCUSDT"
    assert got[-1][1] == pytest.approx(73_005.0)


def test_equity_marks_a_persisted_irt_position_without_a_live_fx_rate(tmp_path, monkeypatch):
    """A restart used to value every IRT position with `usdt_irt = 0` → divide by
    1.0 → Toman counted as dollars. Equity jumped by the FX rate on every
    restart, and with it every risk percentage."""
    b = _broker(tmp_path, monkeypatch)
    pos = run(b.place_limit("BTCIRT", "buy", 0.001, price=498_500_000, stop=480_000_000))
    assert pos is not None, b.last_error
    assert b.equity == pytest.approx(100_000, abs=0.5)

    # Simulate the restart: a fresh broker from the same state, no FX cached yet.
    from paper_trading import PaperBroker
    from risk_engine import RiskConfig, RiskEngine

    b2 = PaperBroker(100_000, b.client, RiskEngine(RiskConfig(equity=100_000)))
    b2.usdt_irt = 0.0
    b2._mark_equity_minute()
    assert b2.equity == pytest.approx(100_000, abs=0.5)


def test_closing_an_irt_position_uses_its_entry_rate_not_a_missing_one(tmp_path, monkeypatch):
    b = _broker(tmp_path, monkeypatch, price=498_500_000)
    pos = run(b.place_limit("BTCIRT", "buy", 0.001, price=498_500_000, stop=480_000_000))
    assert pos is not None, b.last_error
    b.usdt_irt = 0.0                       # FX gone after a restart
    run(b._close(pos.id, "test"))
    # Cash must come back to roughly what it was; the old code turned a $7.30
    # position into a 498,500-dollar credit.
    assert b.cash == pytest.approx(100_000, abs=1.0)


def test_every_paper_order_carries_a_documented_client_order_id(tmp_path, monkeypatch):
    """The ticket says orders receive a `clientOrderId`; the docs cap it at 32
    characters and use it to deduplicate. So one is produced, and it fits."""
    b = _broker(tmp_path, monkeypatch)
    pos = run(b.place_limit("BTCUSDT", "buy", 0.001, price=73_040, stop=70_000))
    assert pos is not None, b.last_error
    assert pos.client_order_id and len(pos.client_order_id) <= 32
    submit = [e for e in b.orders if e.kind == "submit"][-1]
    assert submit.extra["clientOrderId"] == pos.client_order_id


# --------------------------------------------------------------------------- #
# The order log: the table must have a real source
# --------------------------------------------------------------------------- #
def _client():
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)


def test_orders_route_reports_a_real_order_with_its_id(tmp_path, monkeypatch):
    """`/api/paper/orders` is the only source the Orders tab has. It must carry
    the clientOrderId, the fill, and the slippage that was actually measured."""
    import main

    b = _broker(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_broker", lambda *a, **k: b)
    monkeypatch.setattr(main, "client", b.client)
    # A limit order names its price, so there is no mid to measure against:
    # the column must stay null rather than print a zero nobody observed.
    pos = run(b.place_limit("BTCUSDT", "buy", 0.001, price=73_040, stop=70_000))
    assert pos is not None, b.last_error
    pos_mkt = run(b.place_limit("BTCUSDT", "buy", 0.001, price=None, stop=70_000))
    assert pos_mkt is not None, b.last_error

    r = _client().get("/api/paper/orders")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["source"] == "paper-broker"
    by_id = {row["clientOrderId"]: row for row in d["rows"]}
    row = by_id[pos.client_order_id]
    assert row["state"] == pos.state
    assert row["filled"] == pytest.approx(pos.filled_qty)
    assert row["type"] == "limit" and row["slip_bps"] is None

    # A market order walked the book, so the mid was recorded and slippage is real.
    mkt = by_id[pos_mkt.client_order_id]
    assert mkt["type"] == "market"
    assert mkt["slip_bps"] is not None and mkt["slip_bps"] > 0


def test_orders_route_keeps_a_rejected_order_as_a_row(tmp_path, monkeypatch):
    """A refusal is an order too. The table says which gate refused it instead
    of showing nothing."""
    import main

    b = _broker(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_broker", lambda *a, **k: b)
    pos = run(b.place_limit("BTCUSDT", "buy", 0.0001, price=73_040, stop=70_000))
    assert pos is None
    d = _client().get("/api/paper/orders").json()
    assert d["counts"]["rejected"] == 1
    row = d["rows"][0]
    assert row["state"] == "REJECTED"
    assert row["reason"] == "min-notional"


def test_orders_route_is_empty_but_valid_on_a_fresh_account(tmp_path, monkeypatch):
    import main

    b = _broker(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_broker", lambda *a, **k: b)
    d = _client().get("/api/paper/orders").json()
    assert d["ok"] is True and d["rows"] == []
    assert d["counts"] == {"total": 0, "open": 0, "closed": 0, "rejected": 0}


def test_legacy_events_without_client_ids_do_not_split_in_two(tmp_path, monkeypatch):
    """State files written before `clientOrderId` exist in the wild. A `submit`
    and its `fill` must still be one row, or every old order appears twice."""
    import main
    from paper_trading import OrderEvent

    b = _broker(tmp_path, monkeypatch)
    b.orders.append(OrderEvent(1000.0, "submit", "BTCUSDT", "buy", 0.001, 73_000, {"source": "SEMI"}))
    b.orders.append(OrderEvent(1000.5, "fill", "BTCUSDT", "buy", 0.001, 73_010, {"fee": 1.0}))
    monkeypatch.setattr(main, "get_broker", lambda *a, **k: b)
    d = _client().get("/api/paper/orders").json()
    assert len(d["rows"]) == 1
    row = d["rows"][0]
    assert row["state"] == "FILLED"
    assert row["events"] == ["submit", "fill"]


def test_two_orders_on_one_market_in_the_same_millisecond_both_exist(tmp_path, monkeypatch):
    """Position ids were `pp-<ms>-<symbol>`. Two fills in one millisecond — an
    approved queue item and a manual ticket, say — produced the same key, so the
    second position overwrote the first: both priced into cash, one recorded."""
    b = _broker(tmp_path, monkeypatch)
    a = run(b.place_limit("BTCUSDT", "buy", 0.001, price=73_040, stop=70_000))
    c = run(b.place_limit("BTCUSDT", "buy", 0.001, price=73_040, stop=70_000))
    assert a is not None and c is not None
    assert a.id != c.id
    assert len([p for p in b.positions.values() if p.state != "CLOSED"]) == 2
    # And the ledger knows about both, not just the last one.
    assert b.cash == pytest.approx(100_000 - 2 * a.size_usd * (1 + 0.002), abs=0.02)


def test_fixture_candles_keep_epoch_seconds(tmp_path, monkeypatch):
    """The fixture adapter used to divide candle timestamps by 1000.

    The chart's x-axis, the "age of last candle" label and the approval queue's
    TTL all read `t` as epoch seconds, so the whole demo ran on candles dated
    January 1970 while every price still looked right. Pin the unit at the
    boundary: what goes in as seconds comes out as seconds.
    """
    import importlib.util

    repo = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "nobi_run_with_mock_exchange", repo / "scripts" / "run_with_mock_exchange.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    now = int(time.time()) // 900 * 900
    rows = [{"t": now - 900, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 7.0},
            {"t": now, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 7.0}]
    udf = mod.to_udf(rows)
    assert udf["t"] == [now - 900, now]
    # Within one candle of the wall clock: catches a /1000 or *1000 rescale.
    assert abs(udf["t"][-1] - time.time()) < 900
    assert udf["c"] == [1.05, 1.05]


# --------------------------------------------------------------------------- #
# WebSocket: the keep-alive, the market-wide channels, and the merge rules
# --------------------------------------------------------------------------- #
class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


def test_ws_answers_the_centrifugo_keepalive_once_per_second():
    """Docs §websocket: the server sends `{}` and expects `{}` back within 25 s.
    `websockets`' protocol ping is a different layer; a client that only replies
    to that one is dropped every 25 seconds, forever."""
    s, _ = _stream()
    ws = _FakeWS()
    handled = run(s._handle_keepalive(ws, "{}"))
    assert handled is True
    assert ws.sent == ["{}"]
    assert s.pongs == 1
    # A peer that echoes the pong back must not start a loop.
    run(s._handle_keepalive(ws, "{}"))
    assert ws.sent == ["{}"] and s.pong_echoes == 1
    assert run(s._handle_keepalive(ws, '{"push": {}}')) is False


def test_ws_market_stats_channel_is_scaled_like_the_rest_route():
    """One Rial→Toman rule, not two. A private copy on the socket path quoted
    every Rial market ten times too high — and the board looked plausible."""
    s, _ = _stream()
    seen = []
    s.on_market.append(lambda sym, st: seen.append((sym, st.latest, st.closed)))
    payload = json.dumps({
        "btc-irt": {"latest": "35077909990", "dayChange": "-5.12", "isClosed": False},
        "usdt-irt": {"latest": "1008050", "isClosed": True},
        "btc-usdt": {"latest": "73000", "isClosed": False},
    })
    s._on_ws_message(json.dumps({"push": {"channel": "public:market-stats-all",
                                          "pub": {"data": payload}}}))
    by_sym = {sym: (px, closed) for sym, px, closed in seen}
    assert by_sym["BTCIRT"][0] == pytest.approx(3_507_790_999.0)   # Toman
    assert by_sym["USDTIRT"][0] == pytest.approx(100_805.0)        # Toman
    assert by_sym["BTCUSDT"][0] == pytest.approx(73_000.0)         # dollars stay
    assert s.markets["USDTIRT"].closed is True
    assert s.stats_frames == 1 and s.ws_messages == 1


def test_ws_candle_replaces_the_last_row_of_a_rest_series():
    """The candle channel is Rial-quoted; `/api/candles` already returns Toman."""
    s, _ = _stream()
    frame = {"push": {"channel": "public:candle-BTCIRT-15",
                      "pub": {"data": json.dumps({"t": 1792488600, "o": 89_700_000_000,
                                                  "h": 89_750_000_000, "l": 89_600_000_000,
                                                  "c": 89_720_000_000, "v": 1.25})}}}
    s._on_ws_message(json.dumps(frame))
    rows = [{"t": 1792487700, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": 1792488600, "o": 8.95e9, "h": 8.95e9, "l": 8.95e9, "c": 8.95e9, "v": 9}]
    merged = s.merge_last_candle("BTCIRT", "15m", rows)
    assert len(merged) == 2                      # same candle: replaced, not appended
    assert merged[-1]["c"] == pytest.approx(8.972e9, rel=1e-6)
    # A newer candle appends.
    nxt = {"push": {"channel": "public:candle-BTCIRT-15",
                    "pub": {"data": json.dumps({"t": 1792489500, "o": 1, "h": 1, "l": 1,
                                                "c": 9_000_000_000, "v": 1})}}}
    s._on_ws_message(json.dumps(nxt))
    assert len(s.merge_last_candle("BTCIRT", "15m", merged)) == 3
    # An older frame never rewrites history.
    old = {"push": {"channel": "public:candle-BTCIRT-15",
                    "pub": {"data": json.dumps({"t": 1792486800, "o": 1, "h": 1, "l": 1,
                                                "c": 1, "v": 1})}}}
    s._on_ws_message(json.dumps(old))
    assert s.merge_last_candle("BTCIRT", "15m", rows) == rows


def test_ws_channels_cover_stats_candles_and_the_book():
    s, _ = _stream()
    s.want_stats = True
    s.candle_tf = "15m"
    s._subs.add("BTCIRT")
    st = s.status()
    # Nothing has been sent on a socket that is not connected.
    assert st["channels"] == []
    wanted = st["channels_wanted"]
    assert "public:market-stats-all" in wanted
    assert "public:candle-BTCIRT-15" in wanted
    assert "public:orderbook-BTCIRT" in wanted


def test_late_subscription_reaches_the_socket():
    """A position opened after the handshake used to be "subscribed" in memory
    and never on the wire: the frames were only sent at connect time."""
    s, _ = _stream()
    s.want_stats = False
    ws = _FakeWS()
    s._ws_conn = ws
    s._subs.add("ETHIRT")
    run(s._send_missing_subs())
    assert len(ws.sent) == 2
    channels = [json.loads(x)["subscribe"]["channel"] for x in ws.sent]
    assert "public:orderbook-ETHIRT" in channels
    assert "public:candle-ETHIRT-15" in channels
    run(s._send_missing_subs())          # idempotent: nothing sent twice
    assert len(ws.sent) == 2


def test_stream_stats_update_the_cached_board_and_the_closed_gate(monkeypatch):
    """The `market-closed` gate must see a market that closes between polls."""
    import main
    from ws_execution import MarketStat

    monkeypatch.setattr(main, "_cached", {**main._cached,
                                          "universe": [{"symbol": "BTCIRT", "last": 1.0,
                                                        "change24": 0.0, "closed": False}]})
    monkeypatch.setattr(main, "_CACHE_CLOSED", set())
    st = MarketStat(latest=3_507_790_999.0, day_change=-5.12, closed=True, updated=1.0)
    main._apply_stream_stat("BTCIRT", st)
    row = main._cached["universe"][0]
    assert row["last"] == pytest.approx(3_507_790_999.0)
    assert row["closed"] is True and row["live"] == "ws"
    assert "BTCIRT" in main._CACHE_CLOSED
    st.closed = False
    main._apply_stream_stat("BTCIRT", st)
    assert "BTCIRT" not in main._CACHE_CLOSED


def test_risk_state_reports_the_account_the_gates_use(tmp_path, monkeypatch):
    """`/api/risk/state` used to print `risk.cfg.equity` — the starting constant —
    while every gate compared against `broker.equity`. After a few trades the
    page and the account disagreed, and `/api/risk/plan` sized new orders from
    money that was no longer there."""
    import main

    b = _broker(tmp_path, monkeypatch)
    run(b.place_limit("BTCUSDT", "buy", 0.001, price=73_040, stop=70_000))
    monkeypatch.setattr(main, "get_broker", lambda *a, **k: b)
    d = _client().get("/api/risk/state").json()
    assert d["equity"] == pytest.approx(b.equity)
    assert d["start_equity"] == pytest.approx(100_000.0)
    assert d["equity"] != pytest.approx(100_000.0)      # fees moved it
    assert d["equity_source"] == "paper-broker"

# --------------------------------------------------------------------------- #
# Execution mode: auto / semi / manual
# --------------------------------------------------------------------------- #
def test_exec_mode_is_server_state_and_survives_a_round_trip(tmp_path, monkeypatch):
    """The mode used to be a browser string: «خودکار» changed a chip and a toast
    and nothing read it, while a hard-coded `symMode` table overrode the human's
    per-symbol choice. It now lives on the server, and this test reads it back
    through the same route the page calls."""
    import main
    from exec_mode import ModeStore

    store = ModeStore()
    monkeypatch.setattr(main, "EXEC_MODE", store)
    monkeypatch.setattr(main, "_EXEC_LOADED", [True])
    monkeypatch.setattr(main, "_exec_mode_save", lambda: None)

    r = _client().post("/api/exec/mode", json={"symbol": "ETHIRT", "mode": "auto"}).json()
    assert r["ok"] is True
    assert r["symbols"] == {"ETHIRT": "auto"}
    assert r["executor"] == "paper" and r["sends_to_exchange"] is False

    d = _client().get("/api/exec/mode").json()
    assert d["default"] == "semi"
    assert d["symbols"]["ETHIRT"] == "auto"
    assert d["auto"]["enabled"] is True

    # Clearing an override is explicit; an unknown mode is refused by name.
    r = _client().post("/api/exec/mode", json={"symbol": "ETHIRT", "mode": ""}).json()
    assert r["symbols"] == {}
    bad = _client().post("/api/exec/mode", json={"mode": "yolo"}).json()
    assert bad["ok"] is False and "auto" in bad["error"]


def _auto_fixture(tmp_path, monkeypatch, *, halted=False):
    """A broker with one LONG signal cached and a deterministic ATR."""
    import main

    b = _broker(tmp_path, monkeypatch, price=73_040.0)
    b.halted = halted
    snap = {"symbol": "BTCUSDT", "direction": "LONG", "lastClose": 73_040.0,
            "risk": {"atr": 0.02 * 73_040.0}, "composite": 81.0}
    monkeypatch.setattr(main, "_cached", {**main._cached,
                                          "signals": {"BTCUSDT": snap},
                                          "signals_by_tf": {"15m": {"BTCUSDT": snap}},
                                          "universe": []})
    monkeypatch.setattr(main, "get_broker", lambda *a, **k: b)
    monkeypatch.setattr(main, "_AUTO_LAST", {})
    monkeypatch.setattr(main, "_AUTO_STATE", {"last_tick_at": None, "placed": 0,
                                             "skipped": 0, "rejected": 0,
                                             "last_error": None, "last_symbol": None,
                                             "last_action": None})

    def fake_audit(*a, **k):
        return 12

    monkeypatch.setattr(main, "audit", fake_audit)
    return main, b


def test_auto_mode_places_a_real_paper_order_and_semi_places_nothing(tmp_path, monkeypatch):
    """`auto` has to end in an order row, not in a chip. `semi` has to end in
    none — the queue is the only way in."""
    import asyncio

    main, b = _auto_fixture(tmp_path, monkeypatch)
    main.EXEC_MODE.default = "semi"
    main.EXEC_MODE.symbols = {}
    res = asyncio.run(main.auto_tick("15m"))
    assert res["placed"] == 0
    assert not b.positions

    main.EXEC_MODE.symbols = {"BTCUSDT": "auto"}
    res = asyncio.run(main.auto_tick("15m"))
    assert res["placed"] == 1, res
    pos = next(iter(b.positions.values()))
    assert pos.symbol == "BTCUSDT" and pos.state == "OPEN"
    assert any(e.extra.get("source") == "AUTO" and e.kind == "submit" for e in b.orders)
    assert main._AUTO_STATE["placed"] == 1 and main._AUTO_STATE["last_action"] == "placed"


def test_auto_mode_does_not_stack_or_repeat_on_the_next_tick(tmp_path, monkeypatch):
    """A second pass must not open a second position on the same market, and a
    refused plan must not be retried on every tick: the cooldown is the reason
    the audit does not fill with the same sentence."""
    import asyncio

    main, b = _auto_fixture(tmp_path, monkeypatch)
    main.EXEC_MODE.symbols = {"BTCUSDT": "auto"}
    first = asyncio.run(main.auto_tick("15m"))
    assert first["placed"] == 1
    main._AUTO_LAST.clear()                     # ignore the cooldown: position guard must hold
    second = asyncio.run(main.auto_tick("15m"))
    assert second["placed"] == 0 and second["skipped"] == 1
    assert len([p for p in b.positions.values() if p.state == "OPEN"]) == 1


def test_auto_mode_surfaces_the_gates_refusal_instead_of_a_position(tmp_path, monkeypatch):
    """With the kill switch armed the executor must place nothing and say which
    gate refused, the same way an approved queue row does."""
    import asyncio

    main, b = _auto_fixture(tmp_path, monkeypatch, halted=True)
    main.EXEC_MODE.symbols = {"BTCUSDT": "auto"}
    res = asyncio.run(main.auto_tick("15m"))
    assert res["placed"] == 0 and res["rejected"]
    assert "halted" in res["rejected"][0]["reason"]
    assert not b.positions
    assert b.last_error and b.last_error.startswith("halted")


def test_auto_default_covers_the_scanned_symbols_only(tmp_path, monkeypatch):
    """`auto` as a portfolio default must not invent symbols: it acts on the
    symbols the last scan actually scored, nothing else."""
    import asyncio

    main, b = _auto_fixture(tmp_path, monkeypatch)
    main.EXEC_MODE.default = "auto"
    main.EXEC_MODE.symbols = {}
    res = asyncio.run(main.auto_tick("15m"))
    assert res["considered"] == 1 and res["placed"] == 1
    assert set(b.positions) and all(p.symbol == "BTCUSDT" for p in b.positions.values())
    main.EXEC_MODE.default = "semi"


def test_positions_are_marked_from_the_socket_not_a_poll(tmp_path, monkeypatch):
    """Marking an open position asked REST for a book on every tick even while
    the socket had just delivered one; the log looked like the WebSocket was
    dead because the same symbol appeared in an HTTP request every few seconds.
    A fresh socket mid is now the first source, and REST is the fallback."""
    import paper_trading

    b = _broker(tmp_path, monkeypatch)
    calls = {"n": 0}

    async def counting(symbol):
        calls["n"] += 1
        return {"bids": [["73000", "1"]], "asks": [["74000", "1"]], "lastTradePrice": "73500"}

    b.client.get_orderbook = counting            # type: ignore[method-assign]
    monkeypatch.setattr(paper_trading, "hot_mid_hook", lambda s: 73_500.0)
    assert run(b._mid_price("BTCUSDT")) == pytest.approx(73_500.0)
    assert calls["n"] == 0

    monkeypatch.setattr(paper_trading, "hot_mid_hook", lambda s: None)   # stale socket
    assert run(b._mid_price("BTCUSDT")) == pytest.approx(73_500.0)
    assert calls["n"] == 1


def test_a_partially_filled_market_order_is_not_reported_as_full(tmp_path, monkeypatch):
    """The orders page computed `filled_pct = filled / amount`, and on a market
    order `amount` *was* the fill — so a 5-of-20 fill printed 100 %. The broker
    now records what was requested, and the row compares against that."""
    import main
    import paper_trading
    from paper_trading import PaperBroker
    from risk_engine import RiskConfig, RiskEngine

    class ThinBook:
        usdt_irt = 1.0

        async def get_orderbook(self, symbol):
            # Two levels, but only five units in total: a twenty-unit order
            # cannot fill.
            return {"bids": [["999", "5"]], "asks": [["1000", "5"]],
                    "lastTradePrice": "1000"}

        async def get_market_stats(self):
            return {"stats": {}}

    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "market_closed_hook", None)
    monkeypatch.setattr(paper_trading, "hot_mid_hook", None)
    b = PaperBroker(100_000, ThinBook(), RiskEngine(RiskConfig(equity=100_000)))
    pos = run(b.place_limit("BTCUSDT", "buy", 20))
    assert pos is not None, b.last_error
    assert pos.filled_qty == pytest.approx(5.0)
    assert pos.requested_qty == pytest.approx(20.0)
    rows = main._order_rows(b)
    assert rows[0]["filled"] == pytest.approx(5.0)
    assert rows[0]["filled_pct"] == pytest.approx(25.0)


def test_a_tick_does_not_move_another_positions_price():
    """`on_tick` used to be a flat list: every registered callback received
    every symbol's price. `streams_start` registers one callback per open
    position, so a BTCIRT tick rewrote an ETHIRT position's `last_price` with
    8.97e9 — and `_mark_equity_minute` divided that by the ETH rate, turning a
    100k account into 5.3 billion."""
    s, got = _stream()
    eth_seen = []
    s.on_tick.append(("ETHIRT", lambda sym, px: eth_seen.append(px)))
    s._emit("BTCIRT", 8_970_000_000.0)
    assert got == [("BTCIRT", 8_970_000_000.0)]      # the global listener still sees all
    assert eth_seen == []                             # the ETH callback does not
    s._emit("ETHIRT", 315_000_000.0)
    assert eth_seen == [315_000_000.0]
