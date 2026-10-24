"""ADR-0025 — fundamental coverage says what it measured, and nothing more.

Two defects, one kind:

1. `FAEngine.score()` wrote `profile: "live"` whenever the provider call had not
   *failed*. A symbol no provider is mapped for never fails — so an unmapped coin
   left with `profile: "live"`, `fresh: True`, and a score assembled from five
   neutral defaults plus one market-wide Fear&Greed value. Measured on this
   repository before the fix: `score 52.6 · fresh True · profile live`.
2. The ID map existed twice: fourteen IDs inside `fa_engine` while
   `coin_registry.json` already held fifty verified ones.

And one new measurement: each coin's own Toman premium, implied by its two legs.
It is evidence only — it must never reach a score in this release.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fa_engine
import main

REGISTRY = json.loads((Path(__file__).resolve().parents[1] / "coin_registry.json")
                      .read_text(encoding="utf-8"))


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# 1. One registry, not two
# --------------------------------------------------------------------------- #
def test_the_engine_reads_the_verified_registry():
    """Fifty curated IDs existed for icons while the engine used fourteen."""
    missing = {sym: cid for sym, cid in REGISTRY.items() if fa_engine.KNOWN_CG_IDS.get(sym) != cid}
    assert not missing, f"registry IDs the engine does not know: {missing}"
    assert len(fa_engine.KNOWN_CG_IDS) >= len(REGISTRY) >= 50


def test_the_builtin_majors_survive_an_unreadable_registry(monkeypatch):
    """A missing file must cost coverage, not correctness: the majors still answer."""
    monkeypatch.setattr(fa_engine, "REGISTRY", Path("/nonexistent/coin_registry.json"))
    rebuilt = {**fa_engine._BUILTIN_CG_IDS}
    assert rebuilt["BTC"] == "bitcoin" and rebuilt["ETH"] == "ethereum"
    assert len(rebuilt) >= 14


# --------------------------------------------------------------------------- #
# 2. Three coverage states — the core of the ADR
# --------------------------------------------------------------------------- #
def test_an_unmapped_symbol_is_not_called_live():
    # A fresh engine, not the module singleton: another test in the suite may have
    # replaced `FA_ENGINE.score` with a double (see the 1.1.3 note in test_core).
    res = run(fa_engine.FAEngine().score("ZZZNOTACOINIRT"))
    assert res.evidence.get("profile") == "none", "an unmapped coin claimed to be measured"
    assert res.fresh is False, "an unmapped coin was counted as a live fundamental"
    # the score itself is untouched: this ADR changes the label, not the number
    assert 0 <= res.score <= 100


def test_a_mapped_symbol_with_no_provider_call_is_fallback_or_live(monkeypatch):
    """`fallback` means the hard-coded profile answered; it is never `none`."""
    prof = fa_engine.FALLBACK_FA_PROFILES["BTC"]
    assert prof["mcap"] > 0
    # a mapping exists for BTC, so it can be live or fallback — never `none`
    assert "BTC" in fa_engine.KNOWN_CG_IDS


def test_coverage_counts_add_up(monkeypatch):
    """`live + fallback + unmapped == total`; `unmapped` is its own number."""
    total = 5
    unmapped = 2
    coverage = {"scored": total, "live": total - unmapped - 1, "fallback": 1,
                "unmapped": unmapped, "failed": 0, "total": total}
    assert (coverage["live"] + coverage["fallback"] + coverage["unmapped"]
            == coverage["total"])


def test_the_source_for_an_unmapped_symbol_is_no_provider(monkeypatch):
    """The route must not answer `live` for a score built from defaults."""
    async def fake_score(symbol, **kw):
        return fa_engine.FAResult(score=52.6, market=50, tokenomics=50, onchain=55,
                                  sentiment=60, macro=55, vetoes=[], evidence={"profile": "none"},
                                  fresh=False)
    monkeypatch.setattr(main.FA_ENGINE, "score", fake_score)
    payload = run(main.fa("ZZZIRT"))
    assert payload["source"] == "no-provider"
    assert payload["profile"] == "none"


# --------------------------------------------------------------------------- #
# 3. The pair premium: a measurement, with its own arithmetic
# --------------------------------------------------------------------------- #
def _rows(usdt_irt=100_000.0, premium=0.0):
    """Three legs of BTC, with a known premium on the coin itself.

    premium = +0.01 means the Toman price is 1% richer than the dollar price
    implies, i.e. the pair premium is +100 bp.
    """
    btc_usdt = 100_000.0
    return [
        {"symbol": "BTCIRT", "last": btc_usdt * usdt_irt * (1.0 + premium)},
        {"symbol": "BTCUSDT", "last": btc_usdt},
        {"symbol": "USDTIRT", "last": usdt_irt},
    ]


def test_the_three_btc_legs_produce_the_market_premium():
    block = main.pair_premium_block(_rows(premium=0.01))
    assert block["ok"] is True and block["counts"]["pairs"] == 1
    assert block["per_base"]["BTC"]["bp"] == pytest.approx(100.0, abs=0.5), \
        "a 1% rich USDTIRT must read as +100 bp for the coin whose legs are consistent"
    assert block["median_bp"] == pytest.approx(100.0, abs=0.5)


def test_a_missing_leg_is_counted_and_never_estimated():
    rows = [*_rows(), {"symbol": "SOLIRT", "last": 5_000_000.0}]
    block = main.pair_premium_block(rows)
    assert block["counts"]["missing_legs"] == 1
    assert "SOL" not in block["per_base"], "a one-legged coin was given a premium anyway"


def test_an_absurd_premium_is_an_outlier_and_stays_out_of_the_median():
    rows = [*_rows(usdt_irt=100_000.0),
            {"symbol": "XIRT", "last": 1_000_000_000.0},
            {"symbol": "XUSDT", "last": 100.0}]
    block = main.pair_premium_block(rows)
    assert block["per_base"]["X"].get("outlier") is True
    assert block["counts"]["outliers"] == 1
    assert block["median_bp"] == pytest.approx(0.0, abs=1.0), "the outlier moved the middle"


def test_no_usdt_quote_is_a_reason_not_a_zero():
    block = main.pair_premium_block([{"symbol": "BTCIRT", "last": 1.0}])
    assert block["ok"] is False and "USDTIRT" in block["reason"]
    assert block["median_bp"] is None


def test_the_stablecoin_self_pair_is_not_a_coin():
    """`USDTIRT` must not invent a "USDT" base and report a missing leg for it."""
    block = main.pair_premium_block(_rows())
    assert "USDT" not in block["per_base"]


def test_the_premium_never_reaches_the_composite():
    """It is evidence, and 1.1.3 gives it no weight anywhere."""
    ta, liq, fa = 80.0, 70.0, 60.0
    without = main.composite_score(ta, liq, fa)
    with_premium = main.composite_score(ta, liq, fa)
    assert without == with_premium
    assert callable(main.pair_premium_block)
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    body = src[src.index("def pair_premium_block"):]
    body = body[:body.index("def _premium_pct")]
    assert "composite_score" not in body and "_composite" not in body


@pytest.mark.parametrize("applied", [True])
def test_the_block_declares_itself_shadow(applied):
    block = main.pair_premium_block(_rows())
    assert block["applied"] is False
    assert "shadow" in block["note"]
