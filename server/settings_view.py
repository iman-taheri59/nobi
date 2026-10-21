"""Read-only configuration view (ADR-0011 page 7).

The rule this module exists to enforce: **the settings page must not be able
to disagree with the running engine.** Every value is read from the module
that actually uses it, and each one carries the `source` it came from, so a
drift shows up as a wrong source string rather than as silent divergence.

What it deliberately does *not* do:

* **Never edit.** Weights live in code (and in `config_hash`); an editor here
  would make backtests and live signals incomparable. Transparency, not a
  control surface — exactly what ADR-0011 asks for.
* **Never print a secret.** Tokens are reported as presence + a masked tail.
  A page that renders a key is a page that leaks it into screenshots, logs and
  browser history.
"""
from __future__ import annotations

import os
import time
from typing import Any

import auth
import fa_engine
import journal
import notify
import risk_engine
import ta_engine
from ws_execution import POLL_S as STREAM_POLL_S
from ws_execution import STREAM_EXECUTOR, WS_URL


def _mask(value: str) -> str | None:
    """Enough to tell two keys apart, not enough to use one."""
    if not value:
        return None
    return f"…{value[-4:]}" if len(value) > 8 else "set"


def _secret(name: str) -> dict[str, Any]:
    raw = os.getenv(name, "").strip()
    return {"name": name, "present": bool(raw), "hint": _mask(raw),
            # Relative on purpose: the absolute path would leak the deployment
            # layout to a browser for no benefit.
            "where": "server/.env"}


def env_file() -> dict[str, Any]:
    """Whether `server/.env` was read at startup — and if not, why not.

    Without this, "token set in .env but the UI says unconfigured" is
    indistinguishable from "token not set at all".
    """
    import _env

    st = dict(_env.STATUS)
    st["path"] = "server/.env"          # never the absolute path
    return st


def weights() -> dict[str, Any]:
    return {
        "ta_components": {
            "values": dict(ta_engine.COMPONENT_WEIGHTS),
            "source": "ta_engine.COMPONENT_WEIGHTS",
            "note": "weights of the four TA components, before the regime gate",
        },
        "composite": {
            "values": {"ta": _main_weights()[0], "fa": _main_weights()[1],
                       "liq": _main_weights()[2]},
            "source": "main.TA_W/FA_W/LIQ_W",
            "note": "when fundamentals are missing their weight is renormalised into TA+LIQ",
        },
        "risk_model": {
            "values": {
                "stop_atr_multiple": journal.RISK_K,
                "tp1_r": journal.TP1_R,
                "tp2_r": journal.TP2_R,
                "trail_after_tp1_r": journal.TRAIL_R,
                "long_min_score": journal.LONG_MIN,
            },
            "source": "journal.RISK_K/TP1_R/TP2_R/TRAIL_R/LONG_MIN",
            "config_hash": journal.CONFIG_HASH,
        },
        "fa_subweights": {
            "values": dict(getattr(fa_engine.FA_ENGINE, "WEIGHTS", {})),
            "source": "fa_engine.FA_ENGINE.WEIGHTS",
            "note": "fundamental sub-scores; only used when live FA is available",
        },
    }


def _main_weights() -> tuple[float, float, float]:
    import main  # local import: `main` imports this module's siblings

    return main.TA_W, main.FA_W, main.LIQ_W


def thresholds() -> dict[str, Any]:
    return {
        "long": {"value": ta_engine.LONG_SCORE_MIN, "source": "ta_engine.LONG_SCORE_MIN"},
        "neutral": {"value": ta_engine.NEUTRAL_SCORE_MIN, "source": "ta_engine.NEUTRAL_SCORE_MIN"},
        "adx_trend": {"value": ta_engine.ADX_TREND_MIN, "source": "ta_engine.ADX_TREND_MIN"},
        "range_gate": {"value": ta_engine.RANGE_GATE, "source": "ta_engine.RANGE_GATE"},
        "msi": {
            "corrupt_ratio": journal.MSI_CORRUPT_RATIO,
            "degraded_ratio": journal.MSI_DEGRADED_RATIO,
            "min_samples": journal.MSI_MIN_SAMPLES,
            "window_days": journal.MSI_WINDOW_DAYS,
            "source": "journal.MSI_*",
        },
    }


def risk_limits() -> dict[str, Any]:
    cfg = risk_engine.RiskConfig(equity=0.0)      # defaults, not live state
    return {
        "values": {
            "risk_pct": cfg.risk_pct,
            "max_risk_pct": cfg.max_risk_pct,
            "max_concurrent": cfg.max_concurrent,
            "max_single_asset_pct": cfg.max_single_asset_pct,
            "max_heat_pct": cfg.max_heat_pct,
            "min_cash_pct": cfg.min_cash_pct,
            "stop_k_multiplier": cfg.risk_k_multiplier,
        },
        "source": "risk_engine.RiskConfig defaults",
    }


def flags() -> list[dict[str, Any]]:
    TICK_S = float(os.getenv("NBX_TICK_S", "8"))
    stream = STREAM_EXECUTOR.status()
    return [
        {"name": "NBX_TICK_S", "value": TICK_S, "unit": "s",
         "meaning": "how often open positions are re-evaluated"},
        {"name": "NBX_STREAM_POLL_S", "value": STREAM_POLL_S, "unit": "s",
         "meaning": "order-book polling cadence of the paper broker"},
        {"name": "NBX_WS_URL", "value": (WS_URL or None),
         "meaning": "empty -> REST polling; set -> attempt a real stream"},
        {"name": "stream.transport", "value": stream.get("transport"),
         "meaning": "what the executor is actually doing right now"},
        {"name": "NBX_RATE_LIMIT", "value": int(os.getenv("NBX_RATE_LIMIT", "60")),
         "unit": "req/min", "meaning": "mutations per client+path"},
        {"name": "NBX_DIGEST_HOURS", "value": float(os.getenv("NBX_DIGEST_HOURS", "0")),
         "unit": "h", "meaning": "0 disables the scheduled journal export"},
        {"name": "NBX_EXPORT_DIR",
         "value": os.getenv("NBX_EXPORT_DIR", str(journal.DB_PATH.parent / "exports")),
         "meaning": "where scheduled exports are written"},
        {"name": "NBX_JOURNAL_DB", "value": str(journal.DB_PATH),
         "meaning": "journal database (runtime artifact, git-ignored)"},
        {"name": "NBX_AUTH_DB", "value": str(auth.AUTH_DB),
         "meaning": "accounts and sessions (hashes only; back it up)"},
        {"name": "NBX_UPSTREAM_TTL_S", "value": float(os.getenv("NBX_UPSTREAM_TTL_S", "20")),
         "unit": "s", "meaning": "exchange probe freshness"},
        {"name": "NBX_SIGNALS_TTL_S", "value": float(os.getenv("NBX_SIGNALS_TTL_S", "90")),
         "unit": "s", "meaning": "cached scan lifetime"},
        {"name": "NBX_UNIVERSE_TTL_S", "value": float(os.getenv("NBX_UNIVERSE_TTL_S", "120")),
         "unit": "s", "meaning": "cached universe lifetime"},
    ]


def secrets() -> list[dict[str, Any]]:
    return [
        {**_secret("NBX_API_TOKEN"), "purpose": "shared secret for mutating endpoints",
         "scope": "mutations only; reads stay open"},
        {**_secret("NBX_TELEGRAM_TOKEN"), "purpose": "Telegram bot token",
         "scope": "sendMessage only"},
        {**_secret("NBX_TELEGRAM_CHAT_ID"), "purpose": "Telegram destination",
         "scope": "receive only"},
        {**_secret("NOBITEX_API_KEY"), "purpose": "exchange key (phase 3+)",
         "scope": "READ — never rendered, never logged"},
        {**_secret("NOBITEX_API_SECRET"), "purpose": "exchange secret (phase 3+)",
         "scope": "READ — never rendered, never logged"},
    ]


def providers(upstream: dict[str, Any] | None = None) -> dict[str, Any]:
    """Which upstream each number came from, and how stale it is.

    `upstream` is supplied by the endpoint (which runs inside the event loop and
    may schedule a probe); this function itself must stay loop-free.
    """
    up = upstream or {"ok": None, "latency_ms": None, "age_s": None,
                      "error": None, "degraded": None}
    fa = fa_engine.FA_ENGINE
    now = time.time()
    cov = getattr(fa, "last_coverage", None) or {}
    return {
        "nobitex": {
            "kind": "market data", "configured": True,
            "ok": up.get("ok"), "latency_ms": up.get("latency_ms"),
            "age_s": up.get("age_s"), "error": up.get("error"),
            "degraded": up.get("degraded"),
            # Tri-state on purpose: `None` means "not measured yet", which is
            # neither fresh nor stale. Collapsing it to False would paint a
            # green badge over an unknown.
            "stale": (None if up.get("ok") is None
                      else bool(up.get("ok") is False or (up.get("age_s") or 0) > 120)),
        },
        "coingecko": {
            "kind": "fundamentals", "configured": True,
            "last_error": getattr(fa, "_last_err", None),
            "note": "rate limited at ~10-30 req/min on the free tier",
        },
        "defillama": {"kind": "fundamentals", "configured": True,
                      "note": "protocol TVL; no key required"},
        "fear_greed": {"kind": "sentiment", "configured": True,
                       "note": "alternative.me; one request per scan"},
        "telegram": {"kind": "notifications", **notify.status()},
        "coverage": {"value": cov, "checked_at": int(now)},
    }


def snapshot(*, upstream: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "read_only": True,
        "config_hash": journal.CONFIG_HASH,
        "weights": weights(),
        "thresholds": thresholds(),
        "risk_limits": risk_limits(),
        "flags": flags(),
        "secrets": secrets(),
        "providers": providers(upstream),
        "env": env_file(),
        "generated_at": int(time.time()),
        "environment": os.getenv("NBX_ENV", "development"),
    }
