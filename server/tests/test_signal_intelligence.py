"""Signal Intelligence v1 uses real journal outcomes and role-gated reviews."""
from __future__ import annotations

import json
import time

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
    assert detail["signal"]["contributions"]["ta"] == 70


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
