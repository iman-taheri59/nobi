"""
Fundamental Analysis engine (ADR-0006) — free data sources only.

Signals produced per symbol:
  FA / 100 score  (0..100)
  vetoes: list[str]  (hard block reasons; see ADR-0006 §8)

Providers (Phase 2, free tier):
  CoinGecko  — market cap, FDV, vol/mcap, listing age, social
  DefiLlama  — TVL (for L1/L2/DeFi protocols), fees, revenue
  CryptoPanic — news event detection (optional; gently-limited)
  Internal   — USDTIRT premium & Nobitex liquidity features

Score formula (YAML-config'd weights, per-asset-class):
  fa = w1·Market + w2·Tokenomics + w3·OnChain + w4·Sentiment + w5·MacroFit − Penalties

Default v1 weights: 0.25/0.25/0.20/0.20/0.10 (ADR-0006 §7).
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("fa-engine")

# ---------------- data carriers ----------------
CG_BASE = "https://api.coingecko.com/api/v3"
LLAMA_BASE = "https://api.llama.fi"

#: The verified map — one source, not two (ADR-0025). `coin_registry.json` was
#: already curated for the coin icons and held fifty IDs while this module kept
#: its own fourteen, so 36 mappable symbols went unused and the two copies could
#: drift apart. The literal below stays as the floor: if the file cannot be read
#: the engine must still answer for the majors rather than lose every mapping.
REGISTRY = Path(__file__).with_name("coin_registry.json")


def _load_registry() -> dict[str, str]:
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except Exception as e:                      # a missing registry is not a crash
        log.warning("coin registry unreadable, using the built-in majors: %s", e)
        return {}
    return {str(k).upper(): str(v) for k, v in data.items() if k and v}


# Map Nobitex symbols → CoinGecko/DefiLlama ids for majors
_BUILTIN_CG_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "ADA": "cardano",
    "TON": "the-open-network", "TRX": "tron", "DOGE": "dogecoin",
    "LTC": "litecoin", "XRP": "ripple", "DOT": "polkadot", "LINK": "chainlink",
    "AVAX": "avalanche-2", "SHIB": "shiba-inu", "MATIC": "matic-network",
}
#: Registry first, built-ins as the fallback: a curated file always wins over a
#: literal, and the union means an unreachable file costs nothing.
KNOWN_CG_IDS: dict[str, str] = {**_BUILTIN_CG_IDS, **_load_registry()}
LLAMA_CHAINS_MAPPED = {
    "ETH": "Ethereum", "SOL": "Solana", "ADA": "Cardano", "TON": "TON",
    "TRX": "Tron", "AVAX": "Avalanche", "MATIC": "Polygon",
}
VETO_CACHE: dict[str, tuple[float, list[str]]] = {}
# Shared upstream payloads: DefiLlama /protocols is ~1MB and Fear&Greed is a
# single global value, so fetching them once per symbol is wasteful (and got
# us rate-limited by CoinGecko in testing).
_LLAMA_CACHE: dict[str, Any] = {"ts": 0.0, "data": []}
_FNG_CACHE: dict[str, Any] = {"ts": 0.0, "data": {"fg_value": 50}}
SHARED_TTL_S = 60
FA_CACHE: dict[str, tuple[float, FAResult]] = {}
CACHE_TTL_S = 60 * 10  # 10 minutes per symbol for FA lookup

FALLBACK_FA_PROFILES: dict[str, dict[str, Any]] = {
    "BTC": {"mcap": 1.44e12, "fdv": 1.53e12, "vol": 3.8e10, "tvl": 0, "days": 5700, "pow": True},
    "ETH": {"mcap": 4.05e11, "fdv": 4.05e11, "vol": 1.9e10, "tvl": 6.4e10, "days": 3350},
    "SOL": {"mcap": 8.2e10, "fdv": 9.9e10, "vol": 4.2e9, "tvl": 8.4e9, "days": 1700},
    "TON": {"mcap": 1.4e10, "fdv": 2.8e10, "vol": 3.5e8, "tvl": 4.5e8, "days": 1500},
    "TRX": {"mcap": 2.0e10, "fdv": 2.0e10, "vol": 5.1e8, "tvl": 7.6e9, "days": 2600},
    "DOGE": {"mcap": 3.1e10, "fdv": 3.1e10, "vol": 2.1e9, "tvl": 0, "days": 4000, "pow": True},
    "XRP": {"mcap": 1.25e11, "fdv": 2.2e11, "vol": 3.4e9, "tvl": 8.0e7, "days": 4200},
    "ADA": {"mcap": 2.5e10, "fdv": 3.2e10, "vol": 6.2e8, "tvl": 3.8e8, "days": 2650},
    "AVAX": {"mcap": 1.3e10, "fdv": 2.2e10, "vol": 4.8e8, "tvl": 1.3e9, "days": 1550},
    "LINK": {"mcap": 1.1e10, "fdv": 1.8e10, "vol": 3.9e8, "tvl": 1.1e9, "days": 2700},
    "DOT": {"mcap": 9.5e9, "fdv": 1.05e10, "vol": 2.7e8, "tvl": 2.5e8, "days": 1600},
    "LTC": {"mcap": 6.8e9, "fdv": 7.4e9, "vol": 4.1e8, "tvl": 0, "days": 4800, "pow": True},
    "SHIB": {"mcap": 1.4e10, "fdv": 1.4e10, "vol": 6.5e8, "tvl": 2.5e7, "days": 1650},
    "MATIC": {"mcap": 4.5e9, "fdv": 4.8e9, "vol": 2.2e8, "tvl": 9.5e8, "days": 2150},
}


def norm_base(sym: str) -> str:
    s = sym.upper()
    for suf in ("IRT", "USDT", "USD", "IRT"):
        if s.endswith(suf) and len(s) > len(suf):
            return s[: -len(suf)]
    return s


@dataclass
class FAResult:
    score: float = 50.0
    market: float = 50.0
    tokenomics: float = 50.0
    onchain: float = 50.0
    sentiment: float = 50.0
    macro: float = 50.0
    vetoes: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    #: True only when the numbers were fetched from a provider during this call.
    fresh: bool = False
    #: True when the answer came from the cache. A cached live read is still a
    #: live read — it must not be described as a static profile.
    cached: bool = False


class FAEngine:
    """Fetch, cache and score fundamentals for Nobitex symbols."""
    WEIGHTS = {"market": 0.25, "tokenomics": 0.25, "onchain": 0.20, "sentiment": 0.20, "macro": 0.10}

    def __init__(self, session_factory: Callable[[], httpx.AsyncClient] | None = None):
        self._factory = session_factory or (lambda: httpx.AsyncClient(timeout=httpx.Timeout(4.0)))
        self._client: httpx.AsyncClient | None = None
        self._last_err: str | None = None
        self._circuit_open_until: float = 0.0

    async def _ensure(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._factory()
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    def _is_circuit_open(self) -> bool:
        return time.monotonic() < self._circuit_open_until

    def _trip_circuit(self, duration_s: float = 180.0) -> None:
        self._circuit_open_until = time.monotonic() + duration_s

    # ---------- data fetchers ----------
    async def _cg_overview(self, cg_id: str) -> dict:
        if self._is_circuit_open():
            raise RuntimeError("FA circuit open")
        c = await self._ensure()
        r = await c.get(f"{CG_BASE}/coins/{cg_id}", params={"localization": "false", "tickers": "false"})
        r.raise_for_status()
        return r.json()

    async def _llama_protocols(self) -> list:
        if _LLAMA_CACHE["data"] and (time.time() - _LLAMA_CACHE["ts"]) < SHARED_TTL_S:
            return _LLAMA_CACHE["data"]
        if self._is_circuit_open():
            raise RuntimeError("FA circuit open")
        c = await self._ensure()
        r = await c.get(f"{LLAMA_BASE}/protocols")
        if not r.is_success:
            return _LLAMA_CACHE["data"] or []
        _LLAMA_CACHE["data"] = r.json() or []
        _LLAMA_CACHE["ts"] = time.time()
        return _LLAMA_CACHE["data"]

    async def _listen_prices(self) -> dict:
        """Fear & Greed + market headlines (best-effort, shared cache)."""
        if _FNG_CACHE["data"] and (time.time() - _FNG_CACHE["ts"]) < SHARED_TTL_S:
            return _FNG_CACHE["data"]
        if self._is_circuit_open():
            return _FNG_CACHE["data"] or {"fg_value": 50}
        try:
            c = await self._ensure()
            r = await c.get("https://api.alternative.me/fng/", params={"limit": 1})
            dat = (r.json() or {}).get("data", [{}])[0]
            _FNG_CACHE["data"] = {"fg_value": int(dat.get("value", 50))}
            _FNG_CACHE["ts"] = time.time()
            return _FNG_CACHE["data"]
        except Exception:
            self._trip_circuit()
            return _FNG_CACHE["data"] or {"fg_value": 50}

    # ---------- scorers ----------
    async def score(self, symbol: str, usdt_premium_dev: float = 0, nobil_depth_score: int = 50) -> FAResult:
        base = norm_base(symbol)
        # The premium veto fires on a threshold crossing, so the cache key has to
        # carry which side of it we are on. Without this a cached "clean" score
        # would survive a premium spike for ten minutes.
        prem_bucket = 1 if usdt_premium_dev > 0.04 else (-1 if usdt_premium_dev < -0.04 else 0)
        cache_key = f"{symbol.upper()}|p{prem_bucket}"
        if cache_key in FA_CACHE and (time.time() - FA_CACHE[cache_key][0]) < CACHE_TTL_S:
            _, cached_res = FA_CACHE[cache_key]
            return FAResult(
                score=cached_res.score,
                market=cached_res.market,
                tokenomics=cached_res.tokenomics,
                onchain=cached_res.onchain,
                sentiment=cached_res.sentiment,
                macro=cached_res.macro,
                vetoes=list(cached_res.vetoes),
                evidence=dict(cached_res.evidence),
                fresh=False,
                cached=True,
            )

        ev: dict[str, Any] = {}
        mkt, tok, onc, sen, mac = 50.0, 50.0, 50.0, 50.0, 50.0
        vetoes: list[str] = []

        cg = KNOWN_CG_IDS.get(base)
        days_since_genesis = None
        used_fallback = False

        try:
            if cg and not self._is_circuit_open():
                ov = await self._cg_overview(cg)
                md = ov.get("market_data", {})
                mcap = md.get("market_cap", {}).get("usd") or 0
                fdv = md.get("fully_diluted_valuation", {}).get("usd") or 0
                vol = md.get("total_volume", {}).get("usd") or 0
                ts = md.get("total_supply") or 0
                ms = md.get("max_supply") or 0
                if mcap >= 5e10:
                    mkt = 85
                elif mcap >= 1e10:
                    mkt = 74
                elif mcap >= 1e9:
                    mkt = 62
                elif mcap >= 1e8:
                    mkt = 50
                else:
                    mkt = 38
                vmc = (vol / mcap) if mcap > 0 else 0
                if vmc > 0.5:
                    mkt = min(mkt - 10, 100)
                elif vmc > 0.05:
                    mkt += 6
                if mcap and fdv:
                    ratio = mcap / fdv
                    tok = int(min(100, ratio * 130))
                if mcap and ts and ms and ms > 0:
                    dilution_gap = (ts / ms)
                    tok -= 12 if dilution_gap < 0.6 else 0
                gen = (ov.get("genesis_date") or None)
                if gen:
                    try:
                        dayms = (datetime.now(UTC) - datetime.fromisoformat(gen).replace(tzinfo=UTC)).days
                        days_since_genesis = dayms
                        if dayms < 90:
                            vetoes.append(f"new-listing<{dayms}d")
                    except Exception:
                        pass
                ev.update({"mcap": mcap, "fdv": fdv, "vmc": round(vmc, 3), "mcap_tfv_ratio": round(mcap / fdv, 2) if fdv else None, "days": days_since_genesis})
            elif base in FALLBACK_FA_PROFILES:
                used_fallback = True
        except Exception as e:
            self._last_err = str(e)
            self._trip_circuit()
            used_fallback = True

        if used_fallback and base in FALLBACK_FA_PROFILES:
            prof = FALLBACK_FA_PROFILES[base]
            mcap = prof["mcap"]
            fdv = prof["fdv"]
            vol = prof["vol"]
            tvl = prof.get("tvl", 0)
            days_since_genesis = prof.get("days", 1500)
            if mcap >= 5e10:
                mkt = 85
            elif mcap >= 1e10:
                mkt = 74
            elif mcap >= 1e9:
                mkt = 62
            else:
                mkt = 50
            vmc = (vol / mcap) if mcap > 0 else 0
            if vmc > 0.05:
                mkt += 6
            tok = int(min(100, (mcap / fdv) * 95)) if fdv else 70
            if tvl > 0:
                onc = round(40 + min(55, math.log10(tvl + 1) * 5.2), 1)
                ev["tvl"] = tvl
            else:
                onc = 58
            if prof.get("pow"):
                onc += 12
                ev["pow_bonus"] = True
            ev.update({"mcap": mcap, "fdv": fdv, "vmc": round(vmc, 3), "mcap_tfv_ratio": round(mcap / fdv, 2) if fdv else None, "days": days_since_genesis})
        else:
            try:
                if cg and not self._is_circuit_open():
                    chains = await self._llama_protocols()
                    match = next((p for p in chains if p.get("name", "").lower() == KNOWN_CG_IDS[base].replace("-", " ").lower() or p.get("symbol", "").upper() == base), None)
                    tvl = float((match or {}).get("tvl") or 0)
                    if tvl > 0:
                        # TVL only makes sense for assets that actually have a
                        # DeFi/L1 TVL figure; a zero/missing value must not
                        # become a "thin-tvl" veto (it used to fire for BTC).
                        onc = 40 + min(60, math.log10(tvl + 1) * 18)
                        ev["tvl"] = tvl
                        if tvl <= 1e7:
                            vetoes.append("thin-tvl")
                    else:
                        onc = 55
                        ev["tvl"] = None
                    if base in {"BTC", "LTC", "DOGE"}:
                        onc += 12
                        ev["pow_bonus"] = True
            except Exception as e:
                log.warning("defillama failed for %s: %s", symbol, e)
                self._trip_circuit()

        try:
            fg = await self._listen_prices()
            fgi = fg["fg_value"]
            sen = 40 + (fgi / 100) * 40
            if fgi <= 15:
                vetoes.append("extreme-fear")
            elif fgi >= 85:
                sen -= 10
            ev["fear_greed"] = fgi
        except Exception:
            pass

        # macro fit vs premium & IRT context
        if usdt_premium_dev > 0.04:
            mac = 40
            vetoes.append("premium-stretched")
        elif usdt_premium_dev < -0.04:
            mac = 62
            ev["cheap_irt"] = True
        else:
            mac = 55

        # unlocks / vesting — placeholder for calendar integration
        if base in {"XYZ"}:  # demo chain
            vetoes.append("unlock-7d>5%")

        score = (
            self.WEIGHTS["market"] * mkt
            + self.WEIGHTS["tokenomics"] * tok
            + self.WEIGHTS["onchain"] * onc
            + self.WEIGHTS["sentiment"] * sen
            + self.WEIGHTS["macro"] * mac
        )
        # adjust for cream volume proxy of nobitex pair
        score = min(score, score + (nobil_depth_score - 50) * 0.2)
        score = max(0, min(100, round(score, 1)))

        # ADR-0025: three states, not two. A symbol no provider is mapped for used
        # to leave here as `profile: "live"` and `fresh: True`, so a score built
        # from five neutral 50s and one market-wide Fear&Greed was counted as a
        # measurement (measured: "XYZIRT" -> 52.6, live, evidence {fear_greed: 51}).
        # The score itself does not change: only the claim about it does.
        # `cg` is the mapped provider id for this base asset; empty means no
        # provider is mapped at all, which is what `none` now says out loud.
        mapped = bool(cg)
        if used_fallback:
            ev["profile"] = "fallback"
        elif not mapped:
            ev["profile"] = "none"
        else:
            ev["profile"] = "live"
        res = FAResult(score=score, market=mkt, tokenomics=tok, onchain=onc, sentiment=sen, macro=mac,
                       vetoes=vetoes, evidence=ev, fresh=ev["profile"] == "live")
        VETO_CACHE[cache_key] = (time.time(), list(vetoes))
        FA_CACHE[cache_key] = (time.time(), res)
        return res


FA_ENGINE = FAEngine()
__all__ = ["FA_ENGINE", "FAResult", "norm_base"]
