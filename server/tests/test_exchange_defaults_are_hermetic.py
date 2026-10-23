# mypy: check_untyped_defs = False
"""The documented exchange minimums must not be rewritten by a network call.

What this file is for. Three tests failed on a machine that can reach Nobitex, and
passed on CI, which cannot:

    test_js_parity.py::test_both_brokers_name_the_same_gate_for_the_same_order
    test_paper_order_route.py::test_a_refused_order_comes_back_as_the_gate_that_refused_it
    test_paper_order_route.py::test_a_risk_plan_from_the_queue_does_not_change_...

All three expect a below-minimum order to be refused. Two other tests start the
app's lifespan (`with TestClient(main.app)`), the lifespan fetches `/v2/options`,
and `apply_options()` writes the *live* minimums (100,000 Rial / 1 USDT) over the
documented ones (3,000,000 Rial / 11 USDT) — process-wide, for every test that runs
afterwards. With 1 USDT as the floor, an order worth $7.30 is perfectly legal, so
the broker filled it and the tests that断言ed a refusal failed three files away
from the change.

The suite is now order-independent (conftest restores the tables after every test)
and this file pins the facts: the shipped numbers, that an override really does
change behaviour, and that the restore really does put them back.
"""

from __future__ import annotations

import pytest

import risk_engine
import units


def test_the_shipped_minimums_are_the_documented_small_order():
    """3,000,000 Rial = 300,000 Toman, and 11 USDT. Not a guess: §market/orders/add."""
    assert units.MIN_NOTIONAL_QUOTE == {"rls": 3_000_000.0, "usdt": 11.0}
    assert units.min_notional_usd("BTCIRT", 100_805.0) == pytest.approx(2.976, abs=0.001)
    assert units.min_notional_usd("BTCUSDT", 100_805.0) == pytest.approx(11.0)


def test_the_exchange_can_still_override_them():
    """The override is a feature — the exchange is authoritative in production."""
    units.apply_options({"nobitex": {"minOrders": {"rls": "100000", "usdt": "1"}}})
    assert units.min_notional_usd("BTCUSDT", 100_805.0) == pytest.approx(1.0)


def test_the_restore_puts_the_documented_numbers_back():
    units.apply_options({"nobitex": {"minOrders": {"rls": "100000", "usdt": "1"},
                                     "amountPrecisions": {"BTCIRT": "0.5"},
                                     "pricePrecisions": {"BTCIRT": "1000"}}})
    assert units.MIN_NOTIONAL_QUOTE["usdt"] == 1.0
    units.reset_documented_defaults()
    assert units.MIN_NOTIONAL_QUOTE == {"rls": 3_000_000.0, "usdt": 11.0}
    assert units.truncate_amount(0.0000021, "BTCIRT") == pytest.approx(0.000002)


def test_with_the_live_minimum_the_same_order_would_have_been_legal():
    """The exact trap: the order that must be refused becomes acceptable.

    $7.30 is under the documented 11 USDT floor and over the exchange's live 1 USDT
    floor, so the *same* order takes a different path depending on whether a
    previous test happened to talk to the exchange.
    """
    qty, price = 0.0001, 73_040.0
    notional = qty * price
    assert notional < units.min_notional_usd("BTCUSDT", 100_805.0)     # documented
    units.apply_options({"nobitex": {"minOrders": {"usdt": "1"}}})
    assert notional > units.min_notional_usd("BTCUSDT", 100_805.0)     # live
    units.reset_documented_defaults()
    assert notional < units.min_notional_usd("BTCUSDT", 100_805.0)     # restored


def test_a_gate_built_now_uses_the_documented_floor():
    """The gate the broker builds reads the current tables — so they must be right."""
    notional = 0.0001 * 73_040.0
    pf = risk_engine.preflight(
        equity=100_000.0, cash=100_000.0, open_heat=0.0, open_positions=0,
        concurrent_limit=5, max_heat_pct=6.0, min_cash_pct=10.0,
        max_single_asset_pct=25.0, risk_pct_cap=0.75, halted=False,
        notional_usd=notional, fee_usd=0.0, risk_usd=30.0,
        min_notional_usd=units.min_notional_usd("BTCUSDT", 100_805.0),
    )
    gate = [g for g in pf.gates if g.id == "min-notional"]
    assert gate and gate[0].ok is False, "the documented floor must refuse $7.30"
    assert pf.first_block().id == "min-notional"
