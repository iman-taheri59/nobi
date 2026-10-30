"""Signal Intelligence v1.1 linkage, statistics, pagination and export contracts."""

from __future__ import annotations

import csv
import io
import time
from pathlib import Path

from fastapi.testclient import TestClient

import auth
import journal
import main
import signal_cycles
import signal_intelligence


def _db(monkeypatch, tmp_path):
    path = tmp_path / "journal-v11.db"
    monkeypatch.setattr(journal, "DB_PATH", path)
    with journal.connect() as conn:
        signal_cycles.ensure(conn)
    return path


def _row(symbol: str, ts: int, composite: float = 70) -> journal.JournalRow:
    return journal.JournalRow(
        ts=ts,
        symbol=symbol,
        timeframe="15m",
        direction="LONG",
        ta_score=70,
        composite=composite,
        entry=100,
        atr=2,
        regime="trend",
        source="test",
        origin="scan",
    )


def test_cycle_linkage_is_insert_time_exact_and_duplicate_does_not_steal(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    now = int(time.time())
    first = signal_cycles.start(timeframe="15m", origin="scan", universe_count=1)
    with journal.connect() as conn:
        assert (
            journal.insert_rows(
                conn,
                [_row("BTCUSDT", now)],
                scan_cycle_id=first,
                engine_version=signal_cycles.ENGINE_VERSION,
            )
            == 1
        )
    result = signal_cycles.complete(
        first,
        snapshots={"BTCUSDT": {"composite": 77}},
        verdicts={"BTCUSDT": {"code": "spread", "codes": ["spread", "depth"]}},
        skipped=[],
        source="test",
    )
    assert result == {"scored": 1, "emitted": 1, "errors": 0}

    second = signal_cycles.start(timeframe="15m", origin="scan", universe_count=1)
    with journal.connect() as conn:
        assert (
            journal.insert_rows(
                conn,
                [_row("BTCUSDT", now)],
                scan_cycle_id=second,
                engine_version=signal_cycles.ENGINE_VERSION,
            )
            == 0
        )
        signal = conn.execute("SELECT scan_cycle_id,linkage_status FROM signal_log").fetchone()
        assert tuple(signal) == (first, "linked")
        reasons = conn.execute(
            "SELECT reason_code,reason_group,ordinal FROM gate_verdict_reasons ORDER BY ordinal"
        ).fetchall()
        assert [tuple(row) for row in reasons] == [("spread", "market", 0), ("depth", "market", 1)]


def test_skipped_candidates_and_crash_recovery_are_explicit(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    cycle = signal_cycles.start(timeframe="15m", origin="scan", universe_count=1)
    result = signal_cycles.complete(
        cycle, snapshots={}, verdicts={}, skipped=["FAILUSDT", "FAILUSDT"], source="test"
    )
    assert result["errors"] == 1
    with journal.connect() as conn:
        row = conn.execute(
            """SELECT c.state,c.gate_code,r.reason_group FROM scan_candidates c
               JOIN gate_verdict_reasons r ON r.candidate_id=c.id WHERE c.scan_cycle_id=?""",
            (cycle,),
        ).fetchone()
        old = signal_cycles.new_id(1)
        conn.execute(
            """INSERT INTO scan_cycles(id,timeframe,origin,status,started_at,engine_version,
               config_hash) VALUES(?,?,?,?,?,?,?)""",
            (old, "15m", "scan", "running", 1, signal_cycles.ENGINE_VERSION, "cfg"),
        )
        conn.commit()
    assert tuple(row) == ("scan-error", "source-unavailable", "data")
    assert signal_cycles.recover_stale(older_than_s=10, now=100) == 1
    with journal.connect() as conn:
        recovered = conn.execute(
            "SELECT status,error_summary FROM scan_cycles WHERE id=?", (old,)
        ).fetchone()
    assert recovered["status"] == "partial"
    assert "interrupted" in recovered["error_summary"]


def test_legacy_rows_are_never_inferred_and_schema_is_idempotent(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    now = int(time.time())
    with journal.connect() as conn:
        journal.insert_rows(conn, [_row("ETHUSDT", now)])
        signal_cycles.ensure(conn)
        signal_cycles.ensure(conn)
        row = conn.execute("SELECT scan_cycle_id,linkage_status FROM signal_log").fetchone()
    assert tuple(row) == (None, "legacy-unlinked")


def test_statistics_maturity_cursor_and_safe_exports(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    now = int(time.time())
    cycle = signal_cycles.start(timeframe="15m", origin="scan", universe_count=4)
    rows = [
        _row("\t=FORMULA", now - 10, 90),
        _row("BTCUSDT", now - 20, 80),
        _row("ETHUSDT", now - 30, 70),
        _row("XRPUSDT", now - journal.HORIZONS["H1"] - 100, 60),
    ]
    with journal.connect() as conn:
        journal.insert_rows(
            conn, rows, scan_cycle_id=cycle, engine_version=signal_cycles.ENGINE_VERSION
        )
        ids = [r[0] for r in conn.execute("SELECT id FROM signal_log ORDER BY ts DESC")]
        for signal_id, r_value in zip(ids[:2], (1.0, -1.0), strict=True):
            conn.execute(
                """INSERT INTO signal_outcomes(signal_id,horizon_key,resolved_at,r_multiple)
                   VALUES(?,?,?,?)""",
                (signal_id, "H1", now, r_value),
            )
        conn.commit()

    summary = signal_intelligence.linked_summary(days=2, horizon="H1")
    assert summary["linkage"]["coverage_pct"] == 100.0
    assert summary["win_rate_ci95_pct"] is not None
    assert summary["expectancy_r"] == 0.0
    assert summary["median_r"] == 0.0
    assert summary["outcome_maturity"] == {"pending_not_due": 1, "due_unresolved": 1}

    first = signal_intelligence.paged_signals(days=2, limit=2)
    second = signal_intelligence.paged_signals(days=2, limit=2, cursor=first["page"]["next_cursor"])
    assert len(first["rows"]) == len(second["rows"]) == 2
    assert {r["id"] for r in first["rows"]}.isdisjoint(r["id"] for r in second["rows"])

    parsed = list(csv.DictReader(io.StringIO(signal_intelligence.export_linked_csv(days=2))))
    assert parsed[0]["symbol"] == "'\t=FORMULA"
    assert len(signal_intelligence.export_linked_rows(days=2)) == 4


def test_versioned_api_and_bounded_export_formats(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    now = int(time.time())
    cycle = signal_cycles.start(timeframe="15m", origin="scan", universe_count=1)
    with journal.connect() as conn:
        journal.insert_rows(
            conn,
            [_row("BTCUSDT", now)],
            scan_cycle_id=cycle,
            engine_version=signal_cycles.ENGINE_VERSION,
        )
    with TestClient(main.app) as client:
        assert client.get("/api/v1/signal-intelligence/summary?days=1").status_code == 200
        page = client.get("/api/v1/signal-intelligence/signals?days=1&limit=1")
        assert page.status_code == 200 and page.json()["rows"][0]["scan_cycle_id"] == cycle
        csv_response = client.get("/api/v1/signal-intelligence/export?days=1&format=csv")
        json_response = client.get("/api/v1/signal-intelligence/export?days=1&format=json")
        assert csv_response.status_code == json_response.status_code == 200
        assert "signal-intelligence.csv" in csv_response.headers["content-disposition"]
        assert json_response.json()["limit"] == 10_000
        assert client.get("/api/v1/signal-intelligence/export?format=xml").status_code == 422


def test_cursor_is_stable_when_timestamps_are_equal(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    now = int(time.time())
    cycle = signal_cycles.start(timeframe="15m", origin="scan", universe_count=3)
    with journal.connect() as conn:
        journal.insert_rows(
            conn,
            [_row("BTCUSDT", now), _row("ETHUSDT", now), _row("XRPUSDT", now)],
            scan_cycle_id=cycle,
            engine_version=signal_cycles.ENGINE_VERSION,
        )
    first = signal_intelligence.paged_signals(days=1, limit=2)
    second = signal_intelligence.paged_signals(
        days=1, limit=2, cursor=first["page"]["next_cursor"]
    )
    ids = [row["id"] for row in first["rows"] + second["rows"]]
    assert ids == sorted(ids, reverse=True)
    assert len(ids) == len(set(ids)) == 3


def test_dashboard_wires_linked_metrics_taxonomy_and_bounded_exports():
    html = (Path(__file__).parents[2] / "index.html").read_text(encoding="utf-8")
    core = (Path(__file__).parents[2] / "nbx-core.js").read_text(encoding="utf-8")
    assert "/v1/signal-intelligence/evidence" in core
    assert "api.getSignalEvidence(qs)" in html
    assert "/v1/signal-intelligence/export" in html
    assert "siExportCsv" in html and "siExportJson" in html
    assert "validateSignalEvidence" in html and "view:'evidence'" in html
    assert "siReadScope" in html and "siExportStatus" in html
    assert "win_rate_ci95_pct" in html and "outcome_maturity" in html
    assert "coverage_pct" in html and "expectancy_ci95_r" in html


def test_invalid_cursor_and_wilson_edges(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    assert signal_cycles.wilson(0, 0) is None
    assert signal_cycles.wilson(0, 1) == [0, 79.35]
    try:
        signal_intelligence.paged_signals(days=1, cursor="not-json")
    except ValueError as exc:
        assert str(exc) == "invalid cursor"
    else:
        raise AssertionError("invalid cursor accepted")
