# mypy: check_untyped_defs = False
# Test doubles stand in for the exchange on purpose; their bodies answer to pytest.
"""ADR-0023 S1 — the scan reads the top books so the gates have something to judge.

Before this, a book was only read when somebody opened `/api/orderbook/{symbol}`.
On a normal scan that meant most rows were `no-book` and the spread/depth gates
never accumulated a verdict, which made stage 2 of ADR-0022 look like it was
working while its sample stayed empty.

Four properties have to hold, and each is a test below:

1. the request count is bounded by a ceiling and a value above it is *refused*,
   not quietly trimmed — a silent trim would make the gates look populated;
2. a symbol that fails is counted and left absent, so the gates keep saying
   `no-book` instead of being fed a zero that reads as "fine";
3. the ranking is not touched: this fills a cache, it does not select;
4. the book arithmetic lives in one function, shared with the route, so the panel
   and the gate cannot disagree.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def book(bid: float, ask: float, size: float = 2.0, levels: int = 4) -> dict:
    """A book shaped like the client's, with an obvious spread and depth."""
    bids = [{"p": bid - i * 0.1, "q": size} for i in range(levels)]
    asks = [{"p": ask + i * 0.1, "q": size} for i in range(levels)]
    return {"bids": bids, "asks": asks, "lastUpdate": 1}


def ranked(n: int) -> list[dict]:
    return [{"symbol": f"S{i}IRT", "composite": 100 - i} for i in range(n)]


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    """Every test starts with no books and no prefetch record."""
    monkeypatch.setitem(main._cached, "books", {})
    monkeypatch.setitem(main._cached, "gate_prefetch", None)
    monkeypatch.setitem(main._cached, "universe", [{"symbol": "USDTIRT", "last": 100_000.0}])
    yield


class Feed:
    """Counts what was asked for, and can fail selected symbols."""

    def __init__(self, failing: set[str] | None = None, price: float = 100.0):
        self.calls: list[str] = []
        self.failing = failing or set()
        self.price = price

    async def get_orderbook(self, sym: str) -> dict:
        self.calls.append(sym)
        if sym in self.failing:
            raise RuntimeError("upstream said no")
        return book(self.price, self.price * 1.001)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# 1. The ceiling is a refusal, not a trim
# --------------------------------------------------------------------------- #
def test_the_default_is_ten_and_zero_means_off(monkeypatch):
    monkeypatch.delenv("NBX_GATE_PREFETCH", raising=False)
    assert main.gate_prefetch_n() == 10
    assert main.gate_prefetch_n(0) == 0
    assert main.gate_prefetch_n("4") == 4


def test_asking_above_the_rate_ceiling_raises_instead_of_fetching_fewer():
    with pytest.raises(ValueError, match="exceeds the rate ceiling"):
        main.gate_prefetch_n(main.GATE_PREFETCH_MAX + 1)
    with pytest.raises(ValueError):
        main.gate_prefetch_n(-1)
    with pytest.raises(ValueError):
        main.gate_prefetch_n("many")


def test_an_env_value_above_the_ceiling_is_refused_too(monkeypatch):
    monkeypatch.setenv("NBX_GATE_PREFETCH", str(main.GATE_PREFETCH_MAX + 5))
    with pytest.raises(ValueError):
        main.gate_prefetch_n()


def test_zero_disables_the_pass_without_touching_the_exchange(monkeypatch):
    feed = Feed()
    monkeypatch.setattr(main, "client", feed)
    out = run(main.gate_prefetch(ranked(30), n=0))
    assert feed.calls == []
    assert out["enabled"] is False and out["asked"] == 0
    assert main._cached["books"] == {}


# --------------------------------------------------------------------------- #
# 2. Only the top N, and a failure is counted — never invented
# --------------------------------------------------------------------------- #
def test_only_the_top_n_are_requested(monkeypatch):
    feed = Feed()
    monkeypatch.setattr(main, "client", feed)
    rows = ranked(30)
    out = run(main.gate_prefetch(rows, n=10))
    assert len(feed.calls) == 10
    assert feed.calls == [f"S{i}IRT" for i in range(10)]
    assert out == {**out, "asked": 10, "ok": 10, "failed": 0}
    assert set(main._cached["books"]) == set(feed.calls)


def test_a_failed_symbol_is_absent_from_the_cache_not_zeroed(monkeypatch):
    feed = Feed(failing={"S2IRT"})
    monkeypatch.setattr(main, "client", feed)
    out = run(main.gate_prefetch(ranked(5), n=5))
    assert out["asked"] == 5 and out["ok"] == 4 and out["failed"] == 1
    assert "S2IRT" not in main._cached["books"], "a failed book was recorded anyway"
    # and the gate for it therefore says "I do not know", not "fine"
    gate = main.gates.evaluate([{"symbol": "S2IRT", "age_bars": 0.5, "book": None}])
    assert gate["verdicts"]["S2IRT"]["code"] == "no-book"
    assert gate["unknown"] == ["S2IRT"]


def test_an_empty_book_counts_as_a_failure(monkeypatch):
    class Empty:
        async def get_orderbook(self, sym):
            return {"bids": [], "asks": []}

    monkeypatch.setattr(main, "client", Empty())
    out = run(main.gate_prefetch(ranked(2), n=2))
    assert out["ok"] == 0 and out["failed"] == 2
    assert main._cached["books"] == {}


def test_a_fresh_book_is_reused_instead_of_requested_again(monkeypatch):
    feed = Feed()
    monkeypatch.setattr(main, "client", feed)
    run(main.gate_prefetch(ranked(3), n=3))
    first = len(feed.calls)
    out = run(main.gate_prefetch(ranked(3), n=3))
    assert len(feed.calls) == first, "the same books were fetched twice inside the TTL"
    assert out["fresh"] == 3 and out["asked"] == 0


def test_a_stale_book_is_refreshed(monkeypatch):
    feed = Feed()
    monkeypatch.setattr(main, "client", feed)
    run(main.gate_prefetch(ranked(1), n=1))
    for row in main._cached["books"].values():
        row["at"] = time.time() - main.BOOK_PREFETCH_TTL_S - 1
    out = run(main.gate_prefetch(ranked(1), n=1))
    assert out["asked"] == 1 and out["fresh"] == 0 and len(feed.calls) == 2


# --------------------------------------------------------------------------- #
# 3. The ranking is not touched
# --------------------------------------------------------------------------- #
def test_the_prefetch_does_not_reorder_or_shorten_the_list(monkeypatch):
    monkeypatch.setattr(main, "client", Feed())
    rows = ranked(30)
    before = [(r["symbol"], r["composite"]) for r in rows]
    run(main.gate_prefetch(rows, n=5))
    assert [(r["symbol"], r["composite"]) for r in rows] == before
    assert len(rows) == 30


# --------------------------------------------------------------------------- #
# 4. One arithmetic, shared with the route
# --------------------------------------------------------------------------- #
def test_the_route_maths_and_the_prefetch_agree():
    raw = book(100.0, 100.1, size=3.0)
    stats = main.book_stats(raw)
    # spread 0.1 on a mid of 100.05 is ~9.995 bps
    assert stats["spread_bps"] == pytest.approx(9.995, abs=0.01)
    assert stats["total_depth"] > 0
    # a USDT book is already dollars
    assert main.book_record("BTCUSDT", stats, book_currency_irt=False)["depth_usd"] == pytest.approx(
        stats["total_depth"])
    # an IRT book is tomans and must be converted, not added
    rec = main.book_record("BTCIRT", stats, book_currency_irt=True)
    assert rec["depth_usd"] == pytest.approx(stats["total_depth"] / 100_000.0)


def test_without_the_usdt_rate_an_irt_depth_is_unknown_not_toman(monkeypatch):
    monkeypatch.setitem(main._cached, "universe", [])
    stats = main.book_stats(book(100.0, 100.1))
    rec = main.book_record("BTCIRT", stats, book_currency_irt=True)
    assert rec["depth_usd"] is None, "tomans were reported as dollars"
    assert rec["spread_bps"] is not None       # a spread has no currency


def test_the_prefetched_row_carries_what_the_gates_read(monkeypatch):
    monkeypatch.setattr(main, "client", Feed())
    run(main.gate_prefetch(ranked(1), n=1))
    row = main._cached["books"]["S0IRT"]
    assert set(row) == {"spread_bps", "depth_usd", "mid", "at"}
    gate = main.gates.evaluate([{"symbol": "S0IRT", "age_bars": 0.2,
                                 "book": {"spread_bps": row["spread_bps"],
                                          "depth_usd": row["depth_usd"]}}])
    assert gate["verdicts"]["S0IRT"]["code"] == "ok"


# --------------------------------------------------------------------------- #
# Wiring: the scan calls it, the block reports it
# --------------------------------------------------------------------------- #
def _main_source() -> str:
    return (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")


def test_the_scan_prefetches_before_it_writes_the_ledger():
    src = _main_source()
    at = src.index("pre = await gate_prefetch(")
    assert at < src.index("judged = gate_write(tf)"), \
        "the ledger was written before the books arrived"


def test_a_prefetch_failure_cannot_break_a_scan():
    src = _main_source()
    at = src.index("pre = await gate_prefetch(")
    window = src[max(0, at - 300):at + 500]
    flat = " ".join(window.split())
    assert "except Exception as e: # a prefetch problem must never break a scan" in flat


def test_the_unknowns_fall_once_the_books_arrive(monkeypatch):
    """The acceptance criterion of S1, in miniature: books 0 -> N, unknowns down."""
    monkeypatch.setattr(main, "client", Feed())
    rows = [{"symbol": "S0IRT", "composite": 90}, {"symbol": "S1IRT", "composite": 80}]
    # a scan that just closed a 15m bar: the *open* stamp of the newest bar
    open_at = time.time() - 15 * 60
    main._cached["signals_bars"] = {"15m": {"S0IRT": open_at, "S1IRT": open_at}}
    before = main.gates_block(rows, "15m")
    assert before["inputs"]["books"] == 0 and len(before["unknown"]) == 2
    run(main.gate_prefetch(rows, n=2))
    after = main.gates_block(rows, "15m")
    assert after["inputs"]["books"] == 2 and after["unknown"] == []


def test_the_gates_block_reports_what_the_prefetch_did(monkeypatch):
    monkeypatch.setattr(main, "client", Feed())
    run(main.gate_prefetch(ranked(2), n=2))
    block = main.gates_block([{"symbol": "S0IRT", "composite": 90},
                              {"symbol": "S1IRT", "composite": 80}], "15m")
    pre = block["inputs"]["prefetch"]
    assert pre["enabled"] is True and pre["ok"] == 2
    assert block["inputs"]["books"] == 2
    assert block["applied"] is False        # S1 fills a cache; it never applies a gate


# --------------------------------------------------------------------------- #
# 5. A book that is too old is not evidence (1.0.30)
# --------------------------------------------------------------------------- #
def test_a_book_past_the_gate_ttl_cannot_judge_a_bar(monkeypatch):
    """The cache is not a measurement: a four-hour-old book is not a fresh "ok".

    A symbol leaves the top N, its last book stays in `_cached["books"]`, and
    without an age check every later bar would be judged — and written to the
    ledger — from a price that no longer exists.
    """
    monkeypatch.setattr(main, "client", Feed())
    run(main.gate_prefetch(ranked(2), n=2))
    # age both books well past the gate TTL
    for row in main._cached["books"].values():
        row["at"] = time.time() - main.BOOK_GATE_TTL_S - 60
    block = main.gates_block([{"symbol": "S0IRT"}, {"symbol": "S1IRT"}], "15m")
    assert block["inputs"]["books"] == 0, "a stale book was counted as a usable input"
    assert block["inputs"]["stale_books"] == 2
    assert block["inputs"]["oldest_book_s"] > main.BOOK_GATE_TTL_S
    assert sorted(block["unknown"]) == ["S0IRT", "S1IRT"], "a stale book produced a verdict"
    assert block["counts"]["ok"] == 0


def test_a_book_inside_the_gate_ttl_still_judges(monkeypatch):
    monkeypatch.setattr(main, "client", Feed())
    run(main.gate_prefetch(ranked(1), n=1))
    main._cached["books"]["S0IRT"]["at"] = time.time() - main.BOOK_GATE_TTL_S + 30
    block = main.gates_block([{"symbol": "S0IRT"}], "15m")
    assert block["inputs"]["books"] == 1 and block["inputs"]["stale_books"] == 0


def test_the_gate_ttl_is_longer_than_the_fetch_ttl():
    """Otherwise every book would expire before the next scan could reuse it."""
    assert main.BOOK_GATE_TTL_S > main.BOOK_PREFETCH_TTL_S
    assert main.BOOK_EVICT_TTL_S > main.BOOK_GATE_TTL_S


def test_a_long_forgotten_book_is_evicted_from_the_cache(monkeypatch):
    monkeypatch.setattr(main, "client", Feed())
    run(main.gate_prefetch(ranked(3), n=3))
    main._cached["books"]["S2IRT"]["at"] = time.time() - main.BOOK_EVICT_TTL_S - 60
    # S2 has dropped out of the top N, so nothing refreshes it: only eviction can
    # remove it. (Asking for it again would simply fetch a new book.)
    out = run(main.gate_prefetch(ranked(2), n=2))
    assert out["evicted"] == 1
    assert "S2IRT" not in main._cached["books"]
    assert "S0IRT" in main._cached["books"]


def test_nothing_is_applied_even_when_books_go_stale(monkeypatch):
    """Filling or emptying the input still cannot move a gate out of shadow."""
    monkeypatch.setattr(main, "client", Feed())
    run(main.gate_prefetch(ranked(2), n=2))
    for row in main._cached["books"].values():
        row["at"] = time.time() - main.BOOK_GATE_TTL_S - 60
    block = main.gates_block([{"symbol": "S0IRT"}, {"symbol": "S1IRT"}], "15m")
    assert block["applied"] is False
    assert block["mode"] == "shadow"
    assert block["would_block"] == []
