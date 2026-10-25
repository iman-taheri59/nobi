"""Every signed-in user can inspect and secure only their own account."""
from __future__ import annotations

from fastapi.testclient import TestClient

import auth
import main


def _signed_in(monkeypatch, tmp_path, role: str = "operator"):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    user = auth.create_user("account-user", "A-strong-account-password!", role=role)
    token, csrf, _ = auth.create_session(user, ip="10.0.0.1", ua="Browser One")
    auth.create_session(user, ip="10.0.0.2", ua="Browser Two")
    client = TestClient(main.app)
    client.cookies.set("nbx_session", token)
    client.cookies.set("nbx_csrf", csrf)
    return client, user, csrf


def test_me_returns_backend_capabilities_policy_usage_and_preferences(monkeypatch, tmp_path):
    client, user, _csrf = _signed_in(monkeypatch, tmp_path)
    auth.set_live_policy(user.id, {
        "enabled": True, "max_order_usdt": 25, "max_daily_usdt": 100,
        "max_daily_orders": 5, "allowed_symbols": ["BTCUSDT"],
        "allowed_execution": ["limit"],
    }, actor="owner")
    with client:
        body = client.get("/api/auth/me").json()
    assert body["capabilities"]["live_place"] is True
    assert body["capabilities"]["manage_users"] is False
    assert body["live_policy"]["max_order_usdt"] == 25
    assert body["live_usage"] == {"orders": 0, "notional_usdt": 0.0}
    assert body["preferences"]["language"] == "fa"


def test_user_can_list_and_revoke_only_own_sessions(monkeypatch, tmp_path):
    client, _user, csrf = _signed_in(monkeypatch, tmp_path)
    with client:
        sessions = client.get("/api/auth/me/sessions").json()["sessions"]
        other = next(row for row in sessions if not row["current"])
        removed = client.delete(f"/api/auth/me/sessions/{other['id']}",
                                headers={"X-CSRF-Token": csrf})
        remaining = client.get("/api/auth/me/sessions").json()["sessions"]
    assert removed.status_code == 200 and removed.json()["ok"] is True
    assert len(remaining) == 1 and remaining[0]["current"] is True


def test_preferences_are_validated_and_csrf_protected(monkeypatch, tmp_path):
    client, _user, csrf = _signed_in(monkeypatch, tmp_path, role="viewer")
    payload = {"language": "en", "timezone": "Europe/Berlin", "theme": "dark",
               "default_page": "overview"}
    with client:
        assert client.patch("/api/auth/me/preferences", json=payload).status_code == 403
        saved = client.patch("/api/auth/me/preferences", json=payload,
                             headers={"X-CSRF-Token": csrf})
        bad = client.patch("/api/auth/me/preferences", json={**payload, "timezone": "../../etc"},
                           headers={"X-CSRF-Token": csrf})
    assert saved.status_code == 200
    assert saved.json()["preferences"]["timezone"] == "Europe/Berlin"
    assert bad.status_code == 422


def test_personal_activity_never_accepts_another_user_id(monkeypatch, tmp_path):
    client, user, _csrf = _signed_in(monkeypatch, tmp_path)
    other = auth.create_user("other-user", "A-strong-other-password!", role="viewer")
    auth.record_activity(user, "mine")
    auth.record_activity(other, "theirs")
    with client:
        events = client.get(f"/api/auth/me/activity?user_id={other.id}").json()["events"]
    assert [event["action"] for event in events] == ["mine"]
