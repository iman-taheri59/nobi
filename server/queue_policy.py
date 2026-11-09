"""Bounded closed-candle approvals. All timestamps are server epoch seconds.

A source candle opens at t, closes at t+step, and can be approved only until
 t+2*step. Reissuing a preview never moves that deadline. Missing clocks,
unfinished candles, non-LONG signals and stale data fail closed.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any

TF_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400, "D": 86400}


def closed_rows(rows: list[dict], tf: str, *, now: float | None = None) -> list[dict]:
    """Only finished bars may feed a closed-bar signal (never relabel a partial)."""
    stamp = time.time() if now is None else now
    step = TF_SECONDS[tf]
    out = []
    for row in rows:
        opened = float(row["t"])
        if not math.isfinite(opened) or opened < 0:
            raise ValueError("unusable candle clock")
        if opened + step <= stamp:
            out.append(row)
    return sorted(out, key=lambda row: float(row["t"]))


def scan_provenance(scanned_at: Any, max_scan_age: float | None, *, now: float) -> dict:
    """How old the scan behind a queue plan is, and whether that is too old.

    2.5.2 — the candle window (`expires_at`) says the *source bar* is still the
    latest closed one; it says nothing about whether anybody has re-scored it
    since. When the dashboard stops polling (hidden tab, lost network) the scan
    stops too, and a 15m plan could sit approvable for up to thirty minutes on
    a score nobody refreshed. The automatic path already refuses that
    (`_auto_signal_refusal`); the human path showed it as fresh.

    Missing/invalid provenance is stale (fail closed), not "unknown but fine".
    """
    try:
        at = float(scanned_at)
    except (TypeError, ValueError, OverflowError):
        at = float("nan")
    if not math.isfinite(at) or at <= 0 or at > now + 5:
        return {"scanned_at": None, "scan_age_s": None, "max_scan_age_s": max_scan_age, "stale": True,
                "stale_reason": "scan-clock-unavailable"}
    age = max(0.0, now - at)
    stale = max_scan_age is not None and age > max_scan_age
    return {"scanned_at": at, "scan_age_s": round(age, 1), "max_scan_age_s": max_scan_age, "stale": stale,
            "stale_reason": "scan-too-old" if stale else None}


def price_drift(plan_entry: Any, mid: Any, atr: Any, *, pct_limit: float, atr_limit: float) -> dict:
    """How far the market has moved from the price the plan was built on.

    2.5.2 — a fresh scan does not make a plan current: between two scans a thin
    pair can move 3 % and the row still says "enter at 100". Both yardsticks
    are reported, whichever trips first locks (`exceeded`): a flat market makes
    0.5×ATR smaller than 1.5 %, and that is exactly when it must speak. Signed
    `drift_pct` keeps the direction (minus = fell toward the stop); the lock
    uses the absolute value. A missing mid is "unknown", not fine: `exceeded`
    is True with reason `mid-unavailable`.
    """
    try:
        entry = float(plan_entry)
        px = float(mid)
    except (TypeError, ValueError, OverflowError):
        entry, px = float("nan"), float("nan")
    out: dict[str, Any] = {"mid": None, "drift_pct": None, "drift_atr": None,
                           "limit_pct": pct_limit, "limit_atr": atr_limit, "exceeded": True, "reason": "mid-unavailable"}
    if not (math.isfinite(entry) and entry > 0 and math.isfinite(px) and px > 0):
        return out
    pct = (px - entry) / entry * 100.0
    out.update({"mid": px, "drift_pct": round(pct, 3)})
    try:
        a = float(atr)
    except (TypeError, ValueError, OverflowError):
        a = float("nan")
    in_atr = (px - entry) / a if math.isfinite(a) and a > 0 else None
    out["drift_atr"] = round(in_atr, 3) if in_atr is not None else None
    by_pct = abs(pct) > pct_limit
    by_atr = in_atr is not None and abs(in_atr) > atr_limit
    out["exceeded"] = by_pct or by_atr
    out["reason"] = "price-moved-pct" if by_pct else ("price-moved-atr" if by_atr else None)
    return out


def describe(
    symbol: str, tf: str, signal: dict | None, bar: Any, *, now: float | None = None,
    scanned_at: Any = None, max_scan_age: float | None = None,
) -> dict:
    stamp = time.time() if now is None else now
    step = TF_SECONDS.get(tf)
    error = None
    try:
        opened = float(bar)
    except (TypeError, ValueError, OverflowError):
        opened = float("nan")
    if not step:
        error = "queue-timeframe-required"
    elif not signal:
        error = "queue-signal-unavailable"
    elif not math.isfinite(opened) or opened <= 0 or opened > stamp + step:
        error = "queue-signal-clock-invalid"
    elif stamp < opened + step:
        error = "queue-signal-candle-open"
    elif stamp >= opened + 2 * step:
        error = "queue-signal-expired"
    elif signal.get("direction") != "LONG":
        error = "queue-signal-not-LONG"
    if not error and signal:
        try:
            usable = all(
                x is not None and math.isfinite(float(x)) and float(x) > 0
                for x in (signal.get("lastClose"), (signal.get("risk") or {}).get("atr"))
            )
            score = signal.get("composite")
            usable = usable and score is not None and math.isfinite(float(score))
        except (TypeError, ValueError, OverflowError):
            usable = False
        if not usable:
            error = "queue-signal-values-invalid"
    if error:
        return {"ok": False, "blocked": True, "error": error}
    assert step is not None and signal is not None
    # Changes within the same source candle must invalidate an old preview too.
    material = {
        "symbol": symbol,
        "tf": tf,
        "bar": opened,
        "direction": signal.get("direction"),
        "entry": signal.get("lastClose"),
        "atr": (signal.get("risk") or {}).get("atr"),
        "score": signal.get("composite"),
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    return {
        "ok": True,
        "symbol": symbol,
        "tf": tf,
        "bar_open": opened,
        "bar_close": opened + step,
        "expires_at": opened + 2 * step,
        "server_now": stamp,
        "signal_key": digest,
        "score": signal.get("composite"),
        **scan_provenance(scanned_at, max_scan_age, now=stamp),
    }


def order_id(context: dict) -> str:
    # Replays (also after a restart) meet the same persisted clientOrderId.
    raw = f"{context['symbol']}:{context['tf']}:{context['bar_open']}"
    return "nobi-q-" + hashlib.sha256(raw.encode()).hexdigest()[:24]
