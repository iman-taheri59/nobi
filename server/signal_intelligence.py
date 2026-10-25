"""Read-only signal quality analytics and operator review workflow (ADR-0044)."""
from __future__ import annotations

import json
import time
from typing import Any

import journal


def _where(*, days: int, symbol: str | None, timeframe: str | None) -> tuple[str, list[Any]]:
    clauses = ["s.ts>=?"]
    params: list[Any] = [int(time.time()) - days * 86400]
    if symbol:
        clauses.append("s.symbol=?")
        params.append(symbol.upper())
    if timeframe:
        clauses.append("s.timeframe=?")
        params.append(timeframe)
    return " AND ".join(clauses), params


def ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS signal_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER NOT NULL,
        actor_user_id INTEGER NOT NULL,
        actor_username TEXT NOT NULL,
        tag TEXT NOT NULL,
        note TEXT,
        created_at INTEGER NOT NULL,
        FOREIGN KEY(signal_id) REFERENCES signal_log(id) ON DELETE CASCADE
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_signal_reviews_signal ON signal_reviews(signal_id, created_at DESC)")
    conn.commit()


def summary(*, days: int = 30, symbol: str | None = None,
            timeframe: str | None = None, horizon: str = "H1") -> dict[str, Any]:
    where, params = _where(days=days, symbol=symbol, timeframe=timeframe)
    with journal.connect() as conn:
        ensure(conn)
        row = conn.execute(f"""SELECT count(*) signals,
            count(DISTINCT CASE WHEN o.resolved_at IS NOT NULL THEN s.id END) resolved,
            sum(CASE WHEN s.voided_at IS NOT NULL THEN 1 ELSE 0 END) voided,
            avg(CASE WHEN o.resolved_at IS NOT NULL THEN o.r_multiple END) avg_r,
            avg(CASE WHEN o.resolved_at IS NOT NULL THEN o.return_pct END) avg_return_pct,
            avg(CASE WHEN o.resolved_at IS NOT NULL THEN o.mfe_pct END) avg_mfe_pct,
            avg(CASE WHEN o.resolved_at IS NOT NULL THEN o.mae_pct END) avg_mae_pct,
            avg(CASE WHEN o.resolved_at IS NOT NULL THEN CASE WHEN o.r_multiple>0 THEN 1.0 ELSE 0.0 END END) win_rate
            FROM signal_log s LEFT JOIN signal_outcomes o
              ON o.signal_id=s.id AND o.horizon_key=? WHERE {where}""", [horizon, *params]).fetchone()
        assert row is not None
        reviews = conn.execute(f"SELECT count(*) FROM signal_reviews r JOIN signal_log s ON s.id=r.signal_id WHERE {where}", params).fetchone()[0]
    def rounded(value, digits=4):
        return round(float(value), digits) if value is not None else None
    return {"window_days": days, "horizon": horizon, "signals": int(row["signals"]),
            "resolved": int(row["resolved"]), "unresolved": int(row["signals"])-int(row["resolved"]),
            "voided": int(row["voided"] or 0), "reviews": int(reviews),
            "win_rate_pct": rounded((row["win_rate"] or 0) * 100, 2) if row["resolved"] else None,
            "expectancy_r": rounded(row["avg_r"]), "avg_r": rounded(row["avg_r"]),
            "avg_return_pct": rounded(row["avg_return_pct"]),
            "avg_mfe_pct": rounded(row["avg_mfe_pct"]), "avg_mae_pct": rounded(row["avg_mae_pct"]),
            "sample_sufficient": int(row["resolved"]) >= 30}


def funnel(*, days: int = 30) -> dict[str, Any]:
    since = int(time.time()) - days * 86400
    with journal.connect() as conn:
        # gate_shadow shares this database but may not exist on a fresh install.
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gate_shadow'").fetchone()
        judged = ready = passed = 0
        reasons: dict[str, int] = {}
        if exists:
            rows = conn.execute("SELECT code,count(*) n FROM gate_shadow WHERE ts>=? GROUP BY code", (since,)).fetchall()
            reasons = {str(row["code"]): int(row["n"]) for row in rows}
            judged = sum(reasons.values())
            passed = reasons.get("ok", 0)
            ready = judged - reasons.get("no-data", 0) - reasons.get("no-book", 0)
        generated = int(conn.execute("SELECT count(*) FROM signal_log WHERE ts>=?", (since,)).fetchone()[0])
        resolved = int(conn.execute("""SELECT count(DISTINCT o.signal_id) FROM signal_outcomes o
            JOIN signal_log s ON s.id=o.signal_id WHERE s.ts>=? AND o.resolved_at IS NOT NULL""", (since,)).fetchone()[0])
    return {"window_days": days, "stages": [
        {"id": "judged", "count": judged}, {"id": "data_ready", "count": ready},
        {"id": "gate_passed", "count": passed}, {"id": "signals", "count": generated},
        {"id": "resolved", "count": resolved}], "gate_reasons": reasons}


def cohorts(*, days: int = 30, horizon: str = "H1", limit: int = 50) -> list[dict[str, Any]]:
    since = int(time.time()) - days * 86400
    with journal.connect() as conn:
        rows = conn.execute("""SELECT s.symbol,s.timeframe,coalesce(s.regime,'unknown') regime,
            coalesce(s.config_hash,'unknown') config_hash,count(*) signals,
            count(o.resolved_at) resolved,avg(o.r_multiple) avg_r,
            avg(CASE WHEN o.resolved_at IS NOT NULL THEN CASE WHEN o.r_multiple>0 THEN 1.0 ELSE 0.0 END END) win_rate
            FROM signal_log s LEFT JOIN signal_outcomes o ON o.signal_id=s.id AND o.horizon_key=?
            WHERE s.ts>=? GROUP BY s.symbol,s.timeframe,s.regime,s.config_hash
            ORDER BY resolved DESC,signals DESC LIMIT ?""", (horizon, since, limit)).fetchall()
    return [{"symbol": r["symbol"], "timeframe": r["timeframe"], "regime": r["regime"],
             "config_hash": r["config_hash"], "signals": int(r["signals"]),
             "resolved": int(r["resolved"]),
             "win_rate_pct": round(float(r["win_rate"])*100, 2) if r["win_rate"] is not None else None,
             "expectancy_r": round(float(r["avg_r"]), 4) if r["avg_r"] is not None else None}
            for r in rows]


def signals(*, days: int = 30, limit: int = 100, symbol: str | None = None,
            timeframe: str | None = None, horizon: str = "H1") -> list[dict[str, Any]]:
    where, params = _where(days=days, symbol=symbol, timeframe=timeframe)
    with journal.connect() as conn:
        ensure(conn)
        rows = conn.execute(f"""SELECT s.*,o.resolved_at,o.return_pct,o.r_multiple,o.mfe_pct,o.mae_pct,
            o.hit_tp1,o.hit_sl,(SELECT count(*) FROM signal_reviews r WHERE r.signal_id=s.id) review_count
            FROM signal_log s LEFT JOIN signal_outcomes o ON o.signal_id=s.id AND o.horizon_key=?
            WHERE {where} ORDER BY s.ts DESC LIMIT ?""", [horizon, *params, limit]).fetchall()
    keys = ("id","ts","symbol","timeframe","direction","composite","ta_score","fa_score",
            "liquidity_score","rank_in_cycle","regime","entry","stop","tp1","tp2","atr",
            "source","origin","config_hash","voided_at","void_reason","resolved_at","return_pct",
            "r_multiple","mfe_pct","mae_pct","hit_tp1","hit_sl","review_count")
    return [{key: row[key] for key in keys} for row in rows]


def detail(signal_id: int) -> dict[str, Any] | None:
    with journal.connect() as conn:
        ensure(conn)
        row = conn.execute("SELECT * FROM signal_log WHERE id=?", (signal_id,)).fetchone()
        if row is None:
            return None
        outcomes = [dict(item) for item in conn.execute("SELECT * FROM signal_outcomes WHERE signal_id=? ORDER BY horizon_key", (signal_id,))]
        reviews = [dict(item) for item in conn.execute("SELECT id,actor_user_id,actor_username,tag,note,created_at FROM signal_reviews WHERE signal_id=? ORDER BY created_at DESC", (signal_id,))]
    item = dict(row)
    try:
        item["features"] = json.loads(item.get("features") or "{}")
    except (TypeError, ValueError):
        item["features"] = {}
    item["contributions"] = {"ta": item.get("ta_score"), "fa": item.get("fa_score"),
                             "liquidity": item.get("liquidity_score"), "composite": item.get("composite")}
    return {"signal": item, "outcomes": outcomes, "reviews": reviews}


def review(signal_id: int, *, user_id: int, username: str, tag: str, note: str) -> dict[str, Any]:
    allowed = {"good-signal", "bad-data", "late-entry", "risk-block-correct",
               "false-positive", "execution-issue", "needs-replay"}
    if tag not in allowed:
        raise ValueError("unsupported review tag")
    with journal.connect() as conn:
        ensure(conn)
        if conn.execute("SELECT 1 FROM signal_log WHERE id=?", (signal_id,)).fetchone() is None:
            raise LookupError("signal not found")
        cur = conn.execute("INSERT INTO signal_reviews(signal_id,actor_user_id,actor_username,tag,note,created_at) VALUES(?,?,?,?,?,?)",
                           (signal_id, user_id, username, tag, note[:1000], int(time.time())))
        conn.commit()
        return {"id": cur.lastrowid, "signal_id": signal_id, "tag": tag, "note": note[:1000]}
