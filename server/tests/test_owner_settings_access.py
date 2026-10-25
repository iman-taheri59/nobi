"""Configuration, secret-presence and cache internals are owner-only."""
from __future__ import annotations

from fastapi.testclient import TestClient
from test_install_api import _owner_client

import auth
import main
import security


def _role_client(monkeypatch, tmp_path, role: str) -> tuple[TestClient, str]:
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / f"{role}.db")
    auth.reset_caches()
    user = auth.create_user(f"{role}-settings", "A-strong-settings-password!", role=role)
    token, csrf, _ = auth.create_session(user, ip="testclient", ua="pytest")
    client = TestClient(main.app)
    client.cookies.set("nbx_session", token)
    client.cookies.set("nbx_csrf", csrf)
    return client, csrf


def test_owner_can_read_settings_and_cache_internals(monkeypatch, tmp_path):
    client, csrf = _owner_client(monkeypatch, tmp_path)
    with client:
        assert client.get("/api/settings").status_code == 200
        assert client.get("/api/cache/stats").status_code == 200
        # The request reaches the handler (configuration may legitimately be absent).
        assert client.post("/api/settings/telegram/test",
                           headers={"X-CSRF-Token": csrf}).status_code == 200


def test_operator_and_viewer_cannot_read_owner_configuration(monkeypatch, tmp_path):
    for role in ("operator", "viewer"):
        client, csrf = _role_client(monkeypatch, tmp_path, role)
        with client:
            assert client.get("/api/settings").status_code == 403
            assert client.get("/api/cache/stats").status_code == 403
            assert client.post("/api/settings/telegram/test",
                               headers={"X-CSRF-Token": csrf}).status_code == 403


def test_bearer_token_cannot_read_owner_configuration(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    auth.create_user("owner-settings", "A-strong-owner-password!", role="owner")
    monkeypatch.setattr(security, "API_TOKEN", "script-token")
    headers = {"X-API-Token": "script-token"}
    with TestClient(main.app) as client:
        assert client.get("/api/settings", headers=headers).status_code == 401
        assert client.get("/api/cache/stats", headers=headers).status_code == 401


def test_owner_settings_mutation_requires_csrf(monkeypatch, tmp_path):
    client, _csrf = _owner_client(monkeypatch, tmp_path)
    with client:
        assert client.post("/api/settings/telegram/test").status_code == 403
