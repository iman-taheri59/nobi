"""Durable, freshness-gated price/premium alerts (ADR-0049).

The evaluator is deliberately independent from FastAPI, Telegram, and trading.
It consumes timestamped observations and atomically persists rule transitions and
trigger events in the existing journal database. Delivery is recorded later so
an external failure can never repeat a crossing or place an order.
"""
from __future__ import annotations

import math
import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import journal

METRICS = frozenset({"last", "premium_pct"})
OPERATORS = frozenset({"above", "below"})
MAX_RULES = 200
MAX_OBSERVATION_AGE_S = 90.0
_SYMBOL = re.compile(r"[A-Z0-9]{2,24}\Z")
_REASON = re.compile(r"[^a-z0-9_.-]+")

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    metric TEXT NOT NULL CHECK(metric IN ('last', 'premium_pct')),
    operator TEXT NOT NULL CHECK(operator IN ('above', 'below')),
    threshold REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    armed INTEGER NOT NULL DEFAULT 0,
    last_value REAL,
    last_observed_at INTEGER,
    last_status TEXT NOT NULL DEFAULT 'arming',
    last_triggered_at INTEGER,
    last_event_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_price_alert_rules_enabled
    ON price_alert_rules(enabled, id);

CREATE TABLE IF NOT EXISTS price_alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER REFERENCES price_alert_rules(id) ON DELETE SET NULL,
    symbol TEXT NOT NULL,
    metric TEXT NOT NULL,
    operator TEXT NOT NULL,
    threshold REAL NOT NULL,
    previous_value REAL,
    value REAL NOT NULL,
    observed_at INTEGER NOT NULL,
    source TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    delivery_transport TEXT,
    delivery_sent INTEGER,
    delivery_reason TEXT NOT NULL DEFAULT 'pending',
    UNIQUE(rule_id, observed_at)
);
CREATE INDEX IF NOT EXISTS ix_price_alert_events_created
    ON price_alert_events(created_at DESC, id DESC);
"""


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the shared journal and idempotently initialise alert tables."""
    conn = journal.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        return conn
    except Exception:
        conn.close()
        raise


def _rule(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    out["enabled"] = bool(out["enabled"])
    out["armed"] = bool(out["armed"])
    return out


def _event(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    if out.get("delivery_sent") is not None:
        out["delivery_sent"] = bool(out["delivery_sent"])
    return out


def validate_rule(symbol: Any, metric: Any, operator: Any,
                  threshold: Any) -> tuple[str, str, str, float]:
    clean_symbol = str(symbol or "").strip().upper()
    clean_metric = str(metric or "").strip().lower()
    clean_operator = str(operator or "").strip().lower()
    if not _SYMBOL.fullmatch(clean_symbol):
        raise ValueError("symbol must contain 2-24 ASCII letters or digits")
    if clean_metric not in METRICS:
        raise ValueError("metric must be last or premium_pct")
    if clean_operator not in OPERATORS:
        raise ValueError("operator must be above or below")
    if clean_metric == "premium_pct" and clean_symbol != "USDTIRT":
        raise ValueError("premium_pct alerts use the USDTIRT reference")
    try:
        value = float(threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError("threshold must be numeric") from exc
    if not math.isfinite(value):
        raise ValueError("threshold must be finite")
    if clean_metric == "last" and value <= 0:
        raise ValueError("a last-price threshold must be positive")
    if clean_metric == "premium_pct" and abs(value) > 100:
        raise ValueError("a premium threshold must be between -100 and 100 percent")
    return clean_symbol, clean_metric, clean_operator, value


def create_rule(conn: sqlite3.Connection, *, symbol: Any, metric: Any,
                operator: Any, threshold: Any, actor_user_id: int,
                now: int | None = None) -> dict[str, Any]:
    clean = validate_rule(symbol, metric, operator, threshold)
    stamp = int(time.time() if now is None else now)
    try:
        conn.execute("BEGIN IMMEDIATE")
        count = conn.execute("SELECT COUNT(*) FROM price_alert_rules").fetchone()[0]
        if count >= MAX_RULES:
            raise ValueError(f"at most {MAX_RULES} alert rules are allowed")
        cur = conn.execute(
            """INSERT INTO price_alert_rules(
                   symbol, metric, operator, threshold, created_by, created_at, updated_at
               ) VALUES (?,?,?,?,?,?,?)""",
            (*clean, int(actor_user_id), stamp, stamp),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    row = conn.execute(
        "SELECT * FROM price_alert_rules WHERE id=?", (cur.lastrowid,),
    ).fetchone()
    result = _rule(row)
    assert result is not None
    return result


def get_rule(conn: sqlite3.Connection, rule_id: int) -> dict[str, Any] | None:
    return _rule(conn.execute(
        "SELECT * FROM price_alert_rules WHERE id=?", (int(rule_id),),
    ).fetchone())


def list_rules(conn: sqlite3.Connection, *, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM price_alert_rules ORDER BY id DESC LIMIT ?", (int(limit),),
    ).fetchall()
    return [dict(_rule(row) or {}) for row in rows]


def set_enabled(conn: sqlite3.Connection, rule_id: int, enabled: bool, *,
                now: int | None = None) -> dict[str, Any] | None:
    existing = get_rule(conn, rule_id)
    if existing is None or existing["enabled"] == bool(enabled):
        return existing
    stamp = int(time.time() if now is None else now)
    conn.execute(
        """UPDATE price_alert_rules
           SET enabled=?, armed=0, last_status=?, updated_at=? WHERE id=?""",
        (1 if enabled else 0, "arming" if enabled else "disabled", stamp, int(rule_id)),
    )
    conn.commit()
    return get_rule(conn, rule_id)


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> bool:
    """Delete only the rule; event snapshots survive through ON DELETE SET NULL."""
    cur = conn.execute("DELETE FROM price_alert_rules WHERE id=?", (int(rule_id),))
    conn.commit()
    return cur.rowcount == 1


def health_summary(path: str | Path | None = None) -> dict[str, Any]:
    """Aggregate, secret-free counts for `/api/health` (ADR-0024 for alerts).

    `pending` is the honest count of events whose delivery outcome was never
    recorded (sender crash or restart between trigger and delivery); it is not
    retried automatically (ADR-0049), so a monitor must be able to see it.
    """
    conn = connect(path)
    try:
        enabled = conn.execute(
            "SELECT COUNT(*) FROM price_alert_rules WHERE enabled=1",
        ).fetchone()[0]
        sent, failed, pending, oldest_pending = conn.execute(
            """SELECT
                 COALESCE(SUM(delivery_sent=1), 0),
                 COALESCE(SUM(delivery_sent=0), 0),
                 COALESCE(SUM(delivery_sent IS NULL), 0),
                 MIN(CASE WHEN delivery_sent IS NULL THEN created_at END)
               FROM price_alert_events""",
        ).fetchone()
    finally:
        conn.close()
    return {
        "enabled_rules": int(enabled),
        "deliveries": {"sent": int(sent), "failed": int(failed), "pending": int(pending)},
        "oldest_pending_at": int(oldest_pending) if oldest_pending is not None else None,
    }


def has_enabled_rules(path: str | Path | None = None) -> bool:
    conn = connect(path)
    try:
        return conn.execute(
            "SELECT 1 FROM price_alert_rules WHERE enabled=1 LIMIT 1",
        ).fetchone() is not None
    finally:
        conn.close()


def _row_observed_at(row: dict[str, Any], universe_ts: float) -> int:
    value = row.get("live_at") if row.get("live") == "ws" else None
    try:
        stamp = float(value or universe_ts)
    except (TypeError, ValueError):
        return 0
    return int(stamp) if math.isfinite(stamp) and stamp > 0 else 0


def observations_from_universe(rows: list[dict[str, Any]], universe_ts: float,
                               premium_pct: Any) -> dict[str, dict[str, Any]]:
    """Build observations without inventing freshness or a missing premium leg.

    The premium timestamp is the oldest of its three inputs. A websocket update
    to USDTIRT alone therefore cannot make older BTC legs look freshly synced.
    """
    observations: dict[str, dict[str, Any]] = {}
    reference_stamps: dict[str, int] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not _SYMBOL.fullmatch(symbol):
            continue
        observed_at = _row_observed_at(row, universe_ts)
        raw_value = row.get("last")
        if raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value <= 0 or row.get("closed") or observed_at <= 0:
            continue
        observations[f"{symbol}|last"] = {
            "value": value,
            "observed_at": observed_at,
            "source": str(row.get("live") or "rest"),
        }
        if symbol in {"BTCIRT", "BTCUSDT", "USDTIRT"}:
            reference_stamps[symbol] = observed_at
    try:
        premium = float(premium_pct)
    except (TypeError, ValueError):
        premium = math.nan
    # All three valid, open reference markets are required. A missing leg or a
    # closed market cannot accidentally turn a partial set into fresh evidence.
    required = {"BTCIRT", "BTCUSDT", "USDTIRT"}
    if (math.isfinite(premium) and set(reference_stamps) == required
            and min(reference_stamps.values()) > 0):
        observations["USDTIRT|premium_pct"] = {
            "value": premium,
            "observed_at": min(reference_stamps.values()),
            "source": "mixed-market-stats",
        }
    return observations


def _condition(operator: str, value: float, threshold: float) -> bool:
    return value > threshold if operator == "above" else value < threshold


def _invalid_observation(observation: dict[str, Any] | None, *, metric: str,
                         now: int, max_age_s: float) -> str | None:
    if observation is None:
        return "no-data"
    raw_value = observation.get("value")
    raw_observed_at = observation.get("observed_at")
    if raw_value is None or raw_observed_at is None:
        return "invalid-data"
    try:
        value = float(raw_value)
        observed_at = int(raw_observed_at)
    except (TypeError, ValueError):
        return "invalid-data"
    if not math.isfinite(value) or (metric == "last" and value <= 0):
        return "invalid-data"
    if observed_at <= 0 or observed_at > now + 5 or now - observed_at > max_age_s:
        return "stale"
    return None


def evaluate(conn: sqlite3.Connection, observations: dict[str, dict[str, Any]], *,
             now: int | None = None, max_age_s: float = MAX_OBSERVATION_AGE_S
             ) -> list[dict[str, Any]]:
    """Evaluate all enabled rules atomically and return only new trigger events."""
    stamp = int(time.time() if now is None else now)
    events: list[dict[str, Any]] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        rules = conn.execute(
            "SELECT * FROM price_alert_rules WHERE enabled=1 ORDER BY id",
        ).fetchall()
        for rule in rules:
            key = f"{rule['symbol']}|{rule['metric']}"
            observation = observations.get(key)
            invalid = _invalid_observation(
                observation, metric=rule["metric"], now=stamp, max_age_s=max_age_s,
            )
            if invalid is not None:
                conn.execute(
                    """UPDATE price_alert_rules
                       SET armed=0, last_status=?, updated_at=? WHERE id=?""",
                    (invalid, stamp, rule["id"]),
                )
                continue
            assert observation is not None
            value = float(observation["value"])
            observed_at = int(observation["observed_at"])
            if (rule["last_observed_at"] is not None
                    and observed_at <= int(rule["last_observed_at"])):
                continue
            previous = rule["last_value"]
            try:
                previous_value = float(previous) if previous is not None else None
            except (TypeError, ValueError):
                previous_value = None
            current_condition = _condition(rule["operator"], value, float(rule["threshold"]))
            previous_condition = (
                _condition(rule["operator"], previous_value, float(rule["threshold"]))
                if previous_value is not None and math.isfinite(previous_value) else None
            )
            trigger = bool(rule["armed"] and previous_condition is False and current_condition)
            status = "triggered" if trigger else ("condition-met" if current_condition else "armed")
            conn.execute(
                """UPDATE price_alert_rules
                   SET armed=1, last_value=?, last_observed_at=?, last_status=?, updated_at=?
                   WHERE id=?""",
                (value, observed_at, status, stamp, rule["id"]),
            )
            if not trigger:
                continue
            cur = conn.execute(
                """INSERT INTO price_alert_events(
                       rule_id, symbol, metric, operator, threshold, previous_value,
                       value, observed_at, source, created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    rule["id"], rule["symbol"], rule["metric"], rule["operator"],
                    rule["threshold"], previous_value, value, observed_at,
                    str(observation.get("source") or "unknown")[:40], stamp,
                ),
            )
            assert cur.lastrowid is not None
            event_id = int(cur.lastrowid)
            conn.execute(
                """UPDATE price_alert_rules
                   SET last_triggered_at=?, last_event_id=? WHERE id=?""",
                (stamp, event_id, rule["id"]),
            )
            row = conn.execute(
                "SELECT * FROM price_alert_events WHERE id=?", (event_id,),
            ).fetchone()
            events.append(_event(row))
        conn.commit()
        return events
    except Exception:
        conn.rollback()
        raise


def record_delivery(conn: sqlite3.Connection, event_id: int,
                    result: dict[str, Any]) -> bool:
    """Persist only a bounded delivery fact, never Telegram's response body."""
    sent = result.get("sent") is True
    transport_raw = str(result.get("transport") or "unknown").lower()
    transport = transport_raw if transport_raw in {"telegram", "disabled"} else "unknown"
    reason_raw = str(result.get("reason") or ("sent" if sent else "unknown")).lower()
    reason = _REASON.sub("-", reason_raw).strip("-")[:80] or ("sent" if sent else "unknown")
    cur = conn.execute(
        """UPDATE price_alert_events
           SET delivery_transport=?, delivery_sent=?, delivery_reason=? WHERE id=?""",
        (transport, 1 if sent else 0, reason, int(event_id)),
    )
    conn.commit()
    return cur.rowcount == 1


def list_events(conn: sqlite3.Connection, *, limit: int = 50,
                rule_id: int | None = None) -> list[dict[str, Any]]:
    if rule_id is None:
        rows = conn.execute(
            "SELECT * FROM price_alert_events ORDER BY id DESC LIMIT ?", (int(limit),),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM price_alert_events
               WHERE rule_id=? ORDER BY id DESC LIMIT ?""",
            (int(rule_id), int(limit)),
        ).fetchall()
    return [_event(row) for row in rows]


def alert_text(event: dict[str, Any]) -> str:
    """A credential-free message assembled only from validated numeric fields."""
    observed = datetime.fromtimestamp(int(event["observed_at"]), UTC).isoformat()
    return (
        f"Nobi price alert #{int(event['id'])}\n"
        f"{event['symbol']} {event['metric']} crossed {event['operator']} "
        f"{float(event['threshold']):.12g}\n"
        f"previous={float(event['previous_value']):.12g} "
        f"current={float(event['value']):.12g}\n"
        f"observed_at={observed} source={event['source']}"
    )


__all__ = [
    "MAX_RULES", "alert_text", "connect", "create_rule", "delete_rule",
    "evaluate", "get_rule", "has_enabled_rules", "list_events", "list_rules",
    "observations_from_universe", "record_delivery", "set_enabled", "validate_rule",
]
