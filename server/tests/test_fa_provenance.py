# mypy: check_untyped_defs = False
"""FA cache keys and component provenance must track the evidence actually used."""
from __future__ import annotations

import asyncio

import pytest

import fa_engine


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def isolated_fa(monkeypatch):
    monkeypatch.setattr(fa_engine, "FA_CACHE", {})
    monkeypatch.setattr(fa_engine, "VETO_CACHE", {})
    monkeypatch.setattr(fa_engine, "_LLAMA_CACHE", {"ts": 0.0, "data": None, "degraded": False})
    monkeypatch.setattr(fa_engine, "_FNG_CACHE", {"ts": 0.0, "data": None, "degraded": False})
    eng = fa_engine.FAEngine()
    calls = {"cg": 0, "llama": 0, "fng": 0}

    async def overview(_cg_id):
        calls["cg"] += 1
        return {"market_data": {
            "market_cap": {"usd": 1e12}, "fully_diluted_valuation": {"usd": 1e12},
            "total_volume": {"usd": 1e10}, "total_supply": 1, "max_supply": 1,
        }, "genesis_date": "2009-01-03"}

    async def llama():
        calls["llama"] += 1
        return [{"name": "bitcoin", "symbol": "BTC", "tvl": 5e9}]

    async def fng():
        calls["fng"] += 1
        return {"fg_value": 55}

    eng._cg_overview = overview
    eng._llama_protocols = llama
    eng._listen_prices = fng
    return eng, calls


def test_depth_is_part_of_the_cache_key_and_cached_evidence_has_an_age(isolated_fa):
    eng, calls = isolated_fa
    deep = run(eng.score("BTC", nobil_depth_score=80))
    shallow = run(eng.score("BTC", nobil_depth_score=20))
    again = run(eng.score("BTC", nobil_depth_score=20))
    assert shallow.score < deep.score
    assert calls["cg"] == 2  # not one answer reused for every order-book depth
    assert again.score == shallow.score and again.cached is True
    assert again.evidence["cache"]["cached"] is True
    assert again.evidence["cache"]["age_s"] >= 0
    assert calls["cg"] == 2


def test_missing_fear_greed_is_excluded_not_imputed_as_fifty(isolated_fa):
    eng, _ = isolated_fa

    async def unavailable():
        return None

    eng._listen_prices = unavailable
    result = run(eng.score("BTC"))
    assert result.evidence["fear_greed"] is None
    assert result.evidence["providers"]["fear_greed"]["status"] == "unavailable"
    assert "sentiment" not in result.evidence["score_basis"]["available_components"]
    assert result.evidence["score_basis"]["weight_total"] == pytest.approx(0.8)


def test_missing_premium_is_not_a_zero_premium_or_a_cache_alias(isolated_fa):
    eng, calls = isolated_fa
    missing = run(eng.score("BTC", usdt_premium_dev=None))
    zero = run(eng.score("BTC", usdt_premium_dev=0.0))
    assert missing.evidence["premium_dev"] is None
    assert "macro" not in missing.evidence["score_basis"]["available_components"]
    assert zero.evidence["premium_dev"] == 0.0
    assert "macro" in zero.evidence["score_basis"]["available_components"]
    assert calls["cg"] == 2


def test_symbol_only_protocol_match_cannot_supply_another_assets_tvl(isolated_fa):
    eng, _ = isolated_fa

    async def wrong_protocol():
        return [{"name": "unrelated protocol", "symbol": "BTC", "tvl": 5e6}]

    eng._llama_protocols = wrong_protocol
    result = run(eng.score("BTC"))
    assert result.evidence["tvl"] is None
    assert "tvl_protocol" not in result.evidence
    assert "thin-tvl" not in result.vetoes


def test_mapped_provider_failure_without_a_static_profile_is_unavailable(isolated_fa, monkeypatch):
    eng, _ = isolated_fa
    monkeypatch.setitem(fa_engine.KNOWN_CG_IDS, "ZZZ", "zzz-provider-id")

    async def failed(_cg_id):
        raise RuntimeError("provider down")

    eng._cg_overview = failed
    result = run(eng.score("ZZZIRT"))
    assert result.evidence["profile"] == "unavailable"
    assert result.fresh is False
    assert result.evidence["providers"]["coingecko"]["status"] == "failed"
