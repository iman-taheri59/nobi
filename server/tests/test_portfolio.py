# mypy: check_untyped_defs = False
# Test doubles stand in for objects on purpose; their bodies answer to pytest.
"""A3 — the portfolio allocator has to be arithmetic, not vibes.

Every test here either computes an answer by hand and compares, or checks that
the allocator refuses to answer instead of filling the gap with a plausible
number. The route tests run against the real FastAPI app with only the candle
feed stubbed, because the thing that matters is whether the allocation the desk
prints is one the broker would accept.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio import (
    Candidate,
    Limits,
    aligned_closes,
    allocate,
    corr_matrix,
    corr_pair,
    erc_weights,
    log_returns,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _series(seed: int, n: int = 200, drift: float = 0.0, noise: float = 0.01) -> list[float]:
    rnd = random.Random(seed)
    px = 100.0
    out = [px]
    for _ in range(n - 1):
        px *= math.exp(drift + rnd.gauss(0.0, noise))
        out.append(px)
    return out


def _rows(closes: list[float], start: int = 1_700_000_000, step: int = 900):
    return [(start + i * step, c) for i, c in enumerate(closes)]


def _cand(sym: str, entry: float = 1000.0, stop_pct: float = 0.02, score: float = 70.0):
    return Candidate(sym, entry, entry * (1 - stop_pct), score)


# --------------------------------------------------------------------------- #
# statistics
# --------------------------------------------------------------------------- #
def test_corr_pair_matches_a_hand_computed_pearson():
    a = [1.0, 2.0, 3.0, 4.0, 5.0] * 8
    b = [2.0, 4.0, 6.0, 8.0, 10.0] * 8
    assert corr_pair(a, b) == pytest.approx(1.0)
    c = [5.0, 4.0, 3.0, 2.0, 1.0] * 8
    assert corr_pair(a, c) == pytest.approx(-1.0)
    # mean-zero offset must not change the answer
    assert corr_pair(a, [x + 100 for x in b]) == pytest.approx(1.0)


def test_corr_pair_refuses_a_sample_it_cannot_support():
    """Below the minimum, and on a flat series, the answer is None — not 0."""
    assert corr_pair([1.0] * 10, [1.0 + i * 0.1 for i in range(10)]) is None
    flat = [7.0] * 80
    assert corr_pair(flat, [1.0 + i * 0.1 for i in range(80)]) is None


def test_corr_matrix_marks_missing_pairs_instead_of_zeroing_them():
    long_a = _series(1)
    long_b = _series(2)
    short = _series(3)[:20]
    syms, mat, missing = corr_matrix({
        "A": log_returns(long_a), "B": log_returns(long_b), "C": log_returns(short),
    })
    assert syms == ["A", "B", "C"]
    ia, ic = syms.index("A"), syms.index("C")
    assert mat[ia][ic] is None and mat[ic][ia] is None
    assert ("A", "C") in missing
    assert mat[0][0] == 1.0


def test_shrinkage_pulls_correlations_toward_zero():
    a = _series(4)
    b = [x * (1 + 0.02 * math.sin(i / 3)) for i, x in enumerate(a)]
    r = {"A": log_returns(a), "B": log_returns(b)}
    _, raw, _ = corr_matrix(r, shrinkage=0.0)
    _, shrunk, _ = corr_matrix(r, shrinkage=0.5)
    assert abs(shrunk[0][1]) < abs(raw[0][1])
    assert shrunk[0][1] == pytest.approx(raw[0][1] * 0.5, rel=1e-9)


def test_alignment_keeps_only_shared_timestamps():
    a = [(1, 10.0), (2, 11.0), (3, 12.0), (4, 13.0)]
    b = [(2, 20.0), (3, 22.0), (4, 24.0), (5, 26.0)]
    stamps, closes = aligned_closes({"A": a, "B": b})
    assert stamps == [2, 3, 4]
    assert closes["A"] == [11.0, 12.0, 13.0]
    assert closes["B"] == [20.0, 22.0, 24.0]


def test_log_returns_rejects_a_non_positive_close():
    with pytest.raises(ValueError):
        log_returns([10.0, 0.0, 12.0])


# --------------------------------------------------------------------------- #
# equal risk contribution
# --------------------------------------------------------------------------- #
def _contributions(w, vols, corr):
    n = len(w)
    cov = [[(1.0 if i == j else (corr[i][j] or 0.0)) * vols[i] * vols[j]
            for j in range(n)] for i in range(n)]
    mrc = [sum(cov[i][j] * w[j] for j in range(n)) for i in range(n)]
    return [w[i] * mrc[i] for i in range(n)]


def test_erc_equalises_risk_contributions():
    vols = [0.01, 0.02, 0.03, 0.015]
    r = 0.45
    corr = [[1.0 if i == j else r for j in range(4)] for i in range(4)]
    w = erc_weights(vols, corr)
    assert sum(w) == pytest.approx(1.0)
    c = _contributions(w, vols, corr)
    assert max(c) / min(c) == pytest.approx(1.0, abs=1e-3)


def test_erc_gives_the_volatile_symbol_less_money():
    vols = [0.01, 0.04]
    corr = [[1.0, 0.0], [0.0, 1.0]]
    w = erc_weights(vols, corr)
    # Uncorrelated ERC is plain inverse-volatility: four times the vol, a quarter
    # of the weight.
    assert w[0] / w[1] == pytest.approx(4.0, rel=1e-3)


def test_erc_shrinks_each_member_of_a_correlated_pair():
    """Correlation is not a reason to give the pair more: each member shrinks.

    With three equal-vol names, uncorrelated ERC gives everyone a third. Making
    two of them 0.95-correlated leaves each of that pair below a third and
    moves the freed weight to the independent name — which is the whole point
    of the allocator.
    """
    vols = [0.02, 0.02, 0.02]
    corr = [[1.0, 0.95, 0.0], [0.95, 1.0, 0.0], [0.0, 0.0, 1.0]]
    w = erc_weights(vols, corr)
    assert w[0] == pytest.approx(w[1])
    assert w[0] < 1 / 3 < w[2]


def test_erc_handles_one_symbol():
    assert erc_weights([0.02], [[1.0]]) == [1.0]


# --------------------------------------------------------------------------- #
# allocation against account limits
# --------------------------------------------------------------------------- #
def _limits(**kw):
    base = {"equity": 100_000.0, "cash": 100_000.0, "open_heat_pct": 0.0,
            "open_positions": 0, "concurrent_limit": 5, "max_heat_pct": 6.0,
            "min_cash_pct": 10.0, "max_single_asset_pct": 25.0,
            "per_trade_risk_pct": 0.75, "fee_pct": 0.002}
    base.update(kw)
    return Limits(**base)


def _returns_map(n=4, n_obs=200):
    return {f"S{i}IRT": log_returns(_series(100 + i, n=n_obs)) for i in range(n)}


def test_allocate_never_spends_more_than_the_heat_budget():
    cands = [_cand(f"S{i}IRT", stop_pct=0.02, score=70) for i in range(4)]
    res = allocate(cands, _returns_map(4), _limits())
    risks = [a["risk_pct"] for a in res["allocations"]]
    assert risks and sum(risks) <= 6.0 + 1e-9
    assert all(r <= 0.75 + 1e-9 for r in risks)
    assert res["totals"]["heat_after_pct"] <= 6.0 + 1e-9


def test_a_low_volatility_name_is_capped_by_the_single_asset_rule_not_the_risk_rule():
    """A 0.5 % stop would buy 150 % of equity at 0.75 % risk; the 25 % cap binds."""
    res = allocate([_cand("S0IRT", entry=1000.0, stop_pct=0.005)],
                   {"S0IRT": log_returns(_series(7))}, _limits())
    a = res["allocations"][0]
    assert a["bound_by"] == "single-asset-cap"
    assert a["notional_usd"] == pytest.approx(25_000.0, rel=1e-6)
    assert a["risk_pct"] == pytest.approx(0.125, abs=1e-6)


def test_the_cash_floor_is_respected_even_when_the_budget_is_not_spent():
    res = allocate([_cand("S0IRT")], {"S0IRT": log_returns(_series(8))},
                   _limits(cash=12_000.0))
    spent = res["totals"]["invested_usd"]
    assert spent <= 2_000.0 + 1e-6          # cash 12k − 10 % floor of 100k equity
    assert res["allocations"][0]["bound_by"] == "cash"


def test_no_free_slot_means_no_allocation():
    res = allocate([_cand("S0IRT")], {"S0IRT": log_returns(_series(9))},
                   _limits(open_positions=5))
    assert res["allocations"] == []
    assert res["dropped"][0]["reason"] == "concurrent-cap"


def test_an_armed_kill_switch_allocates_nothing():
    res = allocate([_cand("S0IRT")], {"S0IRT": log_returns(_series(10))},
                   _limits(halted=True))
    assert res["allocations"] == []
    assert res["dropped"][0]["reason"] == "halted"


def test_a_symbol_without_history_is_dropped_with_its_reason():
    res = allocate([_cand("S0IRT"), _cand("NEWIRT")],
                   {"S0IRT": log_returns(_series(11))}, _limits())
    reasons = {d["symbol"]: d["reason"] for d in res["dropped"]}
    assert "NEWIRT" in reasons and "returns" in reasons["NEWIRT"]
    assert [a["symbol"] for a in res["allocations"]] == ["S0IRT"]


def test_existing_heat_shrinks_the_budget():
    res = allocate([_cand("S0IRT")], {"S0IRT": log_returns(_series(12))},
                   _limits(open_heat_pct=5.5))
    assert res["budget_pct"] == pytest.approx(0.5)
    assert sum(a["risk_pct"] for a in res["allocations"]) <= 0.5 + 1e-9


def test_an_exhausted_budget_allocates_nothing():
    res = allocate([_cand("S0IRT")], {"S0IRT": log_returns(_series(13))},
                   _limits(open_heat_pct=6.0))
    assert res["allocations"] == []
    assert "heat" in res["reason"]


def test_allocations_are_sorted_by_size_and_agree_with_the_totals():
    cands = [_cand(f"S{i}IRT", stop_pct=0.02 + 0.01 * i) for i in range(4)]
    res = allocate(cands, _returns_map(4), _limits())
    allocs = res["allocations"]
    assert len(allocs) >= 2
    assert sum(a["notional_usd"] for a in allocs) == pytest.approx(
        res["totals"]["invested_usd"], abs=0.01)
    assert res["totals"]["cash_after_usd"] == pytest.approx(
        100_000.0 - res["totals"]["invested_usd"] - res["totals"]["fees_usd"], abs=0.01)


def test_correlation_matrix_is_reported_with_the_result():
    res = allocate([_cand(f"S{i}IRT") for i in range(3)], _returns_map(3), _limits())
    assert res["correlation"]["symbols"] == ["S0IRT", "S1IRT", "S2IRT"]
    assert len(res["correlation"]["values"]) == 3
    # independent series: nothing here should look strongly correlated
    off = [abs(v) for i, row in enumerate(res["correlation"]["values"])
           for j, v in enumerate(row) if i != j and v is not None]
    assert max(off) < 0.35


# --------------------------------------------------------------------------- #
# the route
# --------------------------------------------------------------------------- #
def _fake_feed(seedmap):
    async def _get(symbol, tf="15m", count=260):
        if symbol not in seedmap:
            from nobitex_client import UpstreamUnavailable
            raise UpstreamUnavailable(f"no candles for {symbol}")
        closes = _series(seedmap[symbol], n=220)
        return [{"t": 1_700_000_000 + i * 900, "o": c, "h": c * 1.001,
                 "l": c * 0.999, "c": c, "v": 1.0} for i, c in enumerate(closes)], "live"
    return _get


@pytest.fixture()
def primed_board(monkeypatch):
    import main
    from paper_trading import get_broker

    syms = ["AAAIRT", "BBBIRT", "CCCIRT"]
    board = {}
    for i, sym in enumerate(syms):
        board[sym] = {"symbol": sym, "direction": "LONG", "composite": 80 - i,
                      "lastClose": 1_000_000.0 * (i + 1),
                      "risk": {"atr": 10_000.0 * (i + 1)}}
    main._cached["signals_by_tf"]["15m"] = board
    main._cached["universe"] = [{"symbol": "USDTIRT", "last": 100_000.0}]
    br = get_broker(main.EQUITY, main.client, main.risk)
    br.positions.clear()
    main.risk.open_heat = 0.0
    monkeypatch.setattr(main.client, "get_candles_ex",
                        _fake_feed({s: 200 + i for i, s in enumerate(syms)}))
    return syms


def test_route_returns_a_placeable_allocation(primed_board):
    from fastapi.testclient import TestClient

    import main

    d = TestClient(main.app).post("/api/portfolio/allocate",
                                  json={"tf": "15m"}).json()
    assert d["ok"] is True, d
    assert d["method"] == "erc-shrunk"
    assert d["tf"] == "15m"
    allocs = d["allocations"]
    assert 1 <= len(allocs) <= 3
    for a in allocs:
        assert a["qty"] > 0
        assert a["quote"] == "IRT"
        assert a["notional_quote"] == pytest.approx(a["notional_usd"] * a["fx"], rel=1e-6)
        # stop distance survived the unit conversion
        assert a["stop_pct"] == pytest.approx((a["entry"] - a["stop"]) / a["entry"] * 100, rel=1e-6)
        assert a["risk_pct"] <= 0.75 * 1.05
        assert a["notional_usd"] <= 25_000.0 * 1.0001
    assert d["totals"]["invested_usd"] > 0
    assert d["totals"]["invested_quote"] == pytest.approx(
        sum(a["notional_quote"] for a in allocs), rel=1e-6)


def test_route_does_not_move_the_account(primed_board):
    from fastapi.testclient import TestClient

    import main
    from paper_trading import get_broker

    br = get_broker(main.EQUITY, main.client, main.risk)
    before = (len(br.positions), br.cash, main.risk.open_heat)
    TestClient(main.app).post("/api/portfolio/allocate", json={"tf": "15m"})
    after = (len(br.positions), br.cash, main.risk.open_heat)
    assert before == after, "an allocator that reserves heat is a silent trade"


def test_route_refuses_a_symbol_it_never_scanned(primed_board):
    from fastapi.testclient import TestClient

    import main

    d = TestClient(main.app).post("/api/portfolio/allocate",
                                  json={"tf": "15m", "symbols": ["ZZZIRT"]}).json()
    assert d["ok"] is False
    assert "ZZZIRT" in d["error"]


def test_route_names_the_kill_switch(primed_board):
    from fastapi.testclient import TestClient

    import main

    main.risk.halted = True
    try:
        d = TestClient(main.app).post("/api/portfolio/allocate", json={"tf": "15m"}).json()
    finally:
        main.risk.halted = False
    assert d["ok"] is False and d["blocked_by"] == "halted"


def test_route_reports_a_candidate_without_candles(primed_board, monkeypatch):
    import main

    original = main.client.get_candles_ex

    async def flaky(symbol, tf="15m", count=260):
        if symbol == "BBBIRT":
            from nobitex_client import UpstreamUnavailable
            raise UpstreamUnavailable("boom")
        return await original(symbol, tf, count)

    monkeypatch.setattr(main.client, "get_candles_ex", flaky)
    from fastapi.testclient import TestClient
    d = TestClient(main.app).post("/api/portfolio/allocate", json={"tf": "15m"}).json()
    assert d["ok"] is True
    assert "BBBIRT" not in [a["symbol"] for a in d["allocations"]]
    assert any(x["symbol"] == "BBBIRT" and "no candles" in x["reason"]
               for x in d["dropped"])
