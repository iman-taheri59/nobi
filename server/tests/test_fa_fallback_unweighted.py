"""A hand-written FA profile is shown, not weighted.

`FALLBACK_FA_PROFILES` carries numbers with `as_of: unversioned-legacy-profile`.
Whenever CoinGecko is unreachable — a plausible state for a server inside Iran —
25 % of the composite used to come from them. The row now carries the score for
display (`fa_score`, `fa.profile = fallback`) but the composite is built on
`ta+liq`, exactly as when fundamentals are unavailable, and `fa_weighted` says so.
"""
from __future__ import annotations

import asyncio
import time

import main
from fa_engine import FAResult


async def _noop():
    return 0


def _scan_with(monkeypatch, profile: str) -> dict:
    monkeypatch.setitem(main._cached, "universe", [
        {"symbol": "BTCIRT", "last": 1.0, "vol24": 1e9, "vol24_usd": 10_000.0, "change24": 0.0, "closed": False},
        {"symbol": "USDTIRT", "last": 100_000.0, "vol24": 1e9, "vol24_usd": 10_000.0, "change24": 0.0, "closed": False},
    ])
    for key in ("signals", "signals_by_tf", "signals_ts"):
        monkeypatch.setitem(main._cached, key, {})
    now = int(time.time()) // 900 * 900

    async def fake_candles(symbol, tf, count):
        rows = []
        px = 100.0
        for i in range(260):
            px *= 1.001
            rows.append({"t": now - (260 - i) * 900, "o": px, "h": px * 1.002, "l": px * 0.998, "c": px, "v": 10.0})
        return rows, "live"

    async def fake_fa(symbol, **kw):
        return FAResult(score=90.0, market=90, tokenomics=90, onchain=90, sentiment=90, macro=90,
                        vetoes=[], evidence={"profile": profile}, fresh=(profile == "live"))

    monkeypatch.setattr(main.client, "get_candles_ex", fake_candles)
    monkeypatch.setattr(main.FA_ENGINE, "score", fake_fa)
    monkeypatch.setenv("NBX_JOURNAL", "0")
    monkeypatch.setattr(main, "journal_scan", lambda *a, **k: _noop())
    asyncio.run(main.rescan("15m", top=10))
    return main._cached["signals_by_tf"]["15m"]["BTCIRT"]


def test_fallback_profile_is_not_weighted(monkeypatch):
    live = _scan_with(monkeypatch, "live")
    fb = _scan_with(monkeypatch, "fallback")
    assert live["fa_weighted"] is True and live["composite_basis"] == "ta+fa+liq"
    assert fb["fa_weighted"] is False and fb["composite_basis"] == "ta+liq"
    assert fb["fa_score"] == 90.0 and fb["fa"]["profile"] == "fallback"  # still visible
    expect, _ = main.composite_score(fb["score"], fb["liquidity_score"], None)
    # a 90 FA from a static profile must not lift the composite above the ta+liq figure
    assert fb["composite"] == expect
    assert live["composite"] > fb["composite"]
