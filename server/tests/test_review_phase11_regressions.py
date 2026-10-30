# mypy: check_untyped_defs = False
"""Private SQLite fixtures only. Run through scripts/run_page_regressions.py."""

from __future__ import annotations

import base64
import csv
import io
import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import journal
import main
import signal_cycles
import signal_evidence_view as ev

NOW = 1_800_000_000


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "evidence.db")
    monkeypatch.setattr(ev.time, "time", lambda: NOW)
    with journal.connect():
        pass
    return journal.DB_PATH


def add(
    conn,
    symbol="BTCUSDT",
    timeframe="15m",
    ts=NOW - 4000,
    direction="LONG",
    entry=100,
    atr=2,
    voided_at=None,
    outcome=1.0,
    horizon="H1",
    **extra,
):
    data = dict(
        symbol=symbol,
        timeframe=timeframe,
        ts=ts,
        direction=direction,
        entry=entry,
        atr=atr,
        voided_at=voided_at,
        **extra,
    )
    cur = conn.execute(
        "INSERT INTO signal_log ("
        + ",".join(data)
        + ") VALUES ("
        + ",".join("?" for _ in data)
        + ")",
        list(data.values()),
    )
    sid = cur.lastrowid
    if outcome is not None:
        conn.execute(
            "INSERT INTO signal_outcomes (signal_id,horizon_key,resolved_at,r_multiple,mfe_pct,mae_pct) VALUES (?,?,?,?,?,?)",
            (sid, horizon, NOW, outcome, 2, -1),
        )
    return sid


@pytest.mark.parametrize("horizon,seconds", [("H1", 3600), ("H2", 86400), ("H3", 604800)])
def test_all_views_share_filters_and_server_cutoff(db, horizon, seconds):
    with journal.connect() as c:
        wanted = add(c, horizon=horizon)
        add(c, symbol="ETHUSDT", horizon=horizon)
        add(c, timeframe="1h", horizon=horizon)
        add(c, ts=NOW - 86401, horizon=horizon)
        add(c, ts=NOW + 1, horizon=horizon)
        add(c, symbol="DOGEIRT", horizon=horizon)  # unknown price scale, not inferred
        c.commit()
    r = ev.read(days=1, symbol=" btcusdt ", timeframe="15m", horizon=horizon)
    assert r["filters"] == {
        "days": 1,
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "horizon": horizon,
        "as_of": NOW,
        "since": NOW - 86400,
    }
    assert r["summary"]["signals"] == r["page"]["total"] == 1
    assert r["summary"]["resolved"] == 1 and r["summary"]["expectancy_r"] == 1
    assert r["measurement"]["horizon_seconds"] == seconds
    assert r["measurement"]["independent_samples_established"] is False
    assert r["measurement"]["net_account_pnl"] is False
    assert [x["id"] for x in r["rows"]] == [wanted]
    assert len(r["cohorts"]["rows"]) == r["cohorts"]["total"] == 1
    assert r["cohorts"]["rows"][0]["signals"] == 1
    assert r["pipeline"]["available"] is False
    ex = ev.read(
        days=1,
        symbol="BTCUSDT",
        timeframe="15m",
        horizon=horizon,
        as_of=NOW,
        export=True,
        limit=10000,
    )
    assert ex["rows"] == r["rows"] and ex["filters"] == r["filters"]
    assert ev.read(days=1)["quality"]["excluded_price_contract_records"] == 1


def test_model_statuses_partition_all_rows_and_do_not_turn_nonlong_into_wins(db):
    with journal.connect() as c:
        for i, kwargs in enumerate(
            [
                {},
                {"outcome": -1},
                {"outcome": 0},
                {"direction": "NEUTRAL"},
                {"direction": "EXIT"},
                {"entry": 0},
                {"atr": 0},
                {"entry": None},
                {"atr": float("inf")},
                {"outcome": None},
                {"ts": NOW - 100, "outcome": None},
                {"voided_at": NOW},
                {"outcome": float("inf")},
                {"outcome": "not-a-number"},
            ]
        ):
            add(c, symbol=f"X{i}USDT", **kwargs)
        # A resolved record with no usable R must not become a zero / a win.
        sid = add(c, symbol="NULLRUSDT", outcome=None)
        c.execute(
            "INSERT INTO signal_outcomes(signal_id,horizon_key,resolved_at) VALUES(?,?,?)",
            (sid, "H1", NOW),
        )
        c.commit()
    r = ev.read()
    s = r["summary"]
    assert s["status_counts"] == {
        "resolved": 3,
        "pending_not_due": 1,
        "due_unresolved": 1,
        "non_long_not_applicable": 2,
        "invalid_model_inputs": 4,
        "invalid_model_outcomes": 3,
        "void": 1,
    }
    assert sum(s["status_counts"].values()) == s["signals"] == 15
    assert (s["wins"], s["losses"], s["flats"]) == (1, 1, 1)
    assert s["expectancy_r"] == s["median_r"] == 0 and s["win_rate_pct"] == 33.33
    assert s["win_rate_ci95_pct"] == signal_cycles.wilson(1, 3)
    assert s["expectancy_ci95_r"] == [-1.1316, 1.1316]
    for row in r["rows"]:
        if row["model_status"] != "resolved":
            assert row["r_multiple"] is None and row["mfe_pct"] is None
    assert "Infinity" not in json.dumps(r, allow_nan=False)


@pytest.mark.parametrize("n", [0, 1, 29, 30, 31])
def test_threshold_is_count_only_and_ci_never_claims_independence(db, n):
    with journal.connect() as c:
        for i in range(n):
            add(c, symbol=f"X{i}USDT")
        c.commit()
    r = ev.read()
    s = r["summary"]
    assert s["sample_sufficient"] is (n >= 30)
    assert s["resolved"] == n and s["signals"] == n
    assert r["measurement"]["sample_threshold_is_count_only"] is True
    assert r["measurement"]["independent_samples_established"] is False
    assert "live_allowed" not in r
    assert (s["expectancy_ci95_r"] is None) is (n <= 1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"days": 0},
        {"days": True},
        {"days": 3651},
        {"horizon": "H4"},
        {"timeframe": "5m"},
        {"symbol": "BTC'; DROP TABLE signal_log;--"},
        {"symbol": "A" * 33},
        {"as_of": NOW + 1},
        {"as_of": False},
        {"as_of": 0},
        {"limit": 0},
        {"limit": 501},
        {"limit": True},
        {"cohort_limit": 501},
        {"export": True, "limit": 10001},
    ],
)
def test_bad_filters_limits_and_future_cutoff_fail_explicitly(db, kwargs):
    with pytest.raises(ValueError):
        ev.read(**kwargs)


def test_tied_timestamp_order_and_preview_limits_do_not_limit_summary(db):
    with journal.connect() as c:
        ids = [add(c, symbol=f"X{i:03}USDT") for i in range(120)]
        c.commit()
    r = ev.read()
    assert [x["id"] for x in r["rows"]] == list(reversed(ids))[:100]
    assert r["page"] == {"limit": 100, "total": 120, "loaded": 100, "has_more": True}
    assert r["summary"]["resolved"] == r["summary"]["signals"] == 120
    assert (
        r["cohorts"]["total"] == 120
        and len(r["cohorts"]["rows"]) == 50
        and r["cohorts"]["has_more"]
    )
    assert sum(x["signals"] for x in r["cohorts"]["rows"]) == 50  # preview, not the full population


@pytest.mark.parametrize("n", [9999, 10000, 10001])
def test_export_cap_is_exact_and_partial_not_silently_complete(db, n):
    with journal.connect() as c:
        c.executemany(
            "INSERT INTO signal_log(ts,symbol,timeframe,direction,entry,atr) VALUES(?,?,'15m','LONG',100,2)",
            [(NOW - 100, f"X{i:05}USDT") for i in range(n)],
        )
        c.commit()
    r = ev.read(export=True, limit=10000)
    assert r["page"] == {"limit": 10000, "total": n, "loaded": min(n, 10000), "has_more": n > 10000}
    assert len(r["rows"]) == min(n, 10000)
    assert [x["id"] for x in r["rows"]] == list(range(n, max(n - 10000, 0), -1))
    csv_rows = list(csv.DictReader(io.StringIO(ev.to_csv(r))))
    assert len(csv_rows) == len(r["rows"])
    assert [int(x["id"]) for x in csv_rows] == [x["id"] for x in r["rows"]]
    assert not {"features", "note", "actor_username"} & set(csv_rows[0])


def test_csv_formula_strings_guarded_but_negative_r_stays_numeric(db):
    with journal.connect() as c:
        add(c, symbol="\t=FORMULA", outcome=-1.5, source="\ufeff \t@SUM(1)")
        c.commit()
    r = ev.read(export=True, limit=10000)
    row = next(iter(csv.DictReader(io.StringIO(ev.to_csv(r)))))
    assert row["symbol"].startswith("'\t=") and row["source"].startswith("'\ufeff")
    assert row["r_multiple"] == "-1.5"
    assert r["rows"][0]["symbol"] == "\t=FORMULA"  # JSON is data, not spreadsheet markup


def test_no_schema_migrations_writes_or_inferred_linkage(db, monkeypatch):
    with journal.connect() as c:
        add(c)
        c.commit()
        before = list(c.execute("SELECT name,sql FROM sqlite_master ORDER BY name"))

    def forbidden(*args, **kwargs):
        raise AssertionError("GET attempted initialization/migration/audit")

    original = journal.connect_readonly
    statements = []

    def readonly():
        c = original()
        c.set_trace_callback(statements.append)

        def authorize(action, *args):
            denied = {
                sqlite3.SQLITE_INSERT,
                sqlite3.SQLITE_UPDATE,
                sqlite3.SQLITE_DELETE,
                sqlite3.SQLITE_CREATE_TABLE,
                sqlite3.SQLITE_CREATE_VIEW,
                sqlite3.SQLITE_ALTER_TABLE,
            }
            return sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK

        c.set_authorizer(authorize)
        return c

    with (
        patch.object(journal, "connect", forbidden),
        patch.object(signal_cycles, "ensure", forbidden),
        patch.object(journal, "connect_readonly", readonly),
    ):
        r = ev.read()
    assert (
        r["rows"][0]["linkage_status"] == "legacy-unlinked"
        and r["rows"][0]["scan_cycle_id"] is None
    )
    assert r["summary"]["linkage"]["coverage_pct"] == 0
    assert statements.count("BEGIN") == 1
    with original() as c:
        assert list(c.execute("SELECT name,sql FROM sqlite_master ORDER BY name")) == before


def test_missing_database_and_schema_are_not_fabricated_zero(db, monkeypatch, tmp_path):
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "absent.db")
    with pytest.raises(sqlite3.OperationalError):
        ev.read()
    assert not journal.DB_PATH.exists()
    sqlite3.connect(journal.DB_PATH).close()
    with pytest.raises(sqlite3.OperationalError):
        ev.read()


def test_single_transaction_holds_consistent_rows_counts_and_outcomes_during_writer(
    db, monkeypatch
):
    with journal.connect() as c:
        sid = add(c)
        c.commit()
    original = journal.connect_readonly
    calls = []

    class Wrapped:
        def __init__(self):
            self.c = original()
            self.changed = False

        def execute(self, sql, *args):
            cur = self.c.execute(sql, *args)
            if not self.changed and sql.endswith("SELECT count(*) FROM evidence"):
                self.changed = True
                with sqlite3.connect(db) as writer:
                    writer.execute(
                        "UPDATE signal_outcomes SET r_multiple=9 WHERE signal_id=?", (sid,)
                    )
                    add(writer, symbol="ETHUSDT")
            return cur

        def close(self):
            self.c.close()

    def wrapped():
        calls.append(1)
        return Wrapped()

    monkeypatch.setattr(journal, "connect_readonly", wrapped)
    r = ev.read()
    assert len(calls) == 1 and r["page"]["total"] == len(r["rows"]) == 1
    assert r["rows"][0]["r_multiple"] == r["summary"]["expectancy_r"] == 1
    monkeypatch.setattr(journal, "connect_readonly", original)
    later = ev.read(as_of=r["filters"]["as_of"], export=True, limit=10000)
    assert later["page"]["total"] == 2 and later["rows"][-1]["r_multiple"] == 9
    assert "not historical outcomes" in later["consistency"]


def test_global_pipeline_scope_is_not_selected_cohort_scope(db):
    with journal.connect() as c:
        signal_cycles.ensure(c)
        add(c)
        c.execute(
            "INSERT INTO scan_cycles(id,timeframe,origin,status,started_at,engine_version,config_hash) VALUES('c','1h','scan','completed',?,'fixture','fixture')",
            (NOW - 100,),
        )
        cid = c.execute(
            "INSERT INTO scan_candidates(scan_cycle_id,symbol,timeframe,state,gate_passed,created_at) VALUES('c','ETHUSDT','1h','gate-blocked',0,?)",
            (NOW - 100,),
        ).lastrowid
        c.execute(
            "INSERT INTO gate_verdict_reasons(candidate_id,reason_code,reason_group,ordinal) VALUES(?,'spread','market',0)",
            (cid,),
        )
        c.commit()
    r = ev.read(symbol="BTCUSDT", timeframe="15m")
    p = r["pipeline"]
    assert r["summary"]["signals"] == 1 and p["available"]
    assert p["scope"]["symbol"] is p["scope"]["timeframe"] is p["scope"]["horizon"] is None
    assert p["scope"]["as_of"] == r["filters"]["as_of"]
    assert p["reasons"] == [{"code": "spread", "group": "market", "count": 1}]
    assert p["stages"][1]["count"] == 1


def test_group_null_and_unknown_labels_share_one_explicit_cohort(db):
    with journal.connect() as c:
        add(c, ts=NOW - 1)
        add(c, ts=NOW - 2, regime="unknown", config_hash="unknown")
        c.commit()
    r = ev.read()
    assert r["cohorts"]["total"] == 1 and r["cohorts"]["rows"][0]["signals"] == 2


def test_evidence_api_legacy_export_compatibility_and_audit(db, monkeypatch):
    with journal.connect() as c:
        add(c)
        signal_cycles.ensure(c)
        c.commit()
    events = []
    monkeypatch.setattr(main, "audit", lambda *a, **k: events.append((a, k)))
    # No lifespan: no background tasks or startup initialization.
    client = TestClient(main.app)
    r = client.get("/api/v1/signal-intelligence/evidence?symbol=btcusdt&timeframe=15m")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert not events
    q = f"view=evidence&symbol=BTCUSDT&timeframe=15m&as_of={NOW}"
    csv_response = client.get("/api/v1/signal-intelligence/export?" + q + "&format=csv")
    json_response = client.get("/api/v1/signal-intelligence/export?" + q + "&format=json")
    assert csv_response.status_code == json_response.status_code == 200
    meta = json.loads(base64.b64decode(csv_response.headers["x-nbx-evidence"]))
    assert meta == ev.metadata(json_response.json()) and meta["page"]["has_more"] is False
    assert events[0][0] == ("signal_intelligence.export",)
    assert events[0][1]["detail"]["view"] == "evidence"
    assert events[0][1]["detail"]["symbol"] == "BTCUSDT"
    legacy = client.get("/api/v1/signal-intelligence/export?format=json")
    assert legacy.status_code == 200 and set(legacy.json()) == {"rows", "limit"}
    assert events[-1][1]["detail"] == {"days": 30, "format": "json"}
    assert "horizon_key" not in legacy.json()["rows"][0]  # unchanged linked projection
    assert client.get("/api/v1/signal-intelligence/evidence?timeframe=5m").status_code == 422
    assert (
        client.get("/api/v1/signal-intelligence/evidence?as_of=" + str(NOW + 1)).status_code == 422
    )
    assert client.get("/api/v1/signal-intelligence/export?view=invalid").status_code == 422
    monkeypatch.setattr(journal, "DB_PATH", db.parent / "not-created.db")
    unavailable = client.get("/api/v1/signal-intelligence/evidence")
    assert unavailable.status_code == 503 and not journal.DB_PATH.exists()
    assert str(db.parent) not in unavailable.text


@pytest.mark.parametrize("resolved_at", [0, "invalid", float("inf")])
def test_invalid_outcome_timestamp_cannot_be_valid_model_evidence(db, resolved_at):
    with journal.connect() as c:
        sid = add(c)
        c.execute("UPDATE signal_outcomes SET resolved_at=? WHERE signal_id=?", (resolved_at, sid))
        c.commit()
    r = ev.read()
    assert r["summary"]["resolved"] == 0
    assert r["summary"]["status_counts"]["invalid_model_outcomes"] == 1
    assert r["rows"][0]["r_multiple"] is None
    json.dumps(r, allow_nan=False)


def test_extreme_finite_r_does_not_publish_infinite_or_zero_width_ci(db):
    with journal.connect() as c:
        add(c, symbol="POSUSDT", outcome=1.7976931348623157e308)
        add(c, symbol="NEGUSDT", outcome=-1.7976931348623157e308)
        c.commit()
    s = ev.read()["summary"]
    assert s["resolved"] == 2 and s["expectancy_r"] == 0
    assert s["expectancy_ci95_r"] is None
    assert s["expectancy_ci95_status"] == "numeric-range-unavailable"
    json.dumps(s, allow_nan=False)


def test_nonfinite_optional_numeric_cells_do_not_break_json(db):
    with journal.connect() as c:
        sid = add(c, voided_at=float("inf"))
        c.execute(
            "UPDATE signal_outcomes SET hit_tp1=?,hit_sl=? WHERE signal_id=?",
            (float("inf"), float("-inf"), sid),
        )
        c.commit()
    r = ev.read()
    assert r["rows"][0]["voided_at"] is None
    assert r["rows"][0]["model_status"] == "void"
    json.dumps(r, allow_nan=False)
