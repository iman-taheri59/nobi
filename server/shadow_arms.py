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
* ``E_nochase`` (2.5.2) — the live verdict, but a LONG is refused when the
  bar is a chase: price ran ``≥ 2×ATR`` over the previous 4 h or Bollinger
  ``%B ≥ 1.0`` at issue. The 2026-11-08 pump-chase backfill found those
  LONGs carried the negative 1h/4h tail; this arm records the same rule
  live so it can be judged on bars it never saw.

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
    "E_nochase": {"weights": None, "rule": "live+nochase"},
}
VERSION = 2

#: Chase thresholds, fixed here for the same reason the arm set is.
CHASE_RUN4H_ATR = 2.0
CHASE_PCTB = 1.0


def chase(snap: dict[str, Any]) -> tuple[bool | None, dict[str, Any]]:
    """Is this bar a chase? ``(verdict, inputs)``; ``None`` when neither input exists."""
    diag = snap.get("diagnostics") or {}
    run = diag.get("run4h_atr")
    pb = diag.get("pctB")
    run_f = float(run) if isinstance(run, (int, float)) else None
    pb_f = float(pb) if isinstance(pb, (int, float)) else None
    inputs = {"run4h_atr": run_f, "pctB": pb_f}
    if run_f is None and pb_f is None:
        return None, inputs
    hit = (run_f is not None and run_f >= CHASE_RUN4H_ATR) or (pb_f is not None and pb_f >= CHASE_PCTB)
    return hit, inputs


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
        if spec["rule"] == "live+nochase":
            is_chase, inputs = chase(snap)
            # Unknown inputs cannot refuse: the arm then equals the live row and
            # says so (`chase: None`), instead of inventing a cautious verdict.
            out[name] = {
                "composite": snap.get("composite"),
                "direction": "NEUTRAL" if (live_dir == "LONG" and is_chase) else live_dir,
                "chase": is_chase,
                **inputs,
            }
            continue
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
