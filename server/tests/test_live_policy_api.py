"""Only owners can grant or revoke an operator's bounded live-place capability."""
from __future__ import annotations

from test_install_api import _owner_client

import auth


def test_owner_can_set_operator_live_policy(monkeypatch, tmp_path):
    client, csrf = _owner_client(monkeypatch, tmp_path)
    operator = auth.create_user("selected-operator", "A-strong-operator-password!", role="operator")
    payload = {
        "enabled": True, "max_order_usdt": 25, "max_daily_usdt": 100,
        "max_daily_orders": 5, "allowed_symbols": ["btcusdt", "ETHUSDT"],
        "allowed_execution": ["limit"],
    }
    with client:
        saved = client.patch(f"/api/auth/users/{operator.id}/live-policy", json=payload,
                             headers={"X-CSRF-Token": csrf})
        listed = client.get("/api/auth/users")
    assert saved.status_code == 200
    assert saved.json()["policy"]["allowed_symbols"] == ["BTCUSDT", "ETHUSDT"]
    row = next(user for user in listed.json()["users"] if user["id"] == operator.id)
    assert row["live_policy"]["enabled"] is True


def test_policy_rejects_owner_target_market_orders_and_bad_budget(monkeypatch, tmp_path):
    client, csrf = _owner_client(monkeypatch, tmp_path)
    owner_id = client.get("/api/auth/me").json()["user"]["id"]
    operator = auth.create_user("policy-operator", "A-strong-operator-password!", role="operator")
    base = {"enabled": True, "max_order_usdt": 25, "max_daily_usdt": 100,
            "max_daily_orders": 5, "allowed_symbols": ["BTCUSDT"],
            "allowed_execution": ["limit"]}
    with client:
        owner_target = client.patch(f"/api/auth/users/{owner_id}/live-policy", json=base,
                                    headers={"X-CSRF-Token": csrf})
        market = client.patch(f"/api/auth/users/{operator.id}/live-policy",
                              json={**base, "allowed_execution": ["market"]},
                              headers={"X-CSRF-Token": csrf})
        budget = client.patch(f"/api/auth/users/{operator.id}/live-policy",
                              json={**base, "max_daily_usdt": 10},
                              headers={"X-CSRF-Token": csrf})
    assert owner_target.status_code == 409
    assert market.status_code == budget.status_code == 422
