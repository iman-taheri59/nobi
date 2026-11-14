# mypy: check_untyped_defs = False
"""A limit order is not a promise to fill, and it never fills at its own price.

The paper broker used to take every limit order at face value:

    fill_price = price
    filled_qty = qty

without reading the book. A buy limit below the market and a sell limit above
it therefore filled instantly, completely, at the better price — the paper
account booked the distance between the limit and the market as profit on every
single order. That is not a modelling simplification; it is the mechanism that
manufactures the edge the paper book reports.

What the fix has to guarantee, and what each test below pins:

1. a limit that the book does not cross does not fill at all (`no-cross`);
2. a limit that crosses fills at the *book* price, never at the limit price;
3. a limit that crosses only part of the size fills only that part, and the
   position keeps `requested_qty > filled_qty`;
4. the preview (`execution_preview`) and the submit (`place_limit`) agree,
   because they share `_execution_quote` — a preview that promises a fill the
   submit path refuses is worse than no preview.
"""
from __future__ import annotations

import asyncio

import pytest

from paper_trading import PaperBroker
from risk_engine import RiskConfig, RiskEngine


class Book:
    """A two-sided book whose depth the test controls, in USD-quoted terms."""

    def __init__(self, bids, asks, usdt_irt: float = 1.0):
        self.bids = [[str(p), str(a)] for p, a in bids]
        self.asks = [[str(p), str(a)] for p, a in asks]
        self.usdt_irt = usdt_irt
        self.exchange_calls = 0

    async def get_orderbook(self, symbol):
        return {"bids": self.bids, "asks": self.asks,
                "lastTradePrice": self.asks[0][0] if self.asks else "0"}

    async def get_market_stats(self):
        return {"stats": {"USDTIRT": {"latest": str(self.usdt_irt)}}}

    async def place_order(self, *_a, **_kw):
        self.exchange_calls += 1
        raise AssertionError("a paper order must never reach the exchange")


def _broker(tmp_path, monkeypatch, book: Book):
    import paper_trading

    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "market_closed_hook", None)
    monkeypatch.setattr(paper_trading, "hot_mid_hook", None)
    monkeypatch.setattr(paper_trading, "PAPER_BROKER", None)
    monkeypatch.setattr(paper_trading, "equity_hook", None, raising=False)
    return PaperBroker(
        100_000, book, RiskEngine(RiskConfig(equity=100_000, risk_pct=0.75))  # type: ignore[arg-type]
    )


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------- no fill at all


def test_buy_limit_below_the_ask_does_not_fill(tmp_path, monkeypatch):
    """The classic paper-trading cheat: bid 90 on a 100 market, wait, profit."""
    book = Book(bids=[(99.9, 50)], asks=[(100.0, 50)])
    broker = _broker(tmp_path, monkeypatch, book)

    pos = run(broker.place_limit("BTCUSDT", "buy", 1.0, price=90.0,
                                 stop=85.0, tp1=105.0, tp2=110.0))
    assert pos is None
    assert broker.last_error.startswith("no-cross")
    assert broker.orders[-1].extra["reason"] == "no-cross"
    assert broker.positions == {}
    assert broker.cash == 100_000


def test_sell_limit_above_the_bid_does_not_fill(tmp_path, monkeypatch):
    book = Book(bids=[(100.0, 50)], asks=[(100.1, 50)])
    broker = _broker(tmp_path, monkeypatch, book)

    pos = run(broker.place_limit("BTCUSDT", "sell", 1.0, price=110.0,
                                 stop=115.0, tp1=95.0, tp2=90.0))
    assert pos is None
    assert broker.last_error.startswith("no-cross")
    assert broker.positions == {}


# ------------------------------------------------------------------- fill price


def test_crossing_buy_limit_fills_at_the_book_not_at_the_limit(tmp_path, monkeypatch):
    """A marketable buy must pay the ask, and only be *capped* by the limit."""
    book = Book(bids=[(99.9, 50)], asks=[(100.0, 50)])
    broker = _broker(tmp_path, monkeypatch, book)

    pos = run(broker.place_limit("BTCUSDT", "buy", 1.0, price=101.0,
                                 stop=95.0, tp1=112.0, tp2=119.0))
    assert pos is not None, broker.last_error
    # Used to be 101.0 — a free 1% on every order.
    assert pos.entry_price == pytest.approx(100.0)
    assert pos.entry_price <= 101.0


def test_limit_below_market_that_is_still_marketable_walks_to_the_limit(tmp_path, monkeypatch):
    """Limit 100.5 with asks at 100.0 and 100.4: both levels are inside."""
    book = Book(bids=[(99.5, 50)], asks=[(100.0, 1.0), (100.4, 10.0), (101.0, 10.0)])
    broker = _broker(tmp_path, monkeypatch, book)

    pos = run(broker.place_limit("BTCUSDT", "buy", 3.0, price=100.5,
                                 stop=95.0, tp1=112.0, tp2=119.0))
    assert pos is not None, broker.last_error
    # The broker rounds the fill to the symbol's price step (2 decimals here).
    assert pos.entry_price == pytest.approx(round((100.0 * 1.0 + 100.4 * 2.0) / 3.0, 2))
    assert pos.entry_price < 100.5  # better than the limit, as the book allows


def test_non_crossing_levels_are_not_taken(tmp_path, monkeypatch):
    """Only asks at or below the limit count; the 101.0 level must be skipped."""
    book = Book(bids=[(99.5, 50)], asks=[(100.0, 1.0), (101.0, 10.0)])
    broker = _broker(tmp_path, monkeypatch, book)

    pos = run(broker.place_limit("BTCUSDT", "buy", 5.0, price=100.5,
                                 stop=95.0, tp1=112.0, tp2=119.0))
    assert pos is not None, broker.last_error
    assert pos.entry_price == pytest.approx(100.0)
    assert pos.filled_qty == pytest.approx(1.0)
    assert pos.requested_qty == pytest.approx(5.0)


# ---------------------------------------------------------------- partial fill


def test_thin_book_partially_fills_and_says_so(tmp_path, monkeypatch):
    book = Book(bids=[(99.9, 50)], asks=[(100.0, 0.4)])
    broker = _broker(tmp_path, monkeypatch, book)

    pos = run(broker.place_limit("BTCUSDT", "buy", 2.0, price=100.0,
                                 stop=95.0, tp1=112.0, tp2=119.0))
    # A 2.0 buy against 0.4 of depth: the old code filled all 2.0 from nowhere.
    if pos is not None:
        assert pos.filled_qty == pytest.approx(0.4)
        assert pos.requested_qty == pytest.approx(2.0)
        assert pos.filled_qty < pos.requested_qty
    else:  # a min-notional gate may refuse the residue; either answer is honest
        assert "notional" in broker.last_error or "risk" in broker.last_error


# -------------------------------------------------------------- preview == submit


def test_preview_and_submit_agree_on_a_non_crossing_limit(tmp_path, monkeypatch):
    book = Book(bids=[(99.9, 50)], asks=[(100.0, 50)])
    broker = _broker(tmp_path, monkeypatch, book)

    preview = run(broker.execution_preview(
        "BTCUSDT", "buy", 1.0, stop=85.0, tp1=105.0, tp2=110.0, price=90.0))
    assert preview["executable"] is False
    pos = run(broker.place_limit("BTCUSDT", "buy", 1.0, price=90.0,
                                 stop=85.0, tp1=105.0, tp2=110.0))
    assert pos is None
    assert preview["blocked_by"] == "no-cross"
    assert broker.last_error.startswith("no-cross")


def test_preview_and_submit_agree_on_a_crossing_limit(tmp_path, monkeypatch):
    book = Book(bids=[(99.9, 50)], asks=[(100.0, 50)])
    broker = _broker(tmp_path, monkeypatch, book)

    preview = run(broker.execution_preview(
        "BTCUSDT", "buy", 1.0, stop=95.0, tp1=112.0, tp2=119.0, price=101.0))
    assert preview["executable"] is True, preview
    pos = run(broker.place_limit("BTCUSDT", "buy", 1.0, price=101.0,
                                 stop=95.0, tp1=112.0, tp2=119.0))
    assert pos is not None, broker.last_error
    assert preview["execution_entry"] == pytest.approx(pos.entry_price)
