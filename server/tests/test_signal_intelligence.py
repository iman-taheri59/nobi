"""Signal Intelligence v1 uses real journal outcomes and role-gated reviews."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

import auth
import gate_ledger
import journal
import main
import signal_intelligence


def _seed(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setattr(journal, "DB_PATH", db)
    monkeypatch.setattr(gate_ledger, "DB_PATH", db)
    now = int(time.time())
    with journal.connect() as conn:
        for i, (symbol, r_value) in enumerate((("BTCUSDT", 1.5), ("ETHUSDT", -1.0))):
            cur = conn.execute("""INSERT INTO signal_log(ts,symbol,timeframe,direction,ta_score,fa_score,
                liquidity_score,composite,regime,entry,stop,tp1,tp2,atr,features,source,origin,config_hash,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (now-i, symbol, "15m", "LONG", 70, 60, 80, 72-i, "trend", 100, 95,
                 110, 120, 2, json.dumps({"rsi": 55+i}), "test", "scan", "cfg-1", now))
            conn.execute("""INSERT INTO signal_outcomes(signal_id,horizon_key,resolved_at,exit_ts,
                exit_price,mfe_pct,mae_pct,hit_tp1,hit_sl,timeout,r_multiple,return_pct,bars_used)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cur.lastrowid, "H1", now, now, 101, 2.0+i, -0.5-i, int(r_value>0), int(r_value<0),
                 0, r_value, r_value, 4))
        conn.commit()
    with gate_ledger.connect() as conn:
        conn.execute("""INSERT INTO gate_shadow(symbol,timeframe,ts,code,codes,source,created_at)
            VALUES(?,?,?,?,?,?,?)""", ("BTCUSDT", "15m", now, "ok", '["ok"]', "test", now))
        conn.commit()
    return db


def test_summary_funnel_cohorts_and_detail_are_evidence_based(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    summary = signal_intelligence.summary(days=1)
    assert summary["signals"] == summary["resolved"] == 2
    assert summary["win_rate_pct"] == 50.0
    assert summary["expectancy_r"] == 0.25
    assert summary["avg_mfe_pct"] == 2.5
    funnel = signal_intelligence.funnel(days=1)
    assert {row["id"]: row["count"] for row in funnel["stages"]}["gate_passed"] == 1
    rows = signal_intelligence.cohorts(days=1)
    assert {row["symbol"] for row in rows} == {"BTCUSDT", "ETHUSDT"}
    signals = signal_intelligence.signals(days=1)
    detail = signal_intelligence.detail(signals[0]["id"])
    assert detail and detail["signal"]["features"]["rsi"] in {55, 56}
    assert detail["signal"]["component_scores"]["ta"] == 70


def test_viewer_can_read_but_only_operator_can_review(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    viewer = auth.create_user("intel-viewer", "A-strong-viewer-password!", role="viewer")
    token, csrf, _ = auth.create_session(viewer, ip="testclient", ua="pytest")
    signal_id = signal_intelligence.signals(days=1)[0]["id"]
    with TestClient(main.app) as client:
        client.cookies.set("nbx_session", token)
        client.cookies.set("nbx_csrf", csrf)
        assert client.get("/api/signal-intelligence/summary?days=1").status_code == 200
        denied = client.post(f"/api/signal-intelligence/signals/{signal_id}/review",
                             json={"tag": "good-signal", "note": "ok"},
                             headers={"X-CSRF-Token": csrf})
    assert denied.status_code == 403

    operator = auth.create_user("intel-operator", "A-strong-operator-password!", role="operator")
    op_token, op_csrf, _ = auth.create_session(operator, ip="testclient", ua="pytest")
    with TestClient(main.app) as client:
        client.cookies.set("nbx_session", op_token)
        client.cookies.set("nbx_csrf", op_csrf)
        missing_csrf = client.post(f"/api/signal-intelligence/signals/{signal_id}/review",
                                   json={"tag": "good-signal", "note": "reviewed"})
        saved = client.post(f"/api/signal-intelligence/signals/{signal_id}/review",
                            json={"tag": "good-signal", "note": "reviewed"},
                            headers={"X-CSRF-Token": op_csrf})
    assert missing_csrf.status_code == 403
    assert saved.status_code == 200
    reviewed = signal_intelligence.detail(signal_id)
    assert reviewed is not None
    assert reviewed["reviews"][0]["actor_username"] == "intel-operator"


def test_statuses_are_disjoint_and_sample_threshold_is_explicit(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    with journal.connect() as conn:
        signal_id = conn.execute(
            "SELECT id FROM signal_log WHERE symbol='ETHUSDT'"
        ).fetchone()["id"]
        conn.execute(
            "UPDATE signal_log SET voided_at=?, void_reason=? WHERE id=?",
            (int(time.time()), "bad source", signal_id),
        )
        conn.commit()
    summary = signal_intelligence.summary(days=1)
    assert summary["signals"] == 2
    assert summary["resolved"] == 1
    assert summary["unresolved"] == 0
    assert summary["voided"] == 1
    assert summary["signals"] == (
        summary["resolved"] + summary["unresolved"] + summary["voided"]
    )
    assert summary["sample_minimum"] == 30
    assert summary["sample_sufficient"] is False
    assert summary["expectancy_r"] == 1.5


def test_review_allowlist_length_cap_and_foreign_keys(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    signal_id = signal_intelligence.signals(days=1)[0]["id"]
    with pytest.raises(ValueError, match="unsupported"):
        signal_intelligence.review(
            signal_id, user_id=1, username="operator", tag="invented", note="",
        )
    with pytest.raises(ValueError, match="1000"):
        signal_intelligence.review(
            signal_id, user_id=1, username="operator", tag="good-signal", note="x" * 1001,
        )
    with journal.connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_account_capabilities_name_signal_permissions(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    viewer = auth.create_user("cap-viewer", "A-strong-viewer-password!", role="viewer")
    token, csrf, _ = auth.create_session(viewer, ip="testclient", ua="pytest")
    with TestClient(main.app) as client:
        client.cookies.set("nbx_session", token)
        client.cookies.set("nbx_csrf", csrf)
        body = client.get("/api/auth/me").json()
    assert body["capabilities"]["signal_intelligence_read"] is True
    assert body["capabilities"]["signal_intelligence_review"] is False


def test_empty_state_and_filter_isolation(monkeypatch, tmp_path):
    db = tmp_path / "empty.db"
    monkeypatch.setattr(journal, "DB_PATH", db)
    monkeypatch.setattr(gate_ledger, "DB_PATH", db)
    empty = signal_intelligence.summary(days=1)
    assert empty["signals"] == empty["resolved"] == empty["unresolved"] == 0
    assert empty["win_rate_pct"] is None
    assert empty["expectancy_r"] is None
    assert empty["sample_sufficient"] is False
    assert signal_intelligence.signals(days=1) == []
    assert signal_intelligence.cohorts(days=1) == []

    _seed(tmp_path, monkeypatch)
    btc = signal_intelligence.summary(days=1, symbol="btcusdt", timeframe="15m")
    missing = signal_intelligence.summary(days=1, symbol="BTCUSDT", timeframe="4h")
    assert btc["signals"] == btc["resolved"] == 1
    assert btc["expectancy_r"] == 1.5
    assert missing["signals"] == 0


def test_review_api_rejects_unknown_fields_and_oversized_note(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    operator = auth.create_user("strict-operator", "A-strong-operator-password!", role="operator")
    token, csrf, _ = auth.create_session(operator, ip="testclient", ua="pytest")
    signal_id = signal_intelligence.signals(days=1)[0]["id"]
    with TestClient(main.app) as client:
        client.cookies.set("nbx_session", token)
        client.cookies.set("nbx_csrf", csrf)
        headers = {"X-CSRF-Token": csrf}
        extra = client.post(
            f"/api/signal-intelligence/signals/{signal_id}/review",
            json={"tag": "good-signal", "note": "ok", "role": "owner"}, headers=headers,
        )
        too_long = client.post(
            f"/api/signal-intelligence/signals/{signal_id}/review",
            json={"tag": "good-signal", "note": "x" * 1001}, headers=headers,
        )
    assert extra.status_code == 422
    assert too_long.status_code == 422
