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
STATES = ("risk-off", "neutral", "risk-on", "unknown")


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


def config() -> dict[str, Any]:
    return {
        "mode": MODE,
        "min_median_24h": MIN_MEDIAN_24H,
        "min_breadth_pct": MIN_BREADTH_PCT,
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
) -> dict[str, Any]:
    """Classify one market-overview snapshot. Pure; never raises."""
    mm = MIN_MEDIAN_24H if min_median_24h is None else float(min_median_24h)
    mb = MIN_BREADTH_PCT if min_breadth_pct is None else float(min_breadth_pct)
    ov = overview if isinstance(overview, dict) else {}
    median = _num((ov.get("change") or {}).get("median_24h"))
    pct_up = _num((ov.get("breadth") or {}).get("pct_up"))
    priced = ov.get("priced")
    inputs = {"median_24h": median, "pct_up": pct_up, "priced": priced}
    thresholds = {"min_median_24h": mm, "min_breadth_pct": mb}

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
    would_block = is_long and state == "risk-off"
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
