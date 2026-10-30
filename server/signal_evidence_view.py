"""Filtered Signal Intelligence projections; one read transaction, no migrations.

Legacy analytics/reviews remain in signal_intelligence.py. This view does not
replay outcomes, infer linkage or change scoring/execution policies.
"""

from __future__ import annotations

import csv
import io
import math
import re
import sqlite3
import statistics
import time
from typing import Any

import journal
import signal_cycles

SCHEMA = "nobitrader.signal-evidence.v1"
EXPORT_LIMIT = 10_000
MAX_FLOAT = "1.7976931348623157e308"
STATUSES = (
    "resolved",
    "pending_not_due",
    "due_unresolved",
    "non_long_not_applicable",
    "invalid_model_inputs",
    "invalid_model_outcomes",
    "void",
)
FIELDS = (
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
    "config_hash",
    "source",
    "origin",
    "entry",
    "stop",
    "tp1",
    "tp2",
    "atr",
    "voided_at",
    "void_reason",
    "linkage_status",
    "scan_cycle_id",
    "resolved_at",
    "r_multiple",
    "return_pct",
    "mfe_pct",
    "mae_pct",
    "hit_tp1",
    "hit_sl",
    "review_count",
    "model_status",
)
NUMERIC = {
    "composite",
    "ta_score",
    "fa_score",
    "liquidity_score",
    "rank_in_cycle",
    "entry",
    "stop",
    "tp1",
    "tp2",
    "atr",
    "voided_at",
    "hit_tp1",
    "hit_sl",
    "resolved_at",
    "r_multiple",
    "return_pct",
    "mfe_pct",
    "mae_pct",
}
MEASUREMENT = {
    "basis": "modeled-ohlc-gross-r",
    "net_account_pnl": False,
    "sample_threshold_is_count_only": True,
    "independent_samples_established": False,
    "source_basis": "signal-recording-candles; outcome-candle provenance not stored",
    "export_is_executed_account_ledger": False,
    "expectancy_ci95_method": "normal-approximation-1.96-sample-sd; independence unverified",
    "win_rate_ci95_method": "Wilson; positive-R event, independence unverified",
}


def _finite_sql(name: str) -> str:
    return f"(typeof({name}) IN ('real','integer') AND abs({name})<={MAX_FLOAT})"


def filters(
    *,
    days: int = 30,
    horizon: str = "H1",
    symbol: str | None = None,
    timeframe: str | None = None,
    as_of: int | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    if type(days) is not int or not 1 <= days <= 3650:
        raise ValueError("days must be an integer in 1..3650")
    if horizon not in journal.HORIZONS:
        raise ValueError("unsupported horizon")
    symbol = symbol.strip().upper() if symbol else None
    if symbol is not None and not re.fullmatch(r"[A-Z0-9]{2,32}", symbol):
        raise ValueError("invalid symbol")
    if timeframe is not None and timeframe not in journal.TF_SECONDS:
        raise ValueError("unsupported timeframe")
    if as_of is not None and (type(as_of) is not int or as_of <= 0 or as_of > now):
        raise ValueError("as_of must be a positive server epoch not in the future")
    cutoff = now if as_of is None else as_of
    return {
        "days": days,
        "horizon": horizon,
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of": cutoff,
        "since": cutoff - days * 86400,
    }


def _where(f: dict) -> tuple[str, list[Any]]:
    parts = ["s.ts>=?", "s.ts<=?"]
    args: list[Any] = [f["since"], f["as_of"]]
    for key in ("symbol", "timeframe"):
        if f[key] is not None:
            parts.append(f"s.{key}=?")
            args.append(f[key])
    return " AND ".join(parts), args


def _base(conn: sqlite3.Connection, f: dict) -> tuple[str, list[Any]]:
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(signal_log)")}
    linkage = (
        "coalesce(s.linkage_status,'legacy-unlinked')"
        if "linkage_status" in columns
        else "'legacy-unlinked'"
    )
    cycle = "s.scan_cycle_id" if "scan_cycle_id" in columns else "NULL"
    where, args = _where(f)
    valid_inputs = (
        f"(s.entry>0 AND s.atr>0 AND {_finite_sql('s.entry')} AND {_finite_sql('s.atr')})"
    )
    sql = f"""SELECT s.id,s.ts,s.symbol,s.timeframe,s.direction,s.composite,s.ta_score,s.fa_score,
        s.liquidity_score,s.rank_in_cycle,s.regime,s.config_hash,s.source,s.origin,
        s.entry,s.stop,s.tp1,s.tp2,s.atr,s.voided_at,s.void_reason,
        {linkage} linkage_status,{cycle} scan_cycle_id,
        o.resolved_at,o.r_multiple,o.return_pct,o.mfe_pct,o.mae_pct,o.hit_tp1,o.hit_sl,
        (SELECT count(*) FROM signal_reviews r WHERE r.signal_id=s.id) review_count,
        CASE WHEN s.voided_at IS NOT NULL THEN 'void'
             WHEN s.direction!='LONG' THEN 'non_long_not_applicable'
             WHEN NOT {valid_inputs} THEN 'invalid_model_inputs'
             WHEN o.resolved_at IS NOT NULL THEN CASE WHEN {_finite_sql("o.r_multiple")} AND {_finite_sql("o.resolved_at")} AND o.resolved_at>0
                  THEN 'resolved' ELSE 'invalid_model_outcomes' END
             WHEN s.ts+?<? THEN 'due_unresolved' ELSE 'pending_not_due' END model_status
        FROM signal_evidence s LEFT JOIN signal_outcomes o ON o.signal_id=s.id AND o.horizon_key=?
        WHERE {where}"""
    return sql, [journal.HORIZONS[f["horizon"]], f["as_of"], f["horizon"], *args]


def _number(value: Any, digits: int = 4) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(value):
        return round(float(value), digits)
    return None


def _row(row: sqlite3.Row) -> dict:
    out = dict(row)
    for key in NUMERIC:
        v = out.get(key)
        if v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v)):
            out[key] = None
    if out["model_status"] != "resolved":
        for key in ("r_multiple", "return_pct", "mfe_pct", "mae_pct", "hit_tp1", "hit_sl"):
            out[key] = None
    return out


def _pipeline(conn: sqlite3.Connection, f: dict) -> dict:
    scope = {
        "days": f["days"],
        "since": f["since"],
        "as_of": f["as_of"],
        "symbol": None,
        "timeframe": None,
        "horizon": None,
        "independent_ledgers": True,
    }
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"scan_cycles", "scan_candidates", "gate_verdict_reasons"} <= tables:
        return {"available": False, "scope": scope, "error": "cycle-ledger-unavailable"}
    bounds = (f["since"], f["as_of"])
    states = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT state,count(*) FROM scan_candidates WHERE created_at>=? AND created_at<=? GROUP BY state",
            bounds,
        )
    }
    total = sum(states.values())
    cycles = conn.execute(
        "SELECT count(*) FROM scan_cycles WHERE started_at>=? AND started_at<=?", bounds
    ).fetchone()[0]
    linked = conn.execute(
        "SELECT count(*) FROM scan_candidates WHERE created_at>=? AND created_at<=? AND signal_id IS NOT NULL",
        bounds,
    ).fetchone()[0]
    stages = [
        {"id": k, "count": v}
        for k, v in [
            ("cycles_started", cycles),
            ("candidates_seen", total),
            ("gate_passed", states.get("gate-passed", 0) + states.get("signal-emitted", 0)),
            ("signal_emitted", linked),
        ]
    ]
    reasons = [
        dict(r)
        for r in conn.execute(
            """SELECT r.reason_code code,r.reason_group 'group',count(*) count FROM gate_verdict_reasons r
        JOIN scan_candidates c ON c.id=r.candidate_id WHERE c.created_at>=? AND c.created_at<=? AND r.reason_code!='ok'
        GROUP BY r.reason_code,r.reason_group ORDER BY count DESC,r.reason_group,r.reason_code""",
            bounds,
        )
    ]
    return {"available": True, "scope": scope, "stages": stages, "reasons": reasons}


def read(
    *,
    days: int = 30,
    horizon: str = "H1",
    symbol: str | None = None,
    timeframe: str | None = None,
    as_of: int | None = None,
    limit: int = 100,
    cohort_limit: int = 50,
    export: bool = False,
) -> dict[str, Any]:
    """Filters/signal cutoff are shared; outcomes are current at this read.

    Exporting later with as_of does NOT reconstruct outcome history at as_of.
    Missing schema is an error, not permission to migrate/backfill on a GET.
    """
    f = filters(days=days, horizon=horizon, symbol=symbol, timeframe=timeframe, as_of=as_of)
    max_limit = EXPORT_LIMIT if export else 500
    if type(limit) is not int or not 1 <= limit <= max_limit:
        raise ValueError("invalid row limit")
    if type(cohort_limit) is not int or not 1 <= cohort_limit <= 500:
        raise ValueError("invalid cohort limit")
    conn = journal.connect_readonly()
    try:
        conn.execute("BEGIN")
        sql, args = _base(conn, f)
        prefix = "WITH evidence AS (" + sql + ") "
        total = conn.execute(prefix + "SELECT count(*) FROM evidence", args).fetchone()[0]
        rows = [
            _row(r)
            for r in conn.execute(
                prefix + "SELECT * FROM evidence ORDER BY ts DESC,id DESC LIMIT ?", [*args, limit]
            )
        ]
        where, raw_args = _where(f)
        excluded = conn.execute(
            f"SELECT count(*) FROM signal_log s WHERE {where} AND s.id NOT IN (SELECT id FROM signal_evidence)",
            raw_args,
        ).fetchone()[0]
        result: dict[str, Any] = {
            "schema": SCHEMA,
            "filters": f,
            "rows": rows,
            "page": {
                "limit": limit,
                "total": total,
                "has_more": total > len(rows),
                "loaded": len(rows),
            },
            "measurement": {**MEASUREMENT, "horizon_seconds": journal.HORIZONS[horizon]},
            "quality": {"excluded_price_contract_records": excluded},
            "consistency": "single-read-transaction; as_of bounds signal issuance, not historical outcomes",
        }
        if not export:
            counts = {
                r[0]: r[1]
                for r in conn.execute(
                    prefix + "SELECT model_status,count(*) FROM evidence GROUP BY model_status",
                    args,
                )
            }
            rs = [
                float(r[0])
                for r in conn.execute(
                    prefix
                    + "SELECT r_multiple FROM evidence WHERE model_status='resolved' ORDER BY ts DESC,id DESC",
                    args,
                )
            ]
            n = len(rs)
            wins, losses, flats = (
                sum(x > 0 for x in rs),
                sum(x < 0 for x in rs),
                sum(x == 0 for x in rs),
            )
            mean = statistics.mean(rs) if rs else None
            ci = None
            if n > 1 and mean is not None:
                try:
                    sd = statistics.stdev(rs)
                    spread = (sd / math.sqrt(n)) * 1.96
                    ends = [_number(mean - spread), _number(mean + spread)]
                    if all(x is not None for x in ends):
                        ci = ends
                except (OverflowError, statistics.StatisticsError):
                    # A finite R can still produce an unrepresentable variance.
                    # Unknown CI is not a zero-width interval or model approval.
                    pass
            averages = conn.execute(
                prefix
                + f"""SELECT
                avg(CASE WHEN model_status='resolved' AND {_finite_sql("mfe_pct")} THEN mfe_pct END) mfe,
                avg(CASE WHEN model_status='resolved' AND {_finite_sql("mae_pct")} THEN mae_pct END) mae,
                sum(review_count) reviews,sum(linkage_status='linked') linked FROM evidence""",
                args,
            ).fetchone()
            linked = int(averages["linked"] or 0)
            summary = {
                "window_days": days,
                "horizon": horizon,
                "signals": total,
                "resolved": n,
                "unresolved": total - n - counts.get("void", 0),
                "voided": counts.get("void", 0),
                "wins": wins,
                "losses": losses,
                "flats": flats,
                "reviews": int(averages["reviews"] or 0),
                "win_rate_pct": _number(wins / n * 100, 2) if n else None,
                "expectancy_r": _number(mean),
                "median_r": _number(statistics.median(rs)) if rs else None,
                "avg_mfe_pct": _number(averages["mfe"]),
                "avg_mae_pct": _number(averages["mae"]),
                "win_rate_ci95_pct": signal_cycles.wilson(wins, n),
                "expectancy_ci95_r": ci,
                "expectancy_ci95_status": "approximate-independence-unverified"
                if ci
                else ("insufficient-count" if n < 2 else "numeric-range-unavailable"),
                "sample_minimum": 30,
                "sample_sufficient": n >= 30,
                "status_counts": {k: counts.get(k, 0) for k in STATUSES},
                "outcome_maturity": {
                    k: counts.get(k, 0) for k in ["pending_not_due", "due_unresolved"]
                },
                "unresolved_eligibility": {
                    k: counts.get(k, 0)
                    for k in [
                        "non_long_not_applicable",
                        "invalid_model_inputs",
                        "invalid_model_outcomes",
                    ]
                },
                "linkage": {
                    "linked": linked,
                    "legacy_unlinked": total - linked,
                    "coverage_pct": _number(linked / total * 100, 2) if total else None,
                },
            }
            grouped = "SELECT symbol,timeframe,coalesce(regime,'unknown') regime,coalesce(config_hash,'unknown') config_hash FROM evidence GROUP BY symbol,timeframe,coalesce(regime,'unknown'),coalesce(config_hash,'unknown')"
            groups = conn.execute(
                prefix + "SELECT count(*) FROM (" + grouped + ")", args
            ).fetchone()[0]
            cohort_rows = [
                dict(r)
                for r in conn.execute(
                    prefix
                    + """SELECT symbol,timeframe,coalesce(regime,'unknown') regime,
                coalesce(config_hash,'unknown') config_hash,count(*) signals,sum(model_status='resolved') resolved,
                avg(CASE WHEN model_status='resolved' THEN r_multiple END) expectancy_r,
                avg(CASE WHEN model_status='resolved' THEN CASE WHEN r_multiple>0 THEN 100.0 ELSE 0.0 END END) win_rate_pct
                FROM evidence GROUP BY symbol,timeframe,coalesce(regime,'unknown'),coalesce(config_hash,'unknown')
                ORDER BY resolved DESC,signals DESC,symbol,timeframe,regime,config_hash LIMIT ?""",
                    [*args, cohort_limit],
                )
            ]
            for c in cohort_rows:
                c.update(
                    sample_minimum=30,
                    sample_sufficient=c["resolved"] >= 30,
                    expectancy_r=_number(c["expectancy_r"]),
                    win_rate_pct=_number(c["win_rate_pct"], 2),
                )
            result.update(
                summary=summary,
                cohorts={
                    "rows": cohort_rows,
                    "total": groups,
                    "limit": cohort_limit,
                    "has_more": groups > len(cohort_rows),
                },
                pipeline=_pipeline(conn, f),
            )
        result["generated_at"] = int(time.time())
        return result
    finally:
        conn.close()


def metadata(result: dict) -> dict:
    return {k: v for k, v in result.items() if k != "rows"}


def to_csv(result: dict) -> str:
    """Same ordered rows as JSON; strings are neutralized for spreadsheets."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=FIELDS)
    writer.writeheader()
    for row in result["rows"]:
        safe = {}
        for k, v in row.items():
            if isinstance(v, str) and v.lstrip("\ufeff \t\r\n\v\f").startswith(
                ("=", "+", "-", "@")
            ):
                v = "'" + v
            safe[k] = v
        writer.writerow(safe)
    return out.getvalue()
