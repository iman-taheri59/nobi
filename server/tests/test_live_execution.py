"""B2 execution boundary: no blind retries and no accidental live mode."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

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


def test_list_orders_signs_query_and_sends_bodyless_get(tmp_path):
    seen: dict[str, Any] = {}

    def sender(method, path, body, headers):
        seen.update(method=method, path=path, body=body, headers=headers)
        return {"status": "ok", "orders": []}

    client = _live(tmp_path, sender)
    result = client.list_orders(status="open")
    assert result["orders"] == []
    assert seen["method"] == "GET"
    assert seen["path"] == "/market/orders/list?status=open"
    assert seen["body"] == b""
    assert "Nobitex-Signature" in seen["headers"]


def test_cancel_unknown_id_is_refused_before_network(tmp_path):
    calls = []

    def sender(*args):
        calls.append(args)
        return {}

    client = _live(tmp_path, sender)
    with pytest.raises(ExecutionError, match="unknown clientOrderId"):
        client.cancel("nobi-not-recorded")
    assert calls == []


def test_cancel_timeout_is_not_blindly_retried(tmp_path):
    calls = []

    def sender(_method, path, _body, _headers):
        calls.append(path)
        if path.endswith("/add"):
            return {"status": "ok", "order": {"id": 7}}
        raise TimeoutError("do not persist this text")

    client = _live(tmp_path, sender)
    placed = client.place(_intent())
    first = client.cancel(placed["client_order_id"])
    second = client.cancel(placed["client_order_id"])
    assert first["state"] == second["state"] == "cancel-unknown"
    assert first["error_code"] == "cancel:TimeoutError"
    assert "persist" not in str(first)
    assert calls.count("/market/orders/update-status") == 1


def test_cancel_final_response_is_durable(tmp_path):
    def sender(_method, path, _body, _headers):
        if path.endswith("/add"):
            return {"status": "ok", "order": {"id": 7}}
        return {"status": "ok", "order": {"id": 7, "status": "Canceled"}}

    client = _live(tmp_path, sender)
    placed = client.place(_intent())
    canceled = client.cancel(placed["client_order_id"])
    assert canceled["state"] == "canceled"
    assert client.ledger.get(placed["client_order_id"])["state"] == "canceled"


def test_promotion_evidence_requires_samples_days_and_no_unknown(tmp_path):
    ledger = ExecutionLedger(tmp_path / "evidence.db")
    client = LiveExecutionClient(mode=ExecutionMode.DRY_RUN, ledger=ledger)
    for day in range(5):
        for item in range(6):
            record = client.place(_intent(key=f"day-{day}-item-{item}"))
            with ledger._connect() as conn:
                conn.execute(
                    "UPDATE live_execution SET created_at=? WHERE client_order_id=?",
                    (f"2026-10-{20 + day:02d} 12:00:00", record["client_order_id"]),
                )
    evidence = ledger.promotion_evidence()
    assert evidence["ready"] is True
    assert evidence["samples"] == 30
    assert evidence["days"] == 5
    first = client.place(_intent(key="ambiguous"))
    ledger.transition(first["client_order_id"], "unknown")
    evidence = ledger.promotion_evidence()
    assert evidence["ready"] is False
    assert "ambiguous-outcomes" in evidence["reasons"]
    assert evidence["applied"] is False


def test_empty_promotion_evidence_is_an_explicit_refusal(tmp_path):
    evidence = ExecutionLedger(tmp_path / "empty.db").promotion_evidence()
    assert evidence["samples"] == evidence["days"] == 0
    assert evidence["ready"] is False
    assert evidence["reasons"] == ["insufficient-samples", "insufficient-days"]


def test_client_order_id_matches_exchange_contract():
    value = _intent().client_order_id
    assert len(value) <= 32
    assert value.startswith("nobi-")
    assert value.replace("-", "").isalnum()
