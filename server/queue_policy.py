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


def describe(
    symbol: str, tf: str, signal: dict | None, bar: Any, *, now: float | None = None
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
    }


def order_id(context: dict) -> str:
    # Replays (also after a restart) meet the same persisted clientOrderId.
    raw = f"{context['symbol']}:{context['tf']}:{context['bar_open']}"
    return "nobi-q-" + hashlib.sha256(raw.encode()).hexdigest()[:24]
