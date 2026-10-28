"""Options pricePrecisions use destination-currency units, not display units.

Sources: /options/get-system-options (price step in destination currency)
and /spot_trade/... (Rial-market order prices are in Rial).
"""
from __future__ import annotations

import copy

import pytest

import units


@pytest.fixture(autouse=True)
def defaults(monkeypatch):
    units.reset_documented_defaults()
    monkeypatch.delenv('NBX_PRICE_UNIT', raising=False)
    yield
    units.reset_documented_defaults()


def test_raw_rial_tick_is_normalized_to_toman_once():
    raw = {'nobitex': {'pricePrecisions': {'BTCIRT': '10', 'DOGEIRT': '1', 'BTCUSDT': '0.01'}}}
    saved = copy.deepcopy(raw)
    assert units.apply_options(raw) == 3
    assert units.price_step('BTCIRT') == 1
    assert units.price_step('DOGEIRT') == pytest.approx(0.1)
    assert units.price_step('BTCUSDT') == 0.01
    assert raw == saved
    units.apply_options(raw)
    assert units.price_step('DOGEIRT') == pytest.approx(0.1)


def test_book_override_does_not_change_options_source_unit(monkeypatch):
    monkeypatch.setenv('NBX_PRICE_UNIT', 'toman')
    units.apply_options({'nobitex': {'pricePrecisions': {'BTCIRT': '10'}}})
    assert units.price_step('BTCIRT') == 1


def test_default_rial_tick_is_already_in_normalized_unit():
    assert units.price_step('BTCIRT') == 1
    assert units.round_price(21695.4, 'DOGEIRT') == 21695


@pytest.mark.parametrize('bad', ['NaN', 'Infinity', '0', '-1', 'not-a-number'])
def test_invalid_tick_cannot_replace_good_rule(bad):
    units.apply_options({'nobitex': {'pricePrecisions': {'DOGEIRT': '1'}}})
    assert units.apply_options({'nobitex': {'pricePrecisions': {'DOGEIRT': bad}}}) == 0
    assert units.price_step('DOGEIRT') == pytest.approx(0.1)


def test_half_up_rounding_does_not_depend_on_binary_division():
    units.apply_options({'nobitex': {'pricePrecisions': {'DOGEUSDT': '0.01', 'DOGEIRT': '1'}}})
    assert units.round_price(1.005, 'DOGEUSDT') == 1.01
    assert units.round_price(21695.45, 'DOGEIRT') == 21695.5


def test_fractional_raw_tick_is_not_binary_divided_before_decimal_rounding():
    units.apply_options({'nobitex': {'pricePrecisions': {'DOGEIRT': '0.07'}}})
    assert units.price_step('DOGEIRT') == 0.007
    assert units.round_price(0.0105, 'DOGEIRT') == 0.014
