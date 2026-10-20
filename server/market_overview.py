"""A6 — one page that says what the market did, without inventing an index.

There is no free, reliable "crypto index" for the Iranian market, so this does
not pretend to publish one. It reports the quantities that can be computed from
the exchange's own tickers and states what each one is:

    breadth          of the symbols we track, how many are up over 24 h
    weighted change  the 24 h change weighted by dollar turnover — described
                     as exactly that, not as "the market index"
    dispersion       spread of the changes; high means the move is not uniform
    concentration    share of turnover in the top few names
    turnover         volume converted to USD, because summing IRT and USDT
                     volumes would add tomans to dollars

Every number is derived from rows the caller passes in; nothing is fetched here
and nothing is defaulted. A row without a volume or a change is excluded from
the statistic that needs it and counted in ``excluded``.
"""

from __future__ import annotations

import math

#: Below this many usable rows, breadth is not a statistic — it is a rumour.
MIN_ROWS = 3


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _stdev(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def overview(rows: list[dict], signals: dict[str, dict] | None = None,
             premium_pct: float | None = None, top_n: int = 5) -> dict:
    """Summarise one snapshot of the universe.

    ``rows`` are exchange tickers: ``{symbol, last, vol24, change24}`` where
    ``vol24`` is in the pair's quote currency. ``fx_usd`` on a row converts that
    volume to USD (1.0 for USDT pairs, USDTIRT for IRT pairs).
    """
    # USDT/IRT is the toman rate, not a market participant: including it drags
    # the median toward zero and makes every breadth reading look calm. It is
    # reported on its own, as FX.
    fx_pair = next((r for r in rows if r.get("symbol") == "USDTIRT"), None)
    usable = [r for r in rows if r.get("symbol") and r.get("symbol") != "USDTIRT"]

    # One row per asset. BTC/IRT and BTC/USDT are the same coin quoted twice;
    # counting both double-weights Bitcoin in the breadth and makes a nine-name
    # market look like a seventeen-name one. The domestic listing wins.
    seen: dict[str, dict] = {}
    same_asset = 0
    for r in sorted(usable, key=lambda x: 0 if str(x["symbol"]).endswith("IRT") else 1):
        base = _base(str(r["symbol"]))
        if base in seen:
            same_asset += 1
            continue
        seen[base] = r
    usable = list(seen.values())

    # Narrow once, in a loop, so every statistic below reads a `float` and not
    # "a float, or maybe nothing". The alternative — `(_num(x) or 0)` at each
    # use — turns a missing number into a zero, and a zero change still counts
    # as a flat market.
    priced: list[dict] = []
    changes: list[float] = []
    for r in usable:
        c = _num(r.get("change24"))
        if c is None:
            continue
        priced.append(r)
        changes.append(c)

    excluded = {
        "no_change": len(usable) - len(priced),
        "no_volume": len([r for r in priced if _num(r.get("vol24")) is None]),
        "no_fx": len([r for r in priced if _vol_usd(r) is None]),
        "same_asset": same_asset,
    }

    up = [c for c in changes if c > 0]
    down = [c for c in changes if c < 0]
    flat = [c for c in changes if c == 0]

    with_vol: list[tuple[float, float, float]] = []
    for r, c in zip(priced, changes, strict=True):
        v = _num(r.get("vol24"))
        u = _vol_usd(r)
        if v is not None and u is not None:
            with_vol.append((c, v, u))
    total_usd = sum(u for _c, _v, u in with_vol)

    weighted_change = None
    up_vol_share = None
    if total_usd > 0:
        weighted_change = sum(c * u for c, _v, u in with_vol) / total_usd
        up_usd = sum(u for c, _v, u in with_vol if c > 0)
        up_vol_share = up_usd / total_usd * 100

    ranked = sorted(usable, key=lambda r: -(_vol_usd(r) or 0))
    top = []
    for r in ranked[:top_n]:
        vol_usd = _vol_usd(r)
        top.append({"symbol": r["symbol"],
                    "vol_usd": round(vol_usd, 2) if vol_usd is not None else None,
                    "change24": _num(r.get("change24"))})
    top3_share = None
    if total_usd > 0:
        top3_share = sum((_vol_usd(r) or 0.0) for r in ranked[:3]) / total_usd * 100

    by_change = sorted(priced, key=lambda r: -(_num(r.get("change24")) or 0))
    # "Leaders" must not contain a negative mover just because the board is
    # short: with eight symbols and top_n=5 the fifth name can be down, and a
    # green -0.8 % in a column called leaders is a small lie.
    gainers = [r for r in by_change if (_num(r.get("change24")) or 0) > 0]
    losers = [r for r in reversed(by_change) if (_num(r.get("change24")) or 0) < 0]

    def _brief(r: dict) -> dict:
        vol_usd = _vol_usd(r)
        return {"symbol": r["symbol"], "change24": _num(r.get("change24")),
                "last": _num(r.get("last")),
                "vol_usd": (round(vol_usd, 2) if vol_usd is not None else None)}

    enough = len(priced) >= MIN_ROWS
    median = _median(changes) if enough else None
    dispersion = _stdev(changes) if enough else None
    out = {
        "ok": enough,
        "universe_size": len(usable),
        "priced": len(priced),
        "excluded": excluded,
        "breadth": {
            "up": len(up), "down": len(down), "flat": len(flat),
            "pct_up": round(len(up) / len(priced) * 100, 2) if priced else None,
            "pct_up_vol_weighted": round(up_vol_share, 2) if up_vol_share is not None else None,
        },
        "change": {
            "median_24h": round(median, 3) if median is not None else None,
            "mean_24h": round(sum(changes) / len(changes), 3) if enough else None,
            "vol_weighted_24h": round(weighted_change, 3) if weighted_change is not None and enough else None,
            "dispersion_24h": (round(dispersion, 3) if dispersion is not None else None),
        },
        "turnover": {
            "total_usd": round(total_usd, 2) if priced else None,
            "top3_share_pct": round(top3_share, 2) if top3_share is not None else None,
            "top": top,
        },
        "leaders": [_brief(r) for r in gainers[:top_n]] if enough else [],
        "laggards": [_brief(r) for r in losers[:top_n]] if enough else [],
        # True when even the best performer was down: "leaders" then means
        # least-bad, and the panel should say so rather than implying a rally.
        "all_down": bool(changes) and all(c < 0 for c in changes),
        "all_up": bool(changes) and all(c > 0 for c in changes),
        "premium_pct": premium_pct,
        "fx": ({"symbol": "USDTIRT", "last": _num(fx_pair.get("last")),
                "change24": _num(fx_pair.get("change24"))} if fx_pair else None),
        "note": (
            "volume-weighted change is not a market index; it is the average "
            "24 h move of the tracked symbols weighted by their dollar turnover"
        ),
    }
    if not enough:
        out["reason"] = (f"only {len(priced)} symbols had a 24 h change; "
                         f"at least {MIN_ROWS} are needed for a breadth reading")

    if signals:
        # Count each asset once, like the breadth above: a board that scores both
        # BTC/IRT and BTC/USDT would otherwise report 17 signals for an
        # 8-asset market and look far busier than it is.
        keep = {r["symbol"] for r in usable}
        by_base = {}
        for sym in (signals or {}):
            if sym == "USDTIRT":
                continue
            base = _base(sym)
            # prefer the row the caller kept (domestic listing)
            if sym in keep or base not in by_base:
                by_base[base] = sym
        dirs: dict[str, int] = {}
        regimes: dict[str, int] = {}
        comps: list[float] = []
        for sym in set(by_base.values()):
            snap = signals.get(sym) or {}
            d = (snap or {}).get("direction")
            if d:
                dirs[d] = dirs.get(d, 0) + 1
            rg = (snap or {}).get("regime")
            if rg:
                regimes[rg] = regimes.get(rg, 0) + 1
            c = _num((snap or {}).get("composite"))
            if c is not None:
                comps.append(c)
        out["signals"] = {
            "directions": dirs,
            "regimes": regimes,
            "avg_composite": round(sum(comps) / len(comps), 2) if comps else None,
            "scored": len(comps),
        }
    return out


def _base(sym: str) -> str:
    """The coin part of a pair.

    A bare quote currency ("IRT") has no coin, so it keeps its whole name —
    otherwise two unrelated rows collide on the empty string and one silently
    disappears from the totals.
    """
    for q in ("IRT", "USDT"):
        if sym.endswith(q) and len(sym) > len(q):
            return sym[: -len(q)]
    return sym


def _num(v) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _vol_usd(row: dict) -> float | None:
    """Turnover in USD. Mixing IRT and USDT volumes would add tomans to dollars."""
    v = _num(row.get("vol24"))
    fx = _num(row.get("fx_usd"))
    if v is None or fx is None or fx <= 0:
        return None
    return v / fx
