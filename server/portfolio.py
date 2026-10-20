"""A3 — correlation-aware risk budget across the candidates on the board.

The board can show eight LONG signals at once. Sizing each of them as if it were
the only trade spends 8 × 0.75 % = 6 % of equity on what may be one bet: alts
move together, so eight "independent" trades can be a single concentrated
position wearing eight tickers.

This module answers one question — *if the board's candidates were taken
together, how much risk does each deserve?* — and then checks the answer
against the same gates the paper broker uses, so the allocation it prints is
one the broker would actually accept, order by order.

Method, stated plainly because the honest version is short:

1. Log returns per symbol from the candles, on their common timestamps.
2. Sample correlation, shrunk toward independence (``shrinkage``) because an
   8 × 8 matrix estimated from a few hundred bars is mostly noise.
3. Equal-risk-contribution weights: every candidate contributes the same share
   of portfolio variance, which is what makes two correlated alts split a
   single bet instead of doubling it.
4. Waterfall the risk onto the account's real limits: per-trade cap, the 25 %
   single-asset notional cap (converted to risk through each stop distance),
   the heat budget and the cash floor.
5. Run ``risk_engine.preflight`` on the survivors in order and drop whatever
   would be rejected, naming the gate.

Nothing here mutates state, and nothing invents a number: a pair without enough
overlapping history gets ``None`` for its correlation, not a zero, and a symbol
without a stop or a history is dropped with the reason attached.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

from risk_engine import preflight

#: Aligned returns needed before a correlation is reported at all. Thirty is
#: already too few for a stable estimate; below it we say nothing.
MIN_OBS = 30

#: Shrink correlations toward zero by this much. Estimated correlations from a
#: few hundred bars are biased high by construction (selection of the window),
#: and an inflated matrix makes the allocator cut diversification it cannot see.
DEFAULT_SHRINKAGE = 0.30

_ERC_ITERS = 500
_ERC_TOL = 1e-10


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #
def log_returns(closes: list[float]) -> list[float]:
    """One return per bar. A non-positive close invalidates the series.

    Returning 0.0 for a gap would claim a flat bar that never traded; the
    caller is expected to drop the symbol instead.
    """
    if any(c is None or not math.isfinite(c) or c <= 0 for c in closes):
        raise ValueError("non-positive close")
    return [math.log(b / a) for a, b in itertools.pairwise(closes)]


def aligned_closes(series: dict[str, list[tuple[int, float]]]) -> tuple[list[int], dict[str, list[float]]]:
    """Closes on the timestamps every symbol shares (feed them to log_returns).

    Correlating series with different gaps measures the calendar, not the
    market. The intersection is the honest input; if it is too small the caller
    gets an empty dict and has to say so.
    """
    if not series:
        return [], {}
    maps = {sym: {int(ts): float(c) for ts, c in rows if c is not None} for sym, rows in series.items()}
    common = set.intersection(*(set(m) for m in maps.values())) if maps else set()
    stamps = sorted(common)
    out: dict[str, list[float]] = {}
    for sym, m in maps.items():
        closes = [m[ts] for ts in stamps]
        out[sym] = closes
    return stamps, out


def corr_pair(a: list[float], b: list[float]) -> float | None:
    """Pearson correlation, or ``None`` when the sample cannot support one."""
    n = min(len(a), len(b))
    if n < MIN_OBS:
        return None
    a, b = a[:n], b[:n]
    ma = sum(a) / n
    mb = sum(b) / n
    da = [x - ma for x in a]
    db = [x - mb for x in b]
    va = sum(x * x for x in da)
    vb = sum(x * x for x in db)
    if va <= 0 or vb <= 0:
        return None
    cov = sum(x * y for x, y in zip(da, db, strict=True))
    r = cov / math.sqrt(va * vb)
    return max(-1.0, min(1.0, r))


def corr_matrix(returns: dict[str, list[float]], shrinkage: float = DEFAULT_SHRINKAGE
                ) -> tuple[list[str], list[list[float | None]], list[tuple[str, str]]]:
    """Square correlation matrix with off-diagonals shrunk toward zero.

    Missing pairs stay ``None`` — the caller decides what to do about them
    rather than being handed a zero that reads like "independent".
    """
    syms = sorted(returns)
    n = len(syms)
    matrix: list[list[float | None]] = [[None] * n for _ in range(n)]
    missing: list[tuple[str, str]] = []
    lam = max(0.0, min(1.0, float(shrinkage)))
    for i in range(n):
        matrix[i][i] = 1.0
        for j in range(i + 1, n):
            r = corr_pair(returns[syms[i]], returns[syms[j]])
            if r is None:
                missing.append((syms[i], syms[j]))
                continue
            s = r * (1.0 - lam)
            matrix[i][j] = matrix[j][i] = s
    return syms, matrix, missing


def erc_weights(vols: list[float], corr: list[list[float | None]]) -> list[float]:
    """Equal-risk-contribution weights.

    Fixed point with a damped square-root update: each step pushes every
    candidate's share of portfolio variance toward ``1/n``. Weights sum to 1.
    Symbols whose correlation is unknown are treated as independent here — the
    ``missing`` list is reported alongside so the reader knows which pairs the
    weights did *not* see.
    """
    n = len(vols)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    if any(v <= 0 or not math.isfinite(v) for v in vols):
        raise ValueError("volatility must be positive")
    cov = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            c = corr[i][j]
            r = 1.0 if i == j else (c if c is not None else 0.0)
            cov[i][j] = r * vols[i] * vols[j]
    # Inverse-vol is the right starting point: it is already close to the
    # uncorrelated solution, so the iteration has little work to do.
    w = [1.0 / v for v in vols]
    s = sum(w)
    w = [x / s for x in w]
    for _ in range(_ERC_ITERS):
        mrc = [sum(cov[i][j] * w[j] for j in range(n)) for i in range(n)]
        var = sum(w[i] * mrc[i] for i in range(n))
        if var <= 0:
            break
        target = var / n
        contrib = [w[i] * mrc[i] for i in range(n)]
        if max(abs(c / target - 1.0) for c in contrib) < _ERC_TOL:
            break
        w = [w[i] * math.sqrt(target / c) if c > 0 else w[i] for i, c in enumerate(contrib)]
        s = sum(w)
        if s <= 0:
            break
        w = [x / s for x in w]
    for i, x in enumerate(w):
        if not math.isfinite(x):
            w[i] = 1.0 / n
    s = sum(w) or 1.0
    return [x / s for x in w]


# --------------------------------------------------------------------------- #
# Allocation
# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    symbol: str
    entry: float
    stop: float
    score: float = 0.0

    @property
    def stop_pct(self) -> float:
        """Distance to the stop as a fraction of entry (long side)."""
        return abs(self.entry - self.stop) / self.entry if self.entry > 0 else 0.0


@dataclass
class Limits:
    equity: float
    cash: float
    open_heat_pct: float = 0.0
    open_positions: int = 0
    concurrent_limit: int = 5
    max_heat_pct: float = 6.0
    min_cash_pct: float = 10.0
    max_single_asset_pct: float = 25.0
    per_trade_risk_pct: float = 0.75
    fee_pct: float = 0.002
    halted: bool = False
    shrinkage: float = DEFAULT_SHRINKAGE


@dataclass
class Alloc:
    symbol: str
    entry: float
    stop: float
    stop_pct: float
    risk_pct: float
    notional_usd: float
    fee_usd: float
    score: float
    vol: float
    corr_max: float | None
    risk_share_pct: float          # share of the heat budget this trade takes
    bound_by: str                  # what stopped it from being larger
    gates: list[dict] = field(default_factory=list)


def allocate(candidates: list[Candidate], returns: dict[str, list[float]], lim: Limits) -> dict:
    """Risk-budget the candidates, then keep only what the broker would take.

    ``returns`` are already-aligned log returns per symbol (see
    ``aligned_closes`` then ``log_returns``). Candidates without returns are dropped with a reason.
    """
    dropped: list[dict] = []
    usable: list[Candidate] = []
    for c in candidates:
        if c.entry <= 0 or c.stop <= 0:
            dropped.append({"symbol": c.symbol, "reason": "no entry or stop"})
        elif c.stop >= c.entry:
            dropped.append({"symbol": c.symbol, "reason": "stop is not below entry"})
        elif c.symbol not in returns or len(returns[c.symbol]) < MIN_OBS:
            dropped.append({"symbol": c.symbol, "reason": f"fewer than {MIN_OBS} aligned returns"})
        else:
            usable.append(c)

    if not usable:
        return _empty(lim, dropped, "no candidate had enough history to allocate")

    syms, corr, missing = corr_matrix({c.symbol: returns[c.symbol] for c in usable}, lim.shrinkage)
    order = {s: i for i, s in enumerate(syms)}
    usable.sort(key=lambda c: order.get(c.symbol, 0))

    vols = []
    for c in usable:
        r = returns[c.symbol]
        mean = sum(r) / len(r)
        var = sum((x - mean) ** 2 for x in r) / (len(r) - 1)
        # Per-bar volatility: the horizon cancels out of ERC weights, so no
        # annualisation constant is invented here.
        vols.append(math.sqrt(var))
    try:
        weights = erc_weights(vols, corr)
    except ValueError:
        weights = [1.0 / len(usable)] * len(usable)

    budget = max(0.0, lim.max_heat_pct - max(0.0, lim.open_heat_pct))
    equity = max(float(lim.equity or 0.0), 1e-9)
    if budget <= 0:
        for c in usable:
            dropped.append({"symbol": c.symbol, "reason": "no heat left (heat-cap)"})
        return _empty(lim, dropped, "the heat budget is already spent")

    risk = {c.symbol: w * budget for c, w in zip(usable, weights, strict=True)}
    share = {c.symbol: w for c, w in zip(usable, weights, strict=True)}
    bound: dict[str, str] = {c.symbol: "erc-share" for c in usable}
    capped: set[str] = set()

    # Waterfall: clamp whatever hits a hard cap, hand the leftover back to the
    # rest in proportion to their weights, repeat until nothing moves.
    for _ in range(12):
        moved = False
        for c in usable:
            if c.symbol in capped:
                continue
            per_trade = lim.per_trade_risk_pct
            # notional ≤ 25 % of equity  ⇒  risk ≤ 25 % × stop distance (% of equity)
            from_notional = lim.max_single_asset_pct * c.stop_pct
            cap = min(per_trade, from_notional)
            if risk[c.symbol] > cap:
                risk[c.symbol] = cap
                bound[c.symbol] = "single-asset-cap" if from_notional < per_trade else "per-trade-cap"
                capped.add(c.symbol)
                moved = True
        free = [c for c in usable if c.symbol not in capped]
        if not free:
            break
        left = budget - sum(risk[c.symbol] for c in usable if c.symbol in capped)
        wsum = sum(share[c.symbol] for c in free) or 1.0
        for c in free:
            risk[c.symbol] = max(0.0, left) * share[c.symbol] / wsum
        if not moved:
            break

    # Cash floor: the account cannot buy more than (cash − floor).
    available = max(0.0, float(lim.cash) - lim.min_cash_pct / 100.0 * equity)
    notional = {c.symbol: risk[c.symbol] / 100.0 * equity / max(c.stop_pct, 1e-9) for c in usable}
    gross = {s: n * (1.0 + lim.fee_pct) for s, n in notional.items()}
    total = sum(gross.values())
    scale = 1.0
    if total > available > 0:
        scale = available / total
        for c in usable:
            notional[c.symbol] *= scale
            risk[c.symbol] *= scale
            # The cash floor was the last thing to touch every size, so it is
            # the binding constraint the reader should see — whatever capped
            # the number before it.
            bound[c.symbol] = "cash"
    elif available <= 0:
        dropped.extend({"symbol": c.symbol, "reason": "no cash above the floor"} for c in usable)
        return _empty(lim, dropped, "cash is at or below the floor")

    # Feasibility: the same gates, in order, on a shrinking account.
    kept: list[Alloc] = []
    heat = max(0.0, lim.open_heat_pct)
    cash_left = float(lim.cash)
    for c in sorted(usable, key=lambda x: -notional.get(x.symbol, 0.0)):
        n = notional[c.symbol]
        if n <= 0:
            dropped.append({"symbol": c.symbol, "reason": "sized to zero"})
            continue
        fee = n * lim.fee_pct
        risk_usd = risk[c.symbol] / 100.0 * equity
        pf = preflight(
            equity=equity, cash=cash_left, open_heat=heat,
            open_positions=lim.open_positions + len(kept),
            concurrent_limit=lim.concurrent_limit,
            max_heat_pct=lim.max_heat_pct, min_cash_pct=lim.min_cash_pct,
            max_single_asset_pct=lim.max_single_asset_pct,
            risk_pct_cap=lim.per_trade_risk_pct, halted=lim.halted,
            notional_usd=n, fee_usd=fee, risk_usd=risk_usd,
        )
        if pf.blocked:
            gate = pf.first_block()
            dropped.append({"symbol": c.symbol, "reason": gate.id if gate else "blocked",
                            "detail": gate.detail if gate else ""})
            continue
        kept.append(Alloc(
            symbol=c.symbol, entry=c.entry, stop=c.stop, stop_pct=c.stop_pct,
            risk_pct=risk[c.symbol], notional_usd=n, fee_usd=fee,
            score=c.score, vol=vols[order[c.symbol]] if c.symbol in order else 0.0,
            corr_max=None,     # filled once the kept set is final
            risk_share_pct=(risk[c.symbol] / budget * 100 if budget else 0.0),
            bound_by=bound[c.symbol],
            gates=pf.summary()["gates"],
        ))
        heat += risk[c.symbol]
        cash_left -= n + fee

    # Correlation against the final kept set. Computing it while the list was
    # still being built left the first row with no peers and printed `null`,
    # which reads as "uncorrelated" rather than "not measured yet".
    peers = [a.symbol for a in kept]
    for a in kept:
        a.corr_max = _max_corr(a.symbol, peers, syms, corr)

    return {
        "ok": True,
        "method": "erc-shrunk",
        "shrinkage": lim.shrinkage,
        "min_obs": MIN_OBS,
        "budget_pct": round(budget, 4),
        "allocations": [a.__dict__ | {"risk_share_pct": round(a.risk_share_pct, 3)}
                        for a in kept],
        "dropped": dropped,
        "totals": {
            "equity": round(equity, 2),
            "budget_pct": round(budget, 4),
            "allocated_risk_pct": round(sum(a.risk_pct for a in kept), 4),
            "unallocated_pct": round(max(0.0, budget - sum(a.risk_pct for a in kept)), 4),
            "heat_after_pct": round(lim.open_heat_pct + sum(a.risk_pct for a in kept), 4),
            "invested_usd": round(sum(a.notional_usd for a in kept), 2),
            "fees_usd": round(sum(a.fee_usd for a in kept), 2),
            "cash_after_usd": round(lim.cash - sum(a.notional_usd + a.fee_usd for a in kept), 2),
            "slots_used": len(kept),
            "slots_left": max(0, lim.concurrent_limit - lim.open_positions - len(kept)),
            "cash_scale": round(scale, 6),
        },
        "correlation": {
            "symbols": syms,
            "values": [[None if v is None else round(v, 4) for v in row] for row in corr],
            "missing": [list(p) for p in missing],
        },
        "vol": {c.symbol: round(vols[order[c.symbol]], 8) for c in usable},
        "returned_symbols": [c.symbol for c in usable],
    }


def _max_corr(sym: str, against: list[str], syms: list[str], corr: list[list[float | None]]) -> float | None:
    """Strongest (absolute) correlation between ``sym`` and the others kept."""
    if sym not in syms:
        return None
    i = syms.index(sym)
    vals: list[float] = []
    for other in against:
        if other == sym or other not in syms:
            continue
        c = corr[i][syms.index(other)]
        if c is not None:
            vals.append(abs(c))
    return round(max(vals), 4) if vals else None


def _empty(lim: Limits, dropped: list[dict], reason: str) -> dict:
    return {
        "ok": True,
        "method": "erc-shrunk",
        "shrinkage": lim.shrinkage,
        "min_obs": MIN_OBS,
        "allocations": [],
        "dropped": dropped,
        "reason": reason,
        "budget_pct": round(max(0.0, lim.max_heat_pct - lim.open_heat_pct), 4),
        "totals": {
            "equity": round(float(lim.equity or 0.0), 2),
            "budget_pct": round(max(0.0, lim.max_heat_pct - lim.open_heat_pct), 4),
            "allocated_risk_pct": 0.0,
            "unallocated_pct": round(max(0.0, lim.max_heat_pct - lim.open_heat_pct), 4),
            "heat_after_pct": round(max(0.0, lim.open_heat_pct), 4),
            "invested_usd": 0.0,
            "fees_usd": 0.0,
            "cash_after_usd": round(float(lim.cash or 0.0), 2),
            "slots_used": 0,
            "slots_left": max(0, lim.concurrent_limit - lim.open_positions),
            "cash_scale": 1.0,
        },
        "correlation": {"symbols": [], "values": [], "missing": []},
        "vol": {},
    }
