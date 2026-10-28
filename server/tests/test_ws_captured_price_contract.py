"""Regression from public capture run by the user, NOT synthetic source proof.

2026-10-28, DOGEIRT and BTCUSDT, 15m, one bar each. This does not attest
other WS channels/markets or reconstruct any historical order rejection.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from ws_execution import StreamExecutor

CAPTURE = Path(__file__).parent / 'fixtures' / 'nobitex-unit-evidence-20261028.json'


def test_captured_public_evidence_is_preserved_verbatim():
    assert hashlib.sha256(CAPTURE.read_bytes()).hexdigest() == (
        'de21f05c03132fdab0bdd015b3a88ff8321f75d3aee83fa6dd5a45a8b3457afe')
    evidence = json.loads(CAPTURE.read_text(encoding="utf-8"))
    assert evidence['ws_connected'] is True
    assert not evidence['errors']
    assert len(evidence['ws_candles']) == 4
    assert evidence['orders_sent'] is False and evidence['settings_changed'] is False


@pytest.mark.parametrize('symbol', ['DOGEIRT', 'BTCUSDT'])
def test_real_same_bar_ws_matches_udf_without_extra_division(monkeypatch, symbol):
    monkeypatch.delenv('NBX_WS_CANDLE_PRICE_UNIT', raising=False)
    # The stats/book source contract must not affect a candle source contract.
    monkeypatch.setenv('NBX_PRICE_UNIT', 'rial')
    evidence = json.loads(CAPTURE.read_text(encoding="utf-8"))
    s = StreamExecutor()
    frames = [f for f in evidence['ws_candles'] if f['symbol'] == symbol]
    samples = [r for r in evidence['rest_samples'] if r['symbol'] == symbol]
    for frame in frames:
        raw = frame['raw']
        s._on_ws_message(json.dumps({'push': {'channel': frame['channel'],
                                             'pub': {'data': json.dumps(raw)}}}))
        normalized = s.candles[(symbol, '15m')]
        assert {k: normalized[k] for k in ('t', 'o', 'h', 'l', 'c', 'v')} == raw
        for sample in samples:
            udf = sample['udf']
            index = udf['t'].index(raw['t'])
            expected = {k: udf[k][index] for k in ('t', 'o', 'h', 'l', 'c', 'v')}
            assert raw == expected  # Observed match includes OHLC and volume.
            rows = [{k: udf[k][i] for k in ('t', 'o', 'h', 'l', 'c', 'v')}
                    for i in range(len(udf['t']))]
            saved = copy.deepcopy(rows)
            merged = s.merge_last_candle(symbol, '15m', rows)
            assert merged[-1] == expected
            assert rows == saved
    assert s.candle_price_rejections == 0
    assert s.status()['candle_source_unit'] == 'toman'
