"""
Local candle cache (ADR-0004) — Parquet persistence with TTL and coverage.

Purpose: avoid re-fetching identical candle ranges repeatedly during
walk-forward scans, grid searches and back-to-back analyses.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger("candle-cache")

CACHE_ROOT = Path(__file__).parent / "candle_cache"
CACHE_ROOT.mkdir(exist_ok=True)


def _key(symbol: str, resolution: str, from_ts: int, to_ts: int) -> Path:
    raw = f"{symbol}-{resolution}-{from_ts}-{to_ts}"
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

    def load(self, symbol: str, resolution: str, from_ts: int, to_ts: int) -> list[dict] | None:
        fp = _key(symbol, resolution, from_ts, to_ts)
        if not fp.exists():
            self.misses += 1
            return None
        meta = self._meta_path(fp)
        if meta.exists():
            try:
                import json as _json
                if _json.loads(meta.read_text()).get("synthetic"):
                    # Simulated candles must never be replayed as market data.
                    self.misses += 1
                    self.synthetic_rejected = getattr(self, "synthetic_rejected", 0) + 1
                    return None
            except Exception:
                pass
        # reject stale cache beyond TTL
        if (fp.stat().st_mtime + self.ttl) < __import__("time").time():
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
        fp = _key(symbol, resolution, from_ts, to_ts)
        try:
            df = pd.DataFrame(rows)
            if not df.empty:
                df.to_parquet(fp, engine="pyarrow", index=False)
                import json as _json
                self._meta_path(fp).write_text(_json.dumps({"synthetic": False, "stored_at": __import__("time").time()}))
        except Exception as e:
            log.warning("cache write fail %s: %s", fp, e)

    def stats(self) -> dict:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "synthetic_skipped": getattr(self, "synthetic_skipped", 0),
            "synthetic_rejected": getattr(self, "synthetic_rejected", 0),
            "cache_dir": str(CACHE_ROOT),
        }


CANDLE_CACHE = CandleCache()
__all__ = ["CACHE_ROOT", "CANDLE_CACHE"]
