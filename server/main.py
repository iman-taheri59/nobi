"""
FastAPI main app — Phase 1: real backend for the dashboard.

What it serves:
  GET  /api/health          — backend & Nobitex reachability check
  GET  /api/universe        — ranked tradable symbols from /market/stats
  GET  /api/signals?tf=15m  — full scan + Python-computed TA scores
  GET  /api/candles/{sym}   — candles for one symbol

Run:
    cd server
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn main:app --port 8000 --reload
"""
from __future__ import annotations

import _env  # noqa: F401 — must precede every module that reads os.environ
              # (notify.TOKEN, security.API_TOKEN, ws_execution.WS_URL are all
              # read at import time, so a later load would be a silent no-op)

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from nobitex_client import ClientError, NobitexClient, UpstreamUnavailable
from ta_engine import candles_to_df, ta_score, ta_score_series
from fa_engine import FA_ENGINE, FAResult
from risk_engine import RiskEngine, RiskConfig
import paper_trading
from paper_trading import get_broker, PaperBroker
from backtest import EventBacktester, WalkForwardRunner, run_and_save, grid_search, DEFAULT_GRID
from ws_execution import STREAM_EXECUTOR, streams_start
from security import API_TOKEN, auth_mode, cors_origins, require_client
import journal as signal_journal
import errorlog
import notify

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nbx-app")

TICK_S = float(os.getenv("NBX_TICK_S", "8"))
_ticks = [0]  # scheduler heartbeat, also used by /api/ws/status


async def tick_once(broker=None) -> list:
    """Evaluate every open position once. Returns the ones that closed.

    `broker` defaults to the live paper account. Kept separate from the timer
    so it can be tested deterministically.
    """
    broker = broker if broker is not None else paper_trading.PAPER_BROKER
    if broker is None:
        return []
    open_positions = [p for p in broker.positions.values() if p.state != "CLOSED"]
    if not open_positions:
        return []
    await streams_start(broker, client)
    before = {p.id for p in open_positions}
    await broker.evaluate_positions()
    closed = [p for p in broker.positions.values() if p.state == "CLOSED" and p.id in before]
    if closed:
        log.info("tick closed %d position(s): %s", len(closed),
                 ", ".join(f"{p.symbol}({p.close_reason})" for p in closed))
    return closed


async def _last_bar_ts(symbol: str, tf: str) -> int | None:
    """Close time (epoch seconds) of the most recent *finished* bar."""
    try:
        rows, _src = await client.get_candles_ex(symbol, tf, 3)
    except UpstreamUnavailable:
        return None
    if not rows:
        return None
    last = rows[-1]
    step = signal_journal.TF_SECONDS.get(tf, 900)
    return int(last["t"]) + step


async def journal_scan(tf: str = "15m") -> int:
    """Record one journalled row per symbol for the last closed bar.

    Runs after every live rescan. The timestamp is the bar's **close**, so a
    rescan every 90 s cannot create duplicate rows for the same bar.
    """
    snaps = _cached["signals_by_tf"].get(tf) or _cached["signals"] or {}
    if not snaps:
        return 0
    ranked = sorted(snaps.values(), key=lambda r: r.get("composite") or 0, reverse=True)
    rows = []
    for rank, snap in enumerate(ranked, start=1):
        if snap.get("direction") not in ("LONG", "EXIT", "NEUTRAL"):
            continue
        ts = await _last_bar_ts(snap["symbol"], tf)
        if ts is None:
            continue
        enriched = {**snap, "tf": tf, "config_hash": signal_journal.CONFIG_HASH}
        row = signal_journal.make_row(enriched, ts, rank=rank,
                                      origin="live", source=snap.get("data_source"))
        if row is not None:
            rows.append(row)
    if not rows:
        return 0
    conn = signal_journal.connect()
    try:
        return signal_journal.insert_rows(conn, rows)
    finally:
        conn.close()


def journal_void(active: list[str] | None = None) -> int:
    """Freeze predictions for symbols that are no longer tradeable."""
    universe = active or [u["symbol"] for u in _cached["universe"]]
    if not universe:
        return 0
    conn = signal_journal.connect()
    try:
        return signal_journal.mark_void(conn, universe)
    finally:
        conn.close()


def audit(action: str, *, actor: str = "api", result: str = "ok",
          detail=None, request: Request | None = None) -> int | None:
    """Record a control action. An audit failure must never break the action."""
    try:
        client_ip = None
        if request is not None:
            client_ip = request.client.host if request.client else None
        conn = signal_journal.connect()
        try:
            return signal_journal.audit_record(conn, action, actor=actor,
                                               result=result, detail=detail,
                                               client=client_ip)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        log.warning("audit write failed for %s: %s", action, e)
        return None


async def journal_resolve(tf: str = "15m", symbols: list[str] | None = None) -> int:
    """Fill in every horizon whose future bars now exist."""
    conn = signal_journal.connect()
    filled = 0
    try:
        syms = symbols or [r["symbol"] for r in _cached["universe"][:40]]
        for sym in syms:
            try:
                rows, _src = await client.get_candles_ex(sym, tf, 1200)
            except UpstreamUnavailable as e:
                log.warning("resolve %s skipped: %s", sym, e)
                continue
            if not rows:
                continue
            df = candles_to_df(rows)
            filled += signal_journal.resolve_symbol(conn, sym, tf, df)
    finally:
        conn.close()
    return filled


async def _position_tick_loop() -> None:
    """Evaluate open paper positions on a timer.

    Before this loop, stop-losses only fired when a browser happened to call
    ``/api/paper/positions``. A position could sit past its stop for hours.
    """
    while True:
        try:
            await tick_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the scheduler must survive
            log.warning("position tick failed: %s", e)
        _ticks[0] += 1
        if _ticks[0] % max(1, int(300 / max(TICK_S, 1))) == 0:
            try:
                filled = await journal_resolve("15m")
                if filled:
                    log.info("journal: %d outcome(s) resolved", filled)
                voided = journal_void()
                if voided:
                    log.warning("journal: %d prediction(s) VOID (symbol left universe)", voided)
            except Exception as e:  # noqa: BLE001
                log.warning("journal resolve failed: %s", e)
        await asyncio.sleep(TICK_S)


#: Scheduled export + digest (ADR-0012 §2.6). Off by default: writing files
#: and sending messages are side effects an operator should opt into.
DIGEST_HOURS = float(os.getenv("NBX_DIGEST_HOURS", "0"))
EXPORT_DIR = Path(os.getenv("NBX_EXPORT_DIR", str(Path(__file__).resolve().parent / "exports")))
_last_digest = [0.0]


def write_digest_files(*, days: int = 1) -> dict:
    """Write one CSV+JSON pair for the window and return the digest summary."""
    conn = signal_journal.connect()
    try:
        since = int(time.time()) - days * 86400
        rows = signal_journal.export_rows(conn, since=since)
        info = signal_journal.digest(conn, days=days)
    finally:
        conn.close()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    csv_path = EXPORT_DIR / f"journal-{stamp}.csv"
    csv_path.write_text(signal_journal.export_csv(rows), encoding="utf-8")
    info["rows"] = len(rows)
    info["path"] = str(csv_path)
    return info


async def _digest_loop() -> None:
    """Run the scheduled export at most once per `NBX_DIGEST_HOURS`."""
    if DIGEST_HOURS <= 0:
        log.info("digest: disabled (set NBX_DIGEST_HOURS to enable)")
        return
    interval = DIGEST_HOURS * 3600
    while True:
        try:
            if time.time() - _last_digest[0] >= interval:
                info = await asyncio.to_thread(write_digest_files, days=1)
                _last_digest[0] = time.time()
                log.info("digest: wrote %s (%d rows)", info["path"], info["rows"])
                audit("journal.digest", detail={"rows": info["rows"], "path": info["path"]})
                await notify.send(signal_journal.digest_text(info))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the scheduler must survive
            log.warning("digest failed: %s", e)
            _last_digest[0] = time.time()      # back off a full interval
        await asyncio.sleep(min(300, max(30, interval / 10)))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Any ERROR/FATAL record from any logger now also lands in the error log,
    # which is what the dashboard's Diagnostics page reads.
    errorlog.install()
    await client.connect()
    log.info("security: auth mode=%s, cors=%s", auth_mode(), _CORS_ORIGINS or "same-origin")
    # A background task that dies silently is invisible: the dashboard simply
    # stops updating and nobody knows why. Wrap each one so the death is logged
    # and the other loops keep running.
    def _supervise(name, coro):
        async def runner():
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception as exc:                    # noqa: BLE001
                log.exception("background task %s crashed", name)
                errorlog.capture(source=f"task.{name}", exc=exc,
                                 message=f"background task '{name}' crashed")
        return asyncio.create_task(runner())

    tasks = [
        _supervise("universe", load_universe()),
        _supervise("rescan", rescan("15m")),
        _supervise("position-tick", _position_tick_loop()),
        _supervise("digest", _digest_loop()),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await STREAM_EXECUTOR.close()
        await client.aclose()


app = FastAPI(
    title="Nobitex Trader Bot — Phase 2 (FA + Risk)",
    version="2.2.0",
    lifespan=lifespan,
)

# Same-origin by default: the dashboard is served by this very app, so it needs
# no CORS at all. Add explicit origins only for a separate front-end.
_CORS_ORIGINS = cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["content-type", "x-api-token", "authorization"],
)

TIMEFRAMES = ["15m", "1h", "4h", "D"]
client = NobitexClient()
EQUITY = float(os.getenv("NBX_EQUITY_USD", "100000"))  # default: 100k USD test capital
risk = RiskEngine(RiskConfig(equity=EQUITY))

# Freshness budgets (seconds). The dashboard polls every 2 minutes; keeping the
# server cache a little shorter than that is enough to keep it honest without
# hammering Nobitex.
SIGNALS_TTL_S = int(os.getenv("NBX_SIGNALS_TTL_S", "90"))
UNIVERSE_TTL_S = int(os.getenv("NBX_UNIVERSE_TTL_S", "120"))
UPSTREAM_TTL_S = int(os.getenv("NBX_UPSTREAM_TTL_S", "20"))

_cached: dict = {
    "universe": [],
    "universe_ts": 0.0,
    "signals": {},
    "signals_by_tf": {},
    "signals_ts": {},
    "refreshing": {},
    "premium": None,
    "premium_pct": None,
    "last_scan": None,
    "universe_error": None,
    "scan_skipped": [],
}
_upstream: dict = {"ts": 0.0, "ok": None, "latency_ms": None, "error": None, "probing": False}


def norm(s: str) -> str:
    return (s or "").strip().upper()


#: Weights of the three-layer composite. `/api/settings` reports these, so the
#: page and the engine cannot disagree.
TA_W, FA_W, LIQ_W = 0.55, 0.25, 0.20

#: Ceilings applied when the fundamental engine raises vetoes. Both sit below
#: `ta_engine.LONG_SCORE_MIN` (68) so a flagged symbol cannot be promoted.
VETO_CAP = 67.0
VETO_CAP_SEVERE = 45.0


#: A veto is a hard flag from the fundamental engine. Until now nothing read
#: them: `/api/signals` reported their count and the score behaved as if every
#: symbol were clean, so the panel could show "2 vetoes" beside a LONG.
#:
#: The rule below is deliberately blunt and visible rather than clever: a
#: vetoed symbol is capped below the LONG threshold, and it is demoted to
#: NEUTRAL at most. Severe flags (a broken market, not just an expensive one)
#: cap lower still. The applied caps are returned so the UI can say *why*.
SEVERE_VETOES = {"thin-tvl", "extreme-fear", "unlock-7d>5%"}


def apply_vetoes(composite: float, direction: str, vetoes: list[str]) -> tuple[float, str, dict]:
    """Cap a vetoed symbol below promotion and explain the cap."""
    if not vetoes:
        return composite, direction, {}
    severe = sorted(v for v in vetoes if v in SEVERE_VETOES)
    cap = VETO_CAP_SEVERE if severe else VETO_CAP
    # Below LONG_SCORE_MIN by construction: a veto that still allowed LONG
    # would be decoration.
    capped = round(min(composite, cap), 1)
    new_dir = "EXIT" if severe else ("NEUTRAL" if direction == "LONG" else direction)
    return capped, new_dir, {
        "veto_cap": cap,
        "veto_severe": severe,
        "uncapped_composite": round(composite, 1),
        "uncapped_direction": direction,
    }


def composite_score(ta: float, liq: float, fa: float | None) -> tuple[float, str]:
    """Composite of TA + liquidity (+ fundamentals when they are available).

    Without fundamentals the remaining weights are renormalised instead of
    leaving a 25 % hole — `composite_basis` says which rule was applied.
    """
    if fa is None:
        w = TA_W + LIQ_W
        return round((TA_W * ta + LIQ_W * liq) / w, 1), "ta+liq"
    return round(TA_W * ta + FA_W * fa + LIQ_W * liq, 1), "ta+fa+liq"


def _short_proc_error(exc: Exception) -> str:
    """The one line worth logging from a failed subprocess call."""
    err = getattr(exc, "stderr", None) or b""
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    lines = [ln.strip() for ln in str(err).splitlines() if ln.strip()]
    return lines[-1][:300] if lines else str(exc)[:300]


#: Sent to the dashboard whenever a number cannot be fetched. The UI renders it
#: in an error style; it never fills the gap with a fabricated value.
UPSTREAM_MSG_FA = ("دسترسی به سرور نوبیتکس امکان‌پذیر نیست؛ دادهٔ زنده دریافت نشد. "
                   "اتصال شبکه یا فیلتر را بررسی کن و دوباره تلاش کن.")
UPSTREAM_MSG_EN = ("The Nobitex server is unreachable; no live data was received. "
                   "Check the network or filtering and try again.")


def upstream_error(kind: str, detail: str = "") -> dict:
    """Uniform payload for "we could not reach the exchange"."""
    return {
        "ok": False,
        "unavailable": True,
        "error": "upstream_unavailable",
        "kind": kind,
        "message": UPSTREAM_MSG_FA,
        "message_en": UPSTREAM_MSG_EN,
        "detail": detail[:300],
        "source": "unavailable",
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def provenance() -> dict:
    """Where the numbers the API is returning actually came from."""
    return {
        "source": "unavailable" if client.degraded_mode else "live",
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def _premium_pct(rows: list[dict]) -> float | None:
    """Implied USDT/IRT premium: (USDTIRT) vs (BTCIRT / BTCUSDT)."""
    d = {r.get("symbol"): r.get("last") for r in rows}
    btc_irt, btc_usdt, usdt_irt = d.get("BTCIRT"), d.get("BTCUSDT"), d.get("USDTIRT")
    if not btc_irt or not btc_usdt or not usdt_irt:
        return None
    try:
        implied = float(btc_irt) / float(btc_usdt)
        if implied <= 0:
            return None
        return round((float(usdt_irt) / implied - 1.0) * 100.0, 3)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


async def _fx_rate(symbol: str) -> float:
    """USD value of one quote unit: 1.0 for USDT pairs, USDTIRT for IRT pairs."""
    if not symbol.upper().endswith("IRT"):
        return 1.0
    row = next((r for r in _cached["universe"] if r["symbol"] == "USDTIRT"), None)
    px = float(row["last"]) if row and row.get("last") else 0.0
    if px <= 0:
        try:
            stats = await client.get_market_stats()
            px = float((stats.get("stats") or {}).get("usdtirt", {}).get("latest") or 0)
        except Exception:  # noqa: BLE001 - FX is best effort here
            px = 0.0
    return px or 0.0


async def _probe_upstream() -> None:
    """Background probe so /api/health never blocks on the exchange."""
    if _upstream["probing"]:
        return
    _upstream["probing"] = True
    t0 = time.perf_counter()
    ok = False
    err = None
    try:
        r = await asyncio.wait_for(client.get_orderbook("BTCIRT"), timeout=4)
        ok = bool(r and r.get("status") == "ok") and not client.degraded_mode
    except Exception as e:  # noqa: BLE001 - health must never raise
        err = str(e) or e.__class__.__name__
    finally:
        _upstream.update(
            ts=time.time(),
            ok=ok,
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            error=err,
            probing=False,
        )


def _upstream_snapshot() -> dict:
    age = time.time() - _upstream["ts"] if _upstream["ts"] else None
    if _upstream["ok"] is None or age is None or age > UPSTREAM_TTL_S:
        asyncio.create_task(_probe_upstream())
    return {
        "ok": _upstream["ok"],
        "latency_ms": _upstream["latency_ms"],
        "age_s": None if age is None else round(age, 1),
        "error": _upstream["error"],
        "degraded": client.degraded_mode,
    }


@app.get("/api/cache/stats")
async def cache_stats():
    from candle_cache import CANDLE_CACHE
    return CANDLE_CACHE.stats() | {"ok": True, "security": {"auth_mode": auth_mode()}}


@app.get("/api/health")
async def health():
    """Liveness of *this* service, independent of Nobitex availability.

    `ok` describes the backend process; `upstream` describes the exchange.
    The dashboard uses `ok` to decide whether it can talk to the API at all,
    and `upstream` only to label the data as live/synthetic.
    """
    return {
        "ok": True,
        "backend": "python-fastapi-nobi",
        "version": app.version,
        "ts": datetime.now(timezone.utc).isoformat(),
        "upstream": _upstream_snapshot(),
        "auth": {"mode": auth_mode(), "token_required": bool(API_TOKEN), "cors_origins": _CORS_ORIGINS},
        "provenance": provenance(),
        "stats": {
            "total": client.stats.total,
            "ok": client.stats.ok,
            "fail": client.stats.fail,
            "success_rate": round(client.stats.success_rate(), 1),
            "last_latency_ms": client.stats.last_latency_ms,
        },
    }


_universe_lock = asyncio.Lock()


async def load_universe():
    async with _universe_lock:
        return await _load_universe_locked()


async def _load_universe_locked():
    try:
        stats = await client.get_market_stats()
        rows = []
        for sym, d in (stats.get("stats") or {}).items():
            sym_n = norm(sym)
            last = float(d.get("latest") or d.get("last") or 0)
            vol = float(d.get("valueDst") or d.get("volumeDst") or d.get("24h_volume") or 0)
            chg = float(d.get("dayChange") or 0)
            if sym_n and last > 0:
                rows.append({"symbol": sym_n, "last": last, "vol24": vol, "change24": chg})
        rows.sort(key=lambda x: x["vol24"], reverse=True)
        _cached["universe"] = rows[:60]
        _cached["universe_ts"] = time.time()
        usdtirt = next((r for r in rows if r["symbol"] == "USDTIRT"), None)
        _cached["premium"] = usdtirt["last"] if usdtirt else None
        _cached["premium_pct"] = _premium_pct(rows)
        log.info("universe: %d rows loaded (premium %s%%)", len(_cached["universe"]), _cached["premium_pct"])
    except (ClientError, UpstreamUnavailable) as e:
        log.warning("load_universe: %s", e)
        _cached["universe"] = []
        _cached["universe_error"] = str(e)
    return _cached["universe"]


@app.get("/api/universe")
async def universe():
    age = time.time() - _cached["universe_ts"] if _cached["universe_ts"] else None
    if not _cached["universe"] or age is None or age > UNIVERSE_TTL_S:
        await load_universe()
        age = time.time() - _cached["universe_ts"] if _cached["universe_ts"] else 0.0
    if not _cached["universe"]:
        return {**upstream_error("universe", _cached.get("universe_error") or ""),
                "ts": datetime.now(timezone.utc).isoformat(),
                "age_s": round(age or 0.0, 1),
                "count": 0, "symbols": [], "premium_usdtirt": None, "premium_pct": None}
    return {
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat(),
        "age_s": round(age or 0.0, 1),
        "premium_usdtirt": _cached["premium"],
        "premium_pct": _cached["premium_pct"],
        "count": len(_cached["universe"]),
        "symbols": _cached["universe"],
        **provenance(),
    }


async def rescan(tf: str = "15m", top: int = 40):
    """Score the universe with TA + liquidity, then enrich with fundamentals.

    Fundamentals are attempted for **every** symbol; when they are unavailable
    the composite is recomputed on the remaining weights and `composite_basis`
    says so. The previous code fed a hard-coded `fa_stub = 50` into 25 % of the
    score for 15 of 25 symbols while the UI labelled it "FA score".
    """
    if not _cached["universe"]:
        await load_universe()
    sem = asyncio.Semaphore(6)
    out: dict = {}
    skipped: list[str] = []
    def _composite(ta: float, liq: float, fa: float | None) -> tuple[float, str]:
        return composite_score(ta, liq, fa)

    async def one(u):
        try:
            async with sem:
                rows, src = await client.get_candles_ex(u["symbol"], tf, 260)
            df = candles_to_df(rows)
            sig = ta_score(df)
            vol = u["vol24"]
            import math
            liq = round(min(100, max(0, 50 + math.log10(vol + 1) * 4)), 0)
            composite, basis = _composite(sig["score"], liq, None)
            out[u["symbol"]] = {
                **u,
                **sig,
                "liquidity_score": liq,
                "fa_score": None,
                "fa": None,
                "composite": composite,
                "composite_basis": basis,
                "data_source": src,
            }
        except UpstreamUnavailable as e:
            skipped.append(u["symbol"])
            log.warning("scan %s: %s", u["symbol"], e)
        except ClientError as e:
            log.warning("scan %s: %s", u["symbol"], e)

    await asyncio.gather(*(one(u) for u in _cached["universe"][:top]))

    # Fundamentals for the whole scan set, at most 4 in flight (providers
    # rate-limit aggressively). Failures degrade the composite instead of
    # silently substituting a neutral score.
    fa_sem = asyncio.Semaphore(4)
    scored: list[str] = []
    live: list[str] = []
    failed: list[str] = []

    async def enrich(sym: str, snap: dict):
        async with fa_sem:
            try:
                # The premium is measured once per universe load; passing a
                # constant 0 here made the documented premium veto unreachable.
                prem_dev = (_cached.get("premium_pct") or 0.0) / 100.0
                fa: FAResult = await FA_ENGINE.score(
                    sym, usdt_premium_dev=prem_dev, nobil_depth_score=snap["liquidity_score"]
                )
                snap["fa"] = {
                    "score": fa.score,
                    "vetoes": fa.vetoes,
                    "evidence": fa.evidence,
                    "fresh": fa.fresh,
                    "profile": fa.evidence.get("profile"),
                }
                snap["fa_score"] = fa.score
                snap["fa_fresh"] = fa.fresh
                raw_composite, basis = _composite(
                    snap["score"], snap["liquidity_score"], fa.score
                )
                # Vetoes are not decoration: they cap the score and the verdict,
                # and the caps travel with the row so the UI can explain them.
                capped, new_dir, veto_meta = apply_vetoes(
                    raw_composite, snap.get("direction") or "NEUTRAL", list(fa.vetoes or [])
                )
                snap["composite"], snap["composite_basis"] = capped, basis
                snap["direction_raw"] = snap.get("direction")
                snap["direction"] = new_dir
                snap.update(veto_meta)
                scored.append(sym)
                if fa.fresh:
                    live.append(sym)
            except Exception as e:  # noqa: BLE001 - one symbol must not kill the scan
                failed.append(sym)
                log.warning("FA unavailable for %s: %s", sym, e)

    await asyncio.gather(*(enrich(sym, snap) for sym, snap in out.items()))

    _cached["fa_coverage"] = {
        "scored": len(scored),
        "live": len(live),
        "fallback": len(scored) - len(live),
        "failed": len(failed),
        "total": len(out),
        "ratio": round(len(scored) / len(out), 3) if out else 0.0,
    }
    _cached["signals"] = out
    _cached["signals_by_tf"][tf] = out
    _cached["scan_skipped"] = skipped
    _cached["signals_ts"][tf] = time.time()
    _cached["last_scan"] = datetime.now(timezone.utc).isoformat()
    log.info("rescan %s: %d symbols, FA %d/%d", tf, len(out), len(scored), len(out))
    if os.getenv("NBX_JOURNAL", "1") != "0":
        try:
            added = await journal_scan(tf)
            if added:
                log.info("journal: %d new prediction(s) on %s", added, tf)
        except Exception as e:  # noqa: BLE001 - journalling must not break scans
            log.warning("journal write failed: %s", e)
    return out


async def _bg_rescan(tf: str) -> None:
    try:
        await rescan(tf)
    except Exception as e:  # noqa: BLE001
        log.warning("background rescan %s failed: %s", tf, e)
    finally:
        _cached["refreshing"][tf] = False


@app.get("/api/signals")
async def signals(tf: str = Query("15m", pattern="^(15m|1h|4h|D)$")):
    """Ranked signals for one timeframe.

    The cache has a TTL; when it expires the previous answer is still served
    (marked `stale: true`) while a background refresh runs. This keeps the
    dashboard responsive and, critically, stops it from showing an initial
    scan forever.
    """
    have = bool(_cached["signals_by_tf"].get(tf))
    age = time.time() - _cached["signals_ts"].get(tf, 0.0) if _cached["signals_ts"].get(tf) else None
    stale = (not have) or age is None or age > SIGNALS_TTL_S

    if not have:
        await rescan(tf)
        age = time.time() - _cached["signals_ts"].get(tf, time.time())
        stale = False
    elif stale and not _cached["refreshing"].get(tf):
        _cached["refreshing"][tf] = True
        asyncio.create_task(_bg_rescan(tf))

    tf_signals = _cached["signals_by_tf"].get(tf) or _cached["signals"]
    ranked = sorted(tf_signals.values(), key=lambda x: x["composite"], reverse=True)
    if not ranked:
        # No live candles ⇒ no scores. Returning an empty list alone would look
        # like "nothing is interesting today"; say which wall was hit.
        skipped = _cached.get("scan_skipped") or []
        why = (f"{len(skipped)}/{len(_cached['universe'])} symbol(s) had no candles"
               if _cached["universe"] else (_cached.get("universe_error") or "no universe"))
        return {**upstream_error("signals", why),
                "ts": datetime.now(timezone.utc).isoformat(), "tf": tf, "count": 0,
                "age_s": round(age or 0.0, 1), "stale": stale, "refreshing": False,
                "scored": [], "premium_usdtirt": _cached["premium"], "premium_pct": _cached["premium_pct"],
                "fa_coverage": _cached.get("fa_coverage")}
    return {
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat(),
        "tf": tf,
        "count": len(ranked),
        "age_s": round(age or 0.0, 1),
        "stale": stale,
        "refreshing": bool(_cached["refreshing"].get(tf)),
        "premium_usdtirt": _cached["premium"],
        "premium_pct": _cached["premium_pct"],
        "fa_coverage": _cached.get("fa_coverage"),
        "scored": ranked,
        **provenance(),
    }


@app.get("/api/candles/{symbol}")
async def candles(symbol: str, tf: str = Query("15m", pattern="^(15m|1h|4h|D)$"), count: int = 260):
    try:
        rows, src = await client.get_candles_ex(symbol, tf, max(60, min(count, 2000)))
    except UpstreamUnavailable as e:
        return {**upstream_error("candles", str(e)),
                "symbol": symbol.upper(), "tf": tf, "count": 0, "rows": [], "rows_expected": 0}
    return {
        "ok": True,
        "symbol": symbol.upper(),
        "tf": tf,
        "count": len(rows),
        "rows": rows,
        "source": src,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def _book_levels(raw, limit: int) -> list[dict]:
    """Normalise one side of a Nobitex book: [[price, amount], ...] as floats.

    The exchange sometimes sends strings and sometimes numbers depending on the
    endpoint generation, so both are accepted; anything that is not a finite
    positive pair is dropped rather than turned into a zero.
    """
    out: list[dict] = []
    for row in raw or []:
        try:
            if isinstance(row, dict):
                px = float(row.get("price") or row.get("p") or 0)
                qty = float(row.get("amount") or row.get("quantity") or row.get("q") or 0)
            else:
                px, qty = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if px > 0 and qty > 0:
            out.append({"p": px, "q": qty})
        if len(out) >= limit:
            break
    return out


@app.get("/api/orderbook/{symbol}")
async def orderbook(symbol: str, limit: int = Query(8, ge=1, le=50)):
    """Depth for one symbol.

    The client could already read the book — it is what the paper broker fills
    against — but no route exposed it, so the depth panel had no way to show
    anything but an apology. This route is the whole gap.

    On failure it returns the uniform `unavailable` payload rather than an
    exception: the UI's three-state contract (ADR-0014) reads that flag, and a
    500 would render as a generic error instead of the sentence.
    """
    sym = symbol.upper()
    try:
        raw = await asyncio.wait_for(client.get_orderbook(sym), timeout=6)
    except (UpstreamUnavailable, ClientError) as e:
        return {**upstream_error("orderbook", str(e)), "symbol": sym,
                "bids": [], "asks": [], "levels": 0}
    except asyncio.TimeoutError:
        return {**upstream_error("orderbook", "timeout"), "symbol": sym,
                "bids": [], "asks": [], "levels": 0}

    bids = _book_levels(raw.get("bids"), limit)
    asks = _book_levels(raw.get("asks"), limit)
    if not bids and not asks:
        return {**upstream_error("orderbook", "empty book"), "symbol": sym,
                "bids": [], "asks": [], "levels": 0}

    best_bid = bids[0]["p"] if bids else None
    best_ask = asks[0]["p"] if asks else None
    mid = (best_bid + best_ask) / 2 if (best_bid and best_ask) else (best_bid or best_ask)
    spread = (best_ask - best_bid) if (best_bid and best_ask) else None
    spread_pct = (spread / mid * 100) if (spread is not None and mid) else None

    # Depth within ±1% of the mid, and the imbalance between the two sides.
    # Both are computed here from the levels actually returned, so the panel
    # never shows a statistic that disagrees with the rows above it.
    band = (mid or 0) * 0.01
    bid_depth = sum(l["p"] * l["q"] for l in bids if abs(l["p"] - mid) <= band)
    ask_depth = sum(l["p"] * l["q"] for l in asks if abs(l["p"] - mid) <= band)
    total = bid_depth + ask_depth
    imbalance = ((bid_depth - ask_depth) / total * 100) if total else None

    return {
        "ok": True,
        "symbol": sym,
        "bids": bids,
        "asks": asks,
        "levels": len(bids) + len(asks),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread": spread,
        "spread_pct": spread_pct,
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "imbalance": imbalance,
        "last_update": raw.get("lastUpdate"),
        **provenance(),
    }


def _signal_lookup(symbol: str, tf: str | None = None):
    """Find a scored symbol, preferring the requested timeframe's cache.

    `_cached["signals"]` is whichever timeframe was scanned last; using it for
    a caller-specified `tf` could size a trade on another timeframe's ATR.
    """
    sym = symbol.upper()
    if tf:
        by_tf = _cached.get("signals_by_tf", {}).get(tf) or {}
        if sym in by_tf:
            return by_tf[sym]
    return _cached["signals"].get(sym)


# ---------------- Prediction journal (ADR-0012) ----------------
@app.get("/api/journal/summary")
async def journal_summary(tf: str | None = Query(None, pattern="^(15m|1h|4h|D)$"),
                          symbol: str | None = None,
                          source: str | None = Query(None, pattern="^(live|cache|synthetic)$"),
                          origin: str | None = Query(None, pattern="^(live|backfill)$"),
                          days: int = Query(90, ge=1, le=3650),
                          limit: int = Query(60, ge=1, le=500)):
    """Everything the Performance page needs, plus its own provenance.

    `origins` separates predictions the engine issued in real time (`live`)
    from rows reconstructed backwards for research (`backfill`); merging them
    silently would let a backfill flatter the hit-rate.
    """
    conn = signal_journal.connect()
    try:
        payload = signal_journal.summary(conn, tf=tf, since=int(time.time()) - days * 86400,
                                         source=source, origin=origin, limit=limit)
        payload["origins"] = {
            r["origin"]: r["n"] for r in conn.execute(
                "SELECT origin, COUNT(*) AS n FROM signal_log GROUP BY origin")
        }
        payload["outcomes"] = conn.execute(
            "SELECT COUNT(*) AS n FROM signal_outcomes").fetchone()["n"]
        payload["db"] = str(signal_journal.DB_PATH)
        payload["generated_at"] = datetime.now(timezone.utc).isoformat()
        return {**payload, **provenance()}
    finally:
        conn.close()


@app.get("/api/journal/stage")
async def journal_stage():
    """Current strategy stage and the rule that produced it (ADR-0012 §3).

    The stage is advisory: it can move a strategy to `watch` or `retire` on its
    own, but nothing here promotes a strategy to live money — that gate stays
    human (ADR-0009) and this payload says so.
    """
    try:
        conn = signal_journal.connect()
    except Exception as e:  # noqa: BLE001 - an unreadable journal is not a 500
        return {"ok": False, "unavailable": True, "error": "journal_unavailable",
                "detail": str(e)[:200], "stage": None}
    try:
        out = signal_journal.lifecycle(conn)
    finally:
        conn.close()
    return {"ok": True, **out}


@app.get("/api/journal/signals")
async def journal_signals(tf: str | None = None, symbol: str | None = None,
                          direction: str | None = None, source: str | None = None,
                          origin: str | None = None,
                          days: int = Query(90, ge=1, le=3650),
                          limit: int = Query(200, ge=1, le=2000),
                          offset: int = Query(0, ge=0)):
    conn = signal_journal.connect()
    try:
        rows = signal_journal.fetch_signals(
            conn, tf=tf, symbol=symbol, direction=direction, source=source, origin=origin,
            since=int(time.time()) - days * 86400, limit=limit, offset=offset)
        return {
            "count": len(rows),
            "signals": [{**dict(r), "features": json.loads(r["features"] or "{}")} for r in rows],
            **provenance(),
        }
    finally:
        conn.close()


@app.get("/api/journal/msi")
async def journal_msi(window_days: int = Query(30, ge=7, le=365),
                      baseline_days: int = Query(180, ge=30, le=3650),
                      min_samples: int = Query(20, ge=5, le=500)):
    """Model Signal Integrity — realized IC vs the reference (ADR-0012 §2.5)."""
    conn = signal_journal.connect()
    try:
        out = signal_journal.msi(conn, window_days=window_days,
                                 baseline_days=baseline_days, min_samples=min_samples)
        return {**out, **provenance()}
    finally:
        conn.close()


@app.post("/api/journal/msi/baseline", dependencies=[Depends(require_client)])
async def journal_msi_baseline(request: Request,
                               tf: str = Query("15m", pattern="^(15m|1h|4h|D)$"),
                               symbols: int = Query(10, ge=1, le=40),
                               bars: int = Query(1200, ge=200, le=3000)):
    """Measure the score's own IC on history and store it as the reference.

    Deliberately a separate, explicit action: a baseline that silently follows
    the live data would make drift undetectable by construction.
    """
    if not _cached["universe"]:
        await load_universe()
    universe = sorted(_cached["universe"], key=lambda r: r.get("vol24") or 0, reverse=True)
    frames = []
    sources: dict[str, int] = {}
    for u in universe[:symbols]:
        try:
            rows, src = await client.get_candles_ex(u["symbol"], tf, bars)
        except Exception as e:  # noqa: BLE001 - one symbol must not kill the baseline
            log.warning("baseline: %s unavailable: %s", u["symbol"], e)
            continue
        if not rows:
            continue
        sources[src] = sources.get(src, 0) + 1
        frames.append((u["symbol"], candles_to_df(rows)))

    horizon = signal_journal.bars_for(tf, signal_journal.HORIZONS["H2"])
    result = signal_journal.backtest_ic(frames, horizon)
    payload = {
        **result,
        "symbols_requested": len(universe[:symbols]),
        "symbols_used": len(frames),
        "tf": tf,
        "sources": sources,
        "config_hash": signal_journal.CONFIG_HASH,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "source": "backtest",
    }
    conn = signal_journal.connect()
    try:
        if result["ic"] is not None:
            signal_journal.set_meta(conn, "msi_baseline", payload)
        audit_id = audit("journal.msi_baseline",
                         detail={"tf": tf, "ic": result["ic"], "n": result["n"],
                                 "sources": sources},
                         request=request)
        return {"stored": result["ic"] is not None, "audit_id": audit_id,
                **payload, **provenance()}
    finally:
        conn.close()


@app.get("/api/journal/signal/{signal_id}")
async def journal_signal_detail(signal_id: int,
                                context_bars: int = Query(30, ge=0, le=200),
                                horizon: str = Query("H2", pattern="^(H1|H2|H3)$")):
    """Everything behind one row of the performance table (ADR-0012).

    The UI opens a row to answer "why did this one resolve like that?" — the
    price path from the signal bar to the horizon, with the entry/stop/TP
    levels drawn on it, plus the score components that produced the call.
    """
    conn = signal_journal.connect()
    try:
        row = conn.execute("SELECT * FROM signal_log WHERE id=?", (int(signal_id),)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="signal not found")
        outcomes = {o["horizon_key"]: dict(o) for o in conn.execute(
            "SELECT * FROM signal_outcomes WHERE signal_id=?", (int(signal_id),))}
    finally:
        conn.close()

    try:
        features = json.loads(row["features"] or "{}")
    except (ValueError, TypeError):
        features = {}

    candles: list[dict] = []
    candle_source = "unavailable"
    try:
        candles, candle_source = await client.get_candles_ex(row["symbol"], row["timeframe"], 1200)
    except Exception as e:  # noqa: BLE001 - the detail view must still render
        log.warning("detail: candles for %s unavailable: %s", row["symbol"], e)

    path = signal_journal.signal_path(row, candles, row["timeframe"],
                                      context_bars=context_bars, horizon_key=horizon)
    return {
        "id": row["id"],
        "signal": {
            "ts": row["ts"], "symbol": row["symbol"], "timeframe": row["timeframe"],
            "direction": row["direction"], "composite": row["composite"],
            "ta_score": row["ta_score"], "fa_score": row["fa_score"],
            "liquidity_score": row["liquidity_score"], "rank_in_cycle": row["rank_in_cycle"],
            "regime": row["regime"], "entry": row["entry"], "stop": row["stop"],
            "tp1": row["tp1"], "tp2": row["tp2"], "atr": row["atr"],
            "source": row["source"], "origin": row["origin"],
            "config_hash": row["config_hash"],
            "voided_at": row["voided_at"], "void_reason": row["void_reason"],
            "components": features.get("components") or {},
            "composite_basis": features.get("composite_basis"),
        },
        "outcomes": outcomes,
        "path": path["bars"],
        "path_reason": path["reason"],
        # The simulator rebuilds its series on every request, anchored to "now",
        # so a re-fetch is not the path that produced the stored outcome.
        # Say so in the payload instead of letting the UI guess.
        "path_usable": candle_source not in ("synthetic", "unavailable"),
        "entry_index": path.get("entry_index"),
        "candle_source": candle_source,
        "horizon": horizon,
        **provenance(),
    }


@app.get("/api/journal/export")
async def journal_export(format: str = Query("csv", pattern="^(csv|json)$"),
                         tf: str | None = Query(None, pattern="^(15m|1h|4h|D)$"),
                         days: int = Query(0, ge=0, le=3650),
                         origin: str | None = Query(None, pattern="^(live|backfill)$"),
                         source: str | None = None):
    """Full journal export (ADR-0012 §2.6). Never paginated.

    One row per prediction with all three horizons joined in, so the file can
    be analysed without re-deriving anything from the database.
    """
    since = int(time.time()) - days * 86400 if days else None
    conn = signal_journal.connect()
    try:
        rows = signal_journal.export_rows(conn, tf=tf, since=since,
                                          source=source, origin=origin)
    finally:
        conn.close()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    if format == "json":
        body = json.dumps({
            "generated_at": int(time.time()),
            "filters": {"tf": tf, "days": days or None, "origin": origin, "source": source},
            "rows": len(rows),
            "config_hash": signal_journal.CONFIG_HASH,
            "data": rows,
        }, ensure_ascii=False, indent=1)
        media, ext = "application/json; charset=utf-8", "json"
    else:
        body = signal_journal.export_csv(rows)
        media, ext = "text/csv; charset=utf-8", "csv"

    return Response(
        content=body,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="nobi-journal-{stamp}.{ext}"',
            "X-Rows": str(len(rows)),
            "Cache-Control": "no-store",
        },
    )


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """Record every unhandled exception, then answer with a JSON error.

    Without this the browser gets a generic 500 and the traceback exists only
    in the server's stdout — unreachable from the dashboard, which is exactly
    where someone notices the problem.
    """
    row = errorlog.capture(
        level="critical", source="http.unhandled", exc=exc,
        message=f"{request.method} {request.url.path} failed",
        context={"path": request.url.path, "method": request.method,
                 "query": str(request.url.query)[:300]},
        client=(request.client.host if request.client else None),
    )
    return JSONResponse(status_code=500,
                        content={"ok": False, "error": "internal",
                                 "log_id": row.get("id"),
                                 "message": row.get("message")})


@app.get("/api/errors")
async def errors_index(limit: int = Query(100, ge=1, le=500),
                       level: str | None = Query(None, pattern="^(debug|info|warning|error|critical)$"),
                       source: str | None = None,
                       hours: int = Query(0, ge=0, le=8760),
                       q: str | None = Query(None, max_length=120)):
    """Newest first. `hours=0` means no time filter."""
    since = int(time.time()) - hours * 3600 if hours else None
    rows = errorlog.recent(limit=limit, level=level, source=source, since=since, q=q)
    return {"errors": rows, "n": len(rows), "counts": errorlog.counts(),
            "filters": {"level": level, "source": source, "hours": hours, "q": q}}


@app.post("/api/errors", dependencies=[Depends(require_client)])
async def errors_report(payload: dict, request: Request):
    """Errors raised in the browser: uncaught exceptions and failed fetches.

    The dashboard is the only thing that sees a broken chart or a rejected
    order; without this channel those failures never leave the user's tab.
    """
    msg = str(payload.get("message") or "")[:500]
    if not msg:
        raise HTTPException(status_code=422, detail="message required")
    src = str(payload.get("source") or "js")[:40]
    # The browser already tags its own sources; don't double-prefix them.
    src = src if src.startswith("client") else "client." + src
    row = errorlog.capture(
        level=str(payload.get("level") or "error"),
        source=src,
        message=msg,
        detail=str(payload.get("stack") or "")[:3000] or None,
        context=str(payload.get("context") or "")[:500] or None,
        client=(request.client.host if request.client else None),
    )
    return {"ok": True, "id": row["id"]}


@app.post("/api/errors/clear", dependencies=[Depends(require_client)])
async def errors_clear(request: Request):
    """Wipe the log. Clearing evidence is itself a control action, so it is
    audited — otherwise "there were no errors" would be unfalsifiable."""
    removed = errorlog.clear()
    audit_id = audit("errors.clear", actor="api", detail={"removed": removed}, request=request)
    return {"ok": True, "removed": removed, "audit_id": audit_id}


@app.post("/api/errors/selftest", dependencies=[Depends(require_client)])
async def errors_selftest():
    """Prove the log can write and read back — a log that is silently dead is
    worse than none, because its emptiness looks like health."""
    return errorlog.self_test()


@app.get("/api/settings")
async def settings_snapshot():
    """Read-only view of what the engine is actually configured to do.

    Every value is read from the module that uses it (see `settings_view`), and
    secrets are reported as presence only. There is no write counterpart on
    purpose: changing weights at runtime would make backtests and live signals
    incomparable, and `config_hash` would stop meaning anything.
    """
    import settings_view

    return {**settings_view.snapshot(upstream=_upstream_snapshot()), **provenance()}


@app.post("/api/settings/telegram/test", dependencies=[Depends(require_client)])
async def settings_telegram_test():
    """Ask Telegram whether the configured bot token is real.

    The token never crosses the wire to the browser: the dashboard asks a
    yes/no question and the server answers it. Nothing is posted to any chat.
    """
    token = os.getenv("NBX_TELEGRAM_TOKEN", "").strip()
    chat = os.getenv("NBX_TELEGRAM_CHAT_ID", "").strip()
    if not token:
        return {"has_token": False, "token_valid": None, "chat_configured": bool(chat),
                "reason": "no-token", "sent_message": False}
    import httpx

    from logsafe import quiet_httpx

    try:
        with quiet_httpx():          # getMe URL contains the bot token
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        try:
            body = r.json()
        except ValueError:
            body = {}
        ok = r.status_code == 200 and bool(body.get("ok"))
        out: dict = {
            "has_token": True,
            "token_valid": ok,
            "chat_configured": bool(chat),
            "sent_message": False,          # getMe never sends anything
            "http_status": r.status_code,
        }
        if ok:
            bot = body.get("result") or {}
            out["bot_username"] = bot.get("username")
            out["bot_id"] = bot.get("id")
        else:
            # Telegram's own description ("Unauthorized" etc). Never build an
            # error string from the request URL: it contains the token.
            out["error"] = str(body.get("description") or "rejected")[:120]
        return out
    except Exception as exc:               # network / DNS / TLS
        return {"has_token": True, "token_valid": None, "chat_configured": bool(chat),
                "sent_message": False, "error": type(exc).__name__}


@app.get("/api/audit")
async def audit_events(limit: int = Query(50, ge=1, le=500),
                       action: str | None = None,
                       days: int = Query(0, ge=0, le=3650)):
    """Control actions recorded server-side. Newest first."""
    since = int(time.time()) - days * 86400 if days else None
    conn = signal_journal.connect()
    try:
        events = signal_journal.audit_list(conn, limit=limit, action=action, since=since)
        counts = signal_journal.audit_counts(conn)
    finally:
        conn.close()
    return {"events": events, "counts": counts, "n": len(events)}


@app.get("/api/notify/status")
async def notify_status():
    """Whether Telegram can actually receive anything right now."""
    return notify.status()


# NOTE: declared before /api/journal/void for readability only — the paths are
# distinct literals, so route order does not matter here.
@app.post("/api/journal/digest", dependencies=[Depends(require_client)])
async def journal_digest_now(request: Request, days: int = Query(1, ge=1, le=90),
                             push: bool = Query(True)):
    """Run the scheduled export immediately (same code path as the timer)."""
    info = await asyncio.to_thread(write_digest_files, days=days)
    audit_id = audit("journal.digest", actor="api:manual",
                     detail={"rows": info["rows"], "path": info["path"], "days": days},
                     request=request)
    tg = await notify.send(signal_journal.digest_text(info)) if push else None
    return {"digest": info, "audit_id": audit_id, "telegram": tg}


@app.post("/api/journal/void", dependencies=[Depends(require_client)])
async def journal_void_now(request: Request, grace_hours: int = Query(12, ge=0, le=720)):
    """Apply the VOID law now (the tick loop also does this every 5 minutes)."""
    universe = [u["symbol"] for u in _cached["universe"]]
    if not universe:
        await load_universe()
        universe = [u["symbol"] for u in _cached["universe"]]
    conn = signal_journal.connect()
    try:
        n = signal_journal.mark_void(conn, universe, grace_s=grace_hours * 3600)
        audit_id = audit("journal.void", detail={"voided": n, "grace_hours": grace_hours},
                         request=request)
        return {"voided": n, "active_symbols": len(universe),
                "audit_id": audit_id, **provenance()}
    finally:
        conn.close()


@app.post("/api/journal/resolve", dependencies=[Depends(require_client)])
async def journal_resolve_now(tf: str = Query("15m", pattern="^(15m|1h|4h|D)$"),
                              symbol: str | None = None):
    """Resolve horizons on demand (the tick loop also does this every 5 min)."""
    filled = await journal_resolve(tf, [symbol.upper()] if symbol else None)
    return {"resolved": filled, "tf": tf, **provenance()}


@app.post("/api/journal/backfill", dependencies=[Depends(require_client)])
async def journal_backfill(request: Request,
                           tf: str = Query("15m", pattern="^(15m|1h|4h|D)$"),
                           symbols: int = Query(10, ge=1, le=40),
                           bars: int = Query(800, ge=200, le=2000)):
    """Reconstruct historical predictions so the report is not empty on day 1.

    `ta_score_series` is causal, so every row carries exactly the score the
    engine would have produced at that bar. The rows are still tagged
    `origin="backfill"` and are never presented as live history.
    """
    if not _cached["universe"]:
        await load_universe()
    universe = sorted(_cached["universe"], key=lambda r: r.get("vol24") or 0, reverse=True)
    picked = [u["symbol"] for u in universe[:symbols]]
    conn = signal_journal.connect()
    written = resolved = 0
    try:
        for sym in picked:
            try:
                rows, src = await client.get_candles_ex(sym, tf, bars)
            except UpstreamUnavailable as e:
                log.warning("backfill %s skipped: %s", sym, e)
                continue
            if not rows or len(rows) < 120:
                continue
            df = candles_to_df(rows)
            series = ta_score_series(df)
            stamps = (df["t"].values.astype("int64") // 10**9).tolist()
            batch = []
            for i, snap in enumerate(series):
                if snap is None:
                    continue
                batch.append(signal_journal.make_row(
                    {**snap, "symbol": sym, "tf": tf, "composite": snap["score"],
                     "config_hash": signal_journal.CONFIG_HASH},
                    int(stamps[i]) + signal_journal.TF_SECONDS.get(tf, 900),
                    origin="backfill", source=src))
                if len(batch) >= 50:
                    written += signal_journal.insert_rows(conn, batch)
                    batch = []
            written += signal_journal.insert_rows(conn, batch)
            resolved += signal_journal.resolve_symbol(conn, sym, tf, df)
        audit_id = audit("journal.backfill",
                         detail={"tf": tf, "symbols": len(picked), "bars": bars,
                                 "rows_written": written, "resolved": resolved},
                         request=request)
        return {"symbols": len(picked), "rows_written": written,
                "outcomes_resolved": resolved, "tf": tf,
                "audit_id": audit_id, **provenance()}
    finally:
        conn.close()


@app.get("/api/fa/{symbol}")
async def fa(symbol: str):
    """Fundamentals for one symbol.

    `source` here describes the *fundamental* providers, not the exchange. It
    used to be `provenance()`, which reports Nobitex reachability — so this
    endpoint answered `source: "unavailable"` while `fresh: true` and
    `profile: live`, which reads as a contradiction to any API consumer.
    """
    s = _cached["signals"].get(symbol.upper())
    liq = s["liquidity_score"] if s else 50
    prem_dev = (_cached.get("premium_pct") or 0.0) / 100.0
    res = await FA_ENGINE.score(symbol.upper(), usdt_premium_dev=prem_dev, nobil_depth_score=liq)
    # Three states, not two: fetched now, served from the cache, or built from a
    # hard-coded profile. `fresh` alone cannot tell cache from fallback.
    profile = (res.evidence or {}).get("profile")
    if profile == "fallback":
        source = "fallback"
    elif res.cached:
        source = "cached"
    else:
        source = "live"
    return {
        "symbol": symbol.upper(),
        **res.__dict__,
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "profile": profile,
    }


@app.get("/api/risk/plan")
async def risk_plan(symbol: str, side: str = "buy", tf: str = Query("15m", pattern="^(15m|1h|4h|D)$")):
    s = _signal_lookup(symbol, tf)
    if not s:
        await rescan(tf, top=60)
        s = _signal_lookup(symbol, tf)
    if not s:
        return {"error": "symbol not loaded"}
    fx = await _fx_rate(symbol)
    equity_quote = risk.cfg.equity * fx if fx else risk.cfg.equity
    # Preview: report the plan without reserving heat (a concurrent order must
    # not observe a phantom reservation).
    plan = risk.evaluate(symbol.upper(), side, s["lastClose"], s["risk"]["atr"], tf,
                         equity_quote=equity_quote, preview=True)
    return {
        "symbol": symbol.upper(),
        "side": side,
        **plan.__dict__,
        "quote": "IRT" if symbol.upper().endswith("IRT") else "USDT",
        "fx_rate": fx,
        "equity_quote": round(equity_quote, 2),
        "healthHeat": risk.open_heat,
        **provenance(),
    }


@app.get("/api/risk/state")
async def risk_state():
    return {
        "equity": risk.cfg.equity,
        "equity_quote": "USD",
        "openHeat": risk.open_heat,
        "dailyPnl": risk.daily_pnl,
        "halted": risk.halted,
        "caps": {
            "riskPct": risk.cfg.risk_pct,
            "maxConcurrent": risk.cfg.max_concurrent,
            "maxSingleAssetPct": risk.cfg.max_single_asset_pct,
            "maxHeatPct": risk.cfg.max_heat_pct,
            "minCashPct": risk.cfg.min_cash_pct,
        },
        **provenance(),
    }


# ---------------- Paper Trading (ADR-0009/0012/0013) ----------------
@app.post("/api/paper/order", dependencies=[Depends(require_client)])
async def paper_order(payload: dict):
    body = payload or {}
    side = (body.get("side") or "buy").lower()
    symbol = (body.get("symbol") or "").upper()
    qty = float(body.get("qty") or 0)
    size = body.get("size_quote") and float(body["size_quote"]) or None
    if not symbol or (not qty and not size):
        return {"error": "symbol and either qty or size_quote required"}
    if not qty and size:
        # Quote-denominated sizing was documented but silently ignored: callers
        # sending only `size_quote` got an error about `qty`.
        px = body.get("price") and float(body["price"]) or None
        if px is None:
            cached = _cached["signals"].get(symbol)
            px = cached["lastClose"] if cached else None
        if not px or px <= 0:
            return {"error": "price required to convert size_quote into qty"}
        qty = size / px
    plan = body.get("risk_plan")
    broker = get_broker(EQUITY, client, risk)
    try:
        if plan:
            # Reserved for the full RiskPlan hand-off (Sprint 2); the broker
            # already enforces the hard guards (halt, concurrency, notional cap).
            log.debug("risk_plan supplied: %s", plan)
        src = (body.get("source") or "SEMI").upper()
        price = body.get("price") and float(body["price"]) or None
        pos = await broker.place_limit(symbol, side, qty, price, body.get("stop") and float(body["stop"]), body.get("tp1") and float(body["tp1"]), body.get("tp2") and float(body["tp2"]), src)
        return {
            "ok": bool(pos),
            "position": pos.__dict__ if pos else None,
            "error": None if pos else (broker.last_error or "rejected"),
            "metrics": broker.metrics(),
            **provenance(),
        }
    except Exception as e:
        log.exception("paper order failed")
        return {"ok": False, "error": str(e), **provenance()}


def _position_payload(pos) -> dict:
    s = _cached["signals"].get(pos.symbol)
    last = None
    if s and s.get("lastClose"):
        last = float(s["lastClose"])
    if last is None:
        last = float(pos.entry_price)
    pnl_pct = round((last / pos.entry_price - 1) * 100, 2) if pos.entry_price else 0.0
    fx = next((float(r["last"]) for r in _cached["universe"] if r["symbol"] == "USDTIRT" and r.get("last")), 0.0)
    value_quote = pos.filled_qty * last
    value_usd = value_quote / fx if (pos.symbol.endswith("IRT") and fx) else value_quote
    return {**pos.__dict__, "last_price": last, "pnl_pct": pnl_pct, "value_usd": round(value_usd, 2)}


@app.get("/api/paper/positions")
async def paper_positions():
    broker = get_broker(EQUITY, client, risk)
    await streams_start(broker, client)
    await broker.evaluate_positions()
    return {
        "positions": [_position_payload(p) for p in broker.positions.values()],
        "metrics": broker.metrics(),
        **provenance(),
    }


@app.get("/api/ws/status")
async def ws_status():
    """Describe the *actual* price feed.

    `transport` is ``ws`` only when a WebSocket handshake succeeded and ticks
    arrived; otherwise it is ``rest-poll`` with its cadence. The legacy field
    names are kept so the existing dashboard keeps working.
    """
    st = STREAM_EXECUTOR.status()
    return {
        **st,
        "books": {
            sym: {
                "best_bid": b.best_bid,
                "best_ask": b.best_ask,
                "mid": b.mid,
                "last_update": b.last_update,
                "latency": b.latency_ms,
                "fresh": b.fresh,
            }
            for sym, b in STREAM_EXECUTOR.books.items()
        },
    }


@app.get("/api/paper/metrics")
async def paper_metrics():
    broker = get_broker(EQUITY, client, risk)
    await broker.evaluate_positions()
    return {
        "metrics": broker.metrics(),
        "curve": [list(x) for x in broker.equity_curve[-200:]],
        "equity_quote": "USD",
        **provenance(),
    }


@app.post("/api/paper/halt", dependencies=[Depends(require_client)])
async def paper_halt(request: Request, flat: bool = Query(False)):
    """Arm the kill switch. Deliberately has no automatic counterpart.

    Every invocation is written to `audit_log` and, when configured, pushed to
    Telegram. The response says which of those actually happened — the UI used
    to claim both unconditionally.
    """
    broker = get_broker(EQUITY, client, risk)
    broker.halted = True
    risk.halted = True
    log.warning("paper trading halted by API request (flat=%s)", flat)
    audit_id = audit("paper.halt", detail={"flat": flat}, request=request)
    opened = sum(1 for p in broker.positions.values() if p.state != "CLOSED")
    tg = await notify.send(
        f"🛑 Nobi: kill switch armed\nopen positions {opened}\nflat request {flat}\n"
        f"audit #{audit_id}")
    return {"halted": broker.halted, "resumed": False, "open_positions": opened,
            "audit_id": audit_id, "telegram": tg}


@app.post("/api/paper/resume", dependencies=[Depends(require_client)])
async def paper_resume(request: Request):
    """Explicit, audited reset of the kill switch.

    The UI used to toast "restart required" without offering any way to do it
    from the dashboard; resuming is its own deliberate action.
    """
    broker = get_broker(EQUITY, client, risk)
    was = broker.halted or risk.halted
    broker.halted = False
    risk.halted = False
    broker.last_error = None
    log.warning("paper trading resumed by API request (was halted=%s)", was)
    audit_id = audit("paper.resume", detail={"was_halted": was}, request=request)
    tg = await notify.send(f"✅ Nobi: kill switch cleared (was halted={was})\naudit #{audit_id}")
    return {"halted": False, "resumed": was, "audit_id": audit_id, "telegram": tg}


# ---------------- Walk-Forward Backtest (ADR-0009) ----------------
@app.post("/api/backtest/run", dependencies=[Depends(require_client)])
async def backtest_run(payload: dict):
    """Walk-forward backtest: optimise on train, report out-of-sample only."""
    b = payload or {}
    symbol = (b.get("symbol") or "BTCIRT").upper()
    tf = (b.get("tf") or "1h")
    mode = (b.get("mode") or "wf").lower()
    bars = max(200, min(int(b.get("bars", 600)), 2000))
    cfg = {
        "initial_equity": float(b.get("equity", 50000)),
        "risk_pct": float(b.get("risk_pct", 0.75)),
        "atr_stop_k": float(b.get("atr_stop_k", 2.0)),
        "tp1_r": float(b.get("tp1_r", 1.5)),
        "tp2_r": float(b.get("tp2_r", 2.5)),
        "min_score": float(b.get("min_score", 0.0)),
        "max_concurrent": int(b.get("max_concurrent", 5)),
        "train_bars": int(b.get("train_bars", 240)),
        "eval_bars": int(b.get("eval_bars", 120)),
        "fixed_params": bool(b.get("fixed_params", False)),
        "slippage_bps": float(b.get("slippage_bps", 0.0)),
    }
    grid = b.get("grid") if isinstance(b.get("grid"), list) else None
    try:
        rows, src = await client.get_candles_ex(symbol, tf, bars)
        res = await asyncio.to_thread(
            run_and_save, symbol, tf, rows, cfg, mode, grid, bool(b.get("auto_window", True)), src
        )
        return {
            "ok": True,
            "report": res,
            "bars_requested": bars,
            "bars_received": len(rows),
            "source": src,
            **provenance(),
        }
    except UpstreamUnavailable as e:
        return {**upstream_error("backtest", str(e))}
    except Exception as e:
        log.exception("backtest failed")
        import traceback
        return {"ok": False, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/backtest/quick")
async def backtest_quick(symbol: str = "BTCIRT", tf: str = "1h", bars: int = 600):
    """One-call walk-forward with auto window sizing and the default grid."""
    try:
        rows, src = await client.get_candles_ex(symbol, tf, max(200, min(bars, 2000)))
    except UpstreamUnavailable as e:
        row = errorlog.capture(level="warning", source="api.backtest.quick",
                               message=f"candles unavailable for {symbol} {tf}: {e}")
        return {**upstream_error("backtest", str(e)), "log_id": row["id"]}
    except Exception as e:
        row = errorlog.capture(level="error", source="api.backtest.quick",
                               message=f"candle fetch failed for {symbol} {tf}: {e}", exc=e)
        return {"ok": False, "error": f"candle fetch failed: {e}", "log_id": row["id"]}
    if not rows:
        row = errorlog.capture(level="warning", source="api.backtest.quick",
                               message=f"no candles returned for {symbol} {tf}")
        return {"ok": False, "error": "no candles returned — check connectivity to apiv2.nobitex.ir",
                "log_id": row["id"]}
    cfg = {"initial_equity": 50000, "risk_pct": 0.75, "atr_stop_k": 2.0,
           "max_concurrent": 5, "train_bars": 240, "eval_bars": 120}
    try:
        res = await asyncio.to_thread(run_and_save, symbol.upper(), tf, rows, cfg, "wf", None, True, src)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "report": res, "bars": len(rows), "source": src}


@app.post("/api/grid-multi", dependencies=[Depends(require_client)])
async def grid_multi(payload: dict):
    b = payload or {}
    symbols = b.get("symbols") or ["BTCIRT", "ETHIRT"]
    tf = (b.get("tf") or "1h")
    syms = [s.upper() for s in symbols][:16]
    sem = asyncio.Semaphore(4)

    async def one(sym: str):
        try:
            async with sem:
                rows = await client.get_candles(sym, tf, 400)
                if not rows:
                    return {"symbol": sym, "error": "no candles"}
                res = await asyncio.to_thread(grid_search, sym, tf, rows, b.get("grid"))
            return {"symbol": sym, "best": res.get("best"), "elapsed_s": res.get("elapsed_s"), "combo_count": res.get("combo_count")}
        except UpstreamUnavailable as e:
            log.warning("multi-grid %s skipped: %s", sym, e)
            return {"symbol": sym, "unavailable": True, "error": UPSTREAM_MSG_FA}
        except Exception as e:
            row = errorlog.capture(level="error", source="api.grid-multi",
                                   message=f"{sym} {tf}: {e}", exc=e,
                                   context={"symbol": sym, "tf": tf})
            log.error("multi-grid %s failed: %s", sym, e, extra={"nbx_captured": True})
            return {"symbol": sym, "error": str(e), "log_id": row["id"]}

    results = await asyncio.gather(*(one(s) for s in syms))
    return {"ok": True, "tf": tf, "results": results}


@app.get("/api/backtest/reports")
async def backtest_reports(limit: int = 20):
    """List saved run reports (newest first) so the dashboard can browse history."""
    import json as _json
    from pathlib import Path
    d = Path(__file__).parent / "backtest_reports"
    if not d.exists():
        return {"ok": True, "reports": []}
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out = []
    for f in files:
        try:
            j = _json.loads(f.read_text())
            out.append({
                "file": f.name,
                "config_hash": j.get("config_hash"),
                "symbol": j.get("symbol"),
                "tf": j.get("tf"),
                "windows": j.get("windows"),
                "aggregate": j.get("aggregate", {}),
            })
        except Exception:
            continue
    return {"ok": True, "reports": out}


@app.post("/api/backtest/grid", dependencies=[Depends(require_client)])
async def backtest_grid(payload: dict):
    b = payload or {}
    symbol = (b.get("symbol") or "BTCIRT").upper()
    tf = b.get("tf") or "1h"
    custom = b.get("grid") and dict(b["grid"]) or None
    try:
        try:
            rows, _src = await client.get_candles_ex(symbol, tf, int(b.get("bars", 600)))
        except UpstreamUnavailable as e:
            return {**upstream_error("grid", str(e))}
        if not rows:
            row = errorlog.capture(level="warning", source="api.backtest.grid",
                                   message=f"no candles for {symbol} {tf} — grid not run",
                                   context={"symbol": symbol, "tf": tf})
            log.warning("grid skipped for %s %s: no candles", symbol, tf,
                        extra={"nbx_captured": True})
            return {"ok": False, "error": "no candles fetched", "log_id": row["id"]}
        res = await asyncio.to_thread(grid_search, symbol, tf, rows, custom)
        return {"ok": True, **res}
    except Exception as e:
        row = errorlog.capture(level="error", source="api.backtest.grid",
                               message=f"grid failed for {symbol} {tf}: {e}", exc=e,
                               context={"symbol": symbol, "tf": tf,
                                        "combos": len((custom or {}).get("risk_pct") or [])})
        log.error("grid failed for %s %s: %s", symbol, tf, e,
                  extra={"nbx_captured": True})
        return {"ok": False, "error": str(e), "log_id": row["id"]}


@app.get("/api/backtest/promotion-gate")
async def backtest_promotion_gate():
    """ADR-0009 promotion gate evaluation over the latest report for each config."""
    import json as _json
    from pathlib import Path
    d = Path(__file__).parent / "backtest_reports"
    if not d.exists():
        return {"ok": True, "gate": None, "reason": "no reports yet"}
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    latest = None
    for f in files:
        try:
            j = _json.loads(f.read_text())
            if (j.get("aggregate") or {}).get("trades", 0) > 0:
                latest = j
                break
        except Exception:
            continue
    if not latest:
        return {"ok": True, "gate": None, "reason": "no trade-producing reports yet"}
    # New reports embed the full gate evaluation; trust it rather than re-deriving
    # a weaker rule here.
    if isinstance(latest.get("gate"), dict):
        g = latest["gate"]
        return {
            "ok": True,
            "gate": g.get("state"),
            "state": g.get("state"),
            "symbol": latest.get("symbol"),
            "tf": latest.get("tf"),
            "config_hash": latest.get("config_hash"),
            "checks": g.get("checks", {}),
            "failed": g.get("failed", []),
            "message": g.get("message"),
            "bars_needed": g.get("bars_needed"),
            "windows": (latest.get("aggregate") or {}).get("windows"),
            "out_of_sample": True,
            "report_file": latest.get("report_file"),
            "note": "Out-of-sample walk-forward evidence only. Paper-trading evidence is still required before live deployment (ADR-0009).",
        }
    a = latest["aggregate"]
    checks = {
        "profit_factor_ge_1_3": {"value": a.get("profit_factor", 0), "pass": (a.get("profit_factor", 0) or 0) >= 1.3, "required": 1.3},
        "max_drawdown_lt_15": {"value": a.get("max_drawdown", 0), "pass": (a.get("max_drawdown", 0) or 0) < 15, "required": 15},
        "trades_ge_min": {"value": a.get("trades", 0), "pass": (a.get("trades", 0) or 0) >= 30, "required": 30},
    }
    passed = all(c["pass"] for c in checks.values())
    return {
        "ok": True,
        "gate": "PASS" if passed else "FAIL",
        "symbol": latest.get("symbol"),
        "tf": latest.get("tf"),
        "config_hash": latest.get("config_hash"),
        "checks": checks,
        "note": "Legacy report (no embedded gate). Paper-trading evidence is still required before any live deployment (ADR-0009).",
    }


# ---------------- Static Frontend Hosting ----------------
REPO_ROOT = Path(__file__).resolve().parent.parent
if (REPO_ROOT / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(REPO_ROOT / "assets")), name="assets")


def _self_contained_page() -> Path:
    """The dashboard as one file, built on demand.

    `index.html` links `assets/css/*.css` and `nbx-*.js`. That is right for
    development and wrong for every context that blocks subresources: a
    sandboxed preview iframe, a phone opening the file from a download folder,
    an air-gapped host. In those contexts the page arrives with no stylesheet
    and no JavaScript at all — no charts, no data, just unstyled markup.

    So `/` serves the inlined build. It is rebuilt whenever a source file is
    newer, and `NBX_SERVE_INLINE=0` restores the plain file for anyone who
    wants to edit and reload.
    """
    standalone = REPO_ROOT / "nobitex-mobile.html"
    if os.getenv("NBX_SERVE_INLINE", "1") == "0":
        return REPO_ROOT / "index.html"

    markup = [REPO_ROOT / "index.html", REPO_ROOT / "nbx-core.js", REPO_ROOT / "nbx-app.js"]
    css = sorted((REPO_ROOT / "assets" / "css").glob("*.css"))
    newest = max((p.stat().st_mtime for p in [*markup, *css] if p.exists()), default=0)
    stale = (not standalone.exists()) or standalone.stat().st_mtime < newest
    if stale:
        import subprocess

        # build_standalone.py refuses to inline a stylesheet older than the
        # markup (classes added since the last CSS build would be missing).
        # Rebuild the CSS first when the toolchain is present; otherwise the
        # page would keep serving yesterday's bundle with no explanation.
        if css and any(p.stat().st_mtime > max(c.stat().st_mtime for c in css)
                       for p in markup if p.exists()):
            tailwind = REPO_ROOT / "node_modules" / ".bin" / "tailwindcss"
            if tailwind.exists():
                try:
                    subprocess.run(["npm", "run", "css"], cwd=str(REPO_ROOT),
                                   check=True, capture_output=True, timeout=60)
                except Exception as exc:  # noqa: BLE001 - report, then try the build
                    log.warning("css build failed: %s", _short_proc_error(exc))
        try:
            subprocess.run([os.sys.executable, str(REPO_ROOT / "scripts" / "build_standalone.py")],
                           cwd=str(REPO_ROOT), check=True, capture_output=True, timeout=30)
        except Exception as exc:                       # never fail the request
            # A stale-but-complete page beats a fresh-but-empty one: index.html
            # outside the server's file tree would arrive with no CSS or JS at
            # all. Prefer the last good build — and say why, not just "exit 1".
            log.warning("standalone build failed (%s); serving the previous build",
                        _short_proc_error(exc))
            return standalone if standalone.exists() else REPO_ROOT / "index.html"
    return standalone if standalone.exists() else REPO_ROOT / "index.html"


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
async def serve_index():
    # no-store on purpose: the dashboard is edited in place, and a browser that
    # heuristically caches it can keep serving a build from before a tab existed.
    return FileResponse(_self_contained_page(),
                        headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/version-check.html", include_in_schema=False)
async def serve_version_check():
    return FileResponse(REPO_ROOT / "version-check.html")


@app.get("/nbx-core.js", include_in_schema=False)
async def serve_nbx_core():
    return FileResponse(REPO_ROOT / "nbx-core.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/nbx-app.js", include_in_schema=False)
async def serve_nbx_app():
    return FileResponse(REPO_ROOT / "nbx-app.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/manifest.json", include_in_schema=False)
async def serve_manifest():
    return FileResponse(REPO_ROOT / "manifest.json", media_type="application/manifest+json")


@app.get("/nobitex-mobile.html", include_in_schema=False)
async def serve_mobile_html():
    target = REPO_ROOT / "nobitex-mobile.html"
    return FileResponse(target if target.exists() else REPO_ROOT / "index.html")


@app.get("/download/nobitex-mobile.html", include_in_schema=False)
async def download_mobile_html():
    target = REPO_ROOT / "nobitex-mobile.html"
    return FileResponse(
        target if target.exists() else REPO_ROOT / "index.html",
        media_type="text/html",
        filename="nobitex-mobile.html",
    )


@app.get("/download/nobitex-trader-robat.zip", include_in_schema=False)
async def download_package_zip():
    return FileResponse(
        REPO_ROOT / "nobitex-trader-robat.zip",
        media_type="application/zip",
        filename="nobitex-trader-robat.zip",
    )


