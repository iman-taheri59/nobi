"""The money type: units that cannot be confused, and no silent 1.0.

Each test here pins a bug the project actually shipped. The names say which.
"""
from __future__ import annotations

import time

import pytest

import money
from money import Money, NoRate, Rate, UnitMismatch, UnknownUnit, rial, toman, usd, usdt


def _rate(toman_per_usdt: float = 100_805.0, age_s: float = 0.0) -> Rate:
    return Rate(toman_per_usdt=toman_per_usdt, as_of=time.time() - age_s)


# --------------------------------------------------------------------------- #
# The definition: 1 Toman = 10 Rial
# --------------------------------------------------------------------------- #
def test_rial_converts_to_toman_by_the_definition_not_a_market_price():
    """`/market/stats` sends Rial and the page labels Toman. Ten, exactly."""
    assert rial(8_970_000_000).to_irt().amount == pytest.approx(897_000_000)
    assert toman(897_000_000).convert("IRR").amount == pytest.approx(8_970_000_000)


def test_a_rial_amount_cannot_be_added_to_a_toman_amount():
    """Both are "money in Iran"; adding them is a 10× error that no compiler
    catches when they are plain floats."""
    with pytest.raises(UnitMismatch):
        rial(1_000) + toman(1_000)
    with pytest.raises(UnitMismatch):
        usd(1) + toman(1)
    assert toman(1_000) + toman(1_000) == toman(2_000)


# --------------------------------------------------------------------------- #
# Crossing the exchange rate: explicit, dated, or refused
# --------------------------------------------------------------------------- #
def test_usd_to_toman_uses_the_supplied_rate():
    r = _rate(100_805.0)
    assert usd(100_000).to_irt(r).amount == pytest.approx(10_080_500_000)
    assert toman(1_008_050).to_usd(r).amount == pytest.approx(10.0)


def test_converting_without_a_rate_raises_instead_of_assuming_one():
    """The two ways this used to go wrong, both silent:
      * `equity_quote = eq * fx if fx else eq` — 100,000 Toman treated as
        100,000 USD-worth of equity, so every order was ~100,000× too small;
      * `fx = await _fx_rate(sym) or 1.0` — a 300,000-Toman minimum order read
        as 300,000 USD, so the single-asset gate vetoed sound orders.
    """
    with pytest.raises(NoRate):
        toman(1).to_usd()
    with pytest.raises(NoRate):
        usd(1).to_irt()
    with pytest.raises(NoRate):
        toman(1).convert("USD")


def test_a_stale_rate_is_refused_when_the_caller_asks_for_freshness():
    """`PaperBroker._quote_rate` cached the first rate it ever saw and never
    refreshed it: a process up for a week sized new orders at last week's
    price. A rate now has an age, and `require_fresh` is the default."""
    old = _rate(100_805.0, age_s=3_600)
    assert old.is_stale is True
    with pytest.raises(NoRate):
        usd(1).to_irt(old)
    # A historical report may legitimately want the old one, and can say so.
    assert usd(1).to_irt(old, require_fresh=False).amount == pytest.approx(100_805)


def test_a_rate_that_is_zero_or_missing_is_not_a_rate():
    for bad in (0, -1, None, "abc"):
        with pytest.raises(NoRate):
            Rate(toman_per_usdt=bad, as_of=time.time())


def test_usdt_is_usd_by_decision_and_says_so():
    """No exchange market quotes USD; the account is denominated in what USDT
    is worth. The identity lives in one function so a depeg has one place to go."""
    assert usdt(11).to_usd().amount == pytest.approx(11.0)
    assert usd(11).convert("USDT").amount == pytest.approx(11.0)


# --------------------------------------------------------------------------- #
# Round trips and percentages
# --------------------------------------------------------------------------- #
def test_a_round_trip_returns_the_original_amount():
    r = _rate(100_805.0)
    for amount in (1, 100, 100_000, 12_345_678.9):
        assert usd(amount).to_irt(r).to_usd(r).amount == pytest.approx(amount)


def test_percentages_require_the_same_unit():
    assert usd(750).pct_of(usd(100_000)) == pytest.approx(0.75)
    with pytest.raises(UnitMismatch):
        usd(750).pct_of(toman(100_000))
    with pytest.raises(money.MoneyError):
        usd(1).pct_of(usd(0))


def test_the_account_currency_is_one_constant():
    assert money.ACCOUNT_UNIT == "USD"


# --------------------------------------------------------------------------- #
# The API boundary: what unit did the exchange actually send?
# --------------------------------------------------------------------------- #
def test_from_api_reads_the_market_to_decide_the_unit():
    """A Rial market sends Rial; a USDT market sends USDT. The symbol is the only
    thing that says which, so it is required rather than defaulted."""
    assert money.from_api(8_970_000_000, "BTCIRT") == rial(8_970_000_000)
    assert money.from_api(73_000, "BTCUSDT") == usdt(73_000)
    with pytest.raises(UnknownUnit):
        Money(1, "BTC")                 # a coin amount is not money


# --------------------------------------------------------------------------- #
# Wired into the broker: one rate, refreshed, and live marking
# --------------------------------------------------------------------------- #
def test_the_brokers_rate_expires_instead_of_being_cached_forever(tmp_path, monkeypatch):
    """`_quote_rate` fetched once and kept the value for the life of the
    process. A server up for a week priced new orders at last week's rate."""
    import paper_trading
    from paper_trading import PaperBroker
    from risk_engine import RiskConfig, RiskEngine

    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "RATE_TTL_S", 10.0)

    class Moving:
        usdt_irt = 100_000.0
        calls = 0

        async def get_market_stats(self):
            Moving.calls += 1
            return {"stats": {"USDTIRT": {"latest": str(Moving.usdt_irt)}}}

    b = PaperBroker(100_000, Moving(), RiskEngine(RiskConfig(equity=100_000)))
    assert run_rate(b._quote_rate("BTCIRT")) == pytest.approx(100_000)
    assert Moving.calls == 1

    Moving.usdt_irt = 200_000.0
    # Inside the window the cached value stands: marking must not become a
    # request per tick.
    assert run_rate(b._quote_rate("BTCIRT")) == pytest.approx(100_000)
    assert Moving.calls == 1

    b.usdt_irt_at -= 11                       # the window has passed
    assert run_rate(b._quote_rate("BTCIRT")) == pytest.approx(200_000)
    assert Moving.calls == 2


def run_rate(coro):
    import asyncio

    return asyncio.run(coro)


def test_an_unreachable_exchange_yields_no_rate_not_zero_and_not_one(tmp_path, monkeypatch):
    import paper_trading
    from paper_trading import PaperBroker
    from risk_engine import RiskConfig, RiskEngine

    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")

    class Dead:
        usdt_irt = 0.0

        async def get_market_stats(self):
            raise RuntimeError("exchange down")

    b = PaperBroker(100_000, Dead(), RiskEngine(RiskConfig(equity=100_000)))
    assert run_rate(b._fx()) is None
    assert run_rate(b._quote_rate("BTCIRT")) == 0.0        # named rejection upstream
    assert run_rate(b._quote_rate("BTCUSDT")) == pytest.approx(1.0)


def test_a_moving_toman_does_not_invent_profit_in_the_usd_account(tmp_path, monkeypatch):
    """The account holds USDT, so a BTC/IRT position's USD value must not change
    when only the exchange rate does.

    The old marking divided the *live* Toman price by the *entry* rate. With the
    Toman halving (and the Toman price of BTC doubling, as arbitrage requires),
    an untouched position reported twice its value: a 100 % gain nobody earned,
    and every later order looked proportionally safer than it was.
    """
    import paper_trading
    from paper_trading import PaperBroker
    from risk_engine import RiskConfig, RiskEngine

    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "market_closed_hook", None)
    monkeypatch.setattr(paper_trading, "hot_mid_hook", None)

    class Book:
        usdt_irt = 100_000.0

        def __init__(self):
            self.px = 1_000_000.0        # Toman per BTC

        async def get_orderbook(self, symbol):
            return {"bids": [[str(self.px), "10"]], "asks": [[str(self.px), "10"]],
                    "lastTradePrice": str(self.px)}

        async def get_market_stats(self):
            return {"stats": {"USDTIRT": {"latest": str(self.usdt_irt)}}}

    feed = Book()
    b = PaperBroker(100_000, feed, RiskEngine(RiskConfig(equity=100_000)))
    pos = run_rate(b.place_limit("BTCIRT", "buy", 2.0, price=1_000_000.0,
                                 stop=950_000.0))
    assert pos is not None, b.last_error
    entry_equity = b.equity
    assert entry_equity == pytest.approx(100_000 - pos.size_usd * paper_trading.FEE_PCT, abs=1)

    # The Toman loses half its value; BTC/IRT doubles; BTC/USDT is unchanged.
    feed.usdt_irt = 200_000.0
    feed.px = 2_000_000.0
    b.usdt_irt_at = 0.0                      # the new rate is now the one in force
    run_rate(b._fx(force=True))
    pos.last_price = feed.px
    b._mark_equity_minute()

    # Same dollars as before the move (within the fee), not double.
    assert b.equity == pytest.approx(entry_equity, abs=2.0)
    assert b.equity < 100_100
