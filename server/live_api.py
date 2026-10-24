"""Private live-control routes. Deliberately no order-placement endpoint."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from live_execution import ExecutionError, ExecutionLedger, ExecutionMode, LiveExecutionClient
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


def _call(action):
    try:
        return action()
    except ExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"exchange transport: {type(exc).__name__}") from exc


@router.get("/status", dependencies=[Depends(require_client)])
def status():
    return {
        "ok": True,
        "mode": CLIENT.mode.value,
        "configured": CLIENT.mode is ExecutionMode.LIVE and CONFIG_PROBLEM is None,
        "configuration_problem": CONFIG_PROBLEM,
        "placement_exposed": False,
    }


@router.get("/orders", dependencies=[Depends(require_client)])
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
