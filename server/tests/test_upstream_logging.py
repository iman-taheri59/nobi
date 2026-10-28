# mypy: check_untyped_defs = False
"""Failures remain visible without a repeated per-pair warning flood."""
from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from nobitex_client import ClientError, NobitexClient, UpstreamUnavailable, safe_request_context
from upstream_logging import RefusalLogLimiter


def test_known_request_context_keeps_market_fields_only():
    result = safe_request_context('/market/udf/history?symbol=DAIIRT&resolution=15&token=secret-query')
    assert result == '/market/udf/history symbol=DAIIRT resolution=15'
    assert safe_request_context('/v3/orderbook/BTCIRT') == '/v3/orderbook/{symbol} symbol=BTCIRT'
    assert safe_request_context('/market/stats') == '/market/stats'


@pytest.mark.parametrize('path', [
    '/private/secret-path?key=secret-query',
    'https://secret-user:secret-password@example.com/private/secret-path',
    '/market/udf/history?symbol=bad%0Asymbol&resolution=bad-secret',
    '/market/udf/history?symbol=BTCIRT&symbol=secret-value',
    'https://[invalid',
    '/market/stats?' + '&'.join(f'key{i}=secret' for i in range(101)),
])
def test_request_context_drops_untrusted_values(path):
    result = safe_request_context(path)
    for secret in ('secret', 'password', 'example.com', 'bad\n'):
        assert secret not in result


def test_http_403_log_names_route_symbol_and_resolution_without_secrets(caplog):
    async def run():
        transport = httpx.MockTransport(lambda r: httpx.Response(
            403, json={'message': 'secret-body'}, headers={'X-Secret': 'secret-response-header'}))
        client = NobitexClient(session_factory=lambda: httpx.AsyncClient(
            base_url='https://exchange.test', transport=transport,
            headers={'Authorization': 'Bearer secret-auth-header'}))
        try:
            with pytest.raises(ClientError) as err:
                await client.get('/market/udf/history', {
                    'symbol': 'DAIIRT', 'resolution': '15', 'token': 'secret-query'})
            assert err.value.meta['status'] == 403
        finally:
            await client.aclose()
    with caplog.at_level(logging.ERROR):
        asyncio.run(run())
    assert len(caplog.records) == 1
    assert 'status=403 GET /market/udf/history symbol=DAIIRT resolution=15' in caplog.text
    assert 'secret' not in caplog.text


def test_business_error_message_and_code_are_not_echoed(monkeypatch, caplog):
    client = NobitexClient()

    async def reject(*args, **kwargs):
        raise ClientError('secret-code\n', 'secret-body', {'status': 200, 'token': 'secret-meta'})

    monkeypatch.setattr(client, '_request', reject)
    with caplog.at_level(logging.ERROR), pytest.raises(ClientError):
        asyncio.run(client.get('/market/stats?token=secret-query'))
    assert 'secret' not in caplog.text
    assert 'code=[invalid]' in caplog.text
    assert '/market/stats' in caplog.text


def test_refusals_keep_first_warning_and_periodic_count(monkeypatch, caplog):
    import upstream_logging

    clock = [1000.0]
    monkeypatch.setattr(upstream_logging.time, 'monotonic', lambda: clock[0])
    limiter = RefusalLogLimiter(interval_s=900)
    log = logging.getLogger('test-refusals')
    error = UpstreamUnavailable('history refused', {'refused': True})
    with caplog.at_level(logging.WARNING):
        limiter.warning(log, 'scan', 'DAIIRT', '15m', error)
        for _ in range(24):
            limiter.warning(log, 'scan', 'DAIIRT', '15m', error)
        assert len(caplog.records) == 1
        clock[0] += 900
        limiter.warning(log, 'scan', 'DAIIRT', '15m', error)
    assert len(caplog.records) == 2
    assert '24 repeated refusal warnings suppressed' in caplog.records[-1].message


def test_other_symbols_timeframes_operations_and_outages_remain_visible(caplog):
    limiter = RefusalLogLimiter()
    log = logging.getLogger('test-refusals')
    refusal = UpstreamUnavailable('history refused', {'refused': True})
    with caplog.at_level(logging.WARNING):
        for operation, symbol, tf in [('scan', 'DAIIRT', '15m'), ('scan', 'ETHIRT', '15m'),
                                      ('scan', 'DAIIRT', '1h'), ('baseline', 'DAIIRT', '15m')]:
            limiter.warning(log, operation, symbol, tf, refusal)
        for _ in range(2):
            limiter.warning(log, 'scan', 'DAIIRT', '15m', UpstreamUnavailable('timeout'))
    assert len(caplog.records) == 6
    assert sum('timeout' in r.message for r in caplog.records) == 2


def test_limiter_memory_is_bounded():
    limiter = RefusalLogLimiter(max_keys=2)
    log = logging.getLogger('test-refusals')
    for sym in ('DAIIRT', 'ETHIRT', 'BTCIRT'):
        limiter.warning(log, 'scan', sym, '15m', UpstreamUnavailable('refused', {'refused': True}))
    assert len(limiter._state) == 2


def test_scan_still_skips_missing_data_and_keeps_healthy_symbols(monkeypatch, caplog):
    import main

    monkeypatch.setattr(main, 'CANDLE_WARNINGS', RefusalLogLimiter())
    monkeypatch.setitem(main._cached, 'universe', [
        {'symbol': sym, 'last': 1.0, 'vol24': 1e9, 'change24': 0.0, 'closed': False}
        for sym in ('BTCIRT', 'DAIIRT')])
    for key in ('signals', 'signals_by_tf', 'signals_ts', 'signals_bars'):
        monkeypatch.setitem(main._cached, key, {})

    async def candles(symbol, tf, count):
        if symbol == 'DAIIRT':
            raise UpstreamUnavailable('history refused', {'symbol': symbol, 'tf': tf, 'refused': True})
        return ([{'t': i, 'o': 1.0, 'h': 1.1, 'l': 0.9, 'c': 1.0, 'v': 1.0}
                 for i in range(30)], 'live')

    monkeypatch.setattr(main.client, 'get_candles_ex', candles)
    monkeypatch.setenv('NBX_JOURNAL', '0')
    with caplog.at_level(logging.WARNING):
        asyncio.run(main.rescan('15m', top=10))
        asyncio.run(main.rescan('15m', top=10))
    warnings = [r for r in caplog.records if 'scan DAIIRT' in r.message]
    assert len(warnings) == 1
    assert 'BTCIRT' in main._cached['signals']
    assert 'DAIIRT' not in main._cached['signals']


def test_recovered_pair_starts_a_new_warning_episode(caplog):
    limiter = RefusalLogLimiter()
    log = logging.getLogger('test-refusals')
    error = UpstreamUnavailable('history refused', {'refused': True})
    with caplog.at_level(logging.WARNING):
        limiter.warning(log, 'scan', 'DAIIRT', '15m', error)
        limiter.warning(log, 'scan', 'DAIIRT', '15m', error)
        limiter.recovered('scan', 'daiirt', '15m')
        limiter.warning(log, 'scan', 'DAIIRT', '15m', error)
    assert len(caplog.records) == 2
