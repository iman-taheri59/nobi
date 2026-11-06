"""ADR-0051 — shadow A/B arms: alternative verdicts recorded next to the live one.

The live scan produces one verdict per symbol and bar. The journal diagnostic
showed that the *weights* behind that verdict (TA 0.55 / FA 0.25 / liq 0.20)
carry no 1h edge while FA and liquidity alone do. Re-weighting production on
that evidence would be fitting to one bad fortnight. Instead every scan also
scores a small set of fixed **arms** from the same inputs and stores their
verdicts in the row's ``features`` — no second scan, no new data, no change
to what the user sees or trades. After two weeks the resolver has outcomes
for every bar any arm called LONG, and the arms can be compared on identical
bars with identical stop geometry.

Arms are deliberately few and declared here, not in env, so that the arm set
is part of the engine config hash and cannot drift between restarts:

* ``A`` — the live verdict (reference; not stored, it *is* the row).
* ``B_fa_liq`` — TA 0.30 / FA 0.40 / liq 0.30, LONG iff composite ≥ LONG_MIN.
* ``C_gate`` — the live verdict with the ADR-0050 regime gate enforced.
* ``D_comp`` — the live weights but LONG decided by the composite, not by the
  TA score alone (isolates the threshold rule from the weights).

A severe FA veto overrides every arm, as it does the live row.
"""
from __future__ import annotations

import os
from typing import Any

#: Promotion threshold shared with ta_engine / journal.
LONG_MIN = 68.0

#: (ta, fa, liq) weights per arm. ``None`` = use the live verdict.
ARMS: dict[str, dict[str, Any]] = {
    "B_fa_liq": {"weights": (0.30, 0.40, 0.30), "rule": "composite"},
    "C_gate": {"weights": None, "rule": "live+regime"},
    "D_comp": {"weights": (0.55, 0.25, 0.20), "rule": "composite"},
}
VERSION = 1


def enabled() -> bool:
    return (os.getenv("NBX_SHADOW_ARMS", "on") or "on").strip().lower() not in ("off", "0", "false", "no")


def config() -> dict[str, Any]:
    return {"enabled": enabled(), "version": VERSION, "arms": sorted(ARMS)}


def _composite(weights: tuple[float, float, float], ta: float, fa: float | None, liq: float) -> float:
    w_ta, w_fa, w_liq = weights
    if fa is None:
        w = w_ta + w_liq
        return round((w_ta * ta + w_liq * liq) / w, 1)
    return round(w_ta * ta + w_fa * fa + w_liq * liq, 1)


def score(snap: dict[str, Any], regime_state: str | None) -> dict[str, Any]:
    """Return ``{arm: {"composite": float|None, "direction": str}}`` for one row.

    Reads only what the live row already carries: ``score`` (TA), ``fa_score``,
    ``liquidity_score``, the live ``direction`` and ``veto_severe``. Never
    mutates ``snap``.
    """
    ta = snap.get("score")
    liq = snap.get("liquidity_score")
    fa = snap.get("fa_score")
    live_dir = str(snap.get("direction") or "NEUTRAL").upper()
    severe = bool(snap.get("veto_severe"))
    out: dict[str, Any] = {}
    for name, spec in ARMS.items():
        if spec["rule"] == "live+regime":
            blocked = live_dir == "LONG" and regime_state == "risk-off"
            out[name] = {
                "composite": snap.get("composite"),
                "direction": "NEUTRAL" if blocked else live_dir,
            }
            continue
        if ta is None or liq is None:
            out[name] = {"composite": None, "direction": "NEUTRAL"}
            continue
        comp = _composite(spec["weights"], float(ta), None if fa is None else float(fa), float(liq))
        if severe:
            direction = "EXIT"
        elif comp >= LONG_MIN:
            direction = "LONG"
        else:
            direction = "NEUTRAL"
        out[name] = {"composite": comp, "direction": direction}
    return out


def any_long(arms: dict[str, Any] | None) -> bool:
    return any((v or {}).get("direction") == "LONG" for v in (arms or {}).values())
