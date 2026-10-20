"""Prediction journal — ADR-0012 (signal performance monitoring).

Every directional signal the engine emits is written, immutably, with the
inputs that produced it; its forward outcome is then filled in per horizon.
The point is to be able to answer "if this signal had been followed, what
would have happened?" *independently of whether the paper broker took it* —
selection bias is exactly what a broker-only P&L hides.

Design notes
------------
* **One row per bar.** ``UNIQUE(symbol, timeframe, ts)`` where ``ts`` is the
  close time of the bar the signal was computed on. Re-scanning the same bar
  cannot inflate the sample.
* **Enter at the bar close.** The score is computed from that bar, so entering
  at its open would be look-ahead. Entry price = close of the signal bar.
* **The R model mirrors ``paper_trading``**: stop at ``2 * ATR``, TP1 at
  ``1.2R`` taking half, stop then moved to breakeven (+0.2 %). A bar that
  touches both stop and TP1 resolves as the stop (conservative, and the same
  convention as ``backtest.py``).
* **Honest provenance.** ``source`` records whether the candles were live,
  cached or synthetic. Synthetic rows are kept (they exercise the pipeline)
  but ``summary()`` can filter them out and the UI labels them.

The module is pure/synchronous (sqlite3 + pandas) so it can be tested without
a server; fetching candles and scheduling live there.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

DB_PATH = Path(os.getenv("NBX_JOURNAL_DB") or Path(__file__).with_name("journal.db"))

TF_SECONDS = {"15m": 900, "1h": 3600, "4h": 14_400, "D": 86_400}

#: Wall-clock horizons from ADR-0012. Converted to bars per timeframe, with a
#: floor of one bar so H1 stays meaningful on the 4h/daily charts.
HORIZONS = {"H1": 3_600, "H2": 86_400, "H3": 7 * 86_400}

#: Same risk geometry as ``risk_engine`` / ``paper_trading``.
RISK_K = 2.0          # stop distance = 2 * ATR
TP1_R = 1.2           # first target at 1.2R
TP2_R = 2.0
TRAIL_R = 0.002       # stop moves to entry * 1.002 after TP1 (long)

#: ``ta_engine`` thresholds, duplicated here so the journal does not need to
#: re-derive a direction for old rows.
LONG_MIN = 68.0


#: ADR-0012 asks every row to carry the config it was scored under, so a later
#: parameter change cannot silently rewrite history.
CONFIG_HASH = hashlib.sha1(
    f"risk_k={RISK_K}|tp1={TP1_R}|tp2={TP2_R}|trail={TRAIL_R}|long_min={LONG_MIN}".encode()
).hexdigest()[:10]


def bars_for(tf: str, seconds: int) -> int:
    step = TF_SECONDS.get(tf, 900)
    return max(1, int(round(seconds / step)))


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open (and initialise) the journal database."""
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    init(conn)
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    ta_score REAL,
    fa_score REAL,
    liquidity_score REAL,
    composite REAL,
    rank_in_cycle INTEGER,
    regime TEXT,
    entry REAL, stop REAL, tp1 REAL, tp2 REAL, atr REAL,
    features TEXT,
    source TEXT,
    origin TEXT,
    config_hash TEXT,
    created_at INTEGER,
    UNIQUE(symbol, timeframe, ts)
);
CREATE INDEX IF NOT EXISTS ix_signal_ts ON signal_log(ts DESC);
CREATE INDEX IF NOT EXISTS ix_signal_sym_tf ON signal_log(symbol, timeframe);

CREATE TABLE IF NOT EXISTS journal_meta (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    action TEXT NOT NULL,
    actor TEXT,
    result TEXT,
    detail TEXT,
    client TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_log(ts DESC);

CREATE TABLE IF NOT EXISTS signal_outcomes (
    signal_id INTEGER NOT NULL,
    horizon_key TEXT NOT NULL,
    resolved_at INTEGER,
    exit_ts INTEGER,
    exit_price REAL,
    mfe_pct REAL,
    mae_pct REAL,
    hit_tp1 INTEGER,
    hit_sl INTEGER,
    timeout INTEGER,
    r_multiple REAL,
    return_pct REAL,
    bars_used INTEGER,
    PRIMARY KEY (signal_id, horizon_key),
    FOREIGN KEY (signal_id) REFERENCES signal_log(id) ON DELETE CASCADE
);
"""


#: Columns added after the first release. SQLite cannot add them through
#: ``CREATE TABLE IF NOT EXISTS``, so they are applied idempotently here.
MIGRATIONS = {
    "signal_log": [
        ("voided_at", "INTEGER"),
        ("void_reason", "TEXT"),
    ],
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in MIGRATIONS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    _repair_nanosecond_exit_ts(conn)
    conn.commit()


def _repair_nanosecond_exit_ts(conn: sqlite3.Connection, *, force: bool = False) -> int:
    """Rewrite exit_ts values that were stored in nanoseconds.

    A seconds epoch is ~1.7e9; anything above 1e10 can only be a nanosecond
    timestamp (or garbage), so the conversion is unambiguous. Runs once per
    database: after the writer fix no new nanosecond value can appear.
    """
    if not force and get_meta(conn, "exit_ts_unit") == "s":
        return 0
    cur = conn.execute(
        "UPDATE signal_outcomes SET exit_ts = CAST(exit_ts / 1000000000 AS INTEGER) "
        "WHERE exit_ts IS NOT NULL AND exit_ts > 10000000000")
    set_meta(conn, "exit_ts_unit", "s")
    return cur.rowcount


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)


# --------------------------------------------------------------------------- #
# Small key/value store (baselines, thresholds, last MSI verdict)
# --------------------------------------------------------------------------- #
def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO journal_meta(key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(value, ensure_ascii=False), int(time.time())),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM journal_meta WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        return default


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
@dataclass
class JournalRow:
    ts: int
    symbol: str
    timeframe: str
    direction: str
    ta_score: float
    composite: float
    entry: float
    atr: float
    regime: str = "UNKNOWN"
    fa_score: float | None = None
    liquidity_score: float | None = None
    rank_in_cycle: int | None = None
    features: dict | None = None
    source: str = "unknown"
    origin: str = "live"
    config_hash: str | None = None

    def targets(self) -> tuple[float, float, float]:
        dist = RISK_K * self.atr
        return (self.entry - dist, self.entry + dist * TP1_R, self.entry + dist * TP2_R)


def make_row(snapshot: dict, bar_ts: int, *, rank: int | None = None,
             origin: str = "live", source: str | None = None) -> JournalRow | None:
    """Turn one scored symbol snapshot into a journal row.

    Returns ``None`` when the snapshot has no usable art (no ATR / no close),
    because the R model would be meaningless.
    """
    atr = float((snapshot.get("risk") or {}).get("atr") or 0.0)
    entry = float(snapshot.get("lastClose") or 0.0)
    if entry <= 0 or atr <= 0:
        return None
    features = {
        "components": snapshot.get("components") or {},
        "diagnostics": snapshot.get("diagnostics") or {},
        "composite_basis": snapshot.get("composite_basis"),
        "fa_fresh": snapshot.get("fa_fresh"),
        # Why the stored verdict differs from the raw TA verdict. Without this
        # a journal row would say NEUTRAL and look like it contradicted itself.
        "direction_raw": snapshot.get("direction_raw"),
        "vetoes": list(((snapshot.get("fa") or {}).get("vetoes")) or []),
        "veto_cap": snapshot.get("veto_cap"),
        "veto_severe": snapshot.get("veto_severe"),
        "uncapped_composite": snapshot.get("uncapped_composite"),
    }
    return JournalRow(
        ts=int(bar_ts),
        symbol=str(snapshot.get("symbol") or "").upper(),
        timeframe=str(snapshot.get("tf") or "15m"),
        direction=str(snapshot.get("direction") or "NEUTRAL").upper(),
        ta_score=float(snapshot.get("score") or 0.0),
        composite=float(snapshot.get("composite") or snapshot.get("score") or 0.0),
        entry=entry,
        atr=atr,
        regime=str(snapshot.get("regime") or "UNKNOWN"),
        fa_score=(float(snapshot["fa_score"]) if snapshot.get("fa_score") is not None else None),
        liquidity_score=(float(snapshot["liquidity_score"]) if snapshot.get("liquidity_score") is not None else None),
        rank_in_cycle=rank,
        features=features,
        source=source or str(snapshot.get("data_source") or "unknown"),
        origin=origin,
        config_hash=snapshot.get("config_hash"),
    )


def insert_rows(conn: sqlite3.Connection, rows: Iterable[JournalRow]) -> int:
    """Insert rows, ignoring bars already journalled. Returns the insert count."""
    n = 0
    now = int(time.time())
    for r in rows:
        if r is None or not r.symbol:
            continue
        stop, tp1, tp2 = r.targets()
        cur = conn.execute(
            """INSERT OR IGNORE INTO signal_log
               (ts, symbol, timeframe, direction, ta_score, fa_score, liquidity_score,
                composite, rank_in_cycle, regime, entry, stop, tp1, tp2, atr,
                features, source, origin, config_hash, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r.ts, r.symbol, r.timeframe, r.direction, r.ta_score, r.fa_score,
             r.liquidity_score, r.composite, r.rank_in_cycle, r.regime,
             r.entry, stop, tp1, tp2, r.atr,
             json.dumps(r.features or {}, ensure_ascii=False), r.source, r.origin,
             r.config_hash, now),
        )
        n += cur.rowcount
    conn.commit()
    return n


# --------------------------------------------------------------------------- #
# VOID law (ADR-0012 §2.5)
# --------------------------------------------------------------------------- #
#: A symbol that disappears from the exchange must not leave its predictions
#: "pending" forever — that inflates the pending count and quietly hides a
#: failure mode. Delisted rows are frozen as VOID and excluded from metrics.
DEFAULT_VOID_GRACE_S = 12 * 3600


def mark_void(conn: sqlite3.Connection, active_symbols: Iterable[str],
              *, grace_s: int = DEFAULT_VOID_GRACE_S, now: int | None = None) -> int:
    """Freeze signals whose symbol is no longer tradeable.

    Only unresolved rows older than ``grace_s`` are voided, so a symbol that
    briefly drops out of a refresh (a 429, an empty response) is not killed by
    a transient miss.
    """
    now = now or int(time.time())
    active = {s.upper() for s in active_symbols}
    if not active:
        return 0                    # never void the whole journal on an empty scan
    rows = conn.execute(
        """SELECT id, symbol FROM signal_log
           WHERE voided_at IS NULL
             AND ts < ?
             AND id NOT IN (SELECT signal_id FROM signal_outcomes WHERE horizon_key='H2')""",
        (now - grace_s,),
    ).fetchall()
    doomed = [r["id"] for r in rows if r["symbol"] not in active]
    if not doomed:
        return 0
    marks = ",".join("?" * len(doomed))
    conn.execute(
        f"UPDATE signal_log SET voided_at=?, void_reason='symbol-left-universe' WHERE id IN ({marks})",
        [now, *doomed],
    )
    conn.commit()
    return len(doomed)


# --------------------------------------------------------------------------- #
# Outcome simulation (pure)
# --------------------------------------------------------------------------- #
@dataclass
class Outcome:
    horizon_key: str
    exit_ts: int
    exit_price: float
    mfe_pct: float
    mae_pct: float
    hit_tp1: bool
    hit_sl: bool
    timeout: bool
    r_multiple: float
    return_pct: float
    bars_used: int


def simulate_long(df: pd.DataFrame, i: int, entry: float, atr: float,
                  bars: int) -> dict[str, Any] | None:
    """Walk forward from bar ``i`` for ``bars`` bars and score the trade.

    Mirrors ``paper_trading.PaperBroker.evaluate_positions``: half off at TP1,
    stop to breakeven, remainder exits on the trailed stop or at the horizon
    close. Returns ``None`` when there is not a single future bar yet.
    """
    stop_dist = RISK_K * atr
    if stop_dist <= 0 or entry <= 0:
        return None
    stop = entry - stop_dist
    tp1 = entry + stop_dist * TP1_R
    trail = entry * (1 + TRAIL_R)

    end = min(i + bars, len(df) - 1)
    if end <= i:
        return None

    high, low, close = df["h"].values, df["l"].values, df["c"].values
    # `candles_to_df` parses "t" into datetime64[ns]; `int()` on one of those
    # yields NANOSECONDS, not seconds. Every exit_ts written before this fix
    # was ~1e18 instead of ~1.7e9, which silently broke any consumer that
    # compared it to `ts` (charts, exports, retention filters).
    times = pd.to_datetime(df["t"]).astype("int64") // 10**9

    half_done = False
    realised = 0.0
    mfe = 0.0
    mae = 0.0
    leg = 1.0            # remaining fraction of the position
    bars_used = 0
    hit_tp1 = False
    hit_sl = False
    exit_price = float(close[end])

    for k in range(i + 1, end + 1):
        bars_used = k - i
        hi, lo = float(high[k]), float(low[k])
        mfe = max(mfe, (hi / entry - 1) * 100)
        mae = min(mae, (lo / entry - 1) * 100)

        # Stop first: a bar touching both levels is resolved conservatively.
        if lo <= stop:
            hit_sl = True
            realised += leg * ((stop / entry) - 1) * 100 / (stop_dist / entry * 100)
            exit_price = stop
            leg = 0.0
            break

        if not half_done and hi >= tp1:
            half_done = True
            hit_tp1 = True
            realised += 0.5 * TP1_R
            leg = 0.5
            stop = trail          # moved to breakeven after TP1

    if leg > 0:
        # Horizon reached with an open remainder: mark it to the last close.
        px = float(close[end])
        realised += leg * (px - entry) / stop_dist
        exit_price = px

    return {
        "exit_ts": int(times[min(end, len(times) - 1)]),
        "exit_price": round(exit_price, 8),
        "mfe_pct": round(mfe, 3),
        "mae_pct": round(mae, 3),
        "hit_tp1": hit_tp1,
        "hit_sl": hit_sl,
        "timeout": not hit_sl,
        "r_multiple": round(realised, 4),
        "return_pct": round((exit_price / entry - 1) * 100, 3),
        "bars_used": bars_used,
    }


def resolve_symbol(conn: sqlite3.Connection, symbol: str, tf: str,
                   df: pd.DataFrame) -> int:
    """Fill every horizon that the available candles can now resolve.

    Rows are stamped with the **close** time of the bar the signal was issued
    on, so the index is built on close times too: a signal stamped ``T`` is
    evaluated from the first bar *after* ``T`` (entry happens at that close).
    Building the index on candle open times instead would silently shift every
    outcome by one bar.
    """
    if df is None or df.empty:
        return 0
    step = TF_SECONDS.get(tf, 900)
    ts_index = {
        int(t) + step: i
        for i, t in enumerate(df["t"].values.astype("int64") // 10**9)
    }
    pending = conn.execute(
        # Only LONG rows have a tradeable R; EXIT/NEUTRAL are recorded for
        # research but never scored as trades.
        """SELECT id, ts, entry, atr, direction FROM signal_log
           WHERE symbol=? AND timeframe=? AND direction='LONG'
             AND voided_at IS NULL
             AND id NOT IN (SELECT signal_id FROM signal_outcomes
                            WHERE horizon_key=?)""",
        (symbol.upper(), tf, max(HORIZONS)),
    ).fetchall()

    filled = 0
    for row in pending:
        i = ts_index.get(int(row["ts"]))
        if i is None:
            continue
        for key, seconds in HORIZONS.items():
            bars = bars_for(tf, seconds)
            if i + bars > len(df) - 1:
                continue            # not enough future bars yet
            if conn.execute(
                "SELECT 1 FROM signal_outcomes WHERE signal_id=? AND horizon_key=?",
                (row["id"], key),
            ).fetchone():
                continue
            out = simulate_long(df, i, float(row["entry"]), float(row["atr"]), bars)
            if out is None:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO signal_outcomes
                   (signal_id, horizon_key, resolved_at, exit_ts, exit_price,
                    mfe_pct, mae_pct, hit_tp1, hit_sl, timeout, r_multiple,
                    return_pct, bars_used)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row["id"], key, int(time.time()), out["exit_ts"], out["exit_price"],
                 out["mfe_pct"], out["mae_pct"], int(out["hit_tp1"]),
                 int(out["hit_sl"]), int(out["timeout"]), out["r_multiple"],
                 out["return_pct"], out["bars_used"]),
            )
            filled += 1
    conn.commit()
    return filled


# --------------------------------------------------------------------------- #
# Reading / metrics
# --------------------------------------------------------------------------- #
def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation without scipy. ``None`` when the sample is degenerate."""
    n = len(xs)
    if n < 3:
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: v[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 4)


def _family(features_json: str | None) -> str:
    try:
        comp = (json.loads(features_json or "{}").get("components") or {})
    except (ValueError, TypeError):
        return "unknown"
    if not comp:
        return "unknown"
    return max(comp.items(), key=lambda kv: kv[1] or 0)[0]


# --------------------------------------------------------------------------- #
# MSI — Model Signal Integrity (ADR-0012 §2.5)
# --------------------------------------------------------------------------- #
#: "If the realized IC over the last 30 days falls to half of the backtest IC
#: for the same window, raise a signal-corruption warning." (ADR-0012 §2.5)
MSI_WINDOW_DAYS = 30
MSI_BASELINE_DAYS = 180
MSI_MIN_SAMPLES = 20
MSI_CORRUPT_RATIO = 0.5
MSI_DEGRADED_RATIO = 0.8

#: A pegged pair has no directional edge to measure; its returns are noise and
#: pooling it with real assets drags every IC toward zero (or flips its sign).
STABLE_BASES = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "USDD"}


def is_stable(symbol: str) -> bool:
    sym = symbol.upper()
    base = sym[:-3] if sym.endswith("IRT") else (sym[:-4] if sym.endswith("USDT") else sym)
    return base in STABLE_BASES


def _rank_ic(xs: list[float], ys: list[float]) -> float | None:
    return _spearman(xs, ys)


def ic_by_symbol(pairs: dict[str, tuple[list[float], list[float]]],
                 *, min_per_symbol: int = 30) -> dict[str, Any]:
    """Time-series IC per symbol, then an equal-weight mean across symbols.

    Pooling every symbol into one rank correlation conflates two different
    questions ("does this bar score higher than other bars of the same asset?"
    vs "does this asset score higher than another asset?") and lets one flat
    pair dominate. Per symbol + mean is the honest default here.
    """
    per: dict[str, float | None] = {}
    used: list[float] = []
    for sym, (xs, ys) in pairs.items():
        if is_stable(sym) or len(xs) < min_per_symbol:
            per[sym] = None
            continue
        ic = _rank_ic(xs, ys)
        per[sym] = ic
        if ic is not None:
            used.append(ic)

    mean_ic = round(sum(used) / len(used), 4) if used else None
    ordered = sorted(used)
    if not ordered:
        median = None
    elif len(ordered) % 2:
        median = ordered[len(ordered) // 2]
    else:
        median = (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
    return {
        "ic": mean_ic,
        "median": round(median, 4) if median is not None else None,
        "symbols_scored": len(used),
        "per_symbol": {k: (round(v, 4) if v is not None else None) for k, v in per.items()},
        "method": "mean of per-symbol Spearman (stablecoins excluded)",
    }


def ic_for_window(conn: sqlite3.Connection, start: int, end: int) -> tuple[float | None, int]:
    """Realized IC of the score for rows resolved inside a window."""
    rows = conn.execute(
        """SELECT s.symbol, s.composite, o.return_pct
           FROM signal_log s JOIN signal_outcomes o ON o.signal_id = s.id
           WHERE o.horizon_key='H2' AND s.direction='LONG' AND s.voided_at IS NULL
             AND s.ts >= ? AND s.ts < ?""",
        (int(start), int(end)),
    ).fetchall()
    pairs: dict[str, tuple[list[float], list[float]]] = {}
    for r in rows:
        xs, ys = pairs.setdefault(r["symbol"], ([], []))
        xs.append(float(r["composite"] or 0.0))
        ys.append(float(r["return_pct"] or 0.0))
    out = ic_by_symbol(pairs, min_per_symbol=15)
    return out["ic"], len(rows)


def backtest_ic(frames: list[tuple[str, "pd.DataFrame"]], horizon_bars: int,
                *, min_per_symbol: int = 30) -> dict[str, Any]:
    """IC of the score as a forward-return predictor, per symbol then averaged.

    Uses the same causal `ta_score_series` the engine runs, with the return
    measured ``horizon_bars`` ahead — the reference ADR-0012 compares live
    behaviour against.
    """
    from ta_engine import ta_score_series   # local import keeps this module light

    pairs: dict[str, tuple[list[float], list[float]]] = {}
    for sym, df in frames:
        if df is None or len(df) <= horizon_bars + 60:
            continue
        series = ta_score_series(df)
        closes = df["c"].to_numpy(float)
        xs, ys = pairs.setdefault(sym, ([], []))
        for i, snap in enumerate(series):
            if snap is None or i + horizon_bars >= len(closes):
                continue
            xs.append(float(snap["score"]))
            ys.append(float((closes[i + horizon_bars] / closes[i] - 1) * 100))

    out = ic_by_symbol(pairs, min_per_symbol=min_per_symbol)
    out["n"] = sum(len(v[0]) for k, v in pairs.items() if not is_stable(k))
    out["horizon_bars"] = horizon_bars
    out["enough_data"] = out["ic"] is not None
    return out


# --------------------------------------------------------------------------- #
# Strategy lifecycle (ADR-0012 §3, stage transitions)
# --------------------------------------------------------------------------- #
#: Ordered stages. `retire` is terminal: nothing in this module moves a strategy
#: back out of it, because un-retiring is a human decision that should leave a
#: trace, not something a ratio should do silently.
STAGES = ("train", "run", "watch", "retire")


def next_stage(current: str, msi_status: str, *, streak: int = 0) -> tuple[str, str]:
    """Where a strategy should be, and why.

    Pure policy: no database, no clock. That is deliberate — the rule that
    decides whether real money may follow a signal is exactly the rule that has
    to be readable and testable on its own.

    `streak` is how many consecutive evaluations have already agreed with a
    *degraded* verdict. Stepping down on a single bad day would make the
    dashboard thrash; waiting forever would make the rule decorative.
    """
    cur = current if current in STAGES else "train"

    if msi_status == "corrupted":
        return "retire", ("realized IC fell to ≤50% of the reference — retire and "
                          "re-review before any promotion (ADR-0009)")
    if msi_status == "insufficient":
        # No evidence is not evidence of health. Hold the stage; never promote.
        if cur in ("watch", "retire"):
            return cur, "not enough resolved signals to change stage; staying put"
        return cur, "not enough resolved signals to promote; staying put"
    if msi_status == "degraded":
        if cur == "retire":
            return cur, "already retired; un-retiring is a human decision"
        if cur == "train":
            return "train", "still in training; degradation does not promote"
        if cur == "run":
            # One bad reading is not a demotion; three in a row is.
            if streak + 1 >= 3:
                return "watch", "degraded on three consecutive evaluations — move to watch"
            return "run", "degraded once; watching, not demoting yet"
        return "watch", "still degraded while on watch"
    if msi_status == "healthy":
        if cur == "train":
            return "run", "reference held on realized data — eligible to run"
        if cur == "watch":
            return "run", "recovered to the reference — back to run"
        return cur, "healthy; stage unchanged"
    return cur, f"unknown MSI status {msi_status!r}; stage unchanged"


def stage_reason_key(current: str, msi_status: str) -> str:
    """Machine-readable twin of `next_stage`'s sentence.

    The UI is bilingual; shipping an English sentence into a Persian page is the
    same defect as an untranslated label. The sentence stays for logs and for
    API consumers that read English, the key is what the dashboard renders.
    """
    cur = current if current in STAGES else "train"
    if msi_status == "corrupted":
        return "retire_corrupted"
    if msi_status == "insufficient":
        return "hold_insufficient"
    if msi_status == "degraded":
        if cur == "retire":
            return "retired"
        if cur == "train":
            return "hold_training"
        if cur == "run":
            return "degraded_watch"      # decision depends on the streak
        return "watch_degraded"
    if msi_status == "healthy":
        if cur in ("train", "watch"):
            return "promote_to_run"
        return "hold_healthy"
    return "hold_unknown"


def lifecycle(conn: sqlite3.Connection) -> dict[str, Any]:
    """Current stage plus the evaluation that produced it.

    Stored in meta so the stage survives a restart, but recomputed on every
    read: a stage that disagrees with the current MSI would be worse than none.
    """
    verdict = msi(conn)
    stored = get_meta(conn, "strategy_stage") or {}
    current = stored.get("stage") or "train"
    prev_status = stored.get("msi_status")
    streak = int(stored.get("streak") or 0)
    # Streak counts consecutive `degraded` evaluations; anything else resets it.
    # (The first degraded reading is day one of the streak, not day zero.)
    if verdict["status"] != "degraded":
        new_streak = 0
    elif prev_status == "degraded":
        new_streak = streak + 1
    else:
        new_streak = 1

    stage, why = next_stage(current, verdict["status"], streak=streak)
    if stage != current or verdict["status"] != prev_status or streak != new_streak:
        set_meta(conn, "strategy_stage", {
            "stage": stage,
            "msi_status": verdict["status"],
            "streak": new_streak,
            "changed_at": int(time.time()),
        })
    return {
        "stage": stage,
        "previous": current,
        "changed": stage != current,
        "reason": why,
        "reason_key": stage_reason_key(current, verdict["status"]),
        "demoted_for_streak": (verdict["status"] == "degraded" and current == "run"
                               and new_streak >= 3),
        "streak": new_streak,
        "stages": list(STAGES),
        "msi_status": verdict["status"],
        "automatic": True,
        # Promotion *out of* the paper stage is not something this module does;
        # ADR-0009 keeps that gate human.
        "promotion_is_manual": True,
    }


def msi(conn: sqlite3.Connection, *, window_days: int = MSI_WINDOW_DAYS,
        baseline_days: int = MSI_BASELINE_DAYS,
        min_samples: int = MSI_MIN_SAMPLES) -> dict[str, Any]:
    """Compare realized IC to the reference and say plainly what it means."""
    now = int(time.time())
    start = now - window_days * 86400
    realized_ic, realized_n = ic_for_window(conn, start, now)

    stored = get_meta(conn, "msi_baseline") or {}
    base_sources = stored.get("sources") or {}
    # A reference measured on simulated candles describes the simulator, not
    # the market. Say so instead of letting a number look authoritative.
    base_synthetic = bool(base_sources) and all(k == "synthetic" for k in base_sources)

    if stored.get("ic") is not None:
        base_ic = float(stored["ic"])
        base_n = int(stored.get("n") or 0)
        base_source = stored.get("source") or "backtest"
        base_at = stored.get("computed_at")
    else:
        # No backtest baseline yet: fall back to the *preceding* journal window
        # and label it honestly, because it is not a backtest.
        base_ic, base_n = ic_for_window(conn, start - baseline_days * 86400, start)
        base_source = "journal-history"
        base_at = None

    ratio = None
    if realized_ic is not None and base_ic not in (None, 0):
        ratio = round(realized_ic / base_ic, 3)

    if realized_n < min_samples:
        status = "insufficient"
        message = (f"Only {realized_n} resolved signals in the last {window_days} days "
                   f"(need {min_samples}); no integrity verdict is possible yet.")
    elif base_ic is None or base_n < min_samples:
        status = "insufficient"
        message = "No usable baseline (run a backtest baseline first)."
    elif base_ic <= 0:
        status = "degraded"
        message = ("The reference IC is itself non-positive: the score has no "
                   "measured predictive edge, so drift cannot be assessed.")
    elif ratio <= MSI_CORRUPT_RATIO:
        status = "corrupted"
        message = (f"Realized IC {realized_ic:+.3f} is at or below half of the reference "
                   f"{base_ic:+.3f}. Signal corruption: re-review parameters before "
                   f"any promotion to live (ADR-0009).")
    elif ratio <= MSI_DEGRADED_RATIO:
        status = "degraded"
        message = (f"Realized IC {realized_ic:+.3f} is {ratio:.0%} of the reference "
                   f"{base_ic:+.3f}: watch it, do not promote yet.")
    else:
        status = "healthy"
        message = f"Realized IC {realized_ic:+.3f} holds {ratio:.0%} of the reference {base_ic:+.3f}."

    warning = None
    if base_synthetic:
        # A verdict needs a reference that means something. A simulator's IC
        # describes the simulator, so no healthy/degraded/corrupted call is
        # defensible from it — say "insufficient", loudly.
        warning = (
            "The reference was measured on simulated candles. It characterises the "
            "offline generator (whose periodic drift a momentum score follows and "
            "then overshoots), not the market — do not read it as evidence about "
            "the strategy. Re-run the baseline against live candles."
        )
        status = "insufficient"
        message = (f"Reference IC {base_ic:+.3f} comes from simulated candles, so an "
                   f"integrity verdict is not possible. Re-run the baseline against live data.")

    return {
        "status": status,
        "message": message,
        "warning": warning,
        "ratio": ratio,
        "realized": {"ic": realized_ic, "n": realized_n, "window_days": window_days},
        "baseline": {"ic": base_ic, "n": base_n, "source": base_source,
                     "computed_at": base_at, "window_days": baseline_days,
                     "synthetic": base_synthetic, "sources": base_sources},
        "thresholds": {"corrupt_ratio": MSI_CORRUPT_RATIO,
                       "degraded_ratio": MSI_DEGRADED_RATIO,
                       "min_samples": min_samples},
        "checked_at": now,
    }


CALIBRATION_BUCKETS = [(0, 55), (55, 65), (65, 75), (75, 85), (85, 101)]

def fetch_signals(conn: sqlite3.Connection, *, tf: str | None = None,
                  symbol: str | None = None, direction: str | None = None,
                  source: str | None = None, origin: str | None = None,
                  since: int | None = None,
                  limit: int = 200, offset: int = 0) -> list[sqlite3.Row]:
    where, params = ["1=1"], []
    if tf:
        where.append("timeframe=?")
        params.append(tf)
    if symbol:
        where.append("symbol=?")
        params.append(symbol.upper())
    if direction:
        where.append("direction=?")
        params.append(direction.upper())
    if source:
        where.append("source=?")
        params.append(source)
    if origin:
        where.append("origin=?")
        params.append(origin)
    if since:
        where.append("ts>=?")
        params.append(int(since))
    sql = f"""SELECT * FROM signal_log WHERE {' AND '.join(where)}
              ORDER BY ts DESC, composite DESC LIMIT ? OFFSET ?"""
    params += [max(1, min(limit, 2000)), max(0, offset)]
    return conn.execute(sql, params).fetchall()


def _filters(tf, origin, source, since) -> tuple[str, list]:
    where, params = ["1=1"], []
    if tf:
        where.append("s.timeframe=?")
        params.append(tf)
    if source:
        where.append("s.source=?")
        params.append(source)
    if origin:
        where.append("s.origin=?")
        params.append(origin)
    if since:
        where.append("s.ts>=?")
        params.append(int(since))
    return " AND ".join(where), params


def summary(conn: sqlite3.Connection, *, tf: str | None = None,
            since: int | None = None, source: str | None = None,
            origin: str | None = None, limit: int = 60) -> dict[str, Any]:
    """Everything the Performance page needs.

    Metrics are computed with SQL aggregates over the **whole** filtered set,
    not over the page window: a 2 000-row cap used to make `pending` and `void`
    silently under-report as soon as the journal grew.
    """
    where, params = _filters(tf, origin, source, since)

    counts = conn.execute(
        f"""SELECT COUNT(*) AS issued,
                   SUM(CASE WHEN direction='LONG' AND voided_at IS NULL THEN 1 ELSE 0 END) AS sig,
                   SUM(CASE WHEN direction='LONG' AND voided_at IS NOT NULL THEN 1 ELSE 0 END) AS void
            FROM signal_log s WHERE {where}""", params).fetchone()
    issued = counts["issued"] or 0
    sig = counts["sig"] or 0
    voided_n = counts["void"] or 0

    resolved = conn.execute(
        f"""SELECT s.id, s.symbol, s.composite, s.features, s.source, s.ts,
                   o.r_multiple, o.return_pct
            FROM signal_log s JOIN signal_outcomes o ON o.signal_id = s.id
            WHERE o.horizon_key='H2' AND s.direction='LONG' AND s.voided_at IS NULL
              AND {where}
            ORDER BY s.ts DESC""", params).fetchall()

    rs = [float(r["r_multiple"] or 0.0) for r in resolved]
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    hit = (len(wins) / len(rs)) if rs else None
    exp = (sum(rs) / len(rs)) if rs else None
    gross_w, gross_l = sum(wins), abs(sum(losses))
    vpf = (gross_w / gross_l) if gross_l > 0 else (None if not wins else float("inf"))

    pairs: dict[str, tuple[list[float], list[float]]] = {}
    for r in resolved:
        xs, ys = pairs.setdefault(r["symbol"], ([], []))
        xs.append(float(r["composite"] or 0))
        ys.append(float(r["return_pct"] or 0))
    ic_block = ic_by_symbol(pairs, min_per_symbol=15)
    ic = ic_block["ic"]

    horizons = []
    for key in HORIZONS:
        rows = conn.execute(
            f"""SELECT o.r_multiple FROM signal_log s
                JOIN signal_outcomes o ON o.signal_id = s.id
                WHERE o.horizon_key=? AND s.direction='LONG' AND s.voided_at IS NULL
                  AND {where}""", [key, *params]).fetchall()
        vals = [float(r["r_multiple"] or 0.0) for r in rows]
        horizons.append({
            "k": key, "n": len(vals),
            "hit": (sum(1 for v in vals if v > 0) / len(vals)) if vals else None,
            "avgr": (sum(vals) / len(vals)) if vals else None,
        })

    fam: dict[str, list[float]] = {}
    for r in resolved:
        fam.setdefault(_family(r["features"]), []).append(float(r["r_multiple"] or 0.0))
    total_n = sum(len(v) for v in fam.values()) or 1
    families = sorted(
        ({"n": k, "r": round(sum(v) / len(v), 3), "share": round(len(v) / total_n * 100, 1)}
         for k, v in fam.items()),
        key=lambda d: -d["share"])

    calibration = []
    for lo, hi in CALIBRATION_BUCKETS:
        vals = [float(r["r_multiple"] or 0.0) for r in resolved
                if lo <= float(r["composite"] or 0) < hi]
        if vals:
            calibration.append([round((lo + hi) / 2, 1),
                                round(sum(1 for v in vals if v > 0) / len(vals), 4),
                                len(vals)])

    # Two queries instead of one page + a sort. Sorting a newest-first page
    # only *looks* resolved-first: with a small `limit` the page can contain
    # nothing but pending rows, and the table then leads with blanks again.
    resolved_rows = conn.execute(
        f"""SELECT s.* FROM signal_log s
            JOIN signal_outcomes o ON o.signal_id = s.id AND o.horizon_key = 'H2'
            WHERE {where} ORDER BY s.ts DESC LIMIT ?""",
        [*params, limit]).fetchall()
    open_rows = conn.execute(
        f"""SELECT s.* FROM signal_log s
            LEFT JOIN signal_outcomes o ON o.signal_id = s.id AND o.horizon_key = 'H2'
            WHERE o.signal_id IS NULL AND {where}
            ORDER BY s.ts DESC LIMIT ?""",
        [*params, limit]).fetchall()
    rows = list(resolved_rows) + list(open_rows[:max(0, limit - len(resolved_rows))])

    ids = [r["id"] for r in rows]
    outcomes: dict[int, dict[str, sqlite3.Row]] = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for o in conn.execute(
                f"SELECT * FROM signal_outcomes WHERE signal_id IN ({marks})", ids):
            outcomes.setdefault(o["signal_id"], {})[o["horizon_key"]] = o

    view = []
    for r in rows:
        o = outcomes.get(r["id"], {})
        if r["voided_at"]:
            status = "void"
        elif "H2" in o:
            status = "resolved"
        else:
            status = "inprogress" if o else "pending"
        r2 = o.get("H2")
        view.append({
            "id": r["id"], "ts": r["ts"], "sym": r["symbol"], "tf": r["timeframe"],
            "dir": "long" if r["direction"] == "LONG" else "exit",
            "score": round(float(r["composite"] or 0), 1),
            "rank": r["rank_in_cycle"],
            "h1": (float(o["H1"]["r_multiple"]) > 0 if "H1" in o else None),
            "h2": (float(o["H2"]["r_multiple"]) > 0 if "H2" in o else None),
            "h3": (float(o["H3"]["r_multiple"]) > 0 if "H3" in o else None),
            "r": (round(float(r2["r_multiple"]), 3) if r2 else None),
            "st": status, "source": r["source"], "void_reason": r["void_reason"],
            "origin": r["origin"],
        })

    # Already resolved-first by construction; keep the newest within each group.
    view.sort(key=lambda v: (v["r"] is None, -v["ts"]))

    sources: dict[str, int] = {}
    for r in conn.execute(
            f"SELECT s.source AS src, COUNT(*) AS n FROM signal_log s WHERE {where} GROUP BY s.source",
            params):
        sources[r["src"]] = r["n"]
    resolved_sources: dict[str, int] = {}
    for r in resolved:
        resolved_sources[r["source"]] = resolved_sources.get(r["source"], 0) + 1

    return {
        "data_sources": sources,
        "resolved_sources": resolved_sources,
        "kpi": {
            "hit": hit,
            "exp": (round(exp, 3) if exp is not None else None),
            "vpf": (round(vpf, 2) if isinstance(vpf, float) and math.isfinite(vpf) else vpf),
            "ic": ic,
            "ic_median": ic_block["median"],
            "sig": sig,
            "res": len(resolved),
            "issued": issued,
            "pending": max(0, sig - len(resolved)),
            "void": voided_n,
        },
        "horizons": horizons,
        "families": families,
        "calibration": calibration,
        "msi": msi(conn),
        "signals": view,
        "filters": {"tf": tf, "since": since, "source": source, "origin": origin},
    }


# --------------------------------------------------------------------------- #
# Audit trail — every control action, server-side and durable
# --------------------------------------------------------------------------- #
def audit_record(conn: sqlite3.Connection, action: str, *, actor: str = "api",
                 result: str = "ok", detail: Any = None,
                 client: str | None = None) -> int:
    """Write one control action. Never raises on a serialisation problem:
    an audit row must not be able to break the action it is describing."""
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
    cur = conn.execute(
        "INSERT INTO audit_log(ts, action, actor, result, detail, client) VALUES (?,?,?,?,?,?)",
        (int(time.time()), action, actor, result, detail, client))
    conn.commit()
    return int(cur.lastrowid)


def audit_list(conn: sqlite3.Connection, *, limit: int = 50, action: str | None = None,
               since: int | None = None) -> list[dict[str, Any]]:
    where, params = ["1=1"], []
    if action:
        where.append("action=?")
        params.append(action)
    if since:
        where.append("ts>=?")
        params.append(int(since))
    rows = conn.execute(
        f"""SELECT * FROM audit_log WHERE {' AND '.join(where)}
            ORDER BY ts DESC, id DESC LIMIT ?""",
        [*params, max(1, min(limit, 500))]).fetchall()
    out = []
    for r in rows:
        detail = r["detail"]
        try:
            detail = json.loads(detail) if detail else None
        except (ValueError, TypeError):
            pass
        out.append({"id": r["id"], "ts": r["ts"], "action": r["action"],
                    "actor": r["actor"], "result": r["result"],
                    "detail": detail, "client": r["client"]})
    return out


def audit_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {r["action"]: r["n"] for r in conn.execute(
        "SELECT action, COUNT(*) AS n FROM audit_log GROUP BY action ORDER BY n DESC")}


# --------------------------------------------------------------------------- #
# Journal export (ADR-0012 §2.6) — one row per prediction, outcomes joined in
# --------------------------------------------------------------------------- #
EXPORT_COLUMNS = [
    "id", "ts", "utc", "symbol", "timeframe", "direction", "composite",
    "ta_score", "fa_score", "liquidity_score", "rank_in_cycle", "regime",
    "entry", "stop", "tp1", "tp2", "atr", "source", "origin", "config_hash",
    "voided_at", "void_reason",
    "h1_resolved_at", "h1_return_pct", "h1_r_multiple", "h1_hit_tp1", "h1_hit_sl",
    "h2_resolved_at", "h2_return_pct", "h2_r_multiple", "h2_hit_tp1", "h2_hit_sl",
    "h3_resolved_at", "h3_return_pct", "h3_r_multiple", "h3_hit_tp1", "h3_hit_sl",
]


def export_rows(conn: sqlite3.Connection, *, tf: str | None = None,
                since: int | None = None, source: str | None = None,
                origin: str | None = None) -> list[dict[str, Any]]:
    """Every prediction matching the filters, oldest first, with all horizons.

    Unlike `summary()` this is not paginated: an export that silently stops at
    the first page is worse than no export.
    """
    where, params = _filters(tf, origin, source, since)
    signals = conn.execute(
        f"""SELECT * FROM signal_log s WHERE {where} ORDER BY s.ts ASC, s.id ASC""",
        params).fetchall()
    if not signals:
        return []

    outcomes: dict[int, dict[str, sqlite3.Row]] = {}
    ids = [r["id"] for r in signals]
    for start in range(0, len(ids), 900):          # SQLite host-parameter limit
        chunk = ids[start:start + 900]
        marks = ",".join("?" * len(chunk))
        for o in conn.execute(
                f"SELECT * FROM signal_outcomes WHERE signal_id IN ({marks})", chunk):
            outcomes.setdefault(o["signal_id"], {})[o["horizon_key"]] = o

    out = []
    for r in signals:
        row = {
            "id": r["id"], "ts": r["ts"],
            "utc": datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": r["symbol"], "timeframe": r["timeframe"],
            "direction": r["direction"], "composite": r["composite"],
            "ta_score": r["ta_score"], "fa_score": r["fa_score"],
            "liquidity_score": r["liquidity_score"], "rank_in_cycle": r["rank_in_cycle"],
            "regime": r["regime"], "entry": r["entry"], "stop": r["stop"],
            "tp1": r["tp1"], "tp2": r["tp2"], "atr": r["atr"],
            "source": r["source"], "origin": r["origin"], "config_hash": r["config_hash"],
            "voided_at": r["voided_at"], "void_reason": r["void_reason"],
        }
        for hk in ("H1", "H2", "H3"):
            o = outcomes.get(r["id"], {}).get(hk)
            p = hk.lower()
            row[f"{p}_resolved_at"] = o["resolved_at"] if o else None
            row[f"{p}_return_pct"] = o["return_pct"] if o else None
            row[f"{p}_r_multiple"] = o["r_multiple"] if o else None
            row[f"{p}_hit_tp1"] = o["hit_tp1"] if o else None
            row[f"{p}_hit_sl"] = o["hit_sl"] if o else None
        out.append(row)
    return out


def export_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore",
                            lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in EXPORT_COLUMNS})
    return buf.getvalue()


def digest(conn: sqlite3.Connection, *, days: int = 1) -> dict[str, Any]:
    """A one-screen summary of the last N days, for the Telegram digest."""
    since = int(time.time()) - days * 86400
    s = summary(conn, since=since, limit=1)
    k = s["kpi"]
    m = s["msi"]
    return {
        "window_days": days,
        "issued": k["issued"],
        "resolved": k["res"],
        "hit": k["hit"],
        "expectancy_r": k["exp"],
        "voided": k["void"],
        "ic": k["ic"],
        "msi": m["status"],
        "sources": s["data_sources"],
        "generated_at": int(time.time()),
    }


def digest_text(d: dict[str, Any]) -> str:
    hit = "—" if d["hit"] is None else f"{d['hit'] * 100:.1f}%"
    exp = "—" if d["expectancy_r"] is None else f"{d['expectancy_r']:+.3f}R"
    ic = "—" if d["ic"] is None else f"{d['ic']:+.3f}"
    src = ", ".join(f"{k}×{v}" for k, v in (d["sources"] or {}).items()) or "none"
    return (f"Nobi journal — last {d['window_days']}d\n"
            f"issued {d['issued']} · resolved {d['resolved']} · hit {hit} · exp {exp}\n"
            f"IC {ic} · MSI {d['msi']} · void {d['voided']} · source {src}")


# --------------------------------------------------------------------------- #
# Single-signal detail: the price path that decided the outcome
# --------------------------------------------------------------------------- #
def signal_path(signal: sqlite3.Row, candles: list[dict], tf: str, *,
                context_bars: int = 30, horizon_key: str = "H2") -> dict[str, Any]:
    """Slice the candles around one prediction's evaluation window.

    ``signal["ts"]`` is the *close* of the bar the score was computed on, and
    entry is taken at that close — so the entry bar is the one that closes at
    ``ts``, i.e. opens at ``ts - tf_seconds``. Getting this wrong shifts the
    whole chart by one bar (the same bug that once shifted every outcome).

    Returns ``{"bars": [...], "reason": ...}``; an empty list with a reason is
    a legitimate answer when the prediction is older than candle retention.
    """
    step = TF_SECONDS.get(tf)
    if step is None or not candles:
        return {"bars": [], "reason": "no-candles"}
    entry_open = int(signal["ts"]) - step
    times = [int(c["t"]) for c in candles]
    if entry_open not in times:
        newest, oldest = times[-1], times[0]
        reason = ("outside-retention" if entry_open < oldest
                  else "not-yet-open" if entry_open > newest else "gap")
        return {"bars": [], "reason": reason, "oldest": oldest, "newest": newest}

    i = times.index(entry_open)
    a = max(0, i - context_bars)
    b = min(len(candles), i + bars_for(tf, HORIZONS[horizon_key]) + 1)
    bars = [{"t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]),
             "l": float(c["l"]), "c": float(c["c"]), "v": float(c.get("v") or 0)}
            for c in candles[a:b]]
    return {"bars": bars, "reason": "ok", "entry_index": i - a,
            "entry_open": entry_open, "horizon_key": horizon_key}
