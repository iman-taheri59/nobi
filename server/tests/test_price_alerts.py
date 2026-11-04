# mypy: check_untyped_defs = False
"""Contract tests for durable conditional price alerts (ADR-0049)."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import auth
import journal
import main
import price_alerts as alerts


def _obs(symbol: str, value: float, stamp: int, *, metric: str = "last"):
    return {f"{symbol}|{metric}": {
        "value": value, "observed_at": stamp, "source": "test",
    }}


def _create(conn, **overrides):
    body = {
        "symbol": "BTCIRT", "metric": "last", "operator": "above",
        "threshold": 100, "actor_user_id": 7, "now": 1,
    }
    body.update(overrides)
    return alerts.create_rule(conn, **body)


def test_rule_validation_persistence_and_idempotent_state(monkeypatch, tmp_path):
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "alerts.db")
    conn = alerts.connect()
    rule = _create(conn)
    assert rule["enabled"] is True and rule["armed"] is False
    assert rule["created_by"] == 7 and rule["last_status"] == "arming"
    rule_id = rule["id"]
    conn.close()

    conn = alerts.connect()
    try:
        loaded = alerts.get_rule(conn, rule_id)
        assert loaded is not None and loaded["threshold"] == 100
        # Repeating the same state must not disarm a rule or rewrite its clock.
        conn.execute("UPDATE price_alert_rules SET armed=1 WHERE id=?", (rule_id,))
        conn.commit()
        same = alerts.set_enabled(conn, rule_id, True, now=999)
        assert same["armed"] is True and same["updated_at"] == 1
        disabled = alerts.set_enabled(conn, rule_id, False, now=1000)
        assert disabled["enabled"] is False and disabled["armed"] is False
        enabled = alerts.set_enabled(conn, rule_id, True, now=1001)
        assert enabled["enabled"] is True and enabled["last_status"] == "arming"
    finally:
        conn.close()

    bad = (
        {"symbol": "BTC-IRT"},
        {"symbol": "BTCIRT", "metric": "premium_pct"},
        {"symbol": "USDTIRT", "metric": "premium_pct", "threshold": 101},
        {"threshold": 0},
        {"threshold": float("nan")},
        {"operator": "equals"},
    )
    conn = alerts.connect()
    try:
        for override in bad:
            with pytest.raises(ValueError):
                _create(conn, **override)
    finally:
        conn.close()


def test_crossing_is_edge_triggered_once_and_rearms_after_reset(monkeypatch, tmp_path):
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "alerts.db")
    conn = alerts.connect()
    try:
        rule = _create(conn)
        assert alerts.evaluate(conn, _obs("BTCIRT", 95, 990), now=1000) == []
        assert alerts.evaluate(conn, _obs("BTCIRT", 90, 991), now=1000) == []
        first = alerts.evaluate(conn, _obs("BTCIRT", 110, 992), now=1000)
        assert len(first) == 1
        assert first[0]["previous_value"] == 90 and first[0]["value"] == 110
        assert first[0]["delivery_sent"] is None
        assert first[0]["delivery_reason"] == "pending"
        # Staying above the threshold and replaying a timestamp cannot repeat it.
        assert alerts.evaluate(conn, _obs("BTCIRT", 120, 993), now=1000) == []
        assert alerts.evaluate(conn, _obs("BTCIRT", 130, 993), now=1000) == []
        assert alerts.evaluate(conn, _obs("BTCIRT", 90, 994), now=1000) == []
        second = alerts.evaluate(conn, _obs("BTCIRT", 101, 995), now=1000)
        assert len(second) == 1 and second[0]["id"] != first[0]["id"]
        assert len(alerts.list_events(conn)) == 2
        stored = alerts.get_rule(conn, rule["id"])
        assert stored["last_status"] == "triggered"
        assert stored["last_event_id"] == second[0]["id"]
    finally:
        conn.close()


def test_stale_outage_disarms_and_recovery_does_not_fire_late(monkeypatch, tmp_path):
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "alerts.db")
    conn = alerts.connect()
    try:
        rule = _create(conn, operator="below")
        assert alerts.evaluate(conn, _obs("BTCIRT", 105, 990), now=1000) == []
        assert len(alerts.evaluate(conn, _obs("BTCIRT", 90, 991), now=1000)) == 1
        # A stale observation disarms without creating another event.
        assert alerts.evaluate(conn, _obs("BTCIRT", 80, 900), now=1000) == []
        stale = alerts.get_rule(conn, rule["id"])
        assert stale["armed"] is False and stale["last_status"] == "stale"
        # The first fresh value after the outage is only a new baseline.
        assert alerts.evaluate(conn, _obs("BTCIRT", 80, 1001), now=1001) == []
        assert alerts.get_rule(conn, rule["id"])["last_status"] == "condition-met"
        assert alerts.evaluate(conn, _obs("BTCIRT", 110, 1002), now=1002) == []
        assert len(alerts.evaluate(conn, _obs("BTCIRT", 90, 1003), now=1003)) == 1
        # Missing, invalid, and future observations are not evidence either.
        assert alerts.evaluate(conn, {}, now=1004) == []
        assert alerts.get_rule(conn, rule["id"])["last_status"] == "no-data"
        assert alerts.evaluate(conn, _obs("BTCIRT", float("nan"), 1005), now=1005) == []
        assert alerts.evaluate(conn, _obs("BTCIRT", 1, 1010), now=1005) == []
        assert len(alerts.list_events(conn)) == 2
    finally:
        conn.close()


def test_delivery_is_bounded_and_delete_retains_event_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "alerts.db")
    conn = alerts.connect()
    try:
        rule = _create(conn)
        alerts.evaluate(conn, _obs("BTCIRT", 90, 990), now=1000)
        event = alerts.evaluate(conn, _obs("BTCIRT", 110, 991), now=1000)[0]
        assert alerts.record_delivery(conn, event["id"], {
            "sent": False, "transport": "telegram", "reason": "http-400",
            "error": "external response must not be persisted",
        })
        stored = alerts.list_events(conn)[0]
        assert stored["delivery_sent"] is False
        assert stored["delivery_reason"] == "http-400"
        assert "external response" not in str(stored)
        assert "Nobi price alert" in alerts.alert_text(stored)
        assert alerts.delete_rule(conn, rule["id"])
        assert alerts.get_rule(conn, rule["id"]) is None
        retained = alerts.list_events(conn)[0]
        assert retained["rule_id"] is None and retained["symbol"] == "BTCIRT"
        assert retained["threshold"] == 100
    finally:
        conn.close()


def test_observations_use_full_market_and_oldest_premium_leg():
    rows = [
        {"symbol": "BTCIRT", "last": 100, "closed": False},
        {"symbol": "BTCUSDT", "last": 50, "live": "ws", "live_at": 1002},
        {"symbol": "USDTIRT", "last": 2, "live": "ws", "live_at": 1003},
        {"symbol": "SMALLIRT", "last": 7},
        {"symbol": "CLOSEDIRT", "last": 8, "closed": True},
    ]
    obs = alerts.observations_from_universe(rows, 1000, 1.5)
    assert obs["BTCIRT|last"]["observed_at"] == 1000
    assert obs["BTCUSDT|last"]["source"] == "ws"
    assert obs["SMALLIRT|last"]["value"] == 7
    assert "CLOSEDIRT|last" not in obs
    assert obs["USDTIRT|premium_pct"]["observed_at"] == 1000
    assert obs["USDTIRT|premium_pct"]["value"] == 1.5
    assert "USDTIRT|premium_pct" not in alerts.observations_from_universe(
        rows[:1] + rows[2:], 1000, 1.5,
    )
    closed_reference = [dict(row) for row in rows]
    closed_reference[0]["closed"] = True
    assert "USDTIRT|premium_pct" not in alerts.observations_from_universe(
        closed_reference, 1000, 1.5,
    )


def _login(client: TestClient, username: str) -> str:
    response = client.post("/api/auth/login", json={
        "username": username, "password": "A-strong-alert-password!",
    })
    assert response.status_code == 200
    return response.cookies.get("nbx_csrf") or client.cookies.get("nbx_csrf") or ""


def test_alert_api_enforces_roles_csrf_validation_and_history(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "alerts.db")
    auth.reset_caches()
    for role in ("viewer", "operator", "owner"):
        auth.create_user(f"alert-{role}", "A-strong-alert-password!", role=role)
    anonymous = TestClient(main.app)
    assert anonymous.get("/api/alerts/rules").status_code == 401

    viewer = TestClient(main.app)
    viewer_csrf = _login(viewer, "alert-viewer")
    assert viewer.get("/api/alerts/rules").status_code == 403
    assert viewer.get("/api/alerts/events").status_code == 403
    assert viewer.post("/api/alerts/rules", headers={"X-CSRF-Token": viewer_csrf}, json={
        "symbol": "BTCIRT", "metric": "last", "operator": "above", "threshold": 100,
    }).status_code == 403

    operator = TestClient(main.app)
    csrf = _login(operator, "alert-operator")
    body = {"symbol": "btcirt", "metric": "last", "operator": "above", "threshold": 100}
    assert operator.post("/api/alerts/rules", json=body).status_code == 403
    created = operator.post(
        "/api/alerts/rules", headers={"X-CSRF-Token": csrf}, json=body,
    )
    assert created.status_code == 201, created.text
    rule = created.json()["rule"]
    assert rule["symbol"] == "BTCIRT" and rule["enabled"] is True
    assert operator.get("/api/alerts/rules").json()["n"] == 1
    assert operator.post("/api/alerts/rules", headers={"X-CSRF-Token": csrf}, json={
        **body, "unexpected": True,
    }).status_code == 422
    assert operator.post("/api/alerts/rules", headers={"X-CSRF-Token": csrf}, json={
        **body, "metric": "premium_pct",
    }).status_code == 422

    conn = alerts.connect()
    try:
        alerts.evaluate(conn, _obs("BTCIRT", 90, 990), now=1000)
        assert len(alerts.evaluate(conn, _obs("BTCIRT", 110, 991), now=1000)) == 1
    finally:
        conn.close()
    assert operator.get("/api/alerts/events").json()["n"] == 1
    disabled = operator.patch(
        f"/api/alerts/rules/{rule['id']}", headers={"X-CSRF-Token": csrf},
        json={"enabled": False},
    )
    assert disabled.status_code == 200 and disabled.json()["rule"]["enabled"] is False
    deleted = operator.delete(
        f"/api/alerts/rules/{rule['id']}", headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 200 and deleted.json()["history_retained"] is True
    assert operator.get("/api/alerts/rules").json()["n"] == 0
    history = operator.get("/api/alerts/events").json()["events"]
    assert len(history) == 1 and history[0]["rule_id"] is None


def test_scheduler_evaluates_without_network_and_records_delivery(monkeypatch, tmp_path):
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "alerts.db")
    conn = alerts.connect()
    try:
        _create(conn)
    finally:
        conn.close()
    sent = []

    async def fake_send(text):
        sent.append(text)
        return {"sent": True, "transport": "telegram"}

    monkeypatch.setattr(main.notify, "send", fake_send)
    rows = [{"symbol": "BTCIRT", "last": 90, "closed": False}]
    monkeypatch.setattr(main, "_cached", {
        "alert_universe": rows, "universe": rows, "universe_ts": 990,
        "premium_pct": None,
    })
    first = asyncio.run(main.evaluate_price_alerts(now=1000))
    assert first["triggered"] == 0 and sent == []
    rows[0]["last"] = 110
    main._cached["universe_ts"] = 991
    second = asyncio.run(main.evaluate_price_alerts(now=1000))
    assert second["triggered"] == 1 and len(sent) == 1
    assert second["deliveries"] == [{"event_id": 1, "sent": True, "reason": "sent"}]
    rows[0]["last"] = 120
    main._cached["universe_ts"] = 992
    third = asyncio.run(main.evaluate_price_alerts(now=1000))
    assert third["triggered"] == 0 and len(sent) == 1
    conn = alerts.connect()
    try:
        event = alerts.list_events(conn)[0]
        assert event["delivery_sent"] is True and event["delivery_reason"] == "sent"
    finally:
        conn.close()


def test_alert_loop_without_rules_makes_no_exchange_request(monkeypatch, tmp_path):
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "alerts.db")
    called = []

    async def forbidden_load():
        called.append(True)
        raise AssertionError("an empty alert engine must not poll the exchange")

    async def stop_after_tick(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(main, "load_universe", forbidden_load)
    monkeypatch.setattr(main.asyncio, "sleep", stop_after_tick)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main._price_alert_loop())
    assert called == []
