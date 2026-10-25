"""Selected operators may place bounded real orders; ordinary operators may not."""
from __future__ import annotations

import pytest

import auth


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    return auth.create_user("live-operator", "A-strong-operator-password!", role="operator")


def _policy(enabled: bool = True):
    return {
        "enabled": enabled, "max_order_usdt": 25.0, "max_daily_usdt": 40.0,
        "max_daily_orders": 2, "allowed_symbols": ["BTCUSDT", "ETHUSDT"],
        "allowed_execution": ["limit"],
    }


def test_disabled_or_missing_capability_fails_closed(monkeypatch, tmp_path):
    user = _fresh(monkeypatch, tmp_path)
    with pytest.raises(auth.AuthError, match="live_place"):
        auth.authorize_live_order(user, client_order_id="nobi-a", symbol="BTCUSDT",
                                  execution="limit", notional_usdt=10)
    auth.set_live_policy(user.id, _policy(False), actor="owner")
    with pytest.raises(auth.AuthError, match="live_place"):
        auth.authorize_live_order(user, client_order_id="nobi-b", symbol="BTCUSDT",
                                  execution="limit", notional_usdt=10)


def test_policy_enforces_symbol_per_order_and_daily_budgets_atomically(monkeypatch, tmp_path):
    user = _fresh(monkeypatch, tmp_path)
    auth.set_live_policy(user.id, _policy(), actor="owner")
    first = auth.authorize_live_order(user, client_order_id="nobi-1", symbol="BTCUSDT",
                                      execution="limit", notional_usdt=20)
    assert first["reserved"] is True
    repeat = auth.authorize_live_order(user, client_order_id="nobi-1", symbol="BTCUSDT",
                                       execution="limit", notional_usdt=20)
    assert repeat["idempotent"] is True
    with pytest.raises(auth.AuthError, match="سقف operator"):
        auth.authorize_live_order(user, client_order_id="nobi-big", symbol="BTCUSDT",
                                  execution="limit", notional_usdt=30)
    with pytest.raises(auth.AuthError, match="policy"):
        auth.authorize_live_order(user, client_order_id="nobi-symbol", symbol="XRPUSDT",
                                  execution="limit", notional_usdt=10)
    auth.authorize_live_order(user, client_order_id="nobi-2", symbol="ETHUSDT",
                              execution="limit", notional_usdt=20)
    with pytest.raises(auth.AuthError, match="تعداد سفارش روزانه"):
        auth.authorize_live_order(user, client_order_id="nobi-3", symbol="BTCUSDT",
                                  execution="limit", notional_usdt=1)


def test_owner_does_not_need_an_operator_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    owner = auth.create_user("live-owner", "A-strong-owner-password!", role="owner")
    assert auth.authorize_live_order(owner, client_order_id="nobi-owner", symbol="ANYUSDT",
                                     execution="limit", notional_usdt=999)["owner_override"] is True
