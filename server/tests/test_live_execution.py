"""B2 execution boundary: no blind retries and no accidental live mode."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from live_execution import (
    LIVE_CONFIRMATION,
    ExecutionError,
    ExecutionLedger,
    ExecutionMode,
    IdempotencyConflict,
    LiveExecutionClient,
    OrderIntent,
)


def _secret() -> str:
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    return base64.urlsafe_b64encode(raw).decode()


def _intent(key: str = "operator-action-1", amount: str = "0.01") -> OrderIntent:
    return OrderIntent("buy", "btc", "usdt", amount, "70000", key)


def _live(tmp_path: Path, sender):
    return LiveExecutionClient(
        mode=ExecutionMode.LIVE,
        ledger=ExecutionLedger(tmp_path / "live.db"),
        sender=sender,
        public_key="public",
        private_key_b64=_secret(),
        confirmation=LIVE_CONFIRMATION,
    )


def test_disabled_refuses_before_creating_a_record(tmp_path):
    ledger = ExecutionLedger(tmp_path / "disabled.db")
    client = LiveExecutionClient(mode=ExecutionMode.DISABLED, ledger=ledger)
    with pytest.raises(ExecutionError, match="disabled"):
        client.place(_intent())
    assert ledger.get(_intent().client_order_id) is None


def test_dry_run_never_calls_sender_and_is_idempotent(tmp_path):
    calls = []
    ledger = ExecutionLedger(tmp_path / "dry.db")
    def sender(*args):
        calls.append(args)
        return {"status": "ok"}

    client = LiveExecutionClient(
        mode=ExecutionMode.DRY_RUN, ledger=ledger, sender=sender,
    )
    first = client.place(_intent())
    second = client.place(_intent())
    assert first["state"] == second["state"] == "dry-run"
    assert first["client_order_id"] == second["client_order_id"]
    assert first["attempts"] == 0
    assert calls == []


def test_same_key_with_changed_payload_fails_closed(tmp_path):
    client = LiveExecutionClient(
        mode=ExecutionMode.DRY_RUN, ledger=ExecutionLedger(tmp_path / "conflict.db")
    )
    client.place(_intent(amount="0.01"))
    with pytest.raises(IdempotencyConflict):
        client.place(_intent(amount="0.02"))


def test_unconfirmed_live_mode_cannot_be_constructed(tmp_path):
    with pytest.raises(ExecutionError, match="confirmation"):
        LiveExecutionClient(
            mode=ExecutionMode.LIVE, ledger=ExecutionLedger(tmp_path / "live.db"),
            sender=lambda *_: {}, public_key="key", private_key_b64=_secret(),
        )


def test_timeout_becomes_unknown_and_repeat_does_not_send(tmp_path):
    calls = 0

    def timeout(*_args):
        nonlocal calls
        calls += 1
        raise TimeoutError("could include sensitive transport details")

    client = _live(tmp_path, timeout)
    first = client.place(_intent())
    second = client.place(_intent())
    assert first["state"] == second["state"] == "unknown"
    assert first["attempts"] == 1
    assert first["error_code"] == "TimeoutError"
    assert "sensitive" not in str(first)
    assert calls == 1


def test_reconciliation_resolves_unknown_by_client_order_id(tmp_path):
    calls = []

    def sender(_method, path, _body, _headers):
        calls.append(path)
        if path.endswith("/add"):
            raise TimeoutError
        return {"status": "ok", "order": {"id": 42, "status": "Active"}}

    client = _live(tmp_path, sender)
    unknown = client.place(_intent())
    resolved = client.reconcile(unknown["client_order_id"])
    assert resolved["state"] == "reconciled"
    assert resolved["exchange_order_id"] == "42"
    assert resolved["attempts"] == 1
    assert calls == ["/market/orders/add", "/market/orders/status"]


def test_client_order_id_matches_exchange_contract():
    value = _intent().client_order_id
    assert len(value) <= 32
    assert value.startswith("nobi-")
    assert value.replace("-", "").isalnum()
