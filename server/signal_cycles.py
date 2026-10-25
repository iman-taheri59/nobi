"""Linked scan-cycle evidence for Signal Intelligence v1.1 (ADR-0045)."""

from __future__ import annotations

import json
import math
import secrets
import sqlite3
import time
from collections.abc import Mapping
from typing import Any

import journal

ENGINE_VERSION = "1.7.0"

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_cycles (
 id TEXT PRIMARY KEY, timeframe TEXT NOT NULL, origin TEXT NOT NULL,
 status TEXT NOT NULL, started_at INTEGER NOT NULL, completed_at INTEGER,
 engine_version TEXT NOT NULL, config_hash TEXT NOT NULL,
 universe_count INTEGER, scored_count INTEGER, emitted_count INTEGER,
 error_count INTEGER NOT NULL DEFAULT 0, source_summary TEXT, error_summary TEXT
);
CREATE INDEX IF NOT EXISTS ix_scan_cycles_tf_started ON scan_cycles(timeframe,started_at DESC);
CREATE TABLE IF NOT EXISTS scan_candidates (
 id INTEGER PRIMARY KEY AUTOINCREMENT, scan_cycle_id TEXT NOT NULL,
 symbol TEXT NOT NULL, timeframe TEXT NOT NULL, state TEXT NOT NULL,
 rank_in_cycle INTEGER, composite REAL, signal_id INTEGER,
 gate_code TEXT, gate_passed INTEGER, created_at INTEGER NOT NULL,
 UNIQUE(scan_cycle_id,symbol),
 FOREIGN KEY(scan_cycle_id) REFERENCES scan_cycles(id) ON DELETE CASCADE,
 FOREIGN KEY(signal_id) REFERENCES signal_log(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_candidates_cycle_state ON scan_candidates(scan_cycle_id,state);
CREATE INDEX IF NOT EXISTS ix_candidates_symbol_time ON scan_candidates(symbol,timeframe,created_at DESC);
CREATE TABLE IF NOT EXISTS gate_verdict_reasons (
 candidate_id INTEGER NOT NULL, reason_code TEXT NOT NULL,
 reason_group TEXT NOT NULL, ordinal INTEGER NOT NULL, evidence TEXT,
 PRIMARY KEY(candidate_id,reason_code),
 FOREIGN KEY(candidate_id) REFERENCES scan_candidates(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_gate_reasons_code ON gate_verdict_reasons(reason_code,candidate_id);
"""

_GROUPS = {
    "no-data": "data",
    "stale-candles": "data",
    "insufficient-bars": "data",
    "invalid-candle": "data",
    "source-unavailable": "data",
    "no-book": "market",
    "stale-book": "market",
    "spread": "market",
    "depth": "market",
    "halted": "market",
    "market-closed": "market",
    "premium": "market",
    "ok": "pass",
    "score-low": "signal-quality",
    "fa-veto": "signal-quality",
    "liquidity-low": "signal-quality",
    "regime-block": "signal-quality",
    "risk-cap": "risk",
    "heat-cap": "risk",
    "single-asset-cap": "risk",
    "concurrent-cap": "risk",
    "min-cash": "risk",
    "funds": "risk",
    "min-notional": "risk",
    "breaker-open": "operational",
    "rate-budget": "operational",
    "scan-timeout": "operational",
    "internal-error": "operational",
}


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(signal_log)")}
    for name, decl in (
        ("scan_cycle_id", "TEXT"),
        ("engine_version", "TEXT"),
        ("linkage_status", "TEXT NOT NULL DEFAULT 'legacy-unlinked'"),
    ):
        if name not in have:
            conn.execute(f"ALTER TABLE signal_log ADD COLUMN {name} {decl}")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_signal_cycle ON signal_log(scan_cycle_id)")
    conn.commit()


def new_id(now_ms: int | None = None) -> str:
    """Time-sortable, dependency-free 128-bit identifier."""
    stamp = int(time.time() * 1000) if now_ms is None else now_ms
    return f"{stamp:012x}{secrets.token_hex(10)}"


def recover_stale(*, older_than_s: int = 3600, now: int | None = None) -> int:
    """Close cycles left running by a process crash; never fabricate candidates."""
    cutoff = (int(time.time()) if now is None else now) - older_than_s
    with journal.connect() as conn:
        ensure(conn)
        cur = conn.execute(
            """UPDATE scan_cycles SET status='partial',completed_at=?,error_count=error_count+1,
               error_summary='process interrupted before cycle completion'
               WHERE status='running' AND started_at<?""",
            (int(time.time()) if now is None else now, cutoff),
        )
        conn.commit()
        return cur.rowcount


def start(*, timeframe: str, origin: str, universe_count: int) -> str:
    recover_stale()
    cycle_id = new_id()
    with journal.connect() as conn:
        ensure(conn)
        conn.execute(
            """INSERT INTO scan_cycles(id,timeframe,origin,status,started_at,
          engine_version,config_hash,universe_count) VALUES(?,?,?,?,?,?,?,?)""",
            (
                cycle_id,
                timeframe,
                origin,
                "running",
                int(time.time()),
                ENGINE_VERSION,
                journal.CONFIG_HASH,
                universe_count,
            ),
        )
        conn.commit()
    return cycle_id


def complete(
    cycle_id: str,
    *,
    snapshots: Mapping[str, Mapping[str, Any]],
    verdicts: Mapping[str, Mapping[str, Any]],
    skipped: list[str],
    source: str | None = None,
) -> dict[str, int]:
    """Persist candidates/reasons and link signal rows; never infer old linkage."""
    emitted = errors = 0
    with journal.connect() as conn:
        ensure(conn)
        ranked = sorted(
            snapshots.items(), key=lambda item: item[1].get("composite") or 0, reverse=True
        )
        for rank, (symbol, snap) in enumerate(ranked, 1):
            verdict = verdicts.get(symbol) or {"code": "no-data", "codes": ["no-data"]}
            code = str(verdict.get("code") or "ok")
            codes = [str(c) for c in (verdict.get("codes") or [code])]
            signal_id = None
            row = conn.execute(
                "SELECT id FROM signal_log WHERE scan_cycle_id=? AND symbol=?", (cycle_id, symbol)
            ).fetchone()
            if row:
                signal_id = int(row[0])
                emitted += 1
            state = (
                "signal-emitted"
                if signal_id
                else ("gate-passed" if code == "ok" else "gate-blocked")
            )
            cur = conn.execute(
                """INSERT INTO scan_candidates(scan_cycle_id,symbol,timeframe,state,
              rank_in_cycle,composite,signal_id,gate_code,gate_passed,created_at)
              SELECT ?,?,timeframe,?,?,?,?,?,?,? FROM scan_cycles WHERE id=?""",
                (
                    cycle_id,
                    symbol,
                    state,
                    rank,
                    snap.get("composite"),
                    signal_id,
                    code,
                    int(code == "ok"),
                    int(time.time()),
                    cycle_id,
                ),
            )
            assert cur.lastrowid is not None
            cid = int(cur.lastrowid)
            for ordinal, reason in enumerate(dict.fromkeys(codes)):
                conn.execute(
                    "INSERT OR IGNORE INTO gate_verdict_reasons(candidate_id,reason_code,reason_group,ordinal,evidence) VALUES(?,?,?,?,?)",
                    (
                        cid,
                        reason,
                        _GROUPS.get(reason, "unknown"),
                        ordinal,
                        json.dumps(
                            {
                                "spread_bps": verdict.get("spread_bps"),
                                "depth_usd": verdict.get("depth_usd"),
                                "age_bars": verdict.get("age_bars"),
                            },
                            separators=(",", ":"),
                        ),
                    ),
                )
        for symbol in dict.fromkeys(skipped):
            cur = conn.execute(
                """INSERT OR IGNORE INTO scan_candidates(scan_cycle_id,symbol,timeframe,state,
                   gate_code,gate_passed,created_at)
                   SELECT ?,?,timeframe,'scan-error','source-unavailable',0,? FROM scan_cycles WHERE id=?""",
                (cycle_id, symbol, int(time.time()), cycle_id),
            )
            if cur.rowcount and cur.lastrowid is not None:
                conn.execute(
                    """INSERT INTO gate_verdict_reasons
                       (candidate_id,reason_code,reason_group,ordinal,evidence)
                       VALUES(?,'source-unavailable','data',0,'{}')""",
                    (int(cur.lastrowid),),
                )
        errors = len(set(skipped))
        conn.execute(
            """UPDATE scan_cycles SET status='completed',completed_at=?,scored_count=?,
          emitted_count=?,error_count=?,source_summary=? WHERE id=?""",
            (int(time.time()), len(snapshots), emitted, errors, source, cycle_id),
        )
        conn.commit()
    return {"scored": len(snapshots), "emitted": emitted, "errors": errors}


def abandon(cycle_id: str, error: str) -> None:
    with journal.connect() as conn:
        ensure(conn)
        conn.execute(
            "UPDATE scan_cycles SET status='partial',completed_at=?,error_count=error_count+1,error_summary=? WHERE id=?",
            (int(time.time()), error[:300], cycle_id),
        )
        conn.commit()


def wilson(wins: int, total: int, z: float = 1.959964) -> list[float] | None:
    if total <= 0:
        return None
    p = wins / total
    d = 1 + z * z / total
    c = (p + z * z / (2 * total)) / d
    h = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / d
    return [round(max(0, c - h) * 100, 2), round(min(1, c + h) * 100, 2)]
