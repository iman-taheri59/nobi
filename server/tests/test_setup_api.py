"""First-run owner claim is one-time, token-gated and secret-free."""
from __future__ import annotations

import stat

from fastapi.testclient import TestClient

import auth
import main
import setup_api


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    monkeypatch.setattr(setup_api, "TOKEN_FILE", tmp_path / "setup.token")
    auth.reset_caches()


def test_status_creates_console_token_but_never_returns_it(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        response = client.get("/api/setup/status")
    assert response.json() == {"ok": True, "setup_required": True}
    token = setup_api.TOKEN_FILE.read_text(encoding="utf-8").strip()
    assert len(token) >= 20
    assert token not in response.text
    assert stat.S_IMODE(setup_api.TOKEN_FILE.stat().st_mode) == 0o600


def test_valid_token_creates_owner_session_and_closes_setup(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    token = setup_api.ensure_setup_token()
    assert token
    with TestClient(main.app) as client:
        response = client.post("/api/setup/claim", json={
            "setup_token": token,
            "username": "first-owner",
            "password": "A-strong-first-password-2026!",
        })
        assert response.status_code == 200
        assert response.json()["user"]["role"] == "owner"
        assert client.get("/api/auth/me").json()["user"]["role"] == "owner"
        replay = client.post("/api/setup/claim", json={
            "setup_token": token,
            "username": "second-owner",
            "password": "Another-strong-password-2026!",
        })
    assert replay.status_code == 409
    assert not setup_api.TOKEN_FILE.exists()


def test_bad_token_creates_no_user_and_is_not_echoed(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    setup_api.ensure_setup_token()
    bad = "this-is-a-wrong-setup-token-value"
    with TestClient(main.app) as client:
        response = client.post("/api/setup/claim", json={
            "setup_token": bad,
            "username": "attacker",
            "password": "A-strong-password-for-testing!",
        })
    assert response.status_code == 403
    assert bad not in response.text
    assert not auth.any_users()
