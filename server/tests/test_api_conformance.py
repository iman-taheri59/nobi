"""Conformance tests against the Nobitex API documentation.

Each test here pins one documented rule the app used to ignore. The doc reference
is in the test name or docstring; the code under test is the one that decides
what the dashboard shows or what the paper broker places.
"""
from __future__ import annotations

import asyncio
import json

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
    s.on_tick.append(lambda sym, px: got.append((sym, px)))
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
