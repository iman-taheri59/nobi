"""Owner installation settings are allowlisted, validated and secret-free."""
from __future__ import annotations

from fastapi.testclient import TestClient

import auth
import install_api
import main


def _owner_client(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    monkeypatch.setattr(install_api, "MANAGED_ENV", tmp_path / "managed.env")
    auth.reset_caches()
    user = auth.create_user("owner-one", "A-strong-owner-password!", role="owner")
    token, csrf, _ = auth.create_session(user, ip="testclient", ua="pytest")
    client = TestClient(main.app)
    client.cookies.set("nbx_session", token)
    client.cookies.set("nbx_csrf", csrf)
    return client, csrf


def test_owner_can_probe_and_save_allowlisted_paths(monkeypatch, tmp_path):
    client, csrf = _owner_client(monkeypatch, tmp_path)
    data = tmp_path / "data"
    payload = {
        "NBX_JOURNAL_DB": str(data / "journal.db"),
        "NBX_AUTH_DB": str(data / "auth.db"),
        "NBX_LIVE_DB": str(data / "live.db"),
        "NBX_CANDLE_CACHE": str(data / "candles"),
        "NBX_SCAN_LOOP_S": 900,
        "NBX_SCAN_LOOP_TF": "15m",
        "NBX_LIVE_MODE": "dry-run",
    }
    with client:
        probe = client.post("/api/install/probe", json=payload, headers={"X-CSRF-Token": csrf})
        saved = client.post("/api/install/settings", json=payload, headers={"X-CSRF-Token": csrf})
    assert probe.status_code == saved.status_code == 200
    assert saved.json()["restart_required"] is True
    content = install_api.MANAGED_ENV.read_text(encoding="utf-8")
    assert "NBX_LIVE_MODE=dry-run" in content
    assert "SECRET" not in content
    assert install_api.MANAGED_ENV.stat().st_mode & 0o777 == 0o600


def test_unknown_or_relative_settings_fail_closed(monkeypatch, tmp_path):
    client, csrf = _owner_client(monkeypatch, tmp_path)
    with client:
        unknown = client.post("/api/install/settings", json={"COMMAND": "rm -rf /"},
                              headers={"X-CSRF-Token": csrf})
        relative = client.post("/api/install/settings", json={"NBX_AUTH_DB": "relative.db"},
                               headers={"X-CSRF-Token": csrf})
    assert unknown.status_code == relative.status_code == 422
    assert not install_api.MANAGED_ENV.exists()


def test_non_owner_cannot_read_installation_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    user = auth.create_user("operator-one", "A-strong-operator-password!", role="operator")
    token, _csrf, _ = auth.create_session(user, ip="testclient", ua="pytest")
    with TestClient(main.app) as client:
        client.cookies.set("nbx_session", token)
        response = client.get("/api/install/settings")
    assert response.status_code == 403
    with TestClient(main.app) as client:
        client.cookies.set("nbx_session", token)
        suite = client.get("/api/devsuite")
    assert suite.status_code == 403


def test_owner_can_see_fixed_operations_allowlist(monkeypatch, tmp_path):
    client, _csrf = _owner_client(monkeypatch, tmp_path)
    with client:
        response = client.get("/api/devsuite")
    assert response.status_code == 200
    names = {gate["name"] for gate in response.json()["gates"]}
    assert {"pytest", "eslint", "build", "deploy-check"} <= names
