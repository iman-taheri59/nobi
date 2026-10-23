# mypy: check_untyped_defs = False
"""Authoritative exchange data vs independently sourced logos; no live network."""
from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import auth
import coin_metadata
import main
import nobitex_metadata as nm
import settings_view


def options():
    return {"status": "ok", "coins": [
        {"coin": "btc", "name": "نام نوبیتکس", "stdName": "BTC", "displayPrecision": "8",
         "defaultNetwork": "BTC", "networkList": {
             "BTC": {"network": "BTC", "name": "Bitcoin", "isDefault": True,
                     "depositEnable": False, "withdrawEnable": True, "minConfirm": 0,
                     "withdrawFee": "0.0005", "withdrawMin": "0.001"}}},
        {"coin": "xyz", "name": "Exchange-only asset"},
    ], "nobitex": {"allCurrencies": ["btc", "xyz"], "activeCurrencies": ["btc"],
                   "minOrders": {"rls": "500000", "usdt": "5"},
                   "amountPrecisions": {"BTCIRT": "0.000001"},
                   "pricePrecisions": {"BTCIRT": "10", "BTCUSDT": "0.01"}}}


def logos():
    return {"source": "coingecko", "coins": [
        {"id": "bitcoin", "symbol": "BTC", "name": "CoinGecko Name",
         "updated_at": 123, "stale": True, "error": None,
         "image_url": "/api/coins/bitcoin/logo"},
        {"id": "ethereum", "symbol": "ETH", "name": "Ethereum", "updated_at": 456,
         "stale": True, "error": None, "image_url": None},
    ]}


def test_exchange_name_wins_but_logo_and_logo_timestamp_stay_independent(tmp_path):
    cache = nm.NobitexMetadata(tmp_path)
    assert cache.ingest(options())
    merged = cache.merge(logos())
    btc = next(r for r in merged["coins"] if r["symbol"] == "BTC")
    assert btc["name"] == "نام نوبیتکس"
    assert btc["name_source"] == btc["symbol_source"] == "nobitex"
    assert btc["image_url"] == "/api/coins/bitcoin/logo"
    assert btc["logo_source"] == "coingecko"
    assert btc["updated_at"] == 123
    assert btc["stale"] is True
    assert btc["name_stale"] is False
    assert btc["name_updated_at"] == cache.updated_at
    assert btc["exchange"]["default_network"] == "BTC"
    assert btc["exchange"]["networks"]["BTC"]["depositEnable"] is False
    assert merged["exchange"]["trading_rules"]["minOrders"] == {"rls": "500000", "usdt": "5"}


def test_exchange_only_currency_not_limited_to_logo_registry(tmp_path):
    c = nm.NobitexMetadata(tmp_path)
    assert c.ingest(options())
    xyz = next(r for r in c.merge(logos())["coins"] if r["symbol"] == "XYZ")
    assert xyz["name"] == "Exchange-only asset"
    assert xyz["id"] is None
    assert xyz["image_url"] is None
    assert xyz["logo_source"] is None
    assert xyz["exchange"]["active"] is False
    assert xyz["exchange"]["networks"] is None


def test_missing_exchange_name_falls_back_without_fabricating_networks(tmp_path):
    c = nm.NobitexMetadata(tmp_path)
    body = options()
    del body["coins"][0]["name"]
    assert c.ingest(body)
    merged = {r["symbol"]: r for r in c.merge(logos())["coins"]}
    assert merged["BTC"]["name"] == "CoinGecko Name"
    assert merged["BTC"]["name_source"] == "coingecko"
    assert merged["BTC"]["symbol_source"] == "nobitex"
    assert merged["ETH"]["exchange"] is None
    assert merged["ETH"]["name_source"] == "coingecko"
    assert merged["ETH"]["symbol_source"] == "curated_coingecko_mapping"


def test_unmeasured_exchange_is_unknown_not_invented(tmp_path):
    c = nm.NobitexMetadata(tmp_path)
    view = c.merge(logos())
    assert view["exchange"]["updated_at"] is None
    assert view["exchange"]["trading_rules"]["minOrders"] is None
    assert all(r["exchange"] is None for r in view["coins"])
    assert view["coins"][0]["name_source"] == "coingecko"


def test_restart_then_outage_keeps_exchange_name_and_real_age(tmp_path, monkeypatch):
    c = nm.NobitexMetadata(tmp_path)
    assert c.ingest(options())
    saved_time = c.updated_at
    restarted = nm.NobitexMetadata(tmp_path)
    assert restarted.updated_at == saved_time
    monkeypatch.setattr(nm.time, "time", lambda: saved_time + nm.TTL + 1)
    restarted.failed()
    btc = next(r for r in restarted.merge(logos())["coins"] if r["symbol"] == "BTC")
    assert btc["name"] == "نام نوبیتکس"  # stale primary, not a silent rebrand
    assert btc["name_stale"]
    assert btc["name_updated_at"] == saved_time
    assert restarted.error == "refresh_failed"


@pytest.mark.parametrize("bad", [None, [], {}, {"status": "failed"}, {"status": "ok"},
                                     {"status": "ok", "coins": "bad"},
                                     {"status": "ok", "coins": [{"coin": "../x"}]}])
def test_malformed_or_failed_response_preserves_last_good_snapshot(tmp_path, bad):
    c = nm.NobitexMetadata(tmp_path)
    assert c.ingest(options())
    before = c.path.read_bytes()
    assert not c.ingest(bad)
    assert c.path.read_bytes() == before
    assert c.view()["coins"]["BTC"]["name"] == "نام نوبیتکس"


def test_duplicate_identity_rejected(tmp_path):
    c = nm.NobitexMetadata(tmp_path)
    body = options()
    body["coins"].append({"coin": "BTC", "name": "Conflicting name"})
    assert not c.ingest(body)
    assert c.updated_at is None


def test_allowlist_excludes_secret_and_undocumented_logo_fields(tmp_path):
    c = nm.NobitexMetadata(tmp_path)
    body = options()
    body["secret"] = "do-not-persist"
    body["coins"][0]["logo"] = "https://evil.test/logo.png"
    body["coins"][0]["networkList"]["BTC"]["private_key"] = "do-not-persist"
    assert c.ingest(body)
    data = c.path.read_text(encoding="utf-8")
    assert "do-not-persist" not in data
    assert "evil.test" not in data
    assert "logo" not in data


def test_unknown_active_status_stays_null_and_zero_false_are_not_dropped(tmp_path):
    c = nm.NobitexMetadata(tmp_path)
    body = options()
    del body["nobitex"]["activeCurrencies"]
    assert c.ingest(body)
    row = c.view()["coins"]["BTC"]
    assert row["active"] is None
    assert row["networks"]["BTC"]["minConfirm"] == "0"
    assert row["networks"]["BTC"]["depositEnable"] is False


@pytest.mark.parametrize("bad", [True, "NaN", "Infinity", "-1", {}, "hello"])
def test_invalid_numeric_rules_do_not_become_defaults(tmp_path, bad):
    c = nm.NobitexMetadata(tmp_path)
    body = options()
    body["nobitex"]["minOrders"]["rls"] = bad
    assert c.ingest(body)
    assert "rls" not in c.view()["trading_rules"]["minOrders"]
    assert c.view()["trading_rules"]["minOrders"]["usdt"] == "5"


def test_corrupt_cache_is_unknown_and_recovers(tmp_path):
    path = tmp_path / "nobitex-options.json"
    path.write_text("broken", encoding="utf-8")
    c = nm.NobitexMetadata(tmp_path)
    assert c.error == "cache_unreadable"
    assert c.view()["coins"] == {}
    assert c.ingest(options())
    assert c.error is None


def test_disk_failure_does_not_mark_new_snapshot_fresh(tmp_path, monkeypatch):
    c = nm.NobitexMetadata(tmp_path)
    assert c.ingest(options())
    old = c.updated_at
    def fail(*args):
        raise PermissionError("private error text")
    monkeypatch.setattr(nm, "atomic_write", fail)
    body = options()
    body["coins"][0]["name"] = "new name"
    assert not c.ingest(body)
    assert c.updated_at == old
    assert c.view()["coins"]["BTC"]["name"] == "نام نوبیتکس"


def test_shared_options_request_feeds_metadata_and_existing_trading_adapter(tmp_path, monkeypatch):
    c = nm.NobitexMetadata(tmp_path)
    monkeypatch.setattr(nm, "CACHE", c)
    calls = []
    body = options()
    async def get(path, attempts):
        calls.append((path, attempts))
        return body
    applied = []
    monkeypatch.setattr(main, "client", SimpleNamespace(get=get))
    monkeypatch.setattr(main, "apply_options", lambda b: applied.append(b) or 17)
    assert asyncio.run(main._load_options()) == 17
    assert calls == [("/v2/options", 1)]
    assert applied == [body]
    assert c.view()["coins"]["BTC"]["name"] == "نام نوبیتکس"


def test_shared_request_network_failure_does_not_erase_metadata(tmp_path, monkeypatch):
    c = nm.NobitexMetadata(tmp_path)
    assert c.ingest(options())
    monkeypatch.setattr(nm, "CACHE", c)
    async def get(*args, **kw):
        raise TimeoutError("unreachable")
    monkeypatch.setattr(main, "client", SimpleNamespace(get=get))
    assert asyncio.run(main._load_options()) == 0
    assert c.error == "refresh_failed"
    assert c.view()["coins"]["BTC"]["name"] == "نام نوبیتکس"


@pytest.mark.parametrize("failed,delay", [(False, nm.TTL), (True, nm.RETRY)])
def test_options_loop_refreshes_periodically_and_cancels(tmp_path, monkeypatch, failed, delay):
    c = nm.NobitexMetadata(tmp_path)
    monkeypatch.setattr(nm, "CACHE", c)
    calls = []
    async def load():
        calls.append("request")
        c.error = "refresh_failed" if failed else None
    async def sleep(seconds):
        assert seconds == delay
        raise asyncio.CancelledError
    monkeypatch.setattr(main, "_load_options", load)
    monkeypatch.setattr(main.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main._options_loop())
    assert calls == ["request"]


def test_api_and_settings_report_real_sources_without_network(tmp_path, monkeypatch):
    c = nm.NobitexMetadata(tmp_path)
    assert c.ingest(options())
    monkeypatch.setattr(nm, "CACHE", c)
    cg = coin_metadata.CoinMetadata(tmp_path, enabled=False, registry={"BTC": "bitcoin"})
    monkeypatch.setattr(coin_metadata, "CACHE", cg)
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    client = TestClient(main.app)
    data = client.get("/api/coins").json()
    assert data["source"] == "nobitex+coingecko"
    btc = next(r for r in data["coins"] if r["symbol"] == "BTC")
    assert btc["name_source"] == "nobitex"
    assert btc["image_url"] is None
    state = settings_view.providers()["nobitex_metadata"]
    assert state["ok"] is True
    c.failed()
    state = settings_view.providers()["nobitex_metadata"]
    assert state["ok"] is False
    assert state["partial"] is True
    auth.reset_caches()


def test_all_currencies_without_details_has_no_fabricated_name_or_network(tmp_path):
    c = nm.NobitexMetadata(tmp_path)
    assert c.ingest({"status": "ok", "nobitex": {"allCurrencies": ["btc", "abc"]}})
    row = c.view()["coins"]["ABC"]
    assert row["name"] is None
    assert row["networks"] is None
    assert row["active"] is None


def test_merge_does_not_mutate_either_source(tmp_path):
    c = nm.NobitexMetadata(tmp_path)
    assert c.ingest(options())
    cg = logos()
    original = copy.deepcopy(cg)
    merged = c.merge(cg)
    merged["coins"][0]["exchange"]["networks"]["BTC"]["name"] = "mutated"
    assert c.view()["coins"]["BTC"]["networks"]["BTC"]["name"] == "Bitcoin"
    assert cg == original


def test_cache_is_not_applied_to_trading_engine_on_restart(tmp_path, monkeypatch):
    c = nm.NobitexMetadata(tmp_path)
    assert c.ingest(options())
    monkeypatch.setattr(main, "apply_options", lambda _: pytest.fail("display cache applied to orders"))
    restarted = nm.NobitexMetadata(tmp_path)
    assert restarted.view()["trading_rules"]["pricePrecisions"]["BTCIRT"] == "10"


def test_unmeasured_provider_settings_are_not_green(tmp_path, monkeypatch):
    monkeypatch.setattr(nm, "CACHE", nm.NobitexMetadata(tmp_path))
    provider = settings_view.providers()["nobitex_metadata"]
    assert provider["ok"] is None
    assert provider["stale"] is None


def test_future_timestamp_cache_is_rejected(tmp_path):
    (tmp_path / "nobitex-options.json").write_text(json.dumps({
        "updated_at": 10**20, "options": options(),
    }), encoding="utf-8")
    c = nm.NobitexMetadata(tmp_path)
    assert c.updated_at is None
    assert c.error == "cache_unreadable"
