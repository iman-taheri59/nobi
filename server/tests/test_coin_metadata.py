# mypy: check_untyped_defs = False
"""Deterministic provider/cache tests; no live network or real credentials."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import auth
import coin_metadata as cm
import main

PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"
URL = "https://coin-images.coingecko.com/coins/images/1/small/bitcoin.png"


def row(**kw):
    return {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "image": URL, **kw}


def cache(tmp_path, **kw):
    return cm.CoinMetadata(tmp_path, registry={"BTC": "bitcoin"}, **kw)


def refresh(c, handler, **kw):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await c.refresh(client, **kw)
    return asyncio.run(run())


def handler(request):
    if request.url.host == "api.coingecko.com":
        return httpx.Response(200, json=[row()])
    return httpx.Response(200, content=PNG)


def test_fetch_persist_restart_and_no_network_on_view(tmp_path):
    c = cache(tmp_path)
    assert refresh(c, handler)
    restarted = cache(tmp_path)
    view = restarted.view()
    assert view["last_success"] is not None
    assert view["coins"][0]["name"] == "Bitcoin"
    assert view["coins"][0]["image_url"] == "/api/coins/bitcoin/logo"
    assert not view["coins"][0]["stale"]
    assert restarted.logo_path("bitcoin").read_bytes() == PNG
    assert not refresh(restarted, lambda _: pytest.fail("fresh cache fetched again"))


def test_key_goes_only_to_api_never_images_or_public_json(tmp_path):
    c = cache(tmp_path, key="private-demo-key")
    def check(request):
        assert "private-demo-key" not in str(request.url)
        if request.url.host == "api.coingecko.com":
            assert request.headers["x-cg-demo-api-key"] == "private-demo-key"
        else:
            assert "x-cg-demo-api-key" not in request.headers
        return handler(request)
    assert refresh(c, check)
    assert "private-demo-key" not in json.dumps(c.view())
    assert "private-demo-key" not in (tmp_path / "metadata.json").read_text()


@pytest.mark.parametrize("url", [
    "http://coin-images.coingecko.com/coins/images/1.png",
    "https://127.0.0.1/coins/images/1.png",
    "https://coin-images.coingecko.com.evil.test/coins/images/1.png",
    "https://coin-images.coingecko.com@evil.test/coins/images/1.png",
    "https://evil.test/coins/images/1.png",
    "https://coin-images.coingecko.com:8443/coins/images/1.png",
    "https://coin-images.coingecko.com/private/1.png",
    "file:///etc/passwd", "https://[broken",
])
def test_reject_untrusted_image_urls_without_request(tmp_path, url):
    calls = []
    def check(request):
        calls.append(request)
        return httpx.Response(200, json=[row(image=url)])
    c = cache(tmp_path)
    assert not refresh(c, check)
    assert len(calls) == 1
    assert not c.logo_path("bitcoin")


@pytest.mark.parametrize("change", [
    {"id": "fake-bitcoin"}, {"symbol": "fake"}, {"name": None}, {"image": None},
])
def test_id_symbol_and_name_must_match_curated_mapping(tmp_path, change):
    c = cache(tmp_path)
    assert not refresh(c, lambda _: httpx.Response(200, json=[row(**change)]))
    assert c.view()["error"] == "no_verified_metadata"
    assert c.rows == {}


@pytest.mark.parametrize("code", [301, 302, 401, 429, 500])
def test_failures_keep_previous_image_and_age(tmp_path, code):
    c = cache(tmp_path)
    assert refresh(c, handler)
    before = c.rows.copy()
    assert not refresh(c, lambda _: httpx.Response(code, headers={"Location": "https://evil.test"}), force=True)
    assert c.rows == before
    assert c.logo_path("bitcoin").read_bytes() == PNG
    assert c.error == "refresh_failed"
    assert not refresh(c, lambda _: pytest.fail("no retry before cooldown"))


@pytest.mark.parametrize("image", [b"<svg><script>bad</script></svg>", b"not an image", PNG * 50000])
def test_bad_and_oversized_images_preserve_old_logo(tmp_path, image):
    c = cache(tmp_path)
    assert refresh(c, handler)
    def bad(request):
        return handler(request) if request.url.host == "api.coingecko.com" else httpx.Response(200, content=image)
    assert not refresh(c, bad, force=True)
    assert c.logo_path("bitcoin").read_bytes() == PNG


def test_image_redirect_not_followed(tmp_path):
    c = cache(tmp_path)
    calls = []
    def redirected(request):
        calls.append(str(request.url))
        if request.url.host == "api.coingecko.com":
            return handler(request)
        return httpx.Response(302, headers={"Location": "https://evil.test/image.png"})
    assert not refresh(c, redirected)
    assert len(calls) == 2


def test_disabled_does_not_fetch_or_destroy_persistent_cache(tmp_path):
    c = cache(tmp_path)
    assert refresh(c, handler)
    disabled = cache(tmp_path, enabled=False)
    assert not refresh(disabled, lambda _: pytest.fail("disabled fetched"), force=True)
    assert disabled.view()["coins"][0]["image_url"]


def test_corrupt_cache_and_unknown_ids_are_safe(tmp_path):
    (tmp_path / "metadata.json").write_text("broken json")
    c = cache(tmp_path)
    assert c.view()["error"] == "cache_unreadable"
    assert c.view()["coins"][0]["name"] is None
    assert c.view()["coins"][0]["stale"]
    assert c.logo_path("../../secret") is None
    assert c.logo_path("ethereum") is None
    assert refresh(c, handler)


def test_partial_refresh_and_per_coin_staleness(tmp_path):
    c = cm.CoinMetadata(tmp_path, registry={"BTC": "bitcoin", "ETH": "ethereum"})
    assert refresh(c, handler)
    assert c.error == "partial_refresh"
    assert c.last_success is None
    by_symbol = {r["symbol"]: r for r in c.view()["coins"]}
    assert not by_symbol["BTC"]["stale"]
    assert by_symbol["ETH"]["stale"]
    assert by_symbol["ETH"]["image_url"] is None


def test_expired_metadata_is_marked_stale_and_refreshed(tmp_path, monkeypatch):
    c = cache(tmp_path)
    assert refresh(c, handler)
    future = c.last_success + cm.TTL + 1
    monkeypatch.setattr(cm.time, "time", lambda: future)
    assert c.view()["coins"][0]["stale"]
    assert refresh(c, handler)
    assert not c.view()["coins"][0]["stale"]


def test_mapping_rejects_paths_and_duplicate_identity(tmp_path):
    with pytest.raises(ValueError):
        cm.CoinMetadata(tmp_path, registry={"BTC": "../escape"})
    with pytest.raises(ValueError):
        cm.CoinMetadata(tmp_path, registry={"BTC": "bitcoin", "WBTC": "bitcoin"})


def test_endpoint_serves_local_image_and_has_no_refresh_side_effect(tmp_path, monkeypatch):
    c = cache(tmp_path)
    assert refresh(c, handler)
    monkeypatch.setattr(cm, "CACHE", c)
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    client = TestClient(main.app)
    assert client.get("/api/coins").json()["coins"][0]["name"] == "Bitcoin"
    response = client.get("/api/coins/bitcoin/logo")
    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/coins/no-such-coin/logo").status_code == 404
    c.logo_path("bitcoin").write_bytes(b"corrupt")
    assert client.get("/api/coins/bitcoin/logo").status_code == 404
    auth.reset_caches()


def test_metadata_and_images_respect_existing_login_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    auth.create_user("owner1", "a long enough password", role="owner")
    client = TestClient(main.app)
    assert client.get("/api/coins").status_code == 401
    assert client.get("/api/coins/bitcoin/logo").status_code == 401
    assert client.post("/api/auth/login", json={"username": "owner1", "password": "a long enough password"}).status_code == 200
    assert client.get("/api/coins").status_code == 200
    auth.reset_caches()


def test_cancel_background_worker_cleanly(tmp_path):
    c = cache(tmp_path)
    async def run():
        # Cancellation must escape refresh rather than being mistaken for a
        # provider failure, otherwise service shutdown hangs.
        async def slow(*args, **kwargs):
            await asyncio.sleep(3600)
        c.refresh = slow
        task = asyncio.create_task(c.run())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())


def test_partial_retry_downloads_only_missing_or_expired_images(tmp_path, monkeypatch):
    c = cm.CoinMetadata(tmp_path, registry={"BTC": "bitcoin", "ETH": "ethereum"})
    assert refresh(c, handler)
    future = c.last_attempt + cm.RETRY + 1
    monkeypatch.setattr(cm.time, "time", lambda: future)
    calls = []
    def only_api(request):
        calls.append(str(request.url))
        assert request.url.host == "api.coingecko.com"
        return handler(request)
    assert refresh(c, only_api)
    assert len(calls) == 1
    assert c.view()["coins"][1]["error"] == "coin_not_returned"


def test_symbol_mismatch_is_reported_and_persisted_without_substitution(tmp_path):
    c = cm.CoinMetadata(tmp_path, registry={"BTC": "bitcoin", "ETH": "ethereum"})
    def renamed(request):
        if request.url.host == "api.coingecko.com":
            return httpx.Response(200, json=[row(), row(id="ethereum", symbol="different")])
        return handler(request)
    assert refresh(c, renamed)
    assert c.view()["coins"][1]["error"] == "identity_mismatch"
    restarted = cm.CoinMetadata(tmp_path, registry=c.registry)
    assert restarted.view()["error"] == "partial_refresh"
    assert restarted.view()["coins"][1]["error"] == "identity_mismatch"
    assert restarted.logo_path("ethereum") is None


def test_refresh_is_single_flight(tmp_path):
    c = cache(tmp_path)
    calls = []
    async def delayed(request):
        calls.append(str(request.url))
        await asyncio.sleep(0.01)
        return handler(request)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(delayed)) as client:
            results = await asyncio.gather(*(c.refresh(client, force=True) for _ in range(8)))
        assert results.count(True) == 1
    asyncio.run(run())
    assert len(calls) == 2


def test_large_metadata_response_rejected_without_clobbering_cache(tmp_path):
    c = cache(tmp_path)
    assert refresh(c, handler)
    old = (tmp_path / "metadata.json").read_bytes()
    assert not refresh(c, lambda _: httpx.Response(200, content=b" " * (cm.MAX_JSON + 1)), force=True)
    assert (tmp_path / "metadata.json").read_bytes() == old


def test_persistence_failure_does_not_mark_batch_successful(tmp_path, monkeypatch):
    c = cache(tmp_path)
    write = cm.atomic_write
    def broken(path, data):
        if path.name == "metadata.json":
            raise PermissionError("do not log this provider secret")
        write(path, data)
    monkeypatch.setattr(cm, "atomic_write", broken)
    assert not refresh(c, handler)
    assert c.last_success is None
    assert c.error == "refresh_failed"


def test_provider_failure_never_logs_key_or_response_body(tmp_path, caplog):
    c = cache(tmp_path, key="very-private-key")
    assert not refresh(c, lambda _: httpx.Response(401, text="very-private-key"))
    assert "very-private-key" not in caplog.text
    assert "very-private-key" not in json.dumps(c.view())


def test_settings_reports_partial_not_healthy_and_keeps_key_private(tmp_path, monkeypatch):
    import settings_view
    c = cm.CoinMetadata(tmp_path, registry={"BTC": "bitcoin", "ETH": "ethereum"})
    assert refresh(c, handler)
    monkeypatch.setattr(cm, "CACHE", c)
    monkeypatch.setenv("NBX_COINGECKO_API_KEY", "a-private-key-for-server-only")
    provider = settings_view.providers()["coin_metadata"]
    assert provider["partial"] is True
    assert provider["ok"] is False
    assert "1/2 cached" in provider["kind"]
    assert "a-private-key-for-server-only" not in json.dumps(settings_view.snapshot())


def test_settings_unmeasured_provider_is_unknown_not_green(tmp_path, monkeypatch):
    import settings_view
    monkeypatch.setattr(cm, "CACHE", cache(tmp_path))
    provider = settings_view.providers()["coin_metadata"]
    assert provider["ok"] is None
    assert provider["stale"] is None
    assert provider["partial"] is False
