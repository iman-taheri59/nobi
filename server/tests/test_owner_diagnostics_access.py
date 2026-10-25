"""System troubleshooting data and controls are owner-only."""
from __future__ import annotations

from fastapi.testclient import TestClient
from test_install_api import _owner_client

import auth
import install_api
import main
import security


def test_owner_can_read_errors_and_diagnostics(monkeypatch, tmp_path):
    client, csrf = _owner_client(monkeypatch, tmp_path)
    with client:
        errors = client.get("/api/errors?limit=5")
        diagnostics = client.get("/api/diagnostics")
        selftest = client.post("/api/errors/selftest", headers={"X-CSRF-Token": csrf})
    assert errors.status_code == diagnostics.status_code == selftest.status_code == 200


def test_operator_cannot_open_troubleshooting_apis(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    monkeypatch.setattr(install_api, "MANAGED_ENV", tmp_path / "managed.env")
    auth.reset_caches()
    user = auth.create_user("operator-diag", "A-strong-operator-password!", role="operator")
    token, csrf, _ = auth.create_session(user, ip="testclient", ua="pytest")
    with TestClient(main.app) as client:
        client.cookies.set("nbx_session", token)
        client.cookies.set("nbx_csrf", csrf)
        assert client.get("/api/errors").status_code == 403
        assert client.get("/api/diagnostics").status_code == 403
        assert client.get("/api/devsuite").status_code == 403
        assert client.post("/api/errors/clear", headers={"X-CSRF-Token": csrf}).status_code == 403


def test_bearer_token_cannot_read_troubleshooting_data(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    auth.create_user("owner-diag", "A-strong-owner-password!", role="owner")
    monkeypatch.setattr(security, "API_TOKEN", "script-token")
    headers = {"X-API-Token": "script-token"}
    with TestClient(main.app) as client:
        assert client.get("/api/errors", headers=headers).status_code == 401
        assert client.get("/api/diagnostics", headers=headers).status_code == 401
