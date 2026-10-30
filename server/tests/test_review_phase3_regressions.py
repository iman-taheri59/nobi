# mypy: check_untyped_defs = False
"""Pure retained-event projection + private local ASGI; never construct a broker."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from paper_trading import OrderEvent


def position(state="OPEN", **changes):
    values = {"client_order_id": "test-coid", "symbol": "BTCUSDT", "state": state, "filled_qty": 10,
              "entry_price": 100, "realized_pnl": 0, "closed_at": None, "close_reason": None,
              "execution": "market", "requested_qty": 10}
    values.update(changes)
    return SimpleNamespace(**values)


def event(kind, qty=10, price=100, **extra):
    return OrderEvent(1000, kind, "BTCUSDT", "buy", qty, price,
                      {"clientOrderId": "test-coid", **extra})


def broker(pos, *events):
    return SimpleNamespace(positions={"test-id": pos}, orders=list(events))


@pytest.mark.parametrize("state", ["OPEN", "PARTIAL", "CLOSED"])
def test_rejected_exit_stays_rejected_not_related_position_state(state):
    b = broker(position(state), event("submit", requested=10), event("fill"),
               event("reject", price=0, reason="revert-failed"))
    before = copy.deepcopy(b)
    rows = main._order_rows(b)
    rejected = next(r for r in rows if r["record_kind"] == "rejected-attempt")
    assert rejected["state"] == "REJECTED"
    assert rejected["position_state"] == state
    assert rejected["entry_filled"] is rejected["remaining_open"] is rejected["pnl_usd"] is None
    assert rejected["price"] is None
    assert rejected["reason"] == "revert-failed"
    assert b == before  # Reporting must not repair historical filled_qty or state.


def test_tp1_does_not_reduce_entry_fill_percentage():
    b = broker(position("PARTIAL", filled_qty=6), event("submit", requested=10, mid=100),
               event("fill", fee=1), event("tp1", qty=4, price=110, pnl=4, fee=0.4))
    row = main._order_rows(b)[0]
    assert row["filled"] == row["entry_filled"] == 10
    assert row["filled_pct"] == 100
    assert row["retained_exit_qty"] == 4
    assert row["remaining_open"] == 6
    assert row["fee"] == 1 and row["retained_exit_fees_quote"] == 0.4


def test_thin_entry_fill_uses_request_not_remaining():
    b = broker(position(requested_qty=20, filled_qty=5), event("submit", qty=5, requested=20),
               event("fill", qty=5))
    row = main._order_rows(b)[0]
    assert row["requested"] == 20 and row["entry_filled"] == 5 and row["filled_pct"] == 25


def test_closed_snapshot_keeps_historical_qty_and_exposes_model_remaining_separately():
    p = position("CLOSED", filled_qty=6, closed_at=1200, close_reason="sl-hit", realized_pnl=-12)
    b = broker(p, event("submit", requested=10), event("fill"),
               event("tp1", qty=4, pnl=3), event("close", qty=6, price=90, reason="sl-hit", pnl=-12))
    before = copy.deepcopy(b)
    row = main._order_rows(b)[0]
    assert row["remaining_open"] == 0
    assert row["entry_filled"] == 10 and row["filled_pct"] == 100
    assert row["retained_exit_qty"] == 10
    assert row["pnl_usd"] == -12 and row["pnl_usd_basis"] == "position-snapshot"
    assert row["closed_at_iso"] and row["close_reason"] == "sl-hit"
    assert row["event_details"][-1]["price"] == 90
    assert b == before and p.filled_qty == 6


def test_missing_entry_events_stay_unknown_not_backfilled_from_position():
    b = broker(position("PARTIAL", filled_qty=6), event("tp1", qty=4, pnl=3))
    row = main._order_rows(b)[0]
    assert row["record_kind"] == "retained-events-only"
    assert row["entry_filled"] is row["filled_pct"] is None
    assert row["remaining_open"] == 6
    assert row["opened_at_basis"] == "first-retained-event"


def test_unlinked_legacy_row_not_assigned_arbitrary_current_position():
    p = position("CLOSED", client_order_id="", entry_price=777)
    b = broker(p, OrderEvent(1000, "submit", "BTCUSDT", "buy", 10, 100, {}),
               OrderEvent(1001, "fill", "BTCUSDT", "buy", 10, 100, {}))
    row = main._order_rows(b)[0]
    assert row["position_link"] == "unavailable" and row["position_state"] is None
    assert row["price"] == 100 and row["state"] == "FILLED"
    assert row["type"] == "unknown" and row["remaining_open"] is None
    assert row["requested"] is row["filled_pct"] is None


def test_same_timestamp_rejections_are_separate_attempts():
    rows = main._order_rows(broker(position(), event("reject", reason="first"),
                                   event("reject", reason="second")))
    assert len(rows) == 2
    assert {r["reason"] for r in rows} == {"first", "second"}


@pytest.mark.parametrize("side,price,raw,adverse", [
    ("buy", 101, 100, 100), ("buy", 99, -100, -100),
    ("sell", 99, -100, 100), ("sell", 101, 100, -100),
])
def test_adverse_slippage_has_side_normalized_sign(side, price, raw, adverse):
    events = [event("submit", mid=100), event("fill", price=price)]
    for ev in events:
        ev.side = side
    row = main._order_rows(broker(position(), *events))[0]
    assert row["slip_bps"] == raw  # legacy raw signed metric retained
    assert row["adverse_slip_bps"] == adverse


def test_missing_mid_is_unknown_not_zero_slippage():
    row = main._order_rows(broker(position(), event("submit"), event("fill")))[0]
    assert row["slip_bps"] is row["adverse_slip_bps"] is None


def test_position_execution_is_canonical_not_default_limit():
    row = main._order_rows(broker(position(execution="market"), event("fill")))[0]
    assert row["type"] == row["execution"] == "market"


def test_endpoint_counts_are_returned_rows_and_rejections_survive(monkeypatch):
    b = broker(position(), event("submit"), event("fill"), event("reject", reason="revert-failed"))
    before = copy.deepcopy(b)
    monkeypatch.setattr(main, "get_broker", lambda *args: b)
    with TestClient(main.app) as client:
        response = client.get("/api/paper/orders?limit=100")
    body = response.json()
    assert response.status_code == 200
    assert body["counts"] == {"total": 2, "open": 1, "closed": 0, "rejected": 1}
    assert body["coverage"] == {"complete_history": False, "persisted_event_limit": 400,
                                "row_limit": 100, "counts_scope": "returned-rows"}
    assert body["as_of"] and b == before


def test_empty_events_do_not_claim_complete_account_history(monkeypatch):
    b = broker(position("CLOSED"))
    monkeypatch.setattr(main, "get_broker", lambda *args: b)
    with TestClient(main.app) as client:
        body = client.get("/api/paper/orders?limit=1").json()
    assert body["rows"] == [] and body["coverage"]["complete_history"] is False
