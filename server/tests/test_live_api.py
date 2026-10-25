"""Private live controls and owner-only, promotion-gated placement."""
from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from test_install_api import _owner_client

import auth
import live_api
import main
from live_execution import LIVE_CONFIRMATION, ExecutionLedger, ExecutionMode, LiveExecutionClient


def _secret() -> str:
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                            serialization.NoEncryption())
    return base64.urlsafe_b64encode(raw).decode()


def test_openapi_exposes_promotion_gated_live_place(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    paths = main.app.openapi()["paths"]
    assert "/api/live/status" in paths
    assert "/api/live/orders" in paths
    assert "/api/live/cancel" in paths
    assert "/api/live/reconcile" in paths
    assert "/api/live/place" in paths


def test_status_is_secret_free_and_placement_is_not_exposed(monkeypatch, tmp_path):
    client, _csrf = _owner_client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        live_api, "CLIENT",
        LiveExecutionClient(
            mode=ExecutionMode.DISABLED,
            ledger=ExecutionLedger(tmp_path / "live.db"),
        ),
    )
    monkeypatch.setattr(live_api, "CONFIG_PROBLEM", None)
    with client:
        response = client.get("/api/live/status")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "ok": True,
        "mode": "disabled",
        "configured": False,
        "configuration_problem": None,
        "placement_exposed": True,
        "placement_enabled": False,
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
    client, _csrf = _owner_client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        live_api, "CLIENT",
        LiveExecutionClient(
            mode=ExecutionMode.DISABLED,
            ledger=ExecutionLedger(tmp_path / "live.db"),
        ),
    )
    with client:
        response = client.get("/api/live/orders?status=open")
    assert response.status_code == 409
    assert "confirmed live mode" in response.json()["detail"]


def test_live_place_requires_owner_csrf_and_promotion_evidence(monkeypatch, tmp_path):
    calls = []
    ledger = ExecutionLedger(tmp_path / "live-place.db")
    def sender(method, path, body, headers):
        calls.append(path)
        return {"status": "ok", "order": {"id": 42}}

    live = LiveExecutionClient(
        mode=ExecutionMode.LIVE, ledger=ledger, sender=sender,
        public_key="public", private_key_b64=_secret(), confirmation=LIVE_CONFIRMATION,
    )
    monkeypatch.setattr(live_api, "CLIENT", live)
    monkeypatch.setattr(live_api, "CONFIG_PROBLEM", None)

    async def preflight(_payload, _request):
        return {"ok": True, "symbol": "BTCUSDT", "side": "buy", "entry": 70000,
                "qty": 0.001, "notional_usdt": 70.0, "projection": {"blocked": False}}

    monkeypatch.setattr(live_api, "_PREFLIGHT", preflight)
    client, csrf = _owner_client(monkeypatch, tmp_path)
    payload = {"idempotency_key": "real-owner-action-1",
               "confirmation": live_api.PLACE_CONFIRMATION}
    with client:
        blocked = client.post("/api/live/place", json=payload, headers={"X-CSRF-Token": csrf})
        monkeypatch.setattr(ledger, "promotion_evidence", lambda: {
            "ready": True, "samples": 30, "days": 5, "ambiguous": 0,
            "states": {"dry-run": 30}, "required": {"samples": 30, "days": 5, "ambiguous": 0},
            "reasons": [], "applied": False,
        })
        placed = client.post("/api/live/place", json=payload, headers={"X-CSRF-Token": csrf})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["error"] == "promotion-gate-blocked"
    assert placed.status_code == 200
    assert placed.json()["execution"]["state"] == "accepted"
    assert calls == ["/market/orders/add"]


def test_selected_operator_can_place_within_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    user = auth.create_user("selected-live", "A-strong-operator-password!", role="operator")
    auth.set_live_policy(user.id, {
        "enabled": True, "max_order_usdt": 25, "max_daily_usdt": 100,
        "max_daily_orders": 5, "allowed_symbols": ["BTCUSDT"],
        "allowed_execution": ["limit"],
    }, actor="owner")
    token, csrf, _ = auth.create_session(user, ip="testclient", ua="pytest")
    ledger = ExecutionLedger(tmp_path / "operator-live.db")
    sent = []

    def sender(method, path, body, headers):
        sent.append(path)
        return {"status": "ok", "order": {"id": 77}}

    monkeypatch.setattr(live_api, "CLIENT", LiveExecutionClient(
        mode=ExecutionMode.LIVE, ledger=ledger, sender=sender, public_key="public",
        private_key_b64=_secret(), confirmation=LIVE_CONFIRMATION))
    monkeypatch.setattr(live_api, "CONFIG_PROBLEM", None)
    monkeypatch.setattr(ledger, "promotion_evidence", lambda: {
        "ready": True, "samples": 30, "days": 5, "ambiguous": 0,
        "states": {"dry-run": 30}, "required": {}, "reasons": [], "applied": False,
    })

    async def preflight(_payload, _request):
        return {"ok": True, "symbol": "BTCUSDT", "side": "buy", "entry": 10000,
                "qty": 0.001, "notional_usdt": 10.0, "projection": {"blocked": False}}

    monkeypatch.setattr(live_api, "_PREFLIGHT", preflight)
    with TestClient(main.app) as client:
        client.cookies.set("nbx_session", token)
        client.cookies.set("nbx_csrf", csrf)
        response = client.post("/api/live/place", json={
            "idempotency_key": "selected-operator-order",
            "confirmation": live_api.PLACE_CONFIRMATION,
        }, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json()["authorization"]["role"] == "operator"
    assert sent == ["/market/orders/add"]


def test_live_place_rejects_missing_explicit_confirmation(monkeypatch, tmp_path):
    ledger = ExecutionLedger(tmp_path / "live-confirm.db")
    monkeypatch.setattr(live_api, "CLIENT", LiveExecutionClient(
        mode=ExecutionMode.LIVE, ledger=ledger,
        sender=lambda *_: {"status": "ok"}, public_key="public",
        private_key_b64=_secret(), confirmation=LIVE_CONFIRMATION))
    monkeypatch.setattr(live_api, "CONFIG_PROBLEM", None)
    client, csrf = _owner_client(monkeypatch, tmp_path)
    with client:
        response = client.post("/api/live/place", json={"idempotency_key": "x"},
                               headers={"X-CSRF-Token": csrf})
    assert response.status_code == 422
