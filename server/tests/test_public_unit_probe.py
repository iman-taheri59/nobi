"""No real network: exercise the portable probe against public-only stand-ins."""
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    'nobitex_unit_probe', Path(__file__).resolve().parents[2] / 'scripts' / 'nobitex_unit_probe.py')
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def test_compares_same_bar_open_not_moving_close():
    observed = PROBE.compare_same_bar('DOGEIRT', {'t': 900, 'o': '1000', 'c': 999999},
                                      {'s': 'ok', 't': [900], 'o': [100], 'c': [1]})
    assert observed['ws_open_div_udf_open'] == 10


@pytest.mark.parametrize('udf', [
    {'s': 'no_data'}, {'s': 'ok', 't': [1800], 'o': [100]},
    {'s': 'ok', 't': [900], 'o': [0]}, {'s': 'ok', 't': [900], 'o': [float('nan')]},
])
def test_no_same_bar_or_valid_open_means_no_unit_evidence(udf):
    assert PROBE.compare_same_bar('DOGEIRT', {'t': 900, 'o': 1000}, udf) is None


def test_capture_uses_only_public_gets_and_anonymous_ws(monkeypatch):
    urls, sent = [], []

    class Response:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    class Client:
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False
            assert kwargs['follow_redirects'] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params=None):
            urls.append(url)
            if url.endswith('/v2/options'):
                return Response({'nobitex': {'pricePrecisions': {'DOGEIRT': '1'}}})
            if url.endswith('/market/udf/history'):
                return Response({'s': 'ok', 't': [900], 'o': [100], 'c': [105]})
            assert url == PROBE.API + '/v3/orderbook/DOGEIRT'
            return Response({'bids': [['1000', '1']], 'asks': [['1001', '1']]})

    class Socket:
        def __init__(self, url, **kwargs):
            assert url == PROBE.WS

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, raw):
            sent.append(json.loads(raw))

        def __aiter__(self):
            return self.messages()

        async def messages(self):
            yield json.dumps({'id': 1, 'connect': {}})
            yield json.dumps({'id': 2, 'subscribe': {}})
            yield json.dumps({'push': {'channel': 'public:candle-DOGEIRT-15',
                                      'pub': {'data': json.dumps({'t': 900, 'o': 1000, 'c': 1001})}}})
            await asyncio.Event().wait()

    original_sleep = asyncio.sleep

    async def quick_sleep(seconds):
        await original_sleep(0.01 if seconds == 35 else 100)

    monkeypatch.setattr(PROBE.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(PROBE.websockets, 'connect', Socket)
    monkeypatch.setattr(PROBE.asyncio, 'sleep', quick_sleep)
    result = asyncio.run(PROBE.capture(['DOGEIRT'], 35))
    assert result['evidence_status'] == 'same-bar-capture-available'
    assert result['comparisons'][0]['ws_open_div_udf_open'] == 10
    assert set(urls) == {PROBE.API + p for p in
                         ('/v2/options', '/market/udf/history', '/v3/orderbook/DOGEIRT')}
    assert sent == [{'id': 1, 'connect': {}},
                    {'id': 2, 'subscribe': {'channel': 'public:candle-DOGEIRT-15'}}]
    assert result['orders_sent'] is False and result['settings_changed'] is False
