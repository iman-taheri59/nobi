"""Private control routes exist, but placement is deliberately absent."""
from __future__ import annotations

from fastapi.testclient import TestClient

import auth
import live_api
import main
from live_execution import ExecutionLedger, ExecutionMode, LiveExecutionClient


def test_openapi_has_controls_but_no_live_place(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    paths = main.app.openapi()["paths"]
    assert "/api/live/status" in paths
    assert "/api/live/orders" in paths
    assert "/api/live/cancel" in paths
    assert "/api/live/reconcile" in paths
    assert "/api/live/place" not in paths


def test_status_is_secret_free_and_placement_is_not_exposed(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    monkeypatch.setattr(
        live_api, "CLIENT",
        LiveExecutionClient(
            mode=ExecutionMode.DISABLED,
            ledger=ExecutionLedger(tmp_path / "live.db"),
        ),
    )
    monkeypatch.setattr(live_api, "CONFIG_PROBLEM", None)
    with TestClient(main.app) as client:
        response = client.get("/api/live/status")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "ok": True,
        "mode": "disabled",
        "configured": False,
        "configuration_problem": None,
        "placement_exposed": False,
    }
    text = response.text.lower()
    assert "secret" not in text
    assert "private" not in text


def test_dry_run_uses_server_preflight_and_hides_raw_record(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    ledger = ExecutionLedger(tmp_path / "dry.db")
    monkeypatch.setattr(
        live_api, "CLIENT", LiveExecutionClient(mode=ExecutionMode.DRY_RUN, ledger=ledger)
    )

    async def preflight(_payload, _request):
        return {
            "ok": True, "symbol": "BTCUSDT", "side": "buy",
            "entry": 70000, "qty": 0.01,
            "projection": {"blocked": False, "blocked_by": None},
        }

    monkeypatch.setattr(live_api, "_PREFLIGHT", preflight)
    with TestClient(main.app) as client:
        response = client.post("/api/live/dry-run", json={"idempotency_key": "ui-action-1"})
    assert response.status_code == 200
    body = response.json()
    assert body["execution"]["state"] == "dry-run"
    assert "raw_body" not in response.text
    assert "intent_json" not in response.text
    assert len(list(ledger._connect().execute("SELECT 1 FROM live_execution"))) == 1


def test_blocked_preflight_creates_no_intent(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    ledger = ExecutionLedger(tmp_path / "dry.db")
    monkeypatch.setattr(
        live_api, "CLIENT", LiveExecutionClient(mode=ExecutionMode.DRY_RUN, ledger=ledger)
    )

    async def blocked(_payload, _request):
        return {"ok": True, "projection": {"blocked": True, "blocked_by": "heat-cap"}}

    monkeypatch.setattr(live_api, "_PREFLIGHT", blocked)
    with TestClient(main.app) as client:
        response = client.post("/api/live/dry-run", json={"idempotency_key": "blocked"})
    assert response.status_code == 409
    with ledger._connect() as conn:
        assert conn.execute("SELECT count(*) FROM live_execution").fetchone()[0] == 0


def test_disabled_control_fails_before_exchange_network(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    monkeypatch.setattr(
        live_api, "CLIENT",
        LiveExecutionClient(
            mode=ExecutionMode.DISABLED,
            ledger=ExecutionLedger(tmp_path / "live.db"),
        ),
    )
    with TestClient(main.app) as client:
        response = client.get("/api/live/orders?status=open")
    assert response.status_code == 409
    assert "confirmed live mode" in response.json()["detail"]
