"""
Local candle cache (ADR-0004) — Parquet persistence with TTL and coverage.

Purpose: avoid re-fetching identical candle ranges repeatedly during
walk-forward scans, grid searches and back-to-back analyses.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

import pandas as pd

from storage import writable_dir
from units import is_rial_market, udf_price_unit

log = logging.getLogger("candle-cache")

#: Where the Parquet files live: `NBX_CANDLE_CACHE`, or a writable fallback.
#: The cache is data, not code — keeping it in the checkout made a read-only
#: deployment fail at import time (see `storage.py`).
CACHE_ROOT = writable_dir("NBX_CANDLE_CACHE", Path(__file__).parent / "candle_cache",
                          label="candle-cache")

#: Soft cap for the whole cache directory. 2 GB is roughly a week of growth at
#: the measured rate, so a prune that stops running is noticed while there is
#: still six weeks of disk left, not on the day the disk fills.
CACHE_MAX_BYTES = int(float(os.getenv("NBX_CACHE_MAX_MB", "2048")) * 1024 * 1024)
#: Files older than this are useless: TTL is 30 minutes and no caller asks for
#: a range older than the scan window. A week is generous on purpose.
CACHE_MAX_AGE_S = float(os.getenv("NBX_CACHE_MAX_AGE_S", str(7 * 24 * 3600)))


def _key(symbol: str, resolution: str, from_ts: int, to_ts: int) -> Path:
    # Pre-contract caches may contain IRT candles divided by ten twice. Never
    # migrate them by guessing a scale: leave them untouched and fetch again.
    # Include the source-unit override so a configuration change cannot replay
    # values normalized under a different contract, even within the TTL.
    unit = udf_price_unit() if is_rial_market(symbol) else "usdt"
    raw = f"udf-toman-v2-{unit}-{symbol}-{resolution}-{from_ts}-{to_ts}"
    h = hashlib.md5(raw.encode()).hexdigest()[:16]
    return CACHE_ROOT / f"{symbol}-{resolution}-{h}.parquet"


class CandleCache:
    """Expensive fetches hit Nobitex; cached responses returned instantly."""

    def __init__(self, ttl_s: int = 1800):
        self.ttl = ttl_s
        self.hits = 0
        self.misses = 0

    def _meta_path(self, fp: Path) -> Path:
        return fp.with_suffix(".meta.json")

    def load(self, symbol: str, resolution: str, from_ts: int, to_ts: int,
             ttl: float | None = None) -> list[dict] | None:
        """`ttl` overrides the cache-wide TTL for this read (the chart route
        passes a short one: a window that ends on the forming bar is current
        for about a minute, not half an hour)."""
        fp = _key(symbol, resolution, from_ts, to_ts)
        if not fp.exists():
            self.misses += 1
            return None
        meta = self._meta_path(fp)
        if meta.exists():
            try:
                import json as _json
                if _json.loads(meta.read_text(encoding="utf-8")).get("synthetic"):
                    # Simulated candles must never be replayed as market data.
                    self.misses += 1
                    self.synthetic_rejected = getattr(self, "synthetic_rejected", 0) + 1
                    return None
            except Exception:
                pass
        # reject stale cache beyond TTL
        if (fp.stat().st_mtime + (self.ttl if ttl is None else ttl)) < __import__("time").time():
            self.misses += 1
            return None
        try:
            df = pd.read_parquet(fp, engine="pyarrow")
            rows = df.to_dict("records")
            self.hits += 1
            return rows
        except Exception as e:
            log.warning("cache read fail %s: %s", fp, e)
            self.misses += 1
            return None

    #: The cache had no eviction at all: every distinct (symbol, resolution,
    #: range) key wrote a Parquet file plus a sidecar and nothing ever removed
    #: either. Measured growth on production was ~0.27-0.32 GB/day against a
    #: 25 GB disk, i.e. the volume fills in 62-73 days, and the failure mode is
    #: "the service cannot write its own state file", which looks like a crash.
    #: A cap in code is the only bound that survives a forgotten cron entry.
    def prune(self, max_bytes: int | None = None, max_age_s: float | None = None) -> dict:
        """Delete cached files, oldest first, until the cache fits the budget.

        Returns what it did, because a silent prune is indistinguishable from a
        cache that never filled. Never raises: a cache is optional, and a
        read-only directory must not take the service down.
        """
        budget = CACHE_MAX_BYTES if max_bytes is None else max_bytes
        age = CACHE_MAX_AGE_S if max_age_s is None else max_age_s
        removed = 0
        freed = 0
        errors = 0
        try:
            files = [p for p in CACHE_ROOT.glob("*") if p.is_file()]
        except OSError as exc:  # unreadable cache dir: nothing to prune
            return {"removed": 0, "freed_bytes": 0, "errors": 1,
                    "reason": f"{type(exc).__name__}: {exc}"}
        entries = []
        total = 0
        now = time.time()
        for path in files:
            try:
                st = path.stat()
            except OSError:
                errors += 1
                continue
            total += st.st_size
            entries.append((st.st_mtime, st.st_size, path))
        # Expired first, then oversized, both oldest-first: a file that is both
        # old and large should go before a fresh small one either way.
        entries.sort(key=lambda e: e[0])
        for mtime, size, path in entries:
            expired = age is not None and (now - mtime) > age
            over = budget is not None and total > budget
            if not expired and not over:
                continue
            try:
                path.unlink()
            except OSError:
                errors += 1
                continue
            removed += 1
            freed += size
            total -= size
        if removed:
            log.info("candle cache pruned: %d file(s), %.2f MB, %d left",
                     removed, freed / 1e6, len(entries) - removed)
        return {"removed": removed, "freed_bytes": freed, "errors": errors,
                "remaining_bytes": total, "budget_bytes": budget,
                "max_age_s": age}

    def store(self, symbol: str, resolution: str, from_ts: int, to_ts: int, rows: list[dict],
              synthetic: bool = False):
        """Persist candles. Simulated rows are refused outright.

        Caching the fallback generator used to poison the next 30 minutes with
        fake prices after connectivity returned, with no way for a caller to
        tell them apart from real ones.
        """
        if synthetic:
            self.synthetic_skipped = getattr(self, "synthetic_skipped", 0) + 1
            return
        # Enforce the budget where the growth happens, at most once a minute.
        # A cron entry would do it too, and did not exist; this cannot be
        # forgotten by the next person who reinstalls the host.
        self._maybe_prune()
        fp = _key(symbol, resolution, from_ts, to_ts)
        try:
            df = pd.DataFrame(rows)
            if not df.empty:
                df.to_parquet(fp, engine="pyarrow", index=False)
                import json as _json
                self._meta_path(fp).write_text(
                    _json.dumps({"synthetic": False, "stored_at": __import__("time").time()}),
                    encoding="utf-8")
        except Exception as e:
            log.warning("cache write fail %s: %s", fp, e)

    def _maybe_prune(self) -> None:
        last = getattr(self, "_pruned_at", 0.0)
        if time.time() - last < 60:
            return
        self._pruned_at = time.time()
        self.last_prune = self.prune()

    def stats(self) -> dict:
        return {
            "hits": self.hits,
            "pruned": getattr(self, "last_prune", None),
            "misses": self.misses,
            "synthetic_skipped": getattr(self, "synthetic_skipped", 0),
            "synthetic_rejected": getattr(self, "synthetic_rejected", 0),
            "cache_dir": str(CACHE_ROOT),
        }


CANDLE_CACHE = CandleCache()
__all__ = ["CACHE_ROOT", "CANDLE_CACHE"]
