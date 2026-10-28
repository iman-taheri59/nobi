"""Signal Intelligence v1.2 drift statistics and immutable evidence."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import auth
import journal
import main
import signal_drift


def _db(monkeypatch, tmp_path):
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "drift.db")
    with journal.connect() as conn:
        signal_drift.ensure(conn)


def test_bh_psi_and_mean_difference_are_deterministic():
    assert signal_drift.bh_adjust([0.01, 0.04, 0.03]) == [0.03, 0.04, 0.04]
    score, edges = signal_drift.psi(list(range(100)), list(range(50, 150)))
    assert score is not None and score > 0.25 and len(edges) == 9
    bins = signal_drift.psi_bins(list(range(100)), list(range(50, 150)), edges)
    assert len(bins) == 10
    assert sum(row["baseline_n"] for row in bins) == 100
    assert sum(row["current_n"] for row in bins) == 100
    assert round(sum(row["contribution"] for row in bins), 6) == score
    delta, ci, p = signal_drift.mean_difference([0, 1, 2], [2, 3, 4])
    assert delta == 2 and ci and ci[0] < delta < ci[1] and p < 0.05


def test_empty_run_is_immutable_and_insufficient(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    result = signal_drift.run(days=30, horizon="H1", actor="owner", now=2_000_000_000)
    assert result["run"]["status"] == "stable"
    assert {row["severity"] for row in result["findings"]} == {"insufficient"}
    latest = signal_drift.latest()
    assert latest is not None and latest["run"]["id"] == result["run"]["id"]
    with journal.connect() as conn:
        assert conn.execute("SELECT count(*) FROM drift_runs").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM drift_findings").fetchone()[0] == 6


def test_api_requires_operator_and_csrf(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    viewer = auth.create_user("drift-viewer", "A-strong-viewer-password!", role="viewer")
    token, csrf, _ = auth.create_session(viewer, ip="testclient", ua="pytest")
    with TestClient(main.app) as client:
        client.cookies.set("nbx_session", token)
        client.cookies.set("nbx_csrf", csrf)
        denied = client.post(
            "/api/v1/signal-intelligence/drift/run",
            json={"days": 30, "horizon": "H1"},
            headers={"X-CSRF-Token": csrf},
        )
    assert denied.status_code == 403
    operator = auth.create_user("drift-operator", "A-strong-operator-password!", role="operator")
    op_token, op_csrf, _ = auth.create_session(operator, ip="testclient", ua="pytest")
    with TestClient(main.app) as client:
        client.cookies.set("nbx_session", op_token)
        client.cookies.set("nbx_csrf", op_csrf)
        missing = client.post(
            "/api/v1/signal-intelligence/drift/run", json={"days": 30, "horizon": "H1"}
        )
        created = client.post(
            "/api/v1/signal-intelligence/drift/run",
            json={"days": 30, "horizon": "H1"},
            headers={"X-CSRF-Token": op_csrf},
        )
        latest = client.get("/api/v1/signal-intelligence/drift/latest")
    assert missing.status_code == 403
    assert created.status_code == latest.status_code == 200
    assert len(created.json()["findings"]) == 6
    run_id = created.json()["run"]["id"]
    with TestClient(main.app) as client:
        client.cookies.set("nbx_session", op_token)
        assert (
            client.get("/api/v1/signal-intelligence/drift/runs").json()["rows"][0]["id"] == run_id
        )
        exported = client.get(f"/api/v1/signal-intelligence/drift/export?run_id={run_id}")
    assert exported.status_code == 200 and "composite_psi" in exported.text


def test_concurrent_run_refusal_and_stale_recovery(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with journal.connect() as conn:
        conn.execute(
            """INSERT INTO drift_runs(created_at,actor,current_start,current_end,baseline_start,
               baseline_end,horizon,filters,engine_version,correction_method,thresholds,status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,'running')""",
            (100, "owner", 50, 100, 0, 50, "H1", "{}", "1.7.0", "bh", "{}"),
        )
        conn.commit()
    recovered = signal_drift.run(days=7, horizon="H1", actor="owner", now=10_000)
    assert recovered["run"]["status"] == "stable"
    with journal.connect() as conn:
        assert conn.execute("SELECT status FROM drift_runs WHERE id=1").fetchone()[0] == "partial"
        conn.execute(
            """INSERT INTO drift_runs(created_at,actor,current_start,current_end,baseline_start,
               baseline_end,horizon,filters,engine_version,correction_method,thresholds,status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,'running')""",
            (10_000, "owner", 9_000, 10_000, 8_000, 9_000, "H1", "{}", "1.7.0", "bh", "{}"),
        )
        conn.commit()
    with pytest.raises(RuntimeError, match="already active"):
        signal_drift.run(days=7, horizon="H1", actor="owner", now=10_001)


def test_schedule_defaults_validation_due_and_inventory(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    assert signal_drift.schedule()["enabled"] == 0
    cfg = signal_drift.update_schedule(
        enabled=True,
        interval_s=21600,
        window_days=7,
        horizon="H1",
        cohort_mode="config",
        actor="owner",
        now=10_000,
    )
    assert cfg["next_run_at"] == 31_600
    assert signal_drift.run_due(now=20_000) is None
    result = signal_drift.run_due(now=31_600)
    assert result is not None and result["run"]["actor"] == "scheduler"
    assert signal_drift.schedule()["next_run_at"] == 53_200
    assert signal_drift.config_inventory(days=7, now=31_600) == []
    with pytest.raises(ValueError, match="interval_s"):
        signal_drift.update_schedule(
            enabled=True,
            interval_s=60,
            window_days=7,
            horizon="H1",
            cohort_mode="global",
            actor="owner",
        )


def test_review_is_append_only_and_allowlisted(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    run = signal_drift.run(days=30, horizon="H1", actor="owner", now=int(time.time()))
    finding = run["findings"][0]["id"]
    first = signal_drift.review(
        finding, user_id=1, username="operator", tag="investigate", note="first"
    )
    second = signal_drift.review(
        finding, user_id=1, username="operator", tag="acknowledged", note="second"
    )
    assert first["id"] != second["id"]
    with journal.connect() as conn:
        assert conn.execute("SELECT count(*) FROM drift_reviews").fetchone()[0] == 2


def test_cohort_inventory_is_bounded_allowlisted_and_suppressed(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    now = 2_000_000_000
    with journal.connect() as conn:
        for idx in range(35):
            conn.execute(
                "INSERT INTO signal_log(ts,symbol,timeframe,direction,regime,config_hash) "
                "VALUES(?,?,?,?,?,?)",
                (now - 8 * 86400 + idx, "BTCIRT", "15m", "BUY", "TREND", "cfg-a"),
            )
            conn.execute(
                "INSERT INTO signal_log(ts,symbol,timeframe,direction,regime,config_hash) "
                "VALUES(?,?,?,?,?,?)",
                (now - 38 * 86400 + idx, "BTCIRT", "15m", "BUY", "TREND", "cfg-a"),
            )
        conn.execute(
            "INSERT INTO signal_log(ts,symbol,timeframe,direction,regime,config_hash) "
            "VALUES(?,?,?,?,?,?)",
            (now - 86400, "ETHIRT", "1h", "SELL", "RANGE", "cfg-b"),
        )
        # Explicitly current-contract fixture; legacy rows are tested separately.
        conn.execute("UPDATE signal_log SET price_contract=?", (journal.IRT_PRICE_CONTRACT,))
        conn.commit()
    result = signal_drift.cohort_inventory(
        mode="symbol+timeframe", days=30, now=now, limit=10
    )
    assert result["truncated"] is False and result["dimensions"] == ["symbol", "timeframe"]
    by_key = {row["cohort_key"]: row for row in result["rows"]}
    assert by_key["BTCIRT|15m"]["status"] == "comparable"
    assert by_key["ETHIRT|1h"]["status"] == "new-in-current"
    assert by_key["ETHIRT|1h"]["suppression_reason"] == "sample-low"
    with pytest.raises(ValueError, match="unsupported"):
        signal_drift.cohort_inventory(mode="symbol+regime+config", now=now)


def test_run_persists_cohort_statistics_in_one_correction_family(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    now = 2_000_000_000
    with journal.connect() as conn:
        for current_window in (False, True):
            for idx in range(110):
                ts = now - (5 if current_window else 35) * 86400 + idx
                cur = conn.execute(
                    "INSERT INTO signal_log(ts,symbol,timeframe,direction,regime,config_hash,composite) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (ts, "BTCIRT", "15m", "BUY", "TREND", "cfg", 80 if current_window else 50),
                )
                conn.execute(
                    "INSERT INTO signal_outcomes(signal_id,horizon_key,resolved_at,r_multiple) VALUES(?,?,?,?)",
                    (cur.lastrowid, "H1", ts + 3600, 1.0 if current_window else -1.0),
                )
        # Explicitly current-contract fixture; legacy rows are tested separately.
        conn.execute("UPDATE signal_log SET price_contract=?", (journal.IRT_PRICE_CONTRACT,))
        conn.commit()
    result = signal_drift.run(
        days=30, horizon="H1", actor="owner", cohort_mode="symbol", now=now
    )
    cohort = [row for row in result["findings"] if row["cohort_key"] == "BTCIRT"]
    assert {row["metric"] for row in cohort} == {"expectancy_r", "win_rate", "composite_psi"}
    assert all(row["severity"] != "insufficient" for row in cohort)
    assert all(row["q_value"] is not None for row in cohort if row["metric"] != "composite_psi")
    assert result["run"]["status"] == "alert"
