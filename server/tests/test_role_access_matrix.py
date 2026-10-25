"""Role matrix for operational reads after the access-control review."""
from __future__ import annotations

from fastapi.testclient import TestClient

import auth
import main
import security


def _client_for(monkeypatch, tmp_path, role: str) -> TestClient:
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / f"{role}.db")
    auth.reset_caches()
    user = auth.create_user(f"{role}-matrix", "A-strong-matrix-password!", role=role)
    token, csrf, _ = auth.create_session(user, ip="testclient", ua="pytest")
    client = TestClient(main.app)
    client.cookies.set("nbx_session", token)
    client.cookies.set("nbx_csrf", csrf)
    return client


def test_operator_can_read_operational_audit_notify_and_live_status(monkeypatch, tmp_path):
    client = _client_for(monkeypatch, tmp_path, "operator")
    with client:
        assert client.get("/api/audit").status_code == 200
        assert client.get("/api/notify/status").status_code == 200
        assert client.get("/api/live/status").status_code == 200
        assert client.get("/api/live/evidence").status_code == 200


def test_viewer_cannot_read_operational_or_private_exchange_state(monkeypatch, tmp_path):
    client = _client_for(monkeypatch, tmp_path, "viewer")
    with client:
        for path in ("/api/audit", "/api/notify/status", "/api/live/status",
                     "/api/live/evidence", "/api/live/orders"):
            assert client.get(path).status_code == 403, path


def test_machine_token_does_not_inherit_a_human_role(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    auth.create_user("owner-matrix", "A-strong-owner-password!", role="owner")
    monkeypatch.setattr(security, "API_TOKEN", "script-token")
    headers = {"X-API-Token": "script-token"}
    with TestClient(main.app) as client:
        for path in ("/api/audit", "/api/live/status", "/api/settings"):
            assert client.get(path, headers=headers).status_code == 401, path
