"""Auditable drift analysis for Signal Intelligence v1.2 (ADR-0046)."""

from __future__ import annotations

import csv
import io
import json
import math
import sqlite3
import time
from collections.abc import Iterable
from typing import Any

import journal
import signal_cycles

ENGINE_VERSION = "1.8.0"
SCHEMA = """
CREATE TABLE IF NOT EXISTS drift_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER NOT NULL,
 actor TEXT NOT NULL, current_start INTEGER NOT NULL, current_end INTEGER NOT NULL,
 baseline_start INTEGER NOT NULL, baseline_end INTEGER NOT NULL,
 horizon TEXT NOT NULL, filters TEXT NOT NULL, engine_version TEXT NOT NULL,
 correction_method TEXT NOT NULL, thresholds TEXT NOT NULL, status TEXT NOT NULL,
 completed_at INTEGER, error_summary TEXT
);
CREATE INDEX IF NOT EXISTS ix_drift_runs_created ON drift_runs(created_at DESC,id DESC);
CREATE TABLE IF NOT EXISTS drift_findings (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, cohort_key TEXT NOT NULL,
 metric TEXT NOT NULL, baseline_n INTEGER NOT NULL, current_n INTEGER NOT NULL,
 baseline_value REAL, current_value REAL, delta REAL, ci_low REAL, ci_high REAL,
 p_value REAL, q_value REAL, severity TEXT NOT NULL, suppression_reason TEXT,
 evidence TEXT NOT NULL, FOREIGN KEY(run_id) REFERENCES drift_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_drift_findings_run ON drift_findings(run_id,severity,metric);
CREATE TABLE IF NOT EXISTS drift_schedule (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), enabled INTEGER NOT NULL DEFAULT 0,
 interval_s INTEGER NOT NULL DEFAULT 86400, window_days INTEGER NOT NULL DEFAULT 30,
 horizon TEXT NOT NULL DEFAULT 'H1', cohort_mode TEXT NOT NULL DEFAULT 'global',
 updated_at INTEGER NOT NULL, updated_by TEXT NOT NULL, last_run_at INTEGER,
 next_run_at INTEGER, last_error TEXT
);
CREATE TABLE IF NOT EXISTS drift_reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT, finding_id INTEGER NOT NULL,
 actor_user_id INTEGER NOT NULL, actor_username TEXT NOT NULL,
 tag TEXT NOT NULL, note TEXT NOT NULL, created_at INTEGER NOT NULL,
 FOREIGN KEY(finding_id) REFERENCES drift_findings(id) ON DELETE CASCADE
);
"""
THRESHOLDS = {
    "q_alert": 0.05,
    "psi_watch": 0.10,
    "psi_alert": 0.25,
    "performance_min": 30,
    "pipeline_min": 50,
    "psi_min": 100,
}
REVIEW_TAGS = {"acknowledged", "expected-config-change", "investigate", "false-alarm"}


def ensure(conn: sqlite3.Connection) -> None:
    signal_cycles.ensure(conn)
    conn.executescript(SCHEMA)
    conn.execute(
        """INSERT OR IGNORE INTO drift_schedule(singleton,enabled,interval_s,window_days,
           horizon,cohort_mode,updated_at,updated_by) VALUES(1,0,86400,30,'H1','global',?,'system')""",
        (int(time.time()),),
    )
    conn.commit()


def bh_adjust(values: Iterable[float]) -> list[float]:
    vals = list(values)
    n = len(vals)
    out = [1.0] * n
    ranked = sorted(enumerate(vals), key=lambda item: item[1])
    running = 1.0
    for rank, (idx, value) in reversed(list(enumerate(ranked, 1))):
        running = min(running, value * n / rank)
        out[idx] = round(min(1.0, running), 8)
    return out


def _normal_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


def proportion_test(a_wins: int, a_n: int, b_wins: int, b_n: int) -> tuple[float, float]:
    if min(a_n, b_n) <= 0:
        return 0.0, 1.0
    delta = b_wins / b_n - a_wins / a_n
    pooled = (a_wins + b_wins) / (a_n + b_n)
    se = math.sqrt(pooled * (1 - pooled) * (1 / a_n + 1 / b_n))
    return delta, (_normal_p(delta / se) if se else (0.0 if delta else 1.0))


def mean_difference(
    a: list[float], b: list[float]
) -> tuple[float | None, list[float] | None, float]:
    if not a or not b:
        return None, None, 1.0
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1) if len(a) > 1 else 0.0
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1) if len(b) > 1 else 0.0
    se = math.sqrt(va / len(a) + vb / len(b))
    delta = mb - ma
    return (
        delta,
        ([delta - 1.96 * se, delta + 1.96 * se] if se else [delta, delta]),
        (_normal_p(delta / se) if se else (0.0 if delta else 1.0)),
    )


def psi(
    baseline: list[float], current: list[float], bins: int = 10
) -> tuple[float | None, list[float]]:
    if not baseline or not current:
        return None, []
    ordered = sorted(baseline)
    edges = [
        ordered[min(len(ordered) - 1, math.ceil(i * len(ordered) / bins) - 1)]
        for i in range(1, bins)
    ]
    edges = sorted(set(edges))

    def counts(values: list[float]) -> list[int]:
        result = [0] * (len(edges) + 1)
        for value in values:
            idx = 0
            while idx < len(edges) and value > edges[idx]:
                idx += 1
            result[idx] += 1
        return result

    bc, cc = counts(baseline), counts(current)
    eps = 0.0001
    score = 0.0
    for x, y in zip(bc, cc, strict=True):
        bp, cp = max(eps, x / len(baseline)), max(eps, y / len(current))
        score += (cp - bp) * math.log(cp / bp)
    return round(score, 6), edges


def psi_bins(baseline: list[float], current: list[float], edges: list[float]) -> list[dict[str, Any]]:
    """Return auditable per-bin PSI contributions for charts and exports."""
    if not baseline or not current:
        return []
    def counts(values: list[float]) -> list[int]:
        result = [0] * (len(edges) + 1)
        for value in values:
            idx = 0
            while idx < len(edges) and value > edges[idx]:
                idx += 1
            result[idx] += 1
        return result
    bc, cc = counts(baseline), counts(current)
    rows: list[dict[str, Any]] = []
    eps = 0.0001
    for idx, (bn, cn) in enumerate(zip(bc, cc, strict=True)):
        bp, cp = max(eps, bn / len(baseline)), max(eps, cn / len(current))
        rows.append({
            "index": idx, "lower": edges[idx - 1] if idx else None,
            "upper": edges[idx] if idx < len(edges) else None,
            "baseline_n": bn, "current_n": cn,
            "baseline_pct": round(bp, 8), "current_pct": round(cp, 8),
            "contribution": round((cp - bp) * math.log(cp / bp), 8),
        })
    return rows


def _window_values(
    conn: sqlite3.Connection, start: int, end: int, horizon: str, config_hash: str | None
) -> list[float]:
    extra, args = "", [horizon, start, end]
    if config_hash:
        extra, args = " AND s.config_hash=?", [*args, config_hash]
    return [
        float(r[0])
        for r in conn.execute(
            """SELECT o.r_multiple FROM signal_log s JOIN signal_outcomes o ON o.signal_id=s.id
           WHERE o.horizon_key=? AND s.ts>=? AND s.ts<? AND s.voided_at IS NULL
           AND o.resolved_at IS NOT NULL AND o.r_multiple IS NOT NULL"""
            + extra,
            args,
        )
    ]


def _pipeline(
    conn: sqlite3.Connection, start: int, end: int, config_hash: str | None
) -> dict[str, int]:
    extra = ""
    args: list[Any] = [start, end]
    if config_hash:
        extra = " AND cy.config_hash=?"
        args.append(config_hash)
    rows = conn.execute(
        """SELECT c.state,count(*) n FROM scan_candidates c
           JOIN scan_cycles cy ON cy.id=c.scan_cycle_id
           WHERE c.created_at>=? AND c.created_at<?"""
        + extra
        + " GROUP BY c.state",
        args,
    ).fetchall()
    return {str(row["state"]): int(row["n"]) for row in rows}


def _scores(conn: sqlite3.Connection, start: int, end: int, config_hash: str | None) -> list[float]:
    extra = ""
    args: list[Any] = [start, end]
    if config_hash:
        extra, args = " AND config_hash=?", [*args, config_hash]
    return [
        float(r[0])
        for r in conn.execute(
            "SELECT composite FROM signal_log WHERE ts>=? AND ts<? AND composite IS NOT NULL"
            + extra,
            args,
        )
    ]


def run(
    *, days: int, horizon: str, actor: str, config_hash: str | None = None,
    cohort_mode: str = "global", now: int | None = None
) -> dict[str, Any]:
    if cohort_mode not in COHORT_DIMENSIONS:
        raise ValueError("unsupported cohort mode")
    now = int(time.time()) if now is None else now
    current_start, baseline_start = now - days * 86400, now - 2 * days * 86400
    cohort_rows = (cohort_inventory(mode=cohort_mode, days=days, now=now)["rows"]
                   if cohort_mode != "global" else [])
    with journal.connect() as conn:
        ensure(conn)
        conn.execute(
            """UPDATE drift_runs SET status='partial',completed_at=?,
               error_summary='process interrupted before drift completion'
               WHERE status='running' AND created_at<?""",
            (now, now - 3600),
        )
        active = conn.execute("SELECT id FROM drift_runs WHERE status='running'").fetchone()
        if active:
            raise RuntimeError("a drift run is already active")
        cur = conn.execute(
            """INSERT INTO drift_runs(created_at,actor,current_start,current_end,baseline_start,
               baseline_end,horizon,filters,engine_version,correction_method,thresholds,status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,'running')""",
            (
                now,
                actor,
                current_start,
                now,
                baseline_start,
                current_start,
                horizon,
                json.dumps({"config_hash": config_hash, "cohort_mode": cohort_mode}),
                ENGINE_VERSION,
                "benjamini-hochberg",
                json.dumps(THRESHOLDS, sort_keys=True),
            ),
        )
        run_id = int(cur.lastrowid or 0)
        baseline = _window_values(conn, baseline_start, current_start, horizon, config_hash)
        current = _window_values(conn, current_start, now, horizon, config_hash)
        findings: list[dict[str, Any]] = []
        delta, ci, p = mean_difference(baseline, current)
        findings.append(
            {
                "metric": "expectancy_r",
                "bn": len(baseline),
                "cn": len(current),
                "bv": sum(baseline) / len(baseline) if baseline else None,
                "cv": sum(current) / len(current) if current else None,
                "delta": delta,
                "ci": ci,
                "p": p,
                "minimum": THRESHOLDS["performance_min"],
            }
        )
        aw, bw = sum(x > 0 for x in baseline), sum(x > 0 for x in current)
        wd, wp = proportion_test(aw, len(baseline), bw, len(current))
        findings.append(
            {
                "metric": "win_rate",
                "bn": len(baseline),
                "cn": len(current),
                "bv": aw / len(baseline) if baseline else None,
                "cv": bw / len(current) if current else None,
                "delta": wd,
                "ci": None,
                "p": wp,
                "minimum": THRESHOLDS["performance_min"],
            }
        )
        bs, cs = (
            _scores(conn, baseline_start, current_start, config_hash),
            _scores(conn, current_start, now, config_hash),
        )
        pv, edges = psi(bs, cs)
        findings.append(
            {
                "metric": "composite_psi",
                "bn": len(bs),
                "cn": len(cs),
                "bv": 0.0,
                "cv": pv,
                "delta": pv,
                "ci": None,
                "p": 1.0,
                "minimum": THRESHOLDS["psi_min"],
                "edges": edges,
                "bins": psi_bins(bs, cs, edges),
            }
        )
        bp = _pipeline(conn, baseline_start, current_start, config_hash)
        cp = _pipeline(conn, current_start, now, config_hash)
        bn, cn = sum(bp.values()), sum(cp.values())
        for metric, states in (
            ("scan_error_rate", {"scan-error"}),
            ("gate_block_rate", {"gate-blocked"}),
            ("signal_emission_rate", {"signal-emitted"}),
        ):
            ax, bx = (
                sum(bp.get(state, 0) for state in states),
                sum(cp.get(state, 0) for state in states),
            )
            pd, pp = proportion_test(ax, bn, bx, cn)
            findings.append(
                {
                    "metric": metric,
                    "bn": bn,
                    "cn": cn,
                    "bv": ax / bn if bn else None,
                    "cv": bx / cn if cn else None,
                    "delta": pd,
                    "ci": None,
                    "p": pp,
                    "minimum": THRESHOLDS["pipeline_min"],
                }
            )
        # Cohort statistics share one BH correction family with global findings.
        # Pipeline findings stay global unless every candidate can be assigned to
        # the requested dimension; this avoids silently dropping blocked rows.
        if cohort_mode != "global":
            for cohort in cohort_rows:
                if cohort["status"] != "comparable":
                    continue
                key = str(cohort["cohort_key"])
                dims = COHORT_DIMENSIONS[cohort_mode]
                values = key.split("|")
                clauses = [f"coalesce(nullif(s.{dim},''),'unknown')=?" for dim in dims]
                where = " AND " + " AND ".join(clauses)
                params = values
                def outcomes(start: int, end: int, where: str = where, params: tuple[str, ...] = tuple(params)) -> list[float]:
                    return [float(row[0]) for row in conn.execute(
                        "SELECT o.r_multiple FROM signal_log s JOIN signal_outcomes o ON o.signal_id=s.id "
                        "WHERE o.horizon_key=? AND s.ts>=? AND s.ts<? AND s.voided_at IS NULL "
                        "AND o.resolved_at IS NOT NULL AND o.r_multiple IS NOT NULL" + where,
                        [horizon, start, end, *params],
                    )]
                def scores(start: int, end: int, where: str = where, params: tuple[str, ...] = tuple(params)) -> list[float]:
                    return [float(row[0]) for row in conn.execute(
                        "SELECT s.composite FROM signal_log s WHERE s.ts>=? AND s.ts<? "
                        "AND s.composite IS NOT NULL" + where, [start, end, *params]
                    )]
                cb, cc = outcomes(baseline_start, current_start), outcomes(current_start, now)
                md, mci, mp = mean_difference(cb, cc)
                findings.append({"cohort_key": key, "metric": "expectancy_r", "bn": len(cb),
                    "cn": len(cc), "bv": sum(cb)/len(cb) if cb else None,
                    "cv": sum(cc)/len(cc) if cc else None, "delta": md, "ci": mci,
                    "p": mp, "minimum": THRESHOLDS["performance_min"]})
                cbw, ccw = sum(x > 0 for x in cb), sum(x > 0 for x in cc)
                pd, pp = proportion_test(cbw, len(cb), ccw, len(cc))
                findings.append({"cohort_key": key, "metric": "win_rate", "bn": len(cb),
                    "cn": len(cc), "bv": cbw/len(cb) if cb else None,
                    "cv": ccw/len(cc) if cc else None, "delta": pd, "ci": None,
                    "p": pp, "minimum": THRESHOLDS["performance_min"]})
                sb, sc = scores(baseline_start, current_start), scores(current_start, now)
                sp, sedges = psi(sb, sc)
                findings.append({"cohort_key": key, "metric": "composite_psi", "bn": len(sb),
                    "cn": len(sc), "bv": 0.0, "cv": sp, "delta": sp, "ci": None,
                    "p": 1.0, "minimum": THRESHOLDS["psi_min"], "edges": sedges,
                    "bins": psi_bins(sb, sc, sedges)})

        statistical = [i for i, f in enumerate(findings) if f["metric"] != "composite_psi"]
        adjusted = bh_adjust([findings[i]["p"] for i in statistical])
        q_by_index = dict(zip(statistical, adjusted, strict=True))
        severities: list[str] = []
        for idx, finding in enumerate(findings):
            insufficient = min(finding["bn"], finding["cn"]) < finding["minimum"]
            q = q_by_index.get(idx)
            if insufficient:
                severity, suppression = "insufficient", "sample-low"
            elif finding["metric"] == "composite_psi":
                severity = (
                    "alert"
                    if (finding["cv"] or 0) >= THRESHOLDS["psi_alert"]
                    else ("watch" if (finding["cv"] or 0) >= THRESHOLDS["psi_watch"] else "stable")
                )
                suppression = None
            else:
                severity, suppression = (
                    ("alert" if q is not None and q <= THRESHOLDS["q_alert"] else "stable"),
                    None,
                )
            severities.append(severity)
            conn.execute(
                """INSERT INTO drift_findings(run_id,cohort_key,metric,baseline_n,current_n,
                   baseline_value,current_value,delta,ci_low,ci_high,p_value,q_value,severity,
                   suppression_reason,evidence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    finding.get("cohort_key", config_hash or "all"),
                    finding["metric"],
                    finding["bn"],
                    finding["cn"],
                    finding["bv"],
                    finding["cv"],
                    finding["delta"],
                    finding["ci"][0] if finding["ci"] else None,
                    finding["ci"][1] if finding["ci"] else None,
                    finding["p"],
                    q,
                    severity,
                    suppression,
                    json.dumps({"edges": finding.get("edges", []), "bins": finding.get("bins", [])}),
                ),
            )
        overall = (
            "alert"
            if "alert" in severities
            else ("watch" if "watch" in severities else "stable")
        )
        conn.execute(
            "UPDATE drift_runs SET status=?,completed_at=? WHERE id=?", (overall, now, run_id)
        )
        conn.commit()
    return detail(run_id) or {}


def detail(run_id: int, *, cohort_key: str | None = None) -> dict[str, Any] | None:
    with journal.connect() as conn:
        ensure(conn)
        run_row = conn.execute("SELECT * FROM drift_runs WHERE id=?", (run_id,)).fetchone()
        if not run_row:
            return None
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM drift_findings WHERE run_id=?"
                + (" AND cohort_key=?" if cohort_key is not None else "")
                + " ORDER BY id",
                ((run_id, cohort_key) if cohort_key is not None else (run_id,)),
            )
        ]
    return {"run": dict(run_row), "findings": rows}


def list_runs(*, limit: int = 50) -> list[dict[str, Any]]:
    with journal.connect() as conn:
        ensure(conn)
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM drift_runs ORDER BY created_at DESC,id DESC LIMIT ?", (limit,)
            )
        ]


def export_csv(run_id: int) -> str:
    result = detail(run_id)
    if result is None:
        raise LookupError("drift run not found")
    fields = [
        "id",
        "run_id",
        "cohort_key",
        "metric",
        "baseline_n",
        "current_n",
        "baseline_value",
        "current_value",
        "delta",
        "ci_low",
        "ci_high",
        "p_value",
        "q_value",
        "severity",
        "suppression_reason",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in result["findings"]:
        safe = {key: row.get(key) for key in fields}
        for key, value in safe.items():
            if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
                safe[key] = "'" + value
        writer.writerow(safe)
    return output.getvalue()


def latest() -> dict[str, Any] | None:
    with journal.connect() as conn:
        ensure(conn)
        row = conn.execute(
            "SELECT id FROM drift_runs WHERE status!='running' ORDER BY created_at DESC,id DESC LIMIT 1"
        ).fetchone()
    return detail(int(row[0])) if row else None


def schedule() -> dict[str, Any]:
    with journal.connect() as conn:
        ensure(conn)
        row = conn.execute("SELECT * FROM drift_schedule WHERE singleton=1").fetchone()
        assert row is not None
        return dict(row)


def update_schedule(
    *,
    enabled: bool,
    interval_s: int,
    window_days: int,
    horizon: str,
    cohort_mode: str,
    actor: str,
    now: int | None = None,
) -> dict[str, Any]:
    if interval_s not in {21600, 43200, 86400}:
        raise ValueError("interval_s must be 21600, 43200, or 86400")
    if not 7 <= window_days <= 365 or horizon not in {"H1", "H2", "H3"}:
        raise ValueError("invalid drift schedule window or horizon")
    if cohort_mode not in COHORT_DIMENSIONS:
        raise ValueError("unsupported cohort mode")
    now = int(time.time()) if now is None else now
    with journal.connect() as conn:
        ensure(conn)
        conn.execute(
            """UPDATE drift_schedule SET enabled=?,interval_s=?,window_days=?,horizon=?,
               cohort_mode=?,updated_at=?,updated_by=?,next_run_at=?,last_error=NULL WHERE singleton=1""",
            (
                int(enabled),
                interval_s,
                window_days,
                horizon,
                cohort_mode,
                now,
                actor,
                now + interval_s if enabled else None,
            ),
        )
        conn.commit()
    return schedule()


def run_due(*, now: int | None = None) -> dict[str, Any] | None:
    now = int(time.time()) if now is None else now
    cfg = schedule()
    if not cfg["enabled"] or (cfg["next_run_at"] is not None and int(cfg["next_run_at"]) > now):
        return None
    try:
        result = run(
            days=int(cfg["window_days"]), horizon=str(cfg["horizon"]), actor="scheduler",
            cohort_mode=str(cfg["cohort_mode"]), now=now
        )
        error = None
    except Exception as exc:
        result, error = None, str(exc)[:300]
    with journal.connect() as conn:
        ensure(conn)
        conn.execute(
            "UPDATE drift_schedule SET last_run_at=?,next_run_at=?,last_error=? WHERE singleton=1",
            (now, now + int(cfg["interval_s"]), error),
        )
        conn.commit()
    return result


def config_inventory(*, days: int = 30, now: int | None = None) -> list[dict[str, Any]]:
    now = int(time.time()) if now is None else now
    current, baseline = now - days * 86400, now - 2 * days * 86400
    with journal.connect() as conn:
        ensure(conn)
        rows = conn.execute(
            """SELECT coalesce(config_hash,'unknown') config_hash,
               sum(CASE WHEN ts>=? THEN 1 ELSE 0 END) current_signals,
               sum(CASE WHEN ts<? THEN 1 ELSE 0 END) baseline_signals,
               min(ts) first_seen,max(ts) last_seen FROM signal_log
               WHERE ts>=? AND ts<? GROUP BY config_hash ORDER BY current_signals DESC,config_hash""",
            (current, current, baseline, now),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["status"] = (
            "not-comparable"
            if item["baseline_signals"] and item["current_signals"]
            and min(item["baseline_signals"], item["current_signals"]) < THRESHOLDS["performance_min"]
            else "new-in-current"
            if not item["baseline_signals"]
            else "missing-in-current"
            if not item["current_signals"]
            else "continued"
        )
        result.append(item)
    return result


COHORT_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "global": (),
    "config": ("config_hash",),
    "symbol": ("symbol",),
    "timeframe": ("timeframe",),
    "regime": ("regime",),
    "symbol+timeframe": ("symbol", "timeframe"),
    "timeframe+regime": ("timeframe", "regime"),
}
MAX_COHORTS = 100


def cohort_inventory(
    *, mode: str, days: int = 30, now: int | None = None, limit: int = MAX_COHORTS
) -> dict[str, Any]:
    """Return bounded, allowlisted cohort populations for baseline/current windows.

    This endpoint deliberately reports population and comparability only. Statistical
    findings remain immutable run evidence and are never inferred from inventory rows.
    """
    if mode not in COHORT_DIMENSIONS:
        raise ValueError("unsupported cohort mode")
    if not 7 <= days <= 365:
        raise ValueError("days must be between 7 and 365")
    if not 1 <= limit <= MAX_COHORTS:
        raise ValueError(f"limit must be between 1 and {MAX_COHORTS}")
    now = int(time.time()) if now is None else now
    current_start, baseline_start = now - days * 86400, now - 2 * days * 86400
    dimensions = COHORT_DIMENSIONS[mode]
    if not dimensions:
        select, group = "'global' AS cohort_key", ""
    else:
        normalized = [f"coalesce(nullif({column},''),'unknown')" for column in dimensions]
        select = " || '|' || ".join(normalized) + " AS cohort_key"
        group = " GROUP BY " + ",".join(normalized)
    query = (
        f"SELECT {select},"
        " sum(CASE WHEN ts>=? THEN 1 ELSE 0 END) current_n,"
        " sum(CASE WHEN ts<? THEN 1 ELSE 0 END) baseline_n"
        " FROM signal_log WHERE ts>=? AND ts<?" + group
        + " ORDER BY (current_n+baseline_n) DESC,cohort_key LIMIT ?"
    )
    with journal.connect() as conn:
        ensure(conn)
        rows = conn.execute(query, (current_start, current_start, baseline_start, now, limit + 1)).fetchall()
    truncated = len(rows) > limit
    output = []
    for row in rows[:limit]:
        item = dict(row)
        minimum = THRESHOLDS["performance_min"]
        item["mode"] = mode
        item["dimensions"] = dict(zip(dimensions, str(item["cohort_key"]).split("|"), strict=True))
        item["status"] = (
            "missing-in-current" if not item["current_n"]
            else "new-in-current" if not item["baseline_n"]
            else "comparable" if min(item["baseline_n"], item["current_n"]) >= minimum
            else "not-comparable"
        )
        item["suppression_reason"] = None if item["status"] == "comparable" else "sample-low"
        output.append(item)
    return {
        "mode": mode, "dimensions": list(dimensions), "rows": output,
        "limit": limit, "truncated": truncated, "minimum": THRESHOLDS["performance_min"],
    }


def review(finding_id: int, *, user_id: int, username: str, tag: str, note: str) -> dict[str, Any]:
    if tag not in REVIEW_TAGS:
        raise ValueError("unsupported drift review tag")
    if len(note) > 1000:
        raise ValueError("note exceeds 1000 characters")
    with journal.connect() as conn:
        ensure(conn)
        if not conn.execute("SELECT 1 FROM drift_findings WHERE id=?", (finding_id,)).fetchone():
            raise LookupError("finding not found")
        cur = conn.execute(
            "INSERT INTO drift_reviews(finding_id,actor_user_id,actor_username,tag,note,created_at) VALUES(?,?,?,?,?,?)",
            (finding_id, user_id, username, tag, note.strip(), int(time.time())),
        )
        conn.commit()
        return {"id": cur.lastrowid, "finding_id": finding_id, "tag": tag}
