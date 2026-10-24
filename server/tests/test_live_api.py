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
