"""ADR-0022 stage 1 — the gates report, and nothing else (1.0.25).

Five of the six hard gates ADR-0007 defined are labels, weights or broker-side
rules today. This module gives them verdicts without giving them power, and these
tests pin the three properties that make such a layer worth having:

1. **Precedence.** A row reports the most fundamental reason first — can it be
   evaluated, is its input valid, is its market tradeable — and never a cosmetic
   failure while a fundamental one is also true.
2. **Unknown is not fine.** "No book", "no candle" are their own verdicts, counted
   apart from both `ok` and `would_block`.
3. **It changes nothing.** `applied` is false, `mode` is `shadow`, and the module
   has no way to drop a row: the caller still owns the list.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gates

MAIN = Path(__file__).resolve().parents[1] / "main.py"


def limits(**kw) -> gates.GateLimits:
    """Both money-shaped thresholds on, so the precedence has something to order."""
    base: dict[str, Any] = {"max_age_bars": 2.0, "max_spread_bps": 25.0,
                            "min_depth_usd": 1_000.0}
    base.update(kw)
    return gates.GateLimits(**base)


BOOK_OK = {"spread_bps": 8.0, "depth_usd": 50_000.0}


def test_a_clean_row_is_ok_and_blocked_by_nothing() -> None:
    out = gates.evaluate_symbol("BTCIRT", age_bars=0.4, book=BOOK_OK, limits=limits())
    assert out["code"] == "ok"
    assert out["codes"] == []
    assert out["detail"] == {}


def test_the_precedence_is_the_documented_one() -> None:
    """The order is a contract, not an accident: reordering it is a decision."""
    assert gates.CODES == ("halted", "no-data", "stale-data", "no-book",
                           "spread-wide", "book-thin", "ok")


def test_a_stale_candle_is_named_with_its_arithmetic() -> None:
    out = gates.evaluate_symbol("DEADIRT", age_bars=288.0, book=BOOK_OK, limits=limits())
    assert out["code"] == "stale-data"
    assert out["codes"] == ["stale-data"]
    assert out["detail"]["age_bars"] == 288.0
    assert out["detail"]["limit"] == 2.0


def test_a_halted_market_outranks_a_stale_candle() -> None:
    """The first question is whether the row can be evaluated at all."""
    out = gates.evaluate_symbol("XYZIRT", age_bars=288.0, halted=True, book=BOOK_OK,
                                limits=limits())
    assert out["code"] == "halted"
    assert out["codes"] == ["halted", "stale-data"]   # both kept, one reported first


def test_a_missing_book_is_unknown_not_fine() -> None:
    out = gates.evaluate_symbol("BTCIRT", age_bars=0.4, book=None, limits=limits())
    assert out["code"] == "no-book"
    assert out["code"] in gates.UNKNOWN_CODES


def test_a_missing_candle_outranks_a_missing_book() -> None:
    out = gates.evaluate_symbol("BTCIRT", age_bars=None, book=None, limits=limits())
    assert out["code"] == "no-data"
    assert out["codes"] == ["no-data", "no-book"]


def test_the_tradeability_gates_name_their_numbers() -> None:
    wide = gates.evaluate_symbol("BTCIRT", age_bars=0.2,
                                 book={"spread_bps": 90.0, "depth_usd": 50_000.0},
                                 limits=limits())
    assert wide["code"] == "spread-wide"
    assert wide["detail"]["spread_bps"] == 90.0 and wide["detail"]["limit"] == 25.0

    thin = gates.evaluate_symbol("BTCIRT", age_bars=0.2,
                                 book={"spread_bps": 8.0, "depth_usd": 10.0},
                                 limits=limits())
    assert thin["code"] == "book-thin"
    assert thin["detail"]["depth_usd"] == 10.0

    both = gates.evaluate_symbol("BTCIRT", age_bars=0.2,
                                 book={"spread_bps": 90.0, "depth_usd": 10.0},
                                 limits=limits())
    assert both["code"] == "spread-wide"                  # first in precedence
    assert both["codes"] == ["spread-wide", "book-thin"]


def test_a_book_without_the_number_a_gate_needs_is_unknown_for_that_gate() -> None:
    out = gates.evaluate_symbol("BTCIRT", age_bars=0.2, book={"spread_bps": None,
                                                              "depth_usd": 5.0},
                                limits=limits())
    assert out["code"] == "no-book"
    assert "spread" in out["detail"]["why"]


def test_the_default_limits_judge_age_and_judge_nothing_else() -> None:
    """The money-shaped thresholds are off until the distribution is measured."""
    default = gates.GateLimits()
    assert default.max_age_bars == 2.0
    assert default.max_spread_bps is None and default.min_depth_usd is None
    assert default.enforcing is False
    assert default.as_dict()["enabled"] == ["age"]
    # With the defaults, a thin and wide book is no objection at all.
    out = gates.evaluate_symbol("BTCIRT", age_bars=0.2,
                                book={"spread_bps": 999.0, "depth_usd": 0.01})
    assert out["code"] == "ok"


def test_the_block_counts_three_populations_separately() -> None:
    out = gates.evaluate([
        {"symbol": "BTCIRT", "age_bars": 0.4, "book": BOOK_OK},
        {"symbol": "DEADIRT", "age_bars": 288.0, "book": BOOK_OK},
        {"symbol": "GHOSTIRT", "age_bars": 0.4, "book": None},
        {"symbol": "EMPTYIRT", "age_bars": None, "book": None},
        {"symbol": "", "age_bars": 0.1},                      # no symbol: skipped
    ], limits=limits())

    assert out["mode"] == "shadow"
    assert out["applied"] is False
    assert out["would_block"] == ["DEADIRT"]
    assert out["unknown"] == ["GHOSTIRT", "EMPTYIRT"]
    assert out["counts"] == {"rows": 4, "ok": 1, "would_block": 1, "unknown": 2}
    assert "nothing was removed" in out["note"]


def test_evaluating_never_drops_or_reorders_the_callers_rows() -> None:
    rows = [{"symbol": "B", "age_bars": 0.1, "book": BOOK_OK},
            {"symbol": "A", "age_bars": 99.0, "book": BOOK_OK}]
    before = [dict(r) for r in rows]
    gates.evaluate(rows, limits=limits())
    assert rows == before, "the gate layer mutated the caller's rows"


def test_the_module_has_no_way_to_apply_anything() -> None:
    """A future edit that made this layer act would have to delete this test."""
    src = (Path(__file__).resolve().parents[1] / "gates.py").read_text(encoding="utf-8")
    assert '"applied": False' in src
    # No filtering of the caller's list, no removal helper, no "blocked" output list
    # that a route could mistake for the new list.
    for forbidden in ("filter(", "drop(", "apply_veto", "remove("):
        assert forbidden not in src, f"gates.py grew a way to remove rows: {forbidden}"
    assert "enforcing" in src          # the flag exists...
    body = src[src.index("def evaluate("):]
    assert "enforcing" not in body, "...but the report path never reads it"


def test_the_ranking_route_reports_the_block_and_keeps_every_row() -> None:
    src = MAIN.read_text(encoding="utf-8")
    route = src[src.index('@app.get("/api/signals")'):]
    route = route[: route.index('@app.get("/api/candles/{symbol}")')]
    assert '"gates": gates_block(' in route
    # The ranked list is the same list: no filtering by verdict.
    assert '"scored": ranked,' in route
    assert "ranked = sorted(" in route
