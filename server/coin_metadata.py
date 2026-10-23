"""CoinGecko metadata + bounded, persistent local images. Never used for trading.

Only curated IDs are queried; symbol-only provider search is deliberately absent.
HTTP handlers read disk/memory only. One background worker refreshes the cache.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)
REGISTRY = Path(__file__).with_name("coin_registry.json")
MAX_IMAGE = 512 * 1024
MAX_JSON = 2 * 1024 * 1024
TTL = 24 * 3600
RETRY = 3600
IMAGE_HOSTS = frozenset({"coin-images.coingecko.com", "assets.coingecko.com"})


def image_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def safe_image_url(url: str) -> bool:
    try:
        u = urlsplit(url)
        return (u.scheme == "https" and u.hostname in IMAGE_HOSTS
                and u.port in (None, 443) and not u.username and not u.password
                and u.path.startswith("/coins/images/"))
    except ValueError:
        return False


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".coin-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


async def bounded_get(client: httpx.AsyncClient, url: str, limit: int, **kwargs) -> bytes:
    async with client.stream("GET", url, follow_redirects=False, **kwargs) as response:
        response.raise_for_status()
        # raise_for_status also rejects redirects: do not follow untrusted URLs.
        chunks = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            chunks.extend(chunk)
            if len(chunks) > limit:
                raise ValueError("response too large")
        return bytes(chunks)


class CoinMetadata:
    def __init__(self, cache_dir: Path, *, key: str = "", enabled: bool = True,
                 registry: dict[str, str] | None = None):
        self.cache_dir = cache_dir
        self.key = key
        self.enabled = enabled
        self.registry = registry if registry is not None else json.loads(REGISTRY.read_text(encoding="utf-8"))
        if any(not re.fullmatch(r"[A-Z0-9]{1,24}", symbol)
               or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", coin_id)
               for symbol, coin_id in self.registry.items()):
            raise ValueError("invalid curated coin mapping")
        if not 1 <= len(self.registry) <= 250:
            raise ValueError("curated mapping must contain 1 to 250 coins")
        if len(set(self.registry.values())) != len(self.registry):
            raise ValueError("duplicate coin IDs in curated mapping")
        self.rows: dict[str, dict[str, Any]] = {}
        self.issues: dict[str, str] = {}
        self.last_attempt: float | None = None
        self.last_success: float | None = None
        self.error: str | None = None
        self.lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        path = self.cache_dir / "metadata.json"
        try:
            if path.stat().st_size > MAX_JSON:
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            # Never trust cached paths, IDs or image URLs. Reconstruct those.
            for symbol, coin_id in self.registry.items():
                row = data.get("coins", {}).get(symbol, {})
                if (isinstance(row, dict) and row.get("id") == coin_id
                        and isinstance(row.get("name"), str)
                        and isinstance(row.get("updated_at"), (int, float))):
                    self.rows[symbol] = {"id": coin_id, "symbol": symbol,
                                         "name": row["name"][:160],
                                         "updated_at": row["updated_at"]}
            allowed = {"coin_not_returned", "identity_mismatch", "invalid_metadata", "image_unavailable"}
            saved_issues = data.get("issues", {})
            if isinstance(saved_issues, dict):
                self.issues = {s: issue for s, issue in saved_issues.items()
                               if s in self.registry and isinstance(issue, str) and issue in allowed}
            if self.issues:
                self.error = "partial_refresh"
            saved = data.get("last_success")
            if isinstance(saved, (int, float)) and 0 < saved <= time.time():
                self.last_success = saved
        except (OSError, ValueError, TypeError, AttributeError):
            self.error = "cache_unreadable" if path.exists() else None

    def logo_path(self, coin_id: str) -> Path | None:
        if coin_id not in self.registry.values():
            return None
        path = self.cache_dir / f"{coin_id}.img"
        try:
            if path.is_file() and path.stat().st_size <= MAX_IMAGE:
                return path
        except OSError:
            return None
        return None

    def view(self) -> dict[str, Any]:
        now = time.time()
        coins = []
        for symbol, coin_id in self.registry.items():
            row = self.rows.get(symbol, {})
            updated = row.get("updated_at")
            path = self.logo_path(coin_id)
            coins.append({"id": coin_id, "symbol": symbol, "name": row.get("name"),
                          "updated_at": updated,
                          "stale": updated is None or now - updated >= TTL,
                          "error": self.issues.get(symbol),
                          "image_url": f"/api/coins/{coin_id}/logo" if path else None})
        return {"source": "coingecko", "enabled": self.enabled,
                "configured": bool(self.key), "last_attempt": self.last_attempt,
                "last_success": self.last_success, "error": self.error,
                "refresh_interval_s": TTL, "coins": coins}

    async def refresh(self, client: httpx.AsyncClient, *, force: bool = False) -> bool:
        if not self.enabled or self.lock.locked():
            return False
        async with self.lock:
            now = time.time()
            if not force:
                if self.last_attempt and now - self.last_attempt < RETRY:
                    return False
                if self.last_success and now - self.last_success < TTL:
                    return False
            self.last_attempt = now
            try:
                # Demo key is optional for deployments where public access works.
                # Never pass it to the image host or put it in query parameters.
                headers = {"x-cg-demo-api-key": self.key} if self.key else {}
                raw = await bounded_get(
                    client, "https://api.coingecko.com/api/v3/coins/markets", MAX_JSON,
                    params={"vs_currency": "usd", "ids": ",".join(self.registry.values()),
                            "per_page": 250, "page": 1, "sparkline": "false"},
                    headers=headers,
                )
                results = json.loads(raw)
                if not isinstance(results, list) or not results:
                    raise ValueError("invalid provider response")
                by_id = {r["id"]: r for r in results
                         if isinstance(r, dict) and isinstance(r.get("id"), str)}
                failed = 0
                for symbol, coin_id in self.registry.items():
                    row = by_id.get(coin_id)
                    issue = ("coin_not_returned" if not row else
                             "identity_mismatch" if str(row.get("symbol", "")).upper() != symbol else
                             "invalid_metadata" if not isinstance(row.get("name"), str)
                             or not safe_image_url(str(row.get("image", ""))) else None)
                    if issue:
                        self.issues[symbol] = issue
                        failed += 1
                        continue
                    assert row is not None
                    self.issues.pop(symbol, None)
                    # On an hourly partial retry, do not redownload the other
                    # already-fresh logos. Force explicitly refreshes all rows.
                    previous = self.rows.get(symbol, {})
                    if (not force and previous.get("updated_at")
                            and now - previous["updated_at"] < TTL and self.logo_path(coin_id)):
                        continue
                    try:
                        image = await bounded_get(client, row["image"], MAX_IMAGE)
                        if not image_type(image):
                            raise ValueError("unsupported image format")
                        # Write image before publishing metadata. Fixed ID filenames
                        # bound disk use; replace is atomic on Windows and Linux.
                        atomic_write(self.cache_dir / f"{coin_id}.img", image)
                        self.rows[symbol] = {"id": coin_id, "symbol": symbol,
                                             "name": row["name"][:160], "updated_at": now}
                    except (httpx.HTTPError, OSError, ValueError):
                        self.issues[symbol] = "image_unavailable"
                        failed += 1
                self.error = "partial_refresh" if failed else None
                if failed == len(self.registry):
                    self.error = "no_verified_metadata"
                    return False
                # Partial results retry hourly; each successful row has its own age.
                success = now if not failed else self.last_success
                atomic_write(self.cache_dir / "metadata.json", json.dumps({
                    "last_success": success, "coins": self.rows, "issues": self.issues,
                }, ensure_ascii=False).encode("utf-8"))
                self.last_success = success
                return True
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                # Do not log request objects, provider bodies or exception text:
                # HTTP errors can carry headers, including the server-only key.
                self.error = "refresh_failed"
                log.warning("coin metadata refresh failed; keeping local cache")
                return False

    async def run(self) -> None:
        if not self.enabled:
            return
        async with httpx.AsyncClient(timeout=15, follow_redirects=False,
                                     headers={"User-Agent": "TraderBot/"}) as client:
            while True:
                await self.refresh(client)
                await asyncio.sleep(RETRY)


def from_env() -> CoinMetadata:
    return CoinMetadata(
        Path(os.getenv("NBX_COIN_CACHE", str(Path(__file__).with_name("coin_cache")))),
        key=os.getenv("NBX_COINGECKO_API_KEY", "").strip(),
        enabled=os.getenv("NBX_COIN_METADATA_ENABLED", "1").strip() != "0",
    )


CACHE = from_env()
