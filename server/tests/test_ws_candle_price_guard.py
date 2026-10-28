"""Source-unit fixtures are contracts under test, not evidence about live WS."""
import json

import pytest

from ws_execution import StreamExecutor


def stream(monkeypatch):
    monkeypatch.delenv('NBX_WS_CANDLE_PRICE_UNIT', raising=False)
    return StreamExecutor()


def frame(s, close, *, low=None, high=None, volume=1):
    data = {'t': 1792488600, 'o': close, 'c': close,
            'l': close if low is None else low,
            'h': close if high is None else high, 'v': volume}
    s._on_ws_message(json.dumps({'push': {'channel': 'public:candle-DOGEIRT-15',
                                         'pub': {'data': json.dumps(data)}}}))


@pytest.mark.parametrize('raw', [2165.52, 216552])
def test_factor_ten_mismatch_keeps_rest_unchanged(monkeypatch, raw):
    s = stream(monkeypatch)
    frame(s, raw)
    rows = [{'t': 1792488600, 'o': 21655.2, 'h': 21660, 'l': 21650,
             'c': 21655.2, 'v': 1}]
    assert s.merge_last_candle('DOGEIRT', '15m', rows) is rows
    assert s.candle_price_rejections == 1
    assert s.last_candle_rejection['reason'] == 'rest-ws-price-mismatch'
    assert s.status()['last_candle_rejection'] == s.last_candle_rejection


def test_consistent_toman_source_can_merge_without_mutating_rest(monkeypatch):
    s = stream(monkeypatch)
    frame(s, 21655.2)
    rows = [{'t': 1792488600, 'c': 21654}]
    merged = s.merge_last_candle('DOGEIRT', '15m', rows)
    assert merged[-1]['c'] == pytest.approx(21655.2)
    assert rows == [{'t': 1792488600, 'c': 21654}]
    assert s.candle_price_rejections == 0


@pytest.mark.parametrize('book_unit', ['rial', 'toman'])
def test_explicit_ws_override_is_independent_of_book_override(monkeypatch, book_unit):
    s = stream(monkeypatch)
    monkeypatch.setenv('NBX_PRICE_UNIT', book_unit)
    frame(s, 21655.2)
    assert s.candles[('DOGEIRT', '15m')]['c'] == pytest.approx(21655.2)
    monkeypatch.setenv('NBX_WS_CANDLE_PRICE_UNIT', 'rial')
    frame(s, 216552)
    assert s.candles[('DOGEIRT', '15m')]['c'] == pytest.approx(21655.2)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), 0, -1])
def test_invalid_prices_cannot_enter_candle_cache(monkeypatch, bad):
    s = stream(monkeypatch)
    frame(s, bad)
    assert not s.candles
    assert s.last_candle_rejection['reason'] == 'invalid-ohlcv'


def test_impossible_ohlc_is_rejected(monkeypatch):
    s = stream(monkeypatch)
    frame(s, 1000, high=999)
    assert not s.candles
    assert s.last_candle_rejection['reason'] == 'invalid-ohlcv'


def test_invalid_source_unit_fails_closed(monkeypatch):
    s = stream(monkeypatch)
    monkeypatch.setenv('NBX_WS_CANDLE_PRICE_UNIT', 'guess')
    frame(s, 1000)
    assert not s.candles
    assert s.last_candle_rejection['reason'] == 'invalid-source-unit'


def test_matching_close_cannot_hide_wrong_scale_in_other_fields(monkeypatch):
    s = stream(monkeypatch)
    frame(s, 21655.2, low=2165.52)
    rows = [{'t': 1792488600, 'c': 21655.2}]
    assert s.merge_last_candle('DOGEIRT', '15m', rows) is rows
    assert s.last_candle_rejection['reason'] == 'rest-ws-price-mismatch'


def test_extreme_ratio_does_not_make_status_unserializable(monkeypatch):
    s = stream(monkeypatch)
    frame(s, 1e308)
    rows = [{'t': 1792488600, 'c': 1e-308}]
    assert s.merge_last_candle('DOGEIRT', '15m', rows) is rows
    json.dumps(s.status(), allow_nan=False)


def test_nonfinite_timestamp_is_ignored_without_throwing(monkeypatch):
    s = stream(monkeypatch)
    s._on_candle_frame('public:candle-DOGEIRT-15', {'t': float('inf')})
    assert not s.candles
