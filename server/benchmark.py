"""A7 — measuring a symbol against the market instead of against zero.

Absolute return answers the wrong question for a trading desk. An alt that made
12 % while BTC made 30 % lost to the market; a flat month with BTC down 20 % was
a good month. This module reduces a return series against a benchmark into the
numbers that make that visible — beta, alpha, tracking error, information ratio,
and up/down capture — and refuses to answer when the overlap is too short.

Definitions used (stated because "alpha" means several things in the wild):

    r_i, r_b   log returns on the bars both series share
    beta       cov(r_i, r_b) / var(r_b)
    alpha      annualised Jensen's alpha: (mean(r_i) − beta · mean(r_b)) · P
    tracking   stdev(r_i − r_b) · sqrt(P)
    info ratio mean(r_i − r_b) · P / tracking error
    capture    mean(r_i | r_b > 0) / mean(r_b | r_b > 0)   (and the down side)

P is the number of bars in a year for the timeframe, so a daily series and a
15-minute series produce comparable annual figures. None of these is a forecast;
they describe what already happened, and the route says so.
"""

from __future__ import annotations

import math

#: Returns required on *both* sides before anything is reported.
MIN_OBS = 30

#: Bars per year. Crypto trades every day, including the weekend — using 252
#: trading days here (the equity convention) would overstate alpha by ~45 %.
PERIODS_PER_YEAR = {"15m": 96 * 365, "1h": 24 * 365, "4h": 6 * 365, "D": 365}


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def relative(asset: list[float], bench: list[float], ppy: float) -> dict | None:
    """Beta and friends for one asset against one benchmark.

    Returns ``None`` when the sample cannot support an answer — no beta is
    better than a beta computed from twelve bars.
    """
    n = min(len(asset), len(bench))
    if n < MIN_OBS:
        return None
    a, b = asset[:n], bench[:n]
    ma, mb = _mean(a), _mean(b)
    da = [x - ma for x in a]
    db = [x - mb for x in b]
    var_b = sum(x * x for x in db) / (n - 1)
    if var_b <= 0:
        return None
    cov = sum(x * y for x, y in zip(da, db, strict=True)) / (n - 1)
    beta = cov / var_b
    alpha_per_bar = ma - beta * mb
    alpha_ann = alpha_per_bar * ppy * 100          # per cent per year

    rs_a, rs_b = _stdev(da), _stdev(db)
    corr = cov / (rs_a * rs_b) if rs_a > 0 and rs_b > 0 else None

    diff = [x - y for x, y in zip(a, b, strict=True)]
    te = _stdev(diff) * math.sqrt(ppy) * 100       # per cent per year
    excess = _mean(diff) * ppy * 100
    ir = (excess / te) if te > 1e-12 else None

    up_a = [x for x, y in zip(a, b, strict=True) if y > 0]
    up_b = [y for x, y in zip(a, b, strict=True) if y > 0]
    dn_a = [x for x, y in zip(a, b, strict=True) if y < 0]
    dn_b = [y for x, y in zip(a, b, strict=True) if y < 0]
    up_capture = (_mean(up_a) / _mean(up_b) * 100) if up_b and _mean(up_b) != 0 else None
    down_capture = (_mean(dn_a) / _mean(dn_b) * 100) if dn_b and _mean(dn_b) != 0 else None

    return {
        "n": n,
        "beta": beta,
        "corr": corr,
        "alpha_annual_pct": alpha_ann,
        "excess_annual_pct": excess,
        "tracking_error_pct": te,
        "info_ratio": ir,
        "up_capture_pct": up_capture,
        "down_capture_pct": down_capture,
        "asset_return_pct": (math.exp(sum(a)) - 1) * 100,
        "bench_return_pct": (math.exp(sum(b)) - 1) * 100,
        "relative_return_pct": ((math.exp(sum(a)) - 1) - (math.exp(sum(b)) - 1)) * 100,
    }


def summarise(rows: list[dict]) -> dict | None:
    """Equal-weighted view of everything that could be measured.

    Not a portfolio statistic — the desk's actual weights live in the
    allocator — but one honest line that says whether the board, as a set, is
    beating the market it trades in.
    """
    usable = [r for r in rows if r.get("beta") is not None]
    if not usable:
        return None
    n = len(usable)
    return {
        "symbols": n,
        "beta": _mean([r["beta"] for r in usable]),
        "alpha_annual_pct": _mean([r["alpha_annual_pct"] for r in usable]),
        "relative_return_pct": _mean([r["relative_return_pct"] for r in usable]),
        "beating_benchmark": sum(1 for r in usable if r["relative_return_pct"] > 0),
    }
