"""Recoverable, fail-closed boundary for future Nobitex spot execution.

No FastAPI route imports this module. Live mode needs an explicit interlock,
credentials and an injected sender; dry-run never invokes the sender.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from nobitex_signing import sign_request

LIVE_CONFIRMATION = "I-UNDERSTAND-THIS-PLACES-REAL-ORDERS"
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,32}$")
Sender = Callable[[str, str, bytes, dict[str, str]], dict[str, Any]]


class ExecutionError(RuntimeError):
    pass


class IdempotencyConflict(ExecutionError):
    pass


class ExecutionMode(StrEnum):
    DISABLED = "disabled"
    DRY_RUN = "dry-run"
    LIVE = "live"


@dataclass(frozen=True)
class OrderIntent:
    side: str
    src_currency: str
    dst_currency: str
    amount: str
    price: str | None
    idempotency_key: str
    execution: str = "limit"

    def validate(self) -> None:
        if self.side not in {"buy", "sell"}:
            raise ExecutionError("side must be buy or sell")
        if self.execution not in {"limit", "market"}:
            raise ExecutionError("only limit and market are enabled at this boundary")
        if self.execution == "limit" and not self.price:
            raise ExecutionError("limit orders require price")
        if not self.idempotency_key.strip():
            raise ExecutionError("idempotency_key is required")
        if not self.src_currency.isalnum() or not self.dst_currency.isalnum():
            raise ExecutionError("currency names must be alphanumeric")

    @property
    def client_order_id(self) -> str:
        digest = hashlib.sha256(self.idempotency_key.encode("utf-8")).hexdigest()[:24]
        value = f"nobi-{digest}"
        if not _CLIENT_ID_RE.fullmatch(value):
            raise ExecutionError("generated clientOrderId is invalid")
        return value

    def request_body(self) -> bytes:
        self.validate()
        body: dict[str, Any] = {
            "type": self.side,
            "execution": self.execution,
            "srcCurrency": self.src_currency.lower(),
            "dstCurrency": self.dst_currency.lower(),
            "amount": self.amount,
            "clientOrderId": self.client_order_id,
            "pro": True,
        }
        if self.price is not None:
            body["price"] = self.price
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


class ExecutionLedger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS live_execution (
                    client_order_id TEXT PRIMARY KEY,
                    intent_json TEXT NOT NULL,
                    raw_body BLOB NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    exchange_order_id TEXT,
                    response_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def reserve(self, intent: OrderIntent, raw_body: bytes) -> tuple[dict[str, Any], bool]:
        client_id = intent.client_order_id
        intent_json = json.dumps(asdict(intent), sort_keys=True, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM live_execution WHERE client_order_id=?", (client_id,)
            ).fetchone()
            if row:
                if row["intent_json"] != intent_json or bytes(row["raw_body"]) != raw_body:
                    raise IdempotencyConflict("idempotency key was already used for another payload")
                return dict(row), False
            conn.execute(
                "INSERT INTO live_execution(client_order_id,intent_json,raw_body,state) VALUES(?,?,?,?)",
                (client_id, intent_json, raw_body, "prepared"),
            )
            row = conn.execute(
                "SELECT * FROM live_execution WHERE client_order_id=?", (client_id,)
            ).fetchone()
            assert row is not None
            return dict(row), True

    def transition(
        self, client_id: str, state: str, *, response: dict[str, Any] | None = None,
        exchange_order_id: str | None = None, error_code: str | None = None,
        increment_attempt: bool = False,
    ) -> dict[str, Any]:
        response_json = json.dumps(response, sort_keys=True, separators=(",", ":")) if response is not None else None
        with self._connect() as conn:
            conn.execute(
                """UPDATE live_execution SET state=?, response_json=?, exchange_order_id=?,
                   error_code=?, attempts=attempts+?, updated_at=CURRENT_TIMESTAMP
                   WHERE client_order_id=?""",
                (state, response_json, exchange_order_id, error_code,
                 1 if increment_attempt else 0, client_id),
            )
            row = conn.execute(
                "SELECT * FROM live_execution WHERE client_order_id=?", (client_id,)
            ).fetchone()
            if row is None:
                raise ExecutionError("execution ledger record disappeared")
            return dict(row)

    def get(self, client_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM live_execution WHERE client_order_id=?", (client_id,)
            ).fetchone()
            return dict(row) if row else None


class LiveExecutionClient:
    def __init__(
        self, *, mode: ExecutionMode, ledger: ExecutionLedger,
        sender: Sender | None = None, public_key: str = "", private_key_b64: str = "",
        confirmation: str = "",
    ):
        self.mode = mode
        self.ledger = ledger
        self._sender = sender
        self._public_key = public_key
        self._private_key_b64 = private_key_b64
        if mode is ExecutionMode.LIVE:
            if confirmation != LIVE_CONFIRMATION:
                raise ExecutionError("live execution confirmation is missing")
            if not sender or not public_key or not private_key_b64:
                raise ExecutionError("live execution requires sender and API credentials")

    def place(self, intent: OrderIntent) -> dict[str, Any]:
        if self.mode is ExecutionMode.DISABLED:
            raise ExecutionError("live execution is disabled")
        raw_body = intent.request_body()
        record, created = self.ledger.reserve(intent, raw_body)
        if not created:
            return record
        client_id = intent.client_order_id
        if self.mode is ExecutionMode.DRY_RUN:
            return self.ledger.transition(client_id, "dry-run", response={"status": "dry-run"})
        assert self._sender is not None
        signed = sign_request(
            self._public_key, self._private_key_b64, "POST", "/market/orders/add", raw_body
        )
        headers = {**signed.headers, "Content-Type": "application/json"}
        try:
            response = self._sender("POST", "/market/orders/add", raw_body, headers)
        except Exception as exc:
            # The request may have reached the exchange. Store only the class,
            # never exception text that could contain a URL/header/secret.
            return self.ledger.transition(
                client_id, "unknown", error_code=type(exc).__name__, increment_attempt=True
            )
        ok = response.get("status") == "ok"
        order_value = response.get("order")
        order: dict[str, Any] = order_value if isinstance(order_value, dict) else {}
        exchange_id = str(order.get("id")) if order.get("id") is not None else None
        return self.ledger.transition(
            client_id, "accepted" if ok else "rejected", response=response,
            exchange_order_id=exchange_id, increment_attempt=True,
        )

    def reconcile(self, client_order_id: str) -> dict[str, Any]:
        record = self.ledger.get(client_order_id)
        if record is None:
            raise ExecutionError("unknown clientOrderId")
        if self.mode is not ExecutionMode.LIVE or self._sender is None:
            raise ExecutionError("reconciliation requires confirmed live mode")
        raw_body = json.dumps(
            {"clientOrderId": client_order_id}, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        signed = sign_request(
            self._public_key, self._private_key_b64, "POST", "/market/orders/status", raw_body
        )
        response = self._sender(
            "POST", "/market/orders/status", raw_body,
            {**signed.headers, "Content-Type": "application/json"},
        )
        order_value = response.get("order")
        order: dict[str, Any] = order_value if isinstance(order_value, dict) else {}
        exchange_id = str(order.get("id")) if order.get("id") is not None else None
        state = "reconciled" if response.get("status") == "ok" else "rejected"
        return self.ledger.transition(
            client_order_id, state, response=response, exchange_order_id=exchange_id
        )


__all__ = [
    "LIVE_CONFIRMATION",
    "ExecutionError",
    "ExecutionLedger",
    "ExecutionMode",
    "IdempotencyConflict",
    "LiveExecutionClient",
    "OrderIntent",
]
