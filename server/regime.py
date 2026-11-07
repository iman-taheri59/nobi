"""ADR-0050 — market-regime gate on LONG issuance.

Why
---
The 2026-11-06 journal diagnostic (`docs/analysis/journal-diagnostic-2026-11-06.md`)
found that in the clean window the whole universe fell 3.5–4 % per day while the
engine kept issuing LONG rows — and those rows did *worse* than the symbols it
labelled EXIT. A LONG-only system has no way to be right in that tape, so the
first lever is not the score, it is **whether a LONG may be issued at all**.

What this module does
---------------------
``assess()`` turns the existing market overview (A6: breadth and median 24 h
move of the tracked universe) into one of four named states::

    fx-shock   |premium_pct| >= fx_premium  OR  |USDT/IRT 24h move| >= fx_move
               (the quote currency is moving, not the coins; checked first)
    risk-off   median_24h <= min_median  OR  pct_up <= min_breadth
    risk-on    median_24h >= +min_median AND pct_up >= 100 - min_breadth
    neutral    anything else with enough rows
    unknown    overview not ok (too few rows) — never treated as risk-off

``apply()`` then decides what happens to a LONG row under the configured mode:

    off      nothing is recorded, nothing changes (pre-ADR behaviour)
    shadow   the state travels with every row (`market_regime`,
             `regime_gate.would_block`) and into the journal features, but the
             direction is untouched  ← default
    enforce  in `risk-off` a LONG becomes NEUTRAL with `regime_cap`, exactly the
             way a fundamental veto caps a verdict; `direction_raw` keeps the
             original so the UI and the journal can explain it

Shadow first is the repository rule (ADR-0009/0021/0022): the gate is measured
on the journal before it is allowed to remove a row. `scripts/journal_diagnostic.py`
splits LONG outcomes by `market_regime`, which is the evidence that promotes
`shadow` to `enforce`.

Configuration (environment, read once at import)
------------------------------------------------
    NBX_REGIME_GATE            off | shadow | enforce      (default shadow)
    NBX_REGIME_MIN_MEDIAN_24H  float, percent              (default -2.0)
    NBX_REGIME_MIN_BREADTH_PCT float, percent of symbols up (default 35)
"""
from __future__ import annotations

import os
from typing import Any

MODES = ("off", "shadow", "enforce")
STATES = ("fx-shock", "risk-off", "neutral", "risk-on", "unknown")
#: States in which a LONG is blocked (shadow: would_block; enforce: NEUTRAL).
BLOCKING_STATES = frozenset({"risk-off", "fx-shock"})


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _mode_env() -> str:
    raw = (os.getenv("NBX_REGIME_GATE") or "shadow").strip().lower()
    return raw if raw in MODES else "shadow"


MODE: str = _mode_env()
MIN_MEDIAN_24H: float = _float_env("NBX_REGIME_MIN_MEDIAN_24H", -2.0)
MIN_BREADTH_PCT: float = _float_env("NBX_REGIME_MIN_BREADTH_PCT", 35.0)
#: `fx-shock` (2026-11-07): the rial, not the coin, is moving. Either the
#: implied USDT/IRT premium is stretched or the toman rate itself jumped. In
#: that tape every IRT pair "trends" in TA terms while the coin does nothing,
#: so a LONG is a currency bet the engine never meant to take. Precedes
#: risk-off because breadth can look fine while the quote currency collapses.
FX_PREMIUM_PCT: float = _float_env("NBX_REGIME_FX_PREMIUM_PCT", 6.0)
FX_MOVE_24H_PCT: float = _float_env("NBX_REGIME_FX_MOVE_24H_PCT", 3.0)


def config() -> dict[str, Any]:
    return {
        "mode": MODE,
        "min_median_24h": MIN_MEDIAN_24H,
        "min_breadth_pct": MIN_BREADTH_PCT,
        "fx_premium_pct": FX_PREMIUM_PCT,
        "fx_move_24h_pct": FX_MOVE_24H_PCT,
        "adr": "ADR-0050",
    }


def _num(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f and f not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def assess(
    overview: dict[str, Any] | None,
    *,
    min_median_24h: float | None = None,
    min_breadth_pct: float | None = None,
    fx_premium_pct: float | None = None,
    fx_move_24h_pct: float | None = None,
) -> dict[str, Any]:
    """Classify one market-overview snapshot. Pure; never raises."""
    mm = MIN_MEDIAN_24H if min_median_24h is None else float(min_median_24h)
    mb = MIN_BREADTH_PCT if min_breadth_pct is None else float(min_breadth_pct)
    fp = FX_PREMIUM_PCT if fx_premium_pct is None else float(fx_premium_pct)
    fm = FX_MOVE_24H_PCT if fx_move_24h_pct is None else float(fx_move_24h_pct)
    ov = overview if isinstance(overview, dict) else {}
    median = _num((ov.get("change") or {}).get("median_24h"))
    pct_up = _num((ov.get("breadth") or {}).get("pct_up"))
    priced = ov.get("priced")
    premium = _num(ov.get("premium_pct"))
    fx_move = _num(ov.get("usdt_irt_change_24h"))
    inputs = {"median_24h": median, "pct_up": pct_up, "priced": priced,
              "premium_pct": premium, "usdt_irt_change_24h": fx_move}
    thresholds = {"min_median_24h": mm, "min_breadth_pct": mb,
                  "fx_premium_pct": fp, "fx_move_24h_pct": fm}

    # The FX check needs no breadth: a stretched premium is a finding even when
    # the overview is thin. Missing FX inputs simply do not trigger it.
    fx_reasons: list[str] = []
    if premium is not None and abs(premium) >= fp:
        fx_reasons.append(f"|premium|>={fp:g}")
    if fx_move is not None and abs(fx_move) >= fm:
        fx_reasons.append(f"|usdt_irt_24h|>={fm:g}")
    if fx_reasons:
        return {"state": "fx-shock", "reasons": fx_reasons, "inputs": inputs, "thresholds": thresholds}

    if not ov.get("ok") or median is None or pct_up is None:
        return {
            "state": "unknown",
            "reasons": ["overview-not-ok" if not ov.get("ok") else "missing-inputs"],
            "inputs": inputs,
            "thresholds": thresholds,
        }

    reasons: list[str] = []
    if median <= mm:
        reasons.append(f"median_24h<={mm:g}")
    if pct_up <= mb:
        reasons.append(f"pct_up<={mb:g}")
    if reasons:
        state = "risk-off"
    elif median >= abs(mm) and pct_up >= 100.0 - mb:
        state = "risk-on"
        reasons = [f"median_24h>={abs(mm):g}", f"pct_up>={100.0 - mb:g}"]
    else:
        state = "neutral"
    return {"state": state, "reasons": reasons, "inputs": inputs, "thresholds": thresholds}


def apply(
    direction: str,
    composite: float | None,
    regime: dict[str, Any] | None,
    *,
    mode: str | None = None,
    neutral_max: float = 67.0,
) -> tuple[str, float | None, dict[str, Any]]:
    """Decide the fate of one row under the gate.

    Returns ``(direction, composite, meta)``. ``meta`` is merged into the signal
    snapshot: ``market_regime`` always (except in ``off``), ``regime_gate`` with
    ``would_block`` for LONG rows in a risk-off tape, and ``regime_cap`` only
    when the verdict was actually changed (enforce mode).
    """
    m = (mode or MODE)
    if m == "off":
        return direction, composite, {}
    state = (regime or {}).get("state") or "unknown"
    is_long = (direction or "").upper() == "LONG"
    would_block = is_long and state in BLOCKING_STATES
    # 67 = VETO_CAP in main.py: one point below LONG_SCORE_MIN (68), the same
    # ceiling a fundamental veto uses, so a capped row can never re-qualify.
    meta: dict[str, Any] = {
        "market_regime": state,
        "regime_gate": {
            "mode": m,
            "would_block": would_block,
            "applied": bool(would_block and m == "enforce"),
            "reasons": list((regime or {}).get("reasons") or []),
        },
    }
    if would_block and m == "enforce":
        capped = composite if composite is None else min(float(composite), neutral_max)
        meta["regime_cap"] = neutral_max
        return "NEUTRAL", capped, meta
    return direction, composite, meta
