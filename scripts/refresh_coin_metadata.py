#!/usr/bin/env python3
"""Refresh local coin metadata once, using the same server .env/config as the app.

Run with the service's environment and user on deployments. No key is accepted
on the command line or printed. A partial/unavailable refresh exits nonzero.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import httpx

import _env  # noqa: F401 — load server/.env before reading config
import coin_metadata


async def refresh() -> int:
    cache = coin_metadata.CACHE
    async with httpx.AsyncClient(timeout=15, follow_redirects=False,
                                 headers={"User-Agent": "TraderBot/"}) as client:
        await cache.refresh(client, force=True)
    view = cache.view()
    print(json.dumps({
        "enabled": view["enabled"], "error": view["error"],
        "mapped": len(view["coins"]),
        "cached": sum(bool(r["image_url"]) for r in view["coins"]),
        "stale": sum(r["stale"] for r in view["coins"]),
        "last_success": view["last_success"],
    }, ensure_ascii=False))
    return 0 if cache.enabled and cache.last_success and not cache.error else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(refresh()))
