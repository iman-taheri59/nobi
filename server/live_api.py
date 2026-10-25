"""Private live controls and owner-only promotion-gated order placement."""
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

import auth
import identity
import install_api
from live_execution import (
    ExecutionError,
    ExecutionLedger,
    ExecutionMode,
    LiveExecutionClient,
    OrderIntent,
)
from security import require_client


def _sender(base_url: str):
    def send(method: str, path: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        response = httpx.request(
            method, f"{base_url.rstrip('/')}{path}", content=body, headers=headers, timeout=20
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ExecutionError("exchange returned a non-object response")
        return value

    return send


def configured_client() -> tuple[LiveExecutionClient, str | None]:
    raw_mode = os.getenv("NBX_LIVE_MODE", "disabled").strip().lower()
    problem: str | None
    try:
        mode = ExecutionMode(raw_mode)
    except ValueError:
        mode = ExecutionMode.DISABLED
        problem = "NBX_LIVE_MODE is invalid; execution was disabled"
    else:
        problem = None
    ledger = ExecutionLedger(
        Path(os.getenv("NBX_LIVE_DB") or Path(__file__).with_name("live_execution.db"))
    )
    kwargs: dict[str, Any] = {"mode": mode, "ledger": ledger}
    if mode is ExecutionMode.LIVE:
        kwargs.update(
            sender=_sender(os.getenv("NBX_BASE_URL", "https://apiv2.nobitex.ir")),
            public_key=os.getenv("NOBITEX_API_KEY", ""),
            private_key_b64=os.getenv("NOBITEX_API_SECRET", ""),
            confirmation=os.getenv("NBX_LIVE_CONFIRM", ""),
        )
    try:
        return LiveExecutionClient(**kwargs), problem
    except ExecutionError as exc:
        # Do not echo environment values. Constructor errors are deliberately
        # secret-free; public market data must remain available.
        return LiveExecutionClient(mode=ExecutionMode.DISABLED, ledger=ledger), str(exc)


CLIENT, CONFIG_PROBLEM = configured_client()
router = APIRouter(prefix="/api/live", tags=["live-control"])
Preflight = Callable[[dict[str, Any], Request], Awaitable[dict[str, Any]]]
Audit = Callable[..., Any]
_PREFLIGHT: Preflight | None = None
_AUDIT: Audit | None = None
PLACE_CONFIRMATION = "PLACE-REAL-ORDER"


def configure(*, preflight: Preflight, audit: Audit) -> None:
    global _PREFLIGHT, _AUDIT
    _PREFLIGHT, _AUDIT = preflight, audit


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record.get(key) for key in (
        "client_order_id", "state", "attempts", "exchange_order_id",
        "error_code", "created_at", "updated_at",
    )}


def _call(action):
    try:
        return action()
    except ExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"exchange transport: {type(exc).__name__}") from exc


def _intent(result: dict[str, Any], key: str) -> tuple[OrderIntent, str]:
    symbol = str(result.get("symbol") or "").upper()
    quote = "IRT" if symbol.endswith("IRT") else "USDT" if symbol.endswith("USDT") else ""
    if not quote:
        raise HTTPException(status_code=422, detail="unsupported market symbol")
    src = symbol[:-len(quote)]
    dst = "rls" if quote == "IRT" else "usdt"
    return OrderIntent(
        side=str(result.get("side") or "buy"), src_currency=src,
        dst_currency=dst, amount=format(float(result["qty"]), ".12g"),
        price=format(float(result["entry"]) * (10 if quote == "IRT" else 1), ".12g"),
        idempotency_key=key,
    ), symbol


async def _checked_intent(
    body: dict[str, Any], request: Request,
) -> tuple[OrderIntent, str, dict[str, Any], float]:
    if _PREFLIGHT is None:
        raise HTTPException(status_code=503, detail="server preflight is not configured")
    key = str(body.get("idempotency_key") or "")
    if not key:
        raise HTTPException(status_code=422, detail="idempotency_key is required")
    result = await _PREFLIGHT(body, request)
    projection_value = result.get("projection")
    projection: dict[str, Any] = projection_value if isinstance(projection_value, dict) else {}
    if not result.get("ok") or projection.get("blocked"):
        raise HTTPException(status_code=409, detail={
            "error": "preflight-blocked", "blocked_by": projection.get("blocked_by"),
        })
    intent, symbol = _intent(result, key)
    notional_usdt = float(result.get("notional_usdt") or 0)
    return intent, symbol, projection, notional_usdt


@router.get("/status", dependencies=[Depends(install_api.require_operator)])
def status():
    return {
        "ok": True,
        "mode": CLIENT.mode.value,
        "configured": CLIENT.mode is ExecutionMode.LIVE and CONFIG_PROBLEM is None,
        "configuration_problem": CONFIG_PROBLEM,
        "placement_exposed": True,
        "placement_enabled": CLIENT.mode is ExecutionMode.LIVE and CONFIG_PROBLEM is None,
    }


@router.post("/dry-run", dependencies=[Depends(require_client)])
async def dry_run(payload: dict, request: Request):
    if CLIENT.mode is not ExecutionMode.DRY_RUN:
        raise HTTPException(status_code=409, detail="dry-run route requires NBX_LIVE_MODE=dry-run")
    body = payload or {}
    intent, symbol, projection, _notional_usdt = await _checked_intent(body, request)
    record = CLIENT.place(intent)
    if _AUDIT is not None:
        _AUDIT("live.dry-run", detail={
            "client_order_id": intent.client_order_id, "symbol": symbol,
            "blocked": False,
        }, request=request)
    return {"ok": True, "execution": _public_record(record), "preflight": projection}


@router.post("/place")
async def place(payload: dict, request: Request):
    user = install_api.require_operator(request)
    session = identity.session_of(request)
    assert session is not None
    if not identity.csrf_ok(request, session):
        raise HTTPException(status_code=403, detail="CSRF token required")
    if CLIENT.mode is not ExecutionMode.LIVE or CONFIG_PROBLEM is not None:
        raise HTTPException(status_code=409, detail="confirmed live mode is not enabled")
    body = payload or {}
    if body.get("confirmation") != PLACE_CONFIRMATION:
        raise HTTPException(status_code=422, detail=f"confirmation must be {PLACE_CONFIRMATION}")
    evidence = CLIENT.ledger.promotion_evidence()
    if not evidence["ready"]:
        raise HTTPException(status_code=409, detail={
            "error": "promotion-gate-blocked", "reasons": evidence["reasons"],
            "required": evidence["required"],
        })
    intent, symbol, projection, notional_usdt = await _checked_intent(body, request)
    if notional_usdt <= 0:
        raise HTTPException(status_code=422, detail="preflight did not produce USD notional")
    try:
        policy_use = auth.authorize_live_order(
            user, client_order_id=intent.client_order_id, symbol=symbol,
            execution=intent.execution, notional_usdt=notional_usdt,
        )
    except auth.AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    record = _call(lambda: CLIENT.place(intent))
    if _AUDIT is not None:
        _AUDIT("live.place", detail={
            "client_order_id": intent.client_order_id, "symbol": symbol,
            "state": record.get("state"), "actor": user.username,
            "owner_override": bool(policy_use.get("owner_override")),
        }, request=request)
    return {"ok": True, "execution": _public_record(record), "preflight": projection,
            "authorization": {"role": user.role, **policy_use}}


@router.get("/evidence", dependencies=[Depends(install_api.require_operator)])
def evidence():
    return {"ok": True, **CLIENT.ledger.promotion_evidence()}


@router.get("/orders", dependencies=[Depends(install_api.require_operator)])
def orders(status: str = Query("open", pattern="^(all|open|done)$")):
    return _call(lambda: CLIENT.list_orders(status=status))


@router.post("/reconcile", dependencies=[Depends(require_client)])
def reconcile(payload: dict):
    client_id = str((payload or {}).get("clientOrderId") or "")
    if not client_id:
        raise HTTPException(status_code=422, detail="clientOrderId is required")
    return _call(lambda: CLIENT.reconcile(client_id))


@router.post("/cancel", dependencies=[Depends(require_client)])
def cancel(payload: dict):
    client_id = str((payload or {}).get("clientOrderId") or "")
    if not client_id:
        raise HTTPException(status_code=422, detail="clientOrderId is required")
    return _call(lambda: CLIENT.cancel(client_id))


__all__ = ["CLIENT", "CONFIG_PROBLEM", "configured_client", "router"]
