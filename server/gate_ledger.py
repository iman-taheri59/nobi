"""ADR-0022 stage 2 — the shadow ledger, and how a gate earns the right to block.

Stage 1 (1.0.25) gave the five label-shaped gates a named verdict each and applied
none of them. That is only useful if the verdicts are **kept**: an argument about
whether "spread too wide" is a real gate cannot be settled by a block that is
overwritten on the next rescan. So every judged bar is written down, once, and the
question becomes arithmetic.

What "earning the right" means here
-----------------------------------
ADR-0022 sets the bar: **one gate**, **at least 30 verdicts**, spanning **at least
5 days**, and the blocked names must actually have done worse afterwards. This
module computes exactly that, and refuses to answer before the sample exists:

* `insufficient` — fewer than 30 blocked verdicts or fewer than 5 days. The
  numbers are still reported (so you can watch the sample fill) but they are not
  a finding. Saying "no difference yet" at n=4 is the classic way a gate gets
  adopted for the wrong reason.
* `inconclusive` — the sample is big enough and the difference is smaller than
  `DELTA_BP`, or the bars disagreed with each other, or the data is simulated.
* `supports` / `contradicts` — the blocked names' forward return differs from the
  rows that passed, in the direction the gate claims, by more than `DELTA_BP`, on
  a majority of the bars measured.

Three decisions that keep the number honest
-------------------------------------------
*Paired per bar.* The comparison is done **inside each bar** — blocked mean minus
allowed mean at the same timestamp, at the same horizon — and only then averaged.
Comparing a blocked symbol's return against the whole market's would measure
whether the market went up, not whether the gate helps.

*Missing is not zero.* A row whose future bars do not exist yet is counted in
`unresolved` and left out of the means. Imputing a zero return for "we do not know
yet" would quietly drag every mean toward the centre and make small samples look
calm.

*Unknown is not a block.* `no-data` and `no-book` are not evidence about a gate;
they are evidence about the feed. Asking this module for one of them is refused
by name rather than answered with a number.

Nothing here applies anything. `applied` is `False` on every path, and the module
cannot reach the limits it is handed: it reads rows and returns a verdict.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import gates

#: Same database as the journal by default: the gate rows are a research record
#: like `signal_log`, and keeping them in one file means one backup, one rotation
#: and one place to look. A separate `NBX_JOURNAL_DB` still isolates a test.
DB_PATH = Path(os.getenv("NBX_JOURNAL_DB") or Path(__file__).with_name("journal.db"))

#: Verdict codes a gate can be measured on. `halted` is included: a market that is
#: closed is a real reason a row could not be traded, and it is testable like any
#: other. The unknown codes are excluded because they are not claims — see
#: `UNKNOWN_CODES` in `gates`.
MEASURABLE = tuple(c for c in gates.CODES if c not in gates.UNKNOWN_CODES and c != "ok")

#: ADR-0022's exit rule, as constants rather than prose.
MIN_VERDICTS = 30
MIN_DAYS = 5

#: A gate has to earn its keep by more than noise. 25 bp on a 4h horizon is small
#: enough to be reachable and large enough that a coin-flip sample will not pass
#: by accident.
DELTA_BP = 25.0

#: The share of bars that must point the same way. With 5+ days and 30+ verdicts,
#: requiring 60 % of bars keeps a single violent bar from deciding the verdict.
MIN_SHARE = 0.6

VERDICTS = ("insufficient", "inconclusive", "supports", "contradicts", "refused")

#: Forward horizons, in bars, kept small and explicit: the evidence is about the
#: next few hours, which is what the gate is protecting, not about a week later.
#: The route accepts overrides; these are what the card asks for.
HORIZON_BARS = 4


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the ledger database and make sure the table exists."""
    p = Path(path or DB_PATH)
    conn = sqlite3.connect(str(p), timeout=10)
    conn.row_factory = sqlite3.Row
    ensure(conn)
    return conn


def ensure(conn: sqlite3.Connection) -> None:
    """Create the table. Idempotent, like every other schema in this repo.

    The primary key is `(symbol, timeframe, ts)` where `ts` is the **close** time
    of the judged bar — the same convention `signal_log` uses. A rescan every 90
    seconds re-judges the same bar over and over; letting those repeat would
    inflate the sample with recency instead of evidence, and the sample size is
    exactly what the exit rule is written against.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS gate_shadow (
               symbol     TEXT    NOT NULL,
               timeframe  TEXT    NOT NULL,
               ts         INTEGER NOT NULL,
               code       TEXT    NOT NULL,
               codes      TEXT    NOT NULL,
               spread_bps REAL,
               depth_usd  REAL,
               age_bars   REAL,
               source     TEXT,
               created_at REAL    NOT NULL,
               PRIMARY KEY (symbol, timeframe, ts)
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS gate_shadow_gate ON gate_shadow(code, timeframe, ts)"
    )
    conn.commit()


def record(
    conn: sqlite3.Connection,
    verdicts: Mapping[str, Mapping[str, Any]],
    *,
    tf: str,
    ts_of: Mapping[str, int],
    source: str | None = None,
    now: float | None = None,
) -> int:
    """Write one row per symbol for the bar it was judged on.

    `verdicts` is the `verdicts` mapping from `gates.evaluate`; `ts_of` says which
    bar each symbol's verdict belongs to (the close time), because the caller is
    the only one who knows which bar it just read. A symbol without a stamp is
    skipped — a verdict with no bar cannot be joined to a future return later, and
    storing it would only pad the counts.

    `INSERT OR IGNORE` on purpose: the **first** verdict for a bar is the one that
    stood when the bar closed. If a later rescan of the same bar would judge it
    differently (a book that arrived late, say), keeping the later one is how a
    ledger starts agreeing with its author.
    """
    stamp = time.time() if now is None else now
    rows: list[tuple[Any, ...]] = []
    for sym, v in (verdicts or {}).items():
        key = str(sym or "").strip().upper()
        if not key:
            continue
        ts = ts_of.get(key)
        if ts is None:
            continue
        try:
            stamp_ts = int(ts)
        except (TypeError, ValueError):
            continue
        rows.append((
            key,
            tf,
            stamp_ts,
            str(v.get("code") or "ok"),
            json.dumps(list(v.get("codes") or []), separators=(",", ":")),
            _num(v.get("spread_bps")),
            _num(v.get("depth_usd")),
            _num(v.get("age_bars")),
            source,
            stamp,
        ))
    if not rows:
        return 0
    before = conn.total_changes
    conn.executemany(
        """INSERT OR IGNORE INTO gate_shadow
           (symbol, timeframe, ts, code, codes, spread_bps, depth_usd, age_bars,
            source, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def rows(conn: sqlite3.Connection, *, gate: str | None = None,
         tf: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    """Read the ledger back as plain dicts, newest first."""
    sql = "SELECT symbol, timeframe, ts, code, codes, spread_bps, depth_usd, age_bars, source FROM gate_shadow"
    where: list[str] = []
    args: list[Any] = []
    if gate:
        where.append("code = ?")
        args.append(gate)
    if tf:
        where.append("timeframe = ?")
        args.append(tf)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC, symbol ASC"
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def counts(conn: sqlite3.Connection, *, gate: str | None = None,
           tf: str | None = None) -> dict[str, Any]:
    """How much evidence exists, per gate code — the part the card can show.

    `days` is the number of distinct UTC dates the verdicts fall on, not the span
    between the first and last one: a burst of 200 verdicts in one afternoon
    covers one day of markets, and the exit rule is about days of markets.
    """
    sql = "SELECT code, ts FROM gate_shadow"
    where: list[str] = []
    args: list[Any] = []
    if gate:
        where.append("code = ?")
        args.append(gate)
    if tf:
        where.append("timeframe = ?")
        args.append(tf)
    if where:
        sql += " WHERE " + " AND ".join(where)
    per: dict[str, dict[str, Any]] = {}
    for code, ts in conn.execute(sql, args).fetchall():
        day = datetime.fromtimestamp(int(ts), UTC).date().isoformat()
        slot = per.setdefault(code, {"verdicts": 0, "days": set(),
                                     "first_ts": None, "last_ts": None})
        slot["verdicts"] += 1
        slot["days"].add(day)
        slot["first_ts"] = int(ts) if slot["first_ts"] is None else min(slot["first_ts"], int(ts))
        slot["last_ts"] = int(ts) if slot["last_ts"] is None else max(slot["last_ts"], int(ts))
    out: dict[str, dict[str, Any]] = {}
    for code, slot in per.items():
        days = len(slot["days"])
        out[code] = {
            "verdicts": slot["verdicts"],
            "days": days,
            "first_ts": slot["first_ts"],
            "last_ts": slot["last_ts"],
            "ready": slot["verdicts"] >= MIN_VERDICTS and days >= MIN_DAYS,
            "missing": {"verdicts": max(0, MIN_VERDICTS - slot["verdicts"]),
                        "days": max(0, MIN_DAYS - days)},
        }
    return {
        "gates": out,
        "limits": {"min_verdicts": MIN_VERDICTS, "min_days": MIN_DAYS,
                   "delta_bp": DELTA_BP, "min_share": MIN_SHARE},
        # The single sentence the UI shows, so readiness is never inferred from a
        # boolean that happens to be absent.
        "ready": sorted(k for k, v in out.items() if v["ready"]),
    }


def forward_bp(frame: Any, ts: int, horizon: int) -> float | None:
    """Close-to-close return in basis points, from the bar that closed at `ts`.

    `frame` is a candle frame with `t` (bar open, seconds) and `c` (close), which
    is what every caller in this repo already holds. The index is built on **close**
    times (`t + step`) exactly as `journal.resolve_symbol` does — a row stamped `T`
    was decided at the close of that bar, so entering at its open would be
    look-ahead. Returns `None` when the future bar is not there yet: "not known" is
    not "zero".
    """
    if frame is None or len(frame) == 0 or horizon <= 0:
        return None
    try:
        opens = [int(t) for t in frame["t"]]
        closes = [float(c) for c in frame["c"]]
    except (KeyError, TypeError, ValueError):
        return None
    n = len(opens)
    if n < 2:
        return None
    step = opens[1] - opens[0]
    if step <= 0:
        return None
    want = int(ts)
    # Frames are small (a few thousand rows) and sorted; a linear scan keeps this
    # module free of pandas and of assumptions about the index type.
    idx = None
    for i, op in enumerate(opens):
        if op + step == want:
            idx = i
            break
    if idx is None or idx + horizon > n - 1:
        return None
    entry = closes[idx]
    if entry <= 0:
        return None
    return (closes[idx + horizon] / entry - 1.0) * 10_000.0


def measure(
    evidence_rows: Iterable[Mapping[str, Any]],
    frames: Mapping[str, Any],
    *,
    gate: str,
    horizon: int = HORIZON_BARS,
    source: str | None = None,
) -> dict[str, Any]:
    """One gate's evidence, paired per bar. Nothing is applied.

    `evidence_rows` are ledger rows for **all** codes on the same timeframe: the
    allowed side of the comparison is every row judged `ok` on that same bar, which
    is why this function needs the whole bar and not just the blocked rows.
    """
    gate = str(gate or "")
    if gate in gates.UNKNOWN_CODES:
        return {
            "gate": gate, "verdict": "refused", "applied": False,
            "reason": "unknown-is-not-a-block",
            "note": (f"{gate} means the gate could not read the market; that is a fact "
                     "about the feed, not evidence for or against a block"),
            "blocked": {"verdicts": 0, "resolved": 0, "unresolved": 0},
            "allowed": {"resolved": 0}, "bars": 0, "bars_used": 0,
            "mean_diff_bp": None, "share_worse": None,
            "limits": {"min_verdicts": MIN_VERDICTS, "min_days": MIN_DAYS,
                       "delta_bp": DELTA_BP, "min_share": MIN_SHARE},
        }
    if gate not in gates.CODES:
        raise ValueError(f"unknown gate code: {gate!r}")

    per_bar: dict[int, dict[str, list[float]]] = {}
    blocked_n = 0
    blocked_unresolved = 0
    allowed_resolved = 0
    days: set[str] = set()
    symbols: set[str] = set()

    for row in evidence_rows or []:
        code = row.get("code")
        if code not in (gate, "ok"):
            continue
        sym = str(row.get("symbol") or "").upper()
        ts = row.get("ts")
        frame = frames.get(sym)
        if frame is None or ts is None:
            if code == gate:
                blocked_n += 1
                blocked_unresolved += 1
            continue
        ret = forward_bp(frame, int(ts), int(horizon))
        if ret is None:
            if code == gate:
                blocked_n += 1
                blocked_unresolved += 1
            continue
        if code == gate:
            blocked_n += 1
            symbols.add(sym)
            days.add(datetime.fromtimestamp(int(ts), UTC).date().isoformat())
        else:
            allowed_resolved += 1
        slot = per_bar.setdefault(int(ts), {"blocked": [], "ok": []})
        slot["blocked" if code == gate else "ok"].append(ret)

    diffs: list[float] = []
    for _ts, slot in sorted(per_bar.items()):
        if slot["blocked"] and slot["ok"]:
            diffs.append(_mean(slot["blocked"]) - _mean(slot["ok"]))

    resolved_blocked = blocked_n - blocked_unresolved
    mean_diff = _mean(diffs) if diffs else None
    share_worse = (sum(1 for d in diffs if d < 0) / len(diffs)) if diffs else None
    n_days = len(days)

    missing = {"verdicts": max(0, MIN_VERDICTS - blocked_n),
               "days": max(0, MIN_DAYS - n_days)}
    verdict, reason = _verdict(
        gate=gate, blocked=blocked_n, days=n_days, diffs=diffs,
        mean_diff=mean_diff, share_worse=share_worse, source=source,
    )

    return {
        "gate": gate,
        "verdict": verdict,
        "reason": reason,
        "applied": False,                 # ADR-0022: evidence is not an instruction
        "blocked": {"verdicts": blocked_n, "resolved": resolved_blocked,
                    "unresolved": blocked_unresolved},
        "allowed": {"resolved": allowed_resolved},
        "bars": len(per_bar),
        "bars_used": len(diffs),
        "days": n_days,
        "symbols": len(symbols),
        "mean_diff_bp": round(mean_diff, 2) if mean_diff is not None else None,
        "share_worse": round(share_worse, 3) if share_worse is not None else None,
        "horizon_bars": int(horizon),
        "missing": missing,
        "limits": {"min_verdicts": MIN_VERDICTS, "min_days": MIN_DAYS,
                   "delta_bp": DELTA_BP, "min_share": MIN_SHARE},
        "note": _NOTE[verdict],
    }


#: One sentence per verdict, so the card never has to paraphrase a number.
_NOTE = {
    "insufficient": ("too few verdicts or too few days: this is a sample, not a finding — "
                     "no gate leaves shadow mode on it"),
    "inconclusive": ("the sample is large enough and the blocked rows did not clearly do "
                     "worse: the gate has not earned the right to block"),
    "supports": ("the blocked rows underperformed the ones that passed by more than the "
                 "threshold: evidence for the gate — still not applied"),
    "contradicts": ("the blocked rows did better than the ones that passed: this gate "
                    "would have removed profitable trades"),
    "refused": ("this code means the gate could not read the market; it is not a block and "
                "cannot be evidence for one"),
}


def _verdict(*, gate: str, blocked: int, days: int, diffs: list[float],
             mean_diff: float | None, share_worse: float | None,
             source: str | None) -> tuple[str, str]:
    """The decision, in the order the ADR states it: sample first, then signal."""
    if blocked < MIN_VERDICTS:
        return "insufficient", f"blocked verdicts {blocked} < {MIN_VERDICTS}"
    if days < MIN_DAYS:
        return "insufficient", f"days covered {days} < {MIN_DAYS}"
    if not diffs:
        return "insufficient", "no bar carried both a blocked and an allowed resolved row"
    if source == "reference":
        # ADR-0009/ADR-0014: simulated candles may demonstrate a method, never
        # certify a rule. A gate promoted on synthetic data is a gate promoted on
        # the author's own assumptions.
        return "inconclusive", "reference data cannot promote a gate"
    assert mean_diff is not None and share_worse is not None
    if mean_diff <= -DELTA_BP and share_worse >= MIN_SHARE:
        return "supports", f"blocked rows earned {round(mean_diff, 1)} bp less"
    if mean_diff >= DELTA_BP and share_worse <= (1.0 - MIN_SHARE):
        return "contradicts", f"blocked rows earned {round(mean_diff, 1)} bp more"
    return "inconclusive", (f"difference {round(mean_diff, 1)} bp on {len(diffs)} bar(s) "
                           f"is inside the {DELTA_BP:g} bp threshold")


def _mean(xs: list[float]) -> float:
    return math.fsum(xs) / len(xs)


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None
