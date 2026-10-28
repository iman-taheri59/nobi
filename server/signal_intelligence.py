"""Read-only signal quality analytics and operator review workflow (ADR-0044)."""

from __future__ import annotations

import base64
import csv
import io
import json
import math
import statistics
import time
from typing import Any

import journal
import signal_cycles


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


def summary(
    *, days: int = 30, symbol: str | None = None, timeframe: str | None = None, horizon: str = "H1"
) -> dict[str, Any]:
    where, params = _where(days=days, symbol=symbol, timeframe=timeframe)
    with journal.connect() as conn:
        row = conn.execute(
            f"""SELECT count(*) signals,
            count(DISTINCT CASE WHEN s.voided_at IS NULL AND o.resolved_at IS NOT NULL THEN s.id END) resolved,
            sum(CASE WHEN s.voided_at IS NOT NULL THEN 1 ELSE 0 END) voided,
            sum(CASE WHEN s.voided_at IS NULL AND o.resolved_at IS NOT NULL AND o.r_multiple>0 THEN 1 ELSE 0 END) wins,
            sum(CASE WHEN s.voided_at IS NULL AND o.resolved_at IS NOT NULL AND o.r_multiple<0 THEN 1 ELSE 0 END) losses,
            sum(CASE WHEN s.voided_at IS NULL AND o.resolved_at IS NOT NULL AND o.r_multiple=0 THEN 1 ELSE 0 END) flats,
            avg(CASE WHEN s.voided_at IS NULL AND o.resolved_at IS NOT NULL THEN o.r_multiple END) avg_r,
            avg(CASE WHEN s.voided_at IS NULL AND o.resolved_at IS NOT NULL AND o.r_multiple>0 THEN o.r_multiple END) avg_win_r,
            avg(CASE WHEN s.voided_at IS NULL AND o.resolved_at IS NOT NULL AND o.r_multiple<0 THEN o.r_multiple END) avg_loss_r,
            avg(CASE WHEN s.voided_at IS NULL AND o.resolved_at IS NOT NULL THEN o.return_pct END) avg_return_pct,
            avg(CASE WHEN s.voided_at IS NULL AND o.resolved_at IS NOT NULL THEN o.mfe_pct END) avg_mfe_pct,
            avg(CASE WHEN s.voided_at IS NULL AND o.resolved_at IS NOT NULL THEN o.mae_pct END) avg_mae_pct,
            avg(CASE WHEN s.voided_at IS NULL AND o.resolved_at IS NOT NULL THEN CASE WHEN o.r_multiple>0 THEN 1.0 ELSE 0.0 END END) win_rate
            FROM signal_evidence s LEFT JOIN signal_outcomes o
              ON o.signal_id=s.id AND o.horizon_key=? WHERE {where}""",
            [horizon, *params],
        ).fetchone()
        assert row is not None
        reviews = conn.execute(
            f"SELECT count(*) FROM signal_reviews r JOIN signal_evidence s ON s.id=r.signal_id WHERE {where}",
            params,
        ).fetchone()[0]

    def rounded(value, digits=4):
        return round(float(value), digits) if value is not None else None

    resolved = int(row["resolved"])
    voided = int(row["voided"] or 0)
    total = int(row["signals"])
    return {
        "window_days": days,
        "horizon": horizon,
        "signals": total,
        "resolved": resolved,
        "unresolved": total - resolved - voided,
        "voided": voided,
        "wins": int(row["wins"] or 0),
        "losses": int(row["losses"] or 0),
        "flats": int(row["flats"] or 0),
        "reviews": int(reviews),
        "win_rate_pct": rounded((row["win_rate"] or 0) * 100, 2) if resolved else None,
        "expectancy_r": rounded(row["avg_r"]),
        "avg_win_r": rounded(row["avg_win_r"]),
        "avg_loss_r": rounded(row["avg_loss_r"]),
        "avg_return_pct": rounded(row["avg_return_pct"]),
        "avg_mfe_pct": rounded(row["avg_mfe_pct"]),
        "avg_mae_pct": rounded(row["avg_mae_pct"]),
        "sample_minimum": 30,
        "sample_sufficient": resolved >= 30,
    }


def funnel(*, days: int = 30) -> dict[str, Any]:
    since = int(time.time()) - days * 86400
    with journal.connect() as conn:
        # gate_shadow shares this database but may not exist on a fresh install.
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gate_shadow'"
        ).fetchone()
        judged = ready = passed = 0
        reasons: dict[str, int] = {}
        if exists:
            rows = conn.execute(
                "SELECT code,count(*) n FROM gate_shadow WHERE ts>=? GROUP BY code", (since,)
            ).fetchall()
            reasons = {str(row["code"]): int(row["n"]) for row in rows}
            judged = sum(reasons.values())
            passed = reasons.get("ok", 0)
            ready = judged - reasons.get("no-data", 0) - reasons.get("no-book", 0)
        generated = int(
            conn.execute("SELECT count(*) FROM signal_evidence WHERE ts>=?", (since,)).fetchone()[0]
        )
        resolved = int(
            conn.execute(
                """SELECT count(DISTINCT o.signal_id) FROM signal_outcomes o
            JOIN signal_evidence s ON s.id=o.signal_id WHERE s.ts>=? AND o.resolved_at IS NOT NULL""",
                (since,),
            ).fetchone()[0]
        )
    return {
        "window_days": days,
        "stages": [
            {"id": "judged", "count": judged},
            {"id": "data_ready", "count": ready},
            {"id": "gate_passed", "count": passed},
            {"id": "signals", "count": generated},
            {"id": "resolved", "count": resolved},
        ],
        "gate_reasons": reasons,
    }


def cohorts(*, days: int = 30, horizon: str = "H1", limit: int = 50) -> list[dict[str, Any]]:
    since = int(time.time()) - days * 86400
    with journal.connect() as conn:
        rows = conn.execute(
            """SELECT s.symbol,s.timeframe,coalesce(s.regime,'unknown') regime,
            coalesce(s.config_hash,'unknown') config_hash,count(*) signals,
            count(CASE WHEN s.voided_at IS NULL THEN o.resolved_at END) resolved,
            avg(CASE WHEN s.voided_at IS NULL THEN o.r_multiple END) avg_r,
            avg(CASE WHEN s.voided_at IS NULL AND o.resolved_at IS NOT NULL THEN CASE WHEN o.r_multiple>0 THEN 1.0 ELSE 0.0 END END) win_rate
            FROM signal_evidence s LEFT JOIN signal_outcomes o ON o.signal_id=s.id AND o.horizon_key=?
            WHERE s.ts>=? GROUP BY s.symbol,s.timeframe,s.regime,s.config_hash
            ORDER BY resolved DESC,signals DESC LIMIT ?""",
            (horizon, since, limit),
        ).fetchall()
    return [
        {
            "symbol": r["symbol"],
            "timeframe": r["timeframe"],
            "regime": r["regime"],
            "config_hash": r["config_hash"],
            "signals": int(r["signals"]),
            "resolved": int(r["resolved"]),
            "sample_minimum": 30,
            "sample_sufficient": int(r["resolved"]) >= 30,
            "win_rate_pct": round(float(r["win_rate"]) * 100, 2)
            if r["win_rate"] is not None
            else None,
            "expectancy_r": round(float(r["avg_r"]), 4) if r["avg_r"] is not None else None,
        }
        for r in rows
    ]


def signals(
    *,
    days: int = 30,
    limit: int = 100,
    symbol: str | None = None,
    timeframe: str | None = None,
    horizon: str = "H1",
) -> list[dict[str, Any]]:
    where, params = _where(days=days, symbol=symbol, timeframe=timeframe)
    with journal.connect() as conn:
        rows = conn.execute(
            f"""SELECT s.*,o.resolved_at,o.return_pct,o.r_multiple,o.mfe_pct,o.mae_pct,
            o.hit_tp1,o.hit_sl,(SELECT count(*) FROM signal_reviews r WHERE r.signal_id=s.id) review_count
            FROM signal_evidence s LEFT JOIN signal_outcomes o ON o.signal_id=s.id AND o.horizon_key=?
            WHERE {where} ORDER BY s.ts DESC LIMIT ?""",
            [horizon, *params, limit],
        ).fetchall()
    keys = (
        "id",
        "ts",
        "symbol",
        "timeframe",
        "direction",
        "composite",
        "ta_score",
        "fa_score",
        "liquidity_score",
        "rank_in_cycle",
        "regime",
        "entry",
        "stop",
        "tp1",
        "tp2",
        "atr",
        "source",
        "origin",
        "config_hash",
        "voided_at",
        "void_reason",
        "resolved_at",
        "return_pct",
        "r_multiple",
        "mfe_pct",
        "mae_pct",
        "hit_tp1",
        "hit_sl",
        "review_count",
    )
    return [{key: row[key] for key in keys} for row in rows]


def detail(signal_id: int) -> dict[str, Any] | None:
    with journal.connect() as conn:
        row = conn.execute("SELECT * FROM signal_log WHERE id=?", (signal_id,)).fetchone()
        if row is None:
            return None
        outcomes = [
            dict(item)
            for item in conn.execute(
                "SELECT * FROM signal_outcomes WHERE signal_id=? ORDER BY horizon_key", (signal_id,)
            )
        ]
        reviews = [
            dict(item)
            for item in conn.execute(
                "SELECT id,actor_user_id,actor_username,tag,note,created_at FROM signal_reviews WHERE signal_id=? ORDER BY created_at DESC",
                (signal_id,),
            )
        ]
    item = dict(row)
    try:
        item["features"] = json.loads(item.get("features") or "{}")
    except (TypeError, ValueError):
        item["features"] = {}
    item["component_scores"] = {
        "ta": item.get("ta_score"),
        "fa": item.get("fa_score"),
        "liquidity": item.get("liquidity_score"),
        "composite": item.get("composite"),
    }
    return {"signal": item, "outcomes": outcomes, "reviews": reviews}


REVIEW_TAGS = {
    "good-signal",
    "bad-data",
    "late-entry",
    "risk-block-correct",
    "false-positive",
    "execution-issue",
    "needs-replay",
}


def review(signal_id: int, *, user_id: int, username: str, tag: str, note: str) -> dict[str, Any]:
    if tag not in REVIEW_TAGS:
        raise ValueError("unsupported review tag")
    if len(note) > 1000:
        raise ValueError("review note must be at most 1000 characters")
    clean_note = note.strip()
    created_at = int(time.time())
    with journal.connect() as conn:
        if conn.execute("SELECT 1 FROM signal_log WHERE id=?", (signal_id,)).fetchone() is None:
            raise LookupError("signal not found")
        cur = conn.execute(
            "INSERT INTO signal_reviews(signal_id,actor_user_id,actor_username,tag,note,created_at) VALUES(?,?,?,?,?,?)",
            (signal_id, user_id, username, tag, clean_note, created_at),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "signal_id": signal_id,
            "tag": tag,
            "note": clean_note,
            "created_at": created_at,
        }


def linked_summary(*, days: int = 30, horizon: str = "H1") -> dict[str, Any]:
    base = summary(days=days, horizon=horizon)
    with journal.connect() as status_conn:
        base["price_contracts"] = journal.price_contract_status(status_conn)
    since = int(time.time()) - days * 86400
    with journal.connect() as conn:
        signal_cycles.ensure(conn)
        linked = int(
            conn.execute(
                "SELECT count(*) FROM signal_evidence WHERE ts>=? AND linkage_status='linked'", (since,)
            ).fetchone()[0]
        )
        legacy = int(
            conn.execute(
                "SELECT count(*) FROM signal_evidence WHERE ts>=? AND linkage_status!='linked'", (since,)
            ).fetchone()[0]
        )
        overdue = int(
            conn.execute(
                """SELECT count(*) FROM signal_evidence s LEFT JOIN signal_outcomes o
          ON o.signal_id=s.id AND o.horizon_key=? WHERE s.ts>=? AND s.voided_at IS NULL
          AND o.resolved_at IS NULL AND s.ts + ? < ?""",
                (horizon, since, journal.HORIZONS[horizon], int(time.time())),
            ).fetchone()[0]
        )
        values = [
            float(r[0])
            for r in conn.execute(
                """SELECT o.r_multiple FROM signal_outcomes o JOIN signal_evidence s ON s.id=o.signal_id
          WHERE s.ts>=? AND s.voided_at IS NULL AND o.horizon_key=? AND o.resolved_at IS NOT NULL AND o.r_multiple IS NOT NULL""",
                (since, horizon),
            )
        ]
    n = len(values)
    mean = sum(values) / n if n else None
    sd = (
        math.sqrt(sum((x - mean) ** 2 for x in values) / (n - 1))
        if n > 1 and mean is not None
        else None
    )
    ci = (
        [round(mean - 1.96 * sd / math.sqrt(n), 4), round(mean + 1.96 * sd / math.sqrt(n), 4)]
        if sd is not None and mean is not None
        else None
    )
    wins = sum(1 for x in values if x > 0)
    total = linked + legacy
    base.update(
        {
            "linkage": {
                "linked": linked,
                "legacy_unlinked": legacy,
                "coverage_pct": round(linked / total * 100, 2) if total else None,
            },
            "outcome_maturity": {
                "pending_not_due": max(0, int(base["unresolved"]) - overdue),
                "due_unresolved": overdue,
            },
            "resolver_backlog": overdue,
            "win_rate_ci95_pct": signal_cycles.wilson(wins, n),
            "expectancy_ci95_r": ci,
            "median_r": round(statistics.median(values), 4) if n else None,
        }
    )
    return base


def linked_pipeline(*, days: int = 30) -> dict[str, Any]:
    since = int(time.time()) - days * 86400
    with journal.connect() as conn:
        signal_cycles.ensure(conn)
        rows = conn.execute(
            "SELECT state,count(*) n FROM scan_candidates WHERE created_at>=? GROUP BY state",
            (since,),
        ).fetchall()
        states = {str(r["state"]): int(r["n"]) for r in rows}
        candidates = sum(states.values())
        cycles = int(
            conn.execute(
                "SELECT count(*) FROM scan_cycles WHERE started_at>=?", (since,)
            ).fetchone()[0]
        )
        linked = int(
            conn.execute(
                "SELECT count(*) FROM scan_candidates WHERE created_at>=? AND signal_id IS NOT NULL",
                (since,),
            ).fetchone()[0]
        )
    stages = [
        ("cycles_started", cycles, None),
        ("candidates_seen", candidates, None),
        ("gate_passed", states.get("gate-passed", 0) + states.get("signal-emitted", 0), candidates),
        ("signal_emitted", linked, candidates),
    ]
    return {
        "window_days": days,
        "linkage_required": True,
        "stages": [
            {
                "id": name,
                "count": count,
                "denominator": den,
                "conversion_pct": round(count / den * 100, 2) if den else None,
            }
            for name, count, den in stages
        ],
    }


def gate_reason_counts(*, days: int = 30, mode: str = "primary") -> list[dict[str, Any]]:
    since = int(time.time()) - days * 86400
    with journal.connect() as conn:
        signal_cycles.ensure(conn)
        if mode == "primary":
            rows = conn.execute(
                """SELECT coalesce(gate_code,'unknown') code,count(*) n FROM scan_candidates
              WHERE created_at>=? AND gate_code!='ok' GROUP BY gate_code ORDER BY n DESC""",
                (since,),
            ).fetchall()
            return [{"code": r["code"], "count": int(r["n"]), "mode": mode} for r in rows]
        rows = conn.execute(
            """SELECT r.reason_code code,r.reason_group,count(*) n FROM gate_verdict_reasons r
          JOIN scan_candidates c ON c.id=r.candidate_id WHERE c.created_at>=? AND r.reason_code!='ok'
          GROUP BY r.reason_code,r.reason_group ORDER BY n DESC""",
            (since,),
        ).fetchall()
        return [
            {"code": r["code"], "group": r["reason_group"], "count": int(r["n"]), "mode": mode}
            for r in rows
        ]


def paged_signals(*, days: int = 30, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
    since = int(time.time()) - days * 86400
    args: list[Any] = [since]
    extra = ""
    if cursor:
        try:
            raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            extra = " AND (s.ts<? OR (s.ts=? AND s.id<?))"
            args += [int(raw["ts"]), int(raw["ts"]), int(raw["id"])]
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError("invalid cursor") from exc
    with journal.connect() as conn:
        signal_cycles.ensure(conn)
        rows = conn.execute(
            f"""SELECT s.id,s.ts,s.symbol,s.timeframe,s.direction,s.composite,s.regime,
          s.config_hash,s.linkage_status,s.scan_cycle_id FROM signal_evidence s WHERE s.ts>=? {extra}
          ORDER BY s.ts DESC,s.id DESC LIMIT ?""",
            [*args, limit + 1],
        ).fetchall()
    more = len(rows) > limit
    selected = rows[:limit]
    next_cursor = None
    if more and selected:
        last = selected[-1]
        payload = json.dumps({"ts": last["ts"], "id": last["id"]}, separators=(",", ":")).encode()
        next_cursor = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return {
        "rows": [dict(r) for r in selected],
        "page": {"limit": limit, "has_more": more, "next_cursor": next_cursor},
    }


def export_linked_rows(*, days: int = 30) -> list[dict[str, Any]]:
    """Return the bounded, stable export projection shared by CSV and JSON."""
    return paged_signals(days=days, limit=10_000)["rows"]


def export_linked_csv(*, days: int = 30) -> str:
    data = export_linked_rows(days=days)
    output = io.StringIO()
    fields = [
        "id",
        "ts",
        "symbol",
        "timeframe",
        "direction",
        "composite",
        "regime",
        "config_hash",
        "linkage_status",
        "scan_cycle_id",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in data:
        safe: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, str):
                visible = value.lstrip(" \t\r\n")
                if visible.startswith(("=", "+", "-", "@")):
                    value = "'" + value
            safe[key] = value
        writer.writerow(safe)
    return output.getvalue()
