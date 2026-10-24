"""ADR-0022 stage 1 — the gates, evaluated in the open and applied to nothing.

ADR-0007 defined six hard gates. Its own 2026-10-20 review recorded that five of
them are labels, weights or broker-side rules rather than gates: data age,
symbol suspension, spread, depth and the daily stop. This module gives those five
a *named verdict each* without letting any of them remove a row.

Why a shadow layer at all
-------------------------
Closing a gate changes which symbols are selected, so it changes behaviour, and
behaviour needs evidence (ADR-0009, and ADR-0021 wrote the same rule for the
thresholds). But a gate does not have to act in order to be measured: the scan
already holds the age of every symbol's candles, and the book route already holds
the spread and the depth. Recording what *would* have happened costs one pass over
rows that are already in memory, and it turns "should this gate block?" from an
argument into a number.

Two rules that make the verdicts worth reading
----------------------------------------------
*Unknown is not fine.* A symbol whose book never arrived is not `ok` and is not
`would_block` either; it is `no-book`, and it is counted on its own. A verdict
that cannot be computed is a result, and it has a name.

*One reason, all reasons.* `code` is the first failing gate in a fixed precedence
— can this even be evaluated (`halted`, `no-data`), then is the input valid
(`stale-data`), then is the market tradeable (`no-book`, `spread-wide`,
`book-thin`) — so a row never reports a cosmetic failure while a fundamental one
is also true. `codes` keeps all of them, because a reader who fixes the spread
deserves to know the candles were stale too.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

#: Verdict codes, in the precedence order described above. The order is part of
#: the contract: a test asserts it, so reordering it is a decision, not an edit.
CODES = (
    "halted",
    "no-data",
    "stale-data",
    "no-book",
    "spread-wide",
    "book-thin",
    "ok",
)

#: Codes that mean "this row could not be judged", as opposed to "this row was
#: judged and failed". They are counted separately everywhere, because a missing
#: measurement is not a finding.
UNKNOWN_CODES = frozenset({"no-data", "no-book"})

MODE_SHADOW = "shadow"


@dataclass(frozen=True)
class GateLimits:
    """Thresholds as parameters, with the off switch as the default.

    `max_age_bars` has a real default because the rule already exists and lives in
    `freshness.DEFAULT_TOLERANCE_BARS`; passing `None` here means "do not judge
    age", never "judge it at zero". The two money-shaped thresholds default to
    `None` on purpose: until the real distribution of spread and depth across the
    Nobitex universe has been measured, any number would be a guess wearing a
    gate's clothing. `enforcing` is inert in this version — the block reports and
    nothing reads it back — and it exists so that turning it on later is a
    deliberate, visible change rather than a new code path nobody reviewed.
    """

    max_age_bars: float | None = 2.0
    max_spread_bps: float | None = None
    min_depth_usd: float | None = None
    enforcing: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_age_bars": self.max_age_bars,
            "max_spread_bps": self.max_spread_bps,
            "min_depth_usd": self.min_depth_usd,
            "enforcing": self.enforcing,
            "enabled": sorted(k for k, v in (
                ("age", self.max_age_bars),
                ("spread", self.max_spread_bps),
                ("depth", self.min_depth_usd),
            ) if v is not None),
        }


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        return out if out == out and out not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _verdict(code: str, **detail: Any) -> dict[str, Any]:
    return {"code": code, **detail}


def evaluate_symbol(
    symbol: str,
    *,
    age_bars: float | None = None,
    halted: bool = False,
    book: dict[str, Any] | None = None,
    limits: GateLimits | None = None,
) -> dict[str, Any]:
    """Verdict for one row. Pure: no clock, no network, no cache.

    `age_bars` is the freshness of *that symbol's* candles (1.0.23 made this
    measurable per symbol, which is why the gates can be too). `book` is whatever
    the caller already has: `{spread_bps, depth_usd}` or `None`.
    """
    lim = limits or GateLimits()
    codes: list[dict[str, Any]] = []

    if halted:
        codes.append(_verdict("halted", why="the market is closed or the symbol left the scan"))

    if age_bars is None:
        codes.append(_verdict("no-data", why="no usable candle stamp for this symbol"))
    elif lim.max_age_bars is not None and age_bars > lim.max_age_bars:
        codes.append(_verdict("stale-data", age_bars=round(age_bars, 2),
                              limit=lim.max_age_bars))

    if book is None:
        codes.append(_verdict("no-book", why="no order book has been read for this symbol"))
    else:
        spread_bps = _num(book.get("spread_bps"))
        depth_usd = _num(book.get("depth_usd"))
        if lim.max_spread_bps is not None:
            if spread_bps is None:
                codes.append(_verdict("no-book", why="the book has no usable spread"))
            elif spread_bps > lim.max_spread_bps:
                codes.append(_verdict("spread-wide", spread_bps=round(spread_bps, 2),
                                      limit=lim.max_spread_bps))
        if lim.min_depth_usd is not None:
            if depth_usd is None:
                codes.append(_verdict("no-book", why="the book has no usable depth"))
            elif depth_usd < lim.min_depth_usd:
                codes.append(_verdict("book-thin", depth_usd=round(depth_usd, 2),
                                      limit=lim.min_depth_usd))

    # ADR-0026: the numbers the gate *saw*, whether or not it objected. The ledger
    # used to store spread/depth only inside a failing verdict's detail, so with
    # the money thresholds switched off every row was `ok`, every row had a NULL
    # spread, and the distribution ADR-0026 needs could never be built. Reporting
    # the inputs separately is what lets "0 spread-wide verdicts" mean "none was
    # over a limit" instead of "no spread was ever recorded".
    inputs = {
        "spread_bps": _num((book or {}).get("spread_bps")) if book else None,
        "depth_usd": _num((book or {}).get("depth_usd")) if book else None,
        "age_bars": None if age_bars is None else round(float(age_bars), 3),
    }

    if not codes:
        return {"symbol": symbol, "code": "ok", "codes": [], "detail": {},
                "inputs": inputs}

    ordered = sorted(codes, key=lambda c: CODES.index(c["code"]))
    first = ordered[0]
    return {
        "symbol": symbol,
        "code": first["code"],
        "codes": [c["code"] for c in ordered],
        "detail": {k: v for c in ordered for k, v in c.items() if k != "code"},
        "inputs": inputs,
    }


def evaluate(
    rows: Iterable[dict[str, Any]],
    *,
    limits: GateLimits | None = None,
    mode: str = MODE_SHADOW,
) -> dict[str, Any]:
    """The block a route returns. Reports; removes nothing.

    `rows` are `{symbol, age_bars, halted, book}` — whatever the caller has. A row
    with no `book` key is `no-book`, not `ok`: the absence of a measurement is a
    verdict, and the counts say so out loud instead of hiding it in an average.
    """
    lim = limits or GateLimits()
    verdicts: dict[str, dict[str, Any]] = {}
    would_block: list[str] = []
    unknown: list[str] = []
    ok: list[str] = []

    for row in rows:
        sym = str((row or {}).get("symbol") or "")
        if not sym:
            continue
        out = evaluate_symbol(
            sym,
            age_bars=_num(row.get("age_bars")),
            halted=bool(row.get("halted")),
            book=row.get("book"),
            limits=lim,
        )
        verdicts[sym] = {"code": out["code"], "codes": out["codes"],
                         **out["detail"], "inputs": out.get("inputs") or {}}
        if out["code"] == "ok":
            ok.append(sym)
        elif out["code"] in UNKNOWN_CODES:
            unknown.append(sym)
        else:
            would_block.append(sym)

    return {
        "mode": mode,
        "applied": False,                      # ADR-0022: nothing is ever applied here
        "limits": lim.as_dict(),
        "would_block": would_block,
        "unknown": unknown,
        "verdicts": verdicts,
        "counts": {
            "rows": len(verdicts),
            "ok": len(ok),
            "would_block": len(would_block),
            "unknown": len(unknown),
        },
        # The sentence the dashboard prints under the chip, so a reader never has
        # to infer what "would_block" means from the field name.
        "note": ("shadow only: these are the symbols a gate would have removed; "
                 "nothing was removed and no ranking changed"),
    }
