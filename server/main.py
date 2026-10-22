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

# (notify.TOKEN, security.API_TOKEN, ws_execution.WS_URL are all
# read at import time, so a later load would be a silent no-op)
import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import _env  # noqa: F401 — must precede every module that reads os.environ
import adaptive
import auth
import auth_api
import coin_metadata
import devsuite
import diagnostics
import errorlog
import freshness
import identity
import journal as signal_journal
import nobitex_metadata
import notify
import paper_trading
import reference_feed
from adaptive import AdaptiveError as AdaptiveRefusal
from backtest import SLIP_BPS, grid_search, run_and_save
from benchmark import MIN_OBS as BENCH_MIN_OBS
from benchmark import PERIODS_PER_YEAR
from benchmark import relative as relative_to
from benchmark import summarise as summarise_relative
from exec_mode import EXEC_MODE, InvalidMode
from fa_engine import FA_ENGINE, FAResult
from fees import DEFAULT_EQUITY_USD
from market_overview import overview as market_overview
from money import Rate, toman, usd
from nobitex_client import ClientError, NobitexClient, UpstreamUnavailable
from paper_trading import FEE_PCT as PAPER_FEE_PCT
from paper_trading import get_broker
from portfolio import Candidate, Limits, aligned_closes, log_returns
from portfolio import allocate as allocate_portfolio
from risk_engine import (
    DEFAULT_RR_LEVELS,
    MIN_RR,
    RiskConfig,
    RiskEngine,
    parse_rr_levels,
    preflight,
)
from security import (
    API_TOKEN,
    auth_mode,
    cors_origins,
    require_client,
    startup_problem,
    trusted_proxies,
)
from ta_engine import RISK_K, candles_to_df, ta_score, ta_score_series
from units import apply_options, market_symbol, min_notional_usd
from ws_execution import STREAM_EXECUTOR, streams_start

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
    except Exception as e:
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
        except Exception as e:  # the scheduler must survive
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
            except Exception as e:
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
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%MZ")
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
        except Exception as e:  # the scheduler must survive
            log.warning("digest failed: %s", e)
            _last_digest[0] = time.time()      # back off a full interval
        await asyncio.sleep(min(300, max(30, interval / 10)))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Any ERROR/FATAL record from any logger now also lands in the error log,
    # which is what the dashboard's Diagnostics page reads.
    errorlog.install()
    # A stale inlined page is a deploy-time problem. Rebuild it here — once,
    # before the first request — when the checkout asks for it; `GET /` must
    # never wait for `npm`.
    _build_page_once()
    # Fail closed and loudly. A process that starts with a configuration its
    # guard rails cannot describe is worse than one that refuses to start.
    problem = startup_problem()
    if problem:
        raise RuntimeError(problem)
    await client.connect()
    log.info("security: auth mode=%s, cors=%s, trusted proxies=%s",
             auth_mode(), _CORS_ORIGINS or "same-origin",
             ", ".join(str(n) for n in trusted_proxies()) or "none")
    # A background task that dies silently is invisible: the dashboard simply
    # stops updating and nobody knows why. Wrap each one so the death is logged
    # and the other loops keep running.
    def _supervise(name, coro):
        async def runner():
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("background task %s crashed", name)
                errorlog.capture(source=f"task.{name}", exc=exc,
                                 message=f"background task '{name}' crashed")
        return asyncio.create_task(runner())

    # Market-wide socket channels are worth having even with no position open:
    # `public:market-stats-all` keeps the board and the closed-market gate
    # current. Live callbacks are registered here, before the socket starts.
    STREAM_EXECUTOR.on_market.append(_apply_stream_stat)
    STREAM_EXECUTOR.want_stats = True
    STREAM_EXECUTOR.ensure_ws()
    # Positions are marked from a fresh socket book when there is one; the REST
    # poll stays as the fallback. Without this, every open position asked the
    # exchange for a book on every tick while the socket had just delivered it.
    paper_trading.hot_mid_hook = lambda sym: _hot_mid(sym)

    options_task = _supervise("options", _options_loop())
    tasks = [
        _supervise("coin-metadata", coin_metadata.CACHE.run()),
        _supervise("universe", load_universe()),
        _supervise("rescan", rescan("15m")),
        _supervise("position-tick", _position_tick_loop()),
        _supervise("digest", _digest_loop()),
        # The exchange publishes its own minimums and tick sizes at /v2/options;
        # reading them once beats shipping a copy that can silently go stale.
        options_task,
        # `auto` mode is a server-side executor, not a chip in the page: the
        # loop reads the same mode the UI writes and places paper orders
        # through the same ten gates as an approved queue row.
        _supervise("auto-exec", _auto_loop()),
        # `public:market-stats-all` keeps the board and the closed-market gate
        # current, and it is worth having with no position open. The socket used
        # to be started only from `tick_once`, which returns early when nothing
        # is open — so the channel that exists for an empty account was never
        # subscribed.
        _supervise("market-stream", streams_start(get_broker(EQUITY, client, risk), client)),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        # Finish both metadata workers before closing their HTTP clients.
        await asyncio.gather(tasks[0], options_task, return_exceptions=True)
        await STREAM_EXECUTOR.close()
        await client.aclose()


app = FastAPI(
    title="nobiTrader",
    version="1.0.22",
    lifespan=lifespan,
)

# Same-origin by default: the dashboard is served by this very app, so it needs
# no CORS at all. Add explicit origins only for a separate front-end.
_CORS_ORIGINS = cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    # Sessions travel in a cookie. A cross-origin front-end (an explicit
    # NBX_CORS_ORIGINS list, never "*") therefore needs credentials allowed,
    # and it needs the CSRF header to survive the preflight.
    allow_credentials=bool(_CORS_ORIGINS),
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["content-type", "x-api-token", "x-csrf-token", "authorization"],
)

app.include_router(auth_api.router)

#: Reachable without a session. `/api/health` stays open because uptime
#: monitors cannot log in, and it is the endpoint a deployment is checked with.
#: Everything under `/api/auth/` handles its own permissions (login must be
#: open; the owner area checks the role itself). Static files stay open because
#: the login screen is part of them.
_PUBLIC_PATHS = {"/api/health"}
_PUBLIC_PREFIXES = ("/api/auth/",)


@app.middleware("http")
async def require_login_for_api(request: Request, call_next):
    """Close every API route once accounts exist.

    `require_client` only guards mutations. Adding accounts and leaving the
    reads open would produce the oddest possible deployment: a login screen in
    front of a dashboard whose positions anyone can fetch with curl. So the gate
    is the middleware, and it is keyed on "has anybody claimed this install?"
    rather than on an address — a laptop with no accounts keeps working exactly
    as before, which is also what keeps the existing tests honest.
    """
    path = request.url.path
    if not (path.startswith("/api/") or path in ("/openapi.json", "/docs", "/redoc")):
        return await call_next(request)
    if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
        return await call_next(request)
    if not auth.any_users():
        return await call_next(request)

    session = identity.session_of(request)
    if session is not None:
        request.state.user = session.user
        request.state.auth_method = "session"
        return await call_next(request)
    if security_token_ok(request):
        request.state.auth_method = "token"
        return await call_next(request)
    return JSONResponse(
        {"detail": "برای دیدن این بخش باید وارد شوید", "auth": "login"},
        status_code=401,
        headers={"WWW-Authenticate": "Cookie"},
    )


def security_token_ok(request: Request) -> bool:
    """Read the shared secret lazily so tests can set it after import."""
    from security import token_valid
    return token_valid(request)


TIMEFRAMES = ["15m", "1h", "4h", "D"]
client = NobitexClient()
EQUITY = float(os.getenv("NBX_EQUITY_USD", str(DEFAULT_EQUITY_USD)))  # 1.0.5: one default, from fees.py


def risk_config_from_env() -> RiskConfig:
    """The live risk policy, including the take-profit ladder.

    `NBX_RR_LEVELS` exists so the operator can hand the backtest's winning ladder
    to the live plan on purpose. It is not automatic: the backtester searches
    `tp1_r/tp2_r` and the live engine applies one policy until someone chooses
    another (ADR-0008 §6). A value that cannot be understood keeps the default and
    says so in the log — quietly running a different ladder is worse than running
    the documented one.
    """
    try:
        levels = parse_rr_levels(os.getenv("NBX_RR_LEVELS"))
    except ValueError as exc:
        log.warning("%s — keeping the default take-profit ladder %s", exc, DEFAULT_RR_LEVELS)
        levels = DEFAULT_RR_LEVELS
    return RiskConfig(equity=EQUITY, rr_levels=levels)


risk = RiskEngine(risk_config_from_env())

# Freshness budgets (seconds). The dashboard polls every 2 minutes; keeping the
# server cache a little shorter than that is enough to keep it honest without
# hammering Nobitex.
SIGNALS_TTL_S = int(os.getenv("NBX_SIGNALS_TTL_S", "90"))
UNIVERSE_TTL_S = int(os.getenv("NBX_UNIVERSE_TTL_S", "120"))
UPSTREAM_TTL_S = int(os.getenv("NBX_UPSTREAM_TTL_S", "20"))

#: Read by `paper_trading.market_closed_hook`; kept as its own object so the
#: hook stays valid across the `_cached` resets below.
_CACHE_CLOSED: set = set()


def _hot_mid(symbol: str) -> float | None:
    """The socket's mid for `symbol`, or None when it is not live/fresh."""
    if STREAM_EXECUTOR.transport != "ws":
        return None
    book = STREAM_EXECUTOR.books.get(symbol.upper())
    if book is None or not book.fresh:
        return None
    return book.mid or None


def _apply_stream_stat(symbol: str, st) -> None:
    """Fold one `public:market-stats-all` row into the cached board.

    Two effects, both real: the price on the board/ticket follows the socket
    instead of waiting for the next REST poll, and the `market-closed` gate sees
    a market that closed *between* polls. A paper fill in a market that has just
    closed is a fill that could not have happened.
    """
    try:
        rows = _cached.get("universe") or []
        for r in rows:
            if r.get("symbol") != symbol:
                continue
            if st.latest > 0:
                r["last"] = st.latest
            r["change24"] = st.day_change
            r["closed"] = bool(st.closed)
            # Provenance travels with the row: the page can say where the price
            # came from instead of implying the REST snapshot is still current.
            r["live"] = "ws"
            r["live_at"] = st.updated
            if symbol == "USDTIRT":
                _cached["premium"] = st.latest
                _cached["premium_pct"] = _premium_pct(rows)
        if st.closed:
            _CACHE_CLOSED.add(symbol)
        else:
            _CACHE_CLOSED.discard(symbol)
        _cached["closed_markets"] = sorted(_CACHE_CLOSED)
    except Exception as e:  # a stats frame must never break the app
        log.debug("stream stats apply failed for %s: %s", symbol, e)

_cached: dict = {
    "universe": [],
    "universe_ts": 0.0,
    "closed_markets": [],
    "signals": {},
    "signals_by_tf": {},
    "signals_ts": {},
    #: Newest candle open (epoch seconds) per timeframe, as seen by the scan that
    #: produced `signals_by_tf`. The scan throws away 260 rows per symbol, so this
    #: stamp is the only thing left that can answer "how old are these numbers".
    "signals_bar": {},
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
        "as_of": datetime.now(UTC).isoformat(),
    }


def provenance() -> dict:
    """Where the numbers the API is returning actually came from."""
    return {
        "source": "unavailable" if client.degraded_mode else "live",
        "as_of": datetime.now(UTC).isoformat(),
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
        pct = (float(usdt_irt) / implied - 1.0) * 100.0
        # A "premium" of ±50 % is not a premium, it is a data error: a stale
        # quote on one leg, or a pair that is not what its name says. Feeding
        # that into the macro veto would silently flip a real signal.
        if abs(pct) > 50.0:
            return None
        return round(pct, 3)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


async def _fx(symbol: str = "BTCIRT") -> Rate | None:
    """The rate every money conversion on this request must use.

    Deliberately the **broker's** rate, not a second lookup: the preview
    (`/api/risk/whatif`, `/api/risk/plan`) and the order (`broker.place_limit`)
    used to read the rate from two different places — one fresh, one cached
    forever — so a moving market could produce a preview that promised a fill
    the broker then refused. One source removes the possibility.

    Returns ``None`` when no rate is available. Callers must refuse; they may
    not substitute 1.0 or fall back to the untouched amount.
    """
    if not (symbol or "").upper().endswith("IRT"):
        return Rate(toman_per_usdt=1.0, as_of=time.time(), source="usdt-quote",
                    max_age_s=86_400.0)
    broker = get_broker(EQUITY, client, risk)
    rate = await broker._fx()
    if rate is not None:
        return rate
    # Broker has never seen a rate (fresh process, exchange unreachable at the
    # first attempt). The universe row is a last chance, with the same unit.
    row = next((r for r in _cached["universe"] if r["symbol"] == "USDTIRT"), None)
    px = float(row["last"]) if row and row.get("last") else 0.0
    if px <= 0:
        return None
    # Dated by when the snapshot was taken, not by when it is read. Stamping
    # `time.time()` here made a universe loaded an hour ago look like a rate
    # read a millisecond ago — the same lie as the position row's `as_of=now`.
    rate = Rate(toman_per_usdt=px, as_of=float(_cached.get("universe_ts") or 0.0),
                source="universe", max_age_s=float(UNIVERSE_TTL_S) * 2)
    return None if rate.is_stale else rate


async def _fx_rate(symbol: str) -> float:
    """Toman per USD for this symbol, or 0.0 when there is no rate.

    Kept for callers that only need the number; the named refusal lives in the
    routes that must not proceed (`_fx` returning ``None``).
    """
    rate = await _fx(symbol)
    return rate.toman_per_usdt if rate else 0.0


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
    except Exception as e:  # health must never raise
        err = str(e) or e.__class__.__name__
    finally:
        _upstream.update(
            ts=time.time(),
            ok=ok,
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            error=err,
            probing=False,
        )


#: Fire-and-forget tasks must be held somewhere: asyncio only keeps a weak
#: reference, so a task nobody points at can be collected before it runs.
_BG_TASKS: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


def _upstream_snapshot() -> dict:
    age = time.time() - _upstream["ts"] if _upstream["ts"] else None
    if _upstream["ok"] is None or age is None or age > UPSTREAM_TTL_S:
        _spawn(_probe_upstream())
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
        "ts": datetime.now(UTC).isoformat(),
        "upstream": _upstream_snapshot(),
        "auth": {"mode": auth_mode(), "token_required": bool(API_TOKEN),
                 "cors_origins": _CORS_ORIGINS,
                 # Whether the install has accounts decides how the API behaves;
                 # a monitor reading this should not have to guess.
                 "accounts": auth.any_users()},
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


async def _load_options() -> int:
    """Apply the exchange's documented minimums and precisions (best effort)."""
    try:
        body = await client.get("/v2/options", attempts=1)
        nobitex_metadata.CACHE.ingest(body)
        n = apply_options(body)
        if n:
            log.info("options: %d precision/minimum override(s) applied", n)
        return n
    except Exception as e:  # defaults stay in force
        nobitex_metadata.CACHE.failed()
        log.warning("options: could not read /v2/options (%s); using documented defaults", e)
        return 0


async def _options_loop():
    # Share the exchange's existing request with metadata; never fetch twice.
    # Good snapshots refresh hourly, failures retry every five minutes.
    while True:
        await _load_options()
        await asyncio.sleep(nobitex_metadata.RETRY if nobitex_metadata.CACHE.error
                            else nobitex_metadata.TTL)


async def _load_universe_locked():
    try:
        stats = await client.get_market_stats()
        # Mixed str/float/bool per row: `dict[str, Any]` is the honest type, and
        # it is what lets `--check-untyped-defs` see the sort and join below.
        rows: list[dict[str, Any]] = []
        for sym, d in (stats.get("stats") or {}).items():
            # The documented key is `btc-rls`; `market_symbol` turns it into the
            # `BTCIRT` the order book and the chart endpoints require. The old
            # `norm()` (upper-case only) produced "BTC-RLS", which is not a
            # market: every lookup, the premium and the FX conversion failed.
            sym_n = market_symbol(sym) or norm(sym)
            last = float(d.get("latest") or d.get("last") or 0)
            # `volumeDst` is the documented field; `valueDst` is not in the docs
            # and is kept only as a legacy alias for older payloads.
            vol = float(d.get("volumeDst") or d.get("valueDst") or d.get("24h_volume") or 0)
            chg = float(d.get("dayChange") or 0)
            if sym_n and last > 0:
                rows.append({"symbol": sym_n, "last": last, "vol24": vol,
                             "change24": chg, "closed": bool(d.get("isClosed"))})
        rows.sort(key=lambda x: x["vol24"], reverse=True)
        _cached["universe"] = rows[:60]
        closed = sorted(r["symbol"] for r in rows if r.get("closed"))
        _cached["closed_markets"] = closed
        _CACHE_CLOSED.clear()
        _CACHE_CLOSED.update(closed)
        try:
            import paper_trading as _pt
            _pt.market_closed_hook = lambda sym: norm(sym) in _CACHE_CLOSED
        except Exception:  # the hook is optional
            pass
        if closed:
            log.info("universe: %d market(s) reporting isClosed: %s", len(closed), ", ".join(closed[:6]))
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
                "ts": datetime.now(UTC).isoformat(),
                "age_s": round(age or 0.0, 1),
                "count": 0, "symbols": [], "premium_usdtirt": None, "premium_pct": None}
    return {
        "ok": True,
        "ts": datetime.now(UTC).isoformat(),
        "age_s": round(age or 0.0, 1),
        "premium_usdtirt": _cached["premium"],
        "premium_pct": _cached["premium_pct"],
        "count": len(_cached["universe"]),
        "symbols": _cached["universe"],
        **provenance(),
    }


#: Markets that exist but cannot be analysed with candles. USDTIRT is the
#: quote asset itself: Nobitex answers `/market/udf/history?symbol=USDTIRT`
#: with 403, so asking spent one request per scan to collect a rejection (and
#: the rejection used to trip the shared breaker — see `nobitex_client`).
NOT_A_TA_SYMBOL = frozenset({"USDTIRT"})


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
    #: The newest candle any symbol reported this cycle. Not "now": if the venue
    #: hands back a series that stopped three days ago, the scores describe three
    #: days ago, and `age_bars` is how the card finds out.
    newest_bars: list[float] = []
    def _composite(ta: float, liq: float, fa: float | None) -> tuple[float, str]:
        return composite_score(ta, liq, fa)

    async def one(u):
        if u["symbol"] in NOT_A_TA_SYMBOL:
            # Not a failure: this market is deliberately outside the scan, so
            # it is not counted as "had no candles" in the empty-scan message.
            return
        try:
            async with sem:
                rows, src = await client.get_candles_ex(u["symbol"], tf, 260)
            # Unusable stamps are not a failed symbol: the block above will answer
            # `no-candles`, which is a result the card can print.
            with suppress(KeyError, TypeError, ValueError):
                newest_bars.append(max(float(r["t"]) for r in rows if r.get("t") is not None))
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
            except Exception as e:  # one symbol must not kill the scan
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
    if newest_bars:
        _cached["signals_bar"][tf] = max(newest_bars)
    _cached["last_scan"] = datetime.now(UTC).isoformat()
    log.info("rescan %s: %d symbols, FA %d/%d", tf, len(out), len(scored), len(out))
    if os.getenv("NBX_JOURNAL", "1") != "0":
        try:
            added = await journal_scan(tf)
            if added:
                log.info("journal: %d new prediction(s) on %s", added, tf)
        except Exception as e:  # journalling must not break scans
            log.warning("journal write failed: %s", e)
    return out


async def _bg_rescan(tf: str) -> None:
    try:
        await rescan(tf)
    except Exception as e:
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
        _spawn(_bg_rescan(tf))

    tf_signals = _cached["signals_by_tf"].get(tf) or _cached["signals"]
    ranked = sorted(tf_signals.values(), key=lambda x: x["composite"], reverse=True)
    if not ranked:
        # No live candles ⇒ no scores. Returning an empty list alone would look
        # like "nothing is interesting today"; say which wall was hit.
        skipped = _cached.get("scan_skipped") or []
        why = (f"{len(skipped)}/{len(_cached['universe'])} symbol(s) had no candles"
               if _cached["universe"] else (_cached.get("universe_error") or "no universe"))
        return {**upstream_error("signals", why),
                "ts": datetime.now(UTC).isoformat(), "tf": tf, "count": 0,
                "age_s": round(age or 0.0, 1), "stale": stale, "refreshing": False,
                "scored": [], "premium_usdtirt": _cached["premium"], "premium_pct": _cached["premium_pct"],
                "fa_coverage": _cached.get("fa_coverage"),
                # No scores is not the same as no candles: the block still says
                # which wall was hit, with a named reason.
                "freshness": freshness_block_at(_cached.get("signals_bar", {}).get(tf), tf)}
    return {
        "ok": True,
        "ts": datetime.now(UTC).isoformat(),
        "tf": tf,
        "count": len(ranked),
        "age_s": round(age or 0.0, 1),
        "stale": stale,
        "refreshing": bool(_cached["refreshing"].get(tf)),
        "premium_usdtirt": _cached["premium"],
        "premium_pct": _cached["premium_pct"],
        "fa_coverage": _cached.get("fa_coverage"),
        # 1.0.21 — the ranking is a claim about the candles the scan read. How old
        # those candles were travels with it, in bars of this very timeframe.
        "freshness": freshness_block_at(_cached.get("signals_bar", {}).get(tf), tf),
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
    # The series ends where the last REST poll left it; the socket publishes the
    # same candle every ~10 s. Refreshing only the last row keeps the chart (and
    # the scan that reads this series) on the market's clock, not the poll's.
    merged = STREAM_EXECUTOR.merge_last_candle(symbol, tf, rows)
    return {
        "ok": True,
        "symbol": symbol.upper(),
        "tf": tf,
        "count": len(merged),
        "rows": merged,
        "source": src,
        "last_candle": "ws" if merged is not rows else "rest",
        # 1.0.22 — the ATR percentile, walked over the bars this response carries,
        # so the chart can draw the rank instead of only naming it.
        "volatility": volatility_block(merged),
        # ...and, as on every card since 1.0.18, how old those bars are.
        "freshness": freshness_block(merged, tf),
        "as_of": datetime.now(UTC).isoformat(),
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
    except TimeoutError:
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
    bid_depth = sum(lvl["p"] * lvl["q"] for lvl in bids if abs(lvl["p"] - mid) <= band)
    ask_depth = sum(lvl["p"] * lvl["q"] for lvl in asks if abs(lvl["p"] - mid) <= band)
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


#: The timeframes the engine scores. A body may name anything; only these four
#: have a series behind them, and everything else is answered with the default
#: rather than with a chart of `undefined`.
_TFS = ("15m", "1h", "4h", "D")


def _tf_or(value: object, default: str) -> str:
    """A timeframe from a request body, or ``default`` — never an unknown one.

    Three routes used `body.get("tf") if body.get("tf") in (...) else "15m"`
    inline. Besides being untyped (it let `None` reach `get_candles_ex`), the
    three copies could drift apart one at a time.
    """
    return value if isinstance(value, str) and value in _TFS else default


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
        payload["generated_at"] = datetime.now(UTC).isoformat()
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
    except Exception as e:  # an unreadable journal is not a 500
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
        except Exception as e:  # one symbol must not kill the baseline
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
        "computed_at": datetime.now(UTC).isoformat(),
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
    except Exception as e:  # the detail view must still render
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

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%MZ")
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


def _innermost(exc: BaseException) -> BaseException:
    """The exception a human wrote, out of any ExceptionGroup around it.

    Middleware run in a task group re-raise the original error wrapped in an
    `ExceptionGroup`. Logging the group buries the one line that matters
    ("deliberate failure ...") under ~4 KB of wrapper frames and the 4000-char
    truncation then cuts it off — the error log would show a 500 with no reason.
    """
    seen = 0
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions and seen < 10:
        exc = exc.exceptions[0]
        seen += 1
    return exc


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """Record every unhandled exception, then answer with a JSON error.

    Without this the browser gets a generic 500 and the traceback exists only
    in the server's stdout — unreachable from the dashboard, which is exactly
    where someone notices the problem.
    """
    leaf = _innermost(exc)
    row = errorlog.capture(
        level="critical", source="http.unhandled", exc=leaf,
        # The message carries the cause, because the traceback is truncated and
        # the list view shows the message first.
        message=f"{request.method} {request.url.path} failed: "
                f"{type(leaf).__name__}: {leaf}",
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


# ---------------- Diagnostics: "is this process healthy right now?" --------
def _diag_ctx() -> diagnostics.Ctx:
    """Hand the checks the same objects the routes use.

    Built per request rather than cached: the whole point is to observe the
    state *now*, and a snapshot taken at import time would answer a question
    nobody asked.
    """
    broker = get_broker(EQUITY, client, risk)

    def paper_state() -> dict:
        return {"equity": broker.equity, "cash": broker.cash,
                "positions": dict(broker.positions)}

    def risk_state() -> dict:
        eq, _start = _live_equity()
        return {"equity": eq, "halted": risk.halted, "openHeat": risk.open_heat,
                "caps": {"riskPct": risk.cfg.risk_pct,
                         "maxConcurrent": risk.cfg.max_concurrent,
                         "maxSingleAssetPct": risk.cfg.max_single_asset_pct,
                         "maxHeatPct": risk.cfg.max_heat_pct,
                         "minCashPct": risk.cfg.min_cash_pct}}

    def ws_state() -> dict:
        st = dict(STREAM_EXECUTOR.status())
        st.setdefault("connected", st.get("transport") == "ws")
        return st

    import settings_view
    return diagnostics.Ctx(
        client=client, cached=_cached, paper_state=paper_state, risk_state=risk_state,
        fx=lambda: _fx("BTCIRT"), ws_status=ws_state,
        secrets={s["name"]: bool(s.get("present")) for s in settings_view.secrets()},
    )


@app.get("/api/diagnostics")
async def diagnostics_catalogue():
    """What can be checked, before anything has been checked."""
    ok, why = devsuite.available()
    return {"checks": diagnostics.catalogue(), "devsuite": {"available": ok, "reason": why}}


@app.post("/api/diagnostics/run", dependencies=[Depends(require_client)])
async def diagnostics_run(body: dict | None = None):
    """Run every check (or the named ones) and return what was observed.

    Synchronous on purpose: the slow checks are three HTTP calls to the
    exchange with a 20s ceiling each, and a run that returned an id would only
    add polling to hide a two-second wait.
    """
    only = (body or {}).get("only") or None
    kinds = (body or {}).get("kinds") or None
    if only is not None and not isinstance(only, list):
        raise HTTPException(status_code=400, detail="`only` must be a list of check ids")
    try:
        return await diagnostics.run(_diag_ctx(), only=only, kinds=kinds)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ---------------- Dev suite: "is this checkout correct?" -------------------
@app.get("/api/devsuite")
async def devsuite_status():
    return devsuite.status()


@app.post("/api/devsuite/run", dependencies=[Depends(require_client)])
async def devsuite_run(body: dict | None = None):
    gates = (body or {}).get("gates") or None
    try:
        run = await devsuite.start(gates)
    except devsuite.Busy as e:
        raise HTTPException(status_code=409,
                            detail={"message": str(e), "run_id": e.run_id}) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"run_id": run.id, "state": run.state}


@app.get("/api/devsuite/{run_id}")
async def devsuite_get(run_id: str):
    run = devsuite.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="no such run (the last 10 are kept)")
    return run.as_dict()


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
        "ts": datetime.now(UTC).isoformat(),
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
    rate = await _fx(symbol)
    if rate is None:
        # `equity_quote = eq * fx if fx else eq` used to answer this by leaving
        # the amount untouched: 100,000 USD of equity read as 100,000 Toman,
        # i.e. ~10,000 USD, and every plan came back ~10× too small with no
        # indication that anything was missing.
        return {**upstream_error("risk-plan", "no USDT/IRT rate to size an IRT trade"),
                "symbol": symbol.upper(), "side": side, "ok": False,
                "error": "no-fx-rate",
                "message": ("a Toman order cannot be sized against a dollar balance "
                            "without the USDT/IRT rate")}
    eq, _start = _live_equity()
    equity_quote = usd(eq).to_irt(rate).amount
    # Preview: report the plan without reserving heat (a concurrent order must
    # not observe a phantom reservation).
    plan = risk.evaluate(symbol.upper(), side, s["lastClose"], s["risk"]["atr"], tf,
                         equity_quote=equity_quote, preview=True)
    return {
        "symbol": symbol.upper(),
        "side": side,
        **plan.__dict__,
        "quote": "IRT" if symbol.upper().endswith("IRT") else "USDT",
        "fx_rate": rate.toman_per_usdt,
        "fx": rate.to_dict(),
        "equity_quote": round(equity_quote, 2),
        "healthHeat": risk.open_heat,
        **provenance(),
    }


@app.post("/api/risk/whatif")
async def risk_whatif(payload: dict, request: Request):
    """Project one candidate order onto the account, without placing it.

    Answers the question A2 was written for: *if I take this, what happens to
    heat, cash and the caps?* Every gate comes from `risk_engine.preflight`,
    the same function `PaperBroker.place_limit` uses, so the projection cannot
    promise something the broker would reject.

    Nothing here mutates state. If a price or ATR is unavailable the answer is
    `unavailable` — a projection built on a filled-in number would be worse
    than no projection at all.
    """
    body = payload or {}
    sym = (body.get("symbol") or "").upper()
    if not sym:
        return {"ok": False, "error": "symbol required"}
    side = "buy" if str(body.get("side") or "buy").lower() in ("buy", "long") else "sell"
    tf = _tf_or(body.get("tf"), "15m")

    snap = _signal_lookup(sym, tf)
    entry = float(body.get("entry") or 0.0) or float((snap or {}).get("lastClose") or 0.0)
    if entry <= 0:
        return {**upstream_error("whatif", f"no price for {sym}"),
                "symbol": sym, "ok": False}

    atr = float(((snap or {}).get("risk") or {}).get("atr") or 0.0)
    stop = float(body.get("stop") or 0.0)
    if stop <= 0:
        if atr <= 0:
            return {**upstream_error("whatif", f"no ATR for {sym} on {tf}"),
                    "symbol": sym, "ok": False}
        stop = entry - RISK_K * atr if side == "buy" else entry + RISK_K * atr
    if abs(entry - stop) <= 0:
        return {**upstream_error("whatif", "entry equals stop; risk is zero"),
                "symbol": sym, "ok": False}

    br = get_broker(EQUITY, client, risk)
    rate = await _fx(sym)
    if rate is None:
        # `or 1.0` here read a 300,000-Toman minimum as 300,000 USD, so the
        # single-asset gate vetoed orders that were perfectly sound.
        return {"ok": False, "symbol": sym, "side": side, "blocked": True,
                "blocked_by": "no-fx-rate", "error": "no-fx-rate",
                "message": ("no USDT/IRT rate: a Toman order cannot be valued "
                            "against a dollar balance"),
                **provenance()}
    fx = rate.toman_per_usdt
    equity_quote = usd(br.equity).to_irt(rate).amount

    # Size: caller's, else what the risk engine would put on this trade.
    size_quote = float(body.get("size_quote") or 0.0)
    sized_by = "caller"
    if size_quote <= 0:
        plan = risk.evaluate(sym, side, entry, abs(entry - stop) / RISK_K, tf,
                             equity_quote=equity_quote, preview=True)
        if plan.blocked:
            # Name the actual gate. Reporting "sizing" for an armed kill switch
            # sends the reader looking at ATR and position maths.
            why = "halted" if "kill-switch-armed" in plan.warnings else "sizing"
            return {"ok": False, "symbol": sym, "side": side, "blocked": True,
                    "blocked_by": why, "detail": plan.warnings,
                    "message": ("the kill switch is armed"
                                if why == "halted"
                                else "the risk engine would not size this trade"),
                    **provenance()}
        size_quote = plan.size_quote
        sized_by = "risk-engine"
    qty = size_quote / entry
    notional_quote = qty * entry
    fee_quote = notional_quote * PAPER_FEE_PCT

    notional_usd = notional_quote / fx
    fee_usd = fee_quote / fx
    risk_usd = abs(entry - stop) * qty / fx

    open_positions = len([p for p in br.positions.values() if p.state != "CLOSED"])
    pf = preflight(
        equity=br.equity, cash=br.cash,
        open_heat=getattr(risk, "open_heat", 0.0),
        open_positions=open_positions,
        concurrent_limit=br.concurrent_limit,
        max_heat_pct=risk.cfg.max_heat_pct,
        min_cash_pct=risk.cfg.min_cash_pct,
        max_single_asset_pct=risk.cfg.max_single_asset_pct,
        risk_pct_cap=risk.cfg.risk_pct,
        halted=bool(br.halted or risk.halted),
        notional_usd=notional_usd, fee_usd=fee_usd, risk_usd=risk_usd,
        # The same two documented exchange rules the broker applies, or the
        # preview promises an order the exchange would refuse.
        min_notional_usd=min_notional_usd(sym, fx),
        market_closed=sym in _cached["closed_markets"],
    )
    out = pf.summary()
    audit("risk.whatif", actor="ui", detail={"symbol": sym, "side": side,
                                             "blocked_by": out["blocked_by"]}, request=request)
    return {
        "ok": True,
        "symbol": sym, "side": side, "tf": tf,
        "entry": round(entry, 8), "stop": round(stop, 8),
        "stop_pct": round(abs(entry - stop) / entry * 100, 3),
        "size_quote": round(notional_quote, 2),
        "qty": qty,
        "quote": "IRT" if sym.endswith("IRT") else "USDT",
        "sized_by": sized_by,
        "atr_source": "signal" if atr > 0 else "none",
        "projection": out,
        **provenance(),
    }


@app.post("/api/portfolio/allocate")
async def portfolio_allocate(payload: dict, request: Request):
    """Split the heat budget across the board's LONG candidates.

    The board can offer eight longs at once. Sizing each as if it were alone
    spends the whole heat budget on what may be one bet, because alts move
    together. This route prices that correlation instead of ignoring it, then
    runs the *same* `preflight` the broker runs on each survivor, so what it
    prints is what the desk can actually place.

    Read-only, like the what-if route: no order, no journal row, no heat.
    """
    body = payload or {}
    tf = _tf_or(body.get("tf"), "15m")
    try:
        lookback = max(60, min(int(body.get("lookback") or 200), 400))
        limit = max(1, min(int(body.get("limit") or 5), 8))
    except (TypeError, ValueError):
        return {"ok": False, "error": "lookback and limit must be integers"}

    br = get_broker(EQUITY, client, risk)
    if br.halted or risk.halted:
        return {"ok": False, "blocked": True, "blocked_by": "halted",
                "message": "the kill switch is armed", **provenance()}

    board = _cached.get("signals_by_tf", {}).get(tf) or _cached.get("signals") or {}
    if not board:
        return {**upstream_error("portfolio", "no scan has run yet"),
                "ok": False, "allocations": []}

    explicit = body.get("symbols")
    if explicit:
        if not isinstance(explicit, list):
            return {"ok": False, "error": "symbols must be a list"}
        selected = [str(x).upper() for x in explicit][:8]
        why_absent = [s for s in selected if s not in board]
        if why_absent:
            return {"ok": False, "error": f"not in the scanned universe: {', '.join(why_absent)}"}
    else:
        longs = [(sym, row.get("composite") or 0.0) for sym, row in board.items()
                 if row.get("direction") == "LONG"]
        selected = [sym for sym, _ in sorted(longs, key=lambda kv: -kv[1])[:limit]]

    if not selected:
        return {"ok": False,
                "error": "the board has no LONG candidate to allocate",
                "message": "nothing to allocate; a NEUTRAL board is not a reason to invent trades",
                **provenance()}

    candidates: list[Candidate] = []
    series: dict[str, list[tuple[int, float]]] = {}
    notes: list[dict] = []
    srcs: dict[str, str] = {}
    for sym in selected:
        snap = board.get(sym) or {}
        entry = float(snap.get("lastClose") or 0.0)
        atr = float((snap.get("risk") or {}).get("atr") or 0.0)
        if entry <= 0 or atr <= 0:
            notes.append({"symbol": sym, "reason": "no price or ATR on this timeframe"})
            continue
        stop = entry - RISK_K * atr
        if stop <= 0:
            notes.append({"symbol": sym, "reason": "ATR stop is below zero"})
            continue
        try:
            rows, src = await client.get_candles_ex(sym, tf, lookback)
        except UpstreamUnavailable as e:
            notes.append({"symbol": sym, "reason": f"no candles: {e}"})
            continue
        series[sym] = [(int(r["t"]), float(r["c"])) for r in rows
                       if r.get("c") is not None and float(r["c"]) > 0]
        srcs[sym] = src
        candidates.append(Candidate(sym, entry, stop, float(snap.get("composite") or 0.0)))

    if not candidates:
        return {"ok": False, "allocations": [], "dropped": notes,
                "error": "no candidate had a price, a stop and candles",
                **provenance()}

    _stamps, closes = aligned_closes(series)
    returns: dict[str, list[float]] = {}
    for sym, cl in closes.items():
        try:
            returns[sym] = log_returns(cl)
        except ValueError:
            notes.append({"symbol": sym, "reason": "non-positive close in the series"})

    lim = Limits(
        equity=br.equity, cash=br.cash,
        open_heat_pct=float(getattr(risk, "open_heat", 0.0) or 0.0),
        open_positions=len([p for p in br.positions.values() if p.state != "CLOSED"]),
        concurrent_limit=br.concurrent_limit,
        max_heat_pct=risk.cfg.max_heat_pct,
        min_cash_pct=risk.cfg.min_cash_pct,
        max_single_asset_pct=risk.cfg.max_single_asset_pct,
        per_trade_risk_pct=risk.cfg.risk_pct,
        fee_pct=PAPER_FEE_PCT,
        halted=bool(br.halted or risk.halted),
        shrinkage=(float(body["shrinkage"]) if body.get("shrinkage") is not None
                   else 0.30),
    )
    res = allocate_portfolio(candidates, returns, lim)
    res["dropped"] = notes + list(res.get("dropped") or [])

    # USD notional → the pair's own quote currency. A quantity printed in USD
    # on an IRT ticket is off by the exchange rate, which is how an allocator
    # becomes a way to lose money politely.
    for row in res.get("allocations", []):
        rate = await _fx(row["symbol"])
        if rate is None:
            row["quote"] = None
            row["notional_quote"] = None
            row["qty"] = None
            row["error"] = "no-fx-rate"
            continue
        fx = rate.toman_per_usdt
        row["fx"] = round(fx, 4)
        row["quote"] = "IRT" if row["symbol"].endswith("IRT") else "USDT"
        row["notional_quote"] = round(row["notional_usd"] * fx, 2)
        row["qty"] = round(row["notional_usd"] * fx / row["entry"], 10)
        row["stop_pct"] = round(row["stop_pct"] * 100, 3)
    res["totals"]["invested_quote"] = round(
        sum(r["notional_quote"] for r in res.get("allocations", [])), 2)

    # `provenance()["source"]` is the exchange status (live/unavailable); the
    # candle origins travel under their own key so the two cannot overwrite
    # each other — the first version of this line did, and the page crashed on
    # a string where it expected a list.
    res.update({"tf": tf, "lookback": lookback,
                "candles_from": sorted(set(srcs.values())) or ["none"],
                **provenance()})
    audit("portfolio.allocate", actor="ui",
          detail={"tf": tf, "n": len(res.get("allocations", [])),
                  "invested_usd": res["totals"]["invested_usd"]}, request=request)
    return res


@app.post("/api/benchmark/relative")
async def benchmark_relative(payload: dict, request: Request):
    """Return, beta, alpha and capture for each symbol against a benchmark.

    Absolute return flatters a desk in a bull market and punishes it in a bear
    one; neither answers "did the signal pick better than the market". This is
    descriptive statistics over the candles both series share — not a forecast,
    and not a portfolio number (weights live in the allocator).

    Read-only. A symbol without enough overlap is dropped with the reason
    instead of being given a beta from twelve bars.
    """
    body = payload or {}
    tf = _tf_or(body.get("tf"), "1h")
    bench = str(body.get("bench") or "BTCIRT").upper()
    try:
        lookback = max(60, min(int(body.get("lookback") or 240), 400))
    except (TypeError, ValueError):
        return {"ok": False, "error": "lookback must be an integer"}

    board = _cached.get("signals_by_tf", {}).get(tf) or _cached.get("signals") or {}
    explicit = body.get("symbols")
    if explicit is not None and not isinstance(explicit, list):
        return {"ok": False, "error": "symbols must be a list"}
    # Two exclusions, both about not comparing things that are the same thing:
    #   * USDT/IRT is a currency pair, not an asset — "alpha vs BTC" for a
    #     stablecoin is noise dressed as performance;
    #   * BTC/USDT against a BTC/IRT benchmark measures the toman/BTC basis, not
    #     BTC's performance.
    # Either stays available when the caller names it explicitly.
    def _base(sym: str) -> str:
        for q in ("IRT", "USDT"):
            if sym.endswith(q):
                return sym[: -len(q)]
        return sym

    STABLE = {"USDTIRT"}

    def _default_ok(sym: str) -> bool:
        return sym != bench and sym not in STABLE and _base(sym) != _base(bench)

    symbols = ([str(x).upper() for x in explicit][:12] if explicit
               else [sym for sym in (board or {}) if _default_ok(sym)][:12])
    symbols = [sym for sym in symbols if sym != bench]
    if not symbols:
        return {"ok": False, "error": "no symbol to measure",
                "message": "run a scan first; an empty board has nothing to compare"}

    series: dict[str, list[tuple[int, float]]] = {}
    dropped: list[dict] = []
    sources: set[str] = set()
    for sym in [bench, *symbols]:
        try:
            rows, src = await client.get_candles_ex(sym, tf, lookback)
        except UpstreamUnavailable as e:
            dropped.append({"symbol": sym, "reason": f"no candles: {e}"})
            continue
        clean = [(int(r["t"]), float(r["c"])) for r in rows
                 if r.get("c") is not None and float(r["c"]) > 0]
        if len(clean) < BENCH_MIN_OBS + 1:
            dropped.append({"symbol": sym, "reason": f"fewer than {BENCH_MIN_OBS} usable bars"})
            continue
        series[sym] = clean
        sources.add(src)

    if bench not in series:
        return {"ok": False, "bench": bench,
                "error": f"no usable candles for the benchmark {bench}",
                "dropped": dropped, **provenance()}

    _stamps, closes = aligned_closes(series)
    try:
        bench_returns = log_returns(closes[bench])
    except (KeyError, ValueError):
        return {"ok": False, "bench": bench, "error": "the benchmark series is unusable",
                "dropped": dropped, **provenance()}

    ppy = PERIODS_PER_YEAR[tf]
    out_rows: list[dict] = []
    for sym in symbols:
        if sym not in closes:
            continue
        try:
            asset_returns = log_returns(closes[sym])
        except ValueError:
            dropped.append({"symbol": sym, "reason": "non-positive close in the series"})
            continue
        stat = relative_to(asset_returns, bench_returns, ppy)
        if stat is None:
            dropped.append({"symbol": sym, "reason": f"fewer than {BENCH_MIN_OBS} shared bars"})
            continue
        snap = (board or {}).get(sym) or {}
        out_rows.append({
            "symbol": sym,
            **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in stat.items()},
            "direction": snap.get("direction"),
            "composite": snap.get("composite"),
        })

    if not out_rows:
        return {"ok": False, "bench": bench, "rows": [], "dropped": dropped,
                "error": "no symbol had enough overlapping history",
                "min_obs": BENCH_MIN_OBS, **provenance()}

    out_rows.sort(key=lambda r: -(r.get("alpha_annual_pct") or 0))
    return {
        "ok": True,
        "bench": bench,
        "tf": tf,
        "lookback": lookback,
        "periods_per_year": ppy,
        "min_obs": BENCH_MIN_OBS,
        "rows": out_rows,
        "summary": summarise_relative(out_rows),
        "dropped": dropped,
        "candles_from": sorted(sources),
        "note": "descriptive statistics over past bars, not a forecast",
        **provenance(),
    }


@app.get("/api/market/overview")
async def market_overview_route(tf: str = Query("15m", pattern="^(15m|1h|4h|D)$")):
    """Breadth, dispersion, concentration and the signal mix in one shot.

    Computed from the tickers the scan already holds. Volumes are converted to
    USD first: a toman volume and a dollar volume are not addable, and the IRT
    numbers are ~100,000× larger, so an unconverted sum is wrong by exactly the
    exchange rate.
    """
    rows = list(_cached.get("universe") or [])
    if not rows:
        return {**upstream_error("market-overview", "the universe has not loaded"),
                "ok": False, "universe_size": 0, "reason": "no exchange tickers yet"}

    usdt_irt = next((float(r["last"]) for r in rows
                     if r.get("symbol") == "USDTIRT" and r.get("last")), 0.0)
    if usdt_irt <= 0:
        return {**upstream_error("market-overview", "no USDTIRT rate to convert turnover"),
                "ok": False, "universe_size": len(rows),
                "reason": "turnover cannot be mixed without a toman rate"}

    enriched = []
    for r in rows:
        sym = str(r.get("symbol") or "")
        fx = 1.0 if sym.endswith("USDT") else usdt_irt
        enriched.append({**r, "fx_usd": fx})

    signals = _cached.get("signals_by_tf", {}).get(tf) or _cached.get("signals") or {}
    out = market_overview(enriched, signals=signals,
                          premium_pct=_cached.get("premium_pct"))
    out.update({"tf": tf, "as_of": datetime.now(UTC).isoformat(), **provenance()})
    return out


def _live_equity() -> tuple[float, float]:
    """(current, starting) equity of the account the gates actually use.

    Sizing a plan from `risk.cfg.equity` — a constant — while every gate runs
    against `broker.equity` meant the two numbers drifted apart with every
    trade: after seven of them the risk page said 100,000 and the account said
    99,976.71, and a plan was sized for money that was no longer there.
    """
    try:
        # The same accessor the order routes use, so a broker injected for a test
        # (or created on first boot) is the one whose money is reported.
        br = get_broker(risk.cfg.equity, client, risk)
        eq = float(getattr(br, "equity", 0) or 0) if br is not None else 0.0
        if eq > 0:
            return eq, float(getattr(br, "start_equity", eq) or eq)
    except Exception:  # no broker yet (first boot, tests)
        pass
    return float(risk.cfg.equity), float(risk.cfg.equity)


@app.get("/api/risk/state")
async def risk_state():
    eq, start = _live_equity()
    rate = await _fx("BTCIRT")
    return {
        # The same number the preflight compares against, not the constant it
        # started from.
        "equity": eq,
        "start_equity": start,
        "equity_source": "paper-broker" if eq != risk.cfg.equity else "config",
        "equity_quote": "USD",
        # The conversion, done once on the server: the page used to read the
        # premium out of a different object and fall back to 1, which made its
        # own risk percentage wrong by ~100,000×.
        "equity_irt": round(usd(eq).to_irt(rate).amount, 2) if rate else None,
        "fx": rate.to_dict() if rate else None,
        "openHeat": risk.open_heat,
        "dailyPnl": risk.daily_pnl,
        "halted": risk.halted,
        "caps": {
            "riskPct": risk.cfg.risk_pct,
            "maxConcurrent": risk.cfg.max_concurrent,
            "maxSingleAssetPct": risk.cfg.max_single_asset_pct,
            "maxHeatPct": risk.cfg.max_heat_pct,
            "minCashPct": risk.cfg.min_cash_pct,
            # Policy, not an account setting: the ticket's R:R warning reads it
            # from here instead of carrying a copy of 1.5.
            "minRR": MIN_RR,
            # The ladder the live plan actually applies. Published for the same
            # reason as `minRR`: a page (or an operator) that wants to compare it
            # with the backtest's best must not keep a private copy.
            "rrLevels": list(risk.cfg.rr_levels),
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
    size = (body.get("size_quote") and float(body["size_quote"])) or None
    if not symbol or (not qty and not size):
        return {"error": "symbol and either qty or size_quote required"}
    if not qty and size:
        # Quote-denominated sizing was documented but silently ignored: callers
        # sending only `size_quote` got an error about `qty`.
        px = (body.get("price") and float(body["price"])) or None
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
        price = (body.get("price") and float(body["price"])) or None
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
    value_quote = pos.filled_qty * last
    # Same rate the account is marked with, so the position row and the equity
    # KPI cannot disagree about what a position is worth — including about
    # *when* they read it. This used to wrap the cached number in
    # `Rate(as_of=time.time())`, which relabelled a 30-minute-old price as a
    # current one; the dollar figure now disappears with the rate instead.
    _broker = get_broker(EQUITY, client, risk)
    rate = _broker.usdt_irt
    if rate > 0 and (time.time() - float(_broker.usdt_irt_at or 0.0)) > paper_trading.RATE_TTL_S:
        rate = 0.0
    if pos.symbol.endswith("IRT"):
        if rate > 0:
            # `value_quote` is already Toman: the positions were priced at the
            # API boundary. Wrapping it in `from_api` would read Toman as Rial
            # and divide the result by ten again — the same mistake as the
            # original bug, arriving through the helper meant to prevent it.
            value_usd = toman(value_quote).to_usd(
                Rate(toman_per_usdt=rate, as_of=_broker.usdt_irt_at,
                     source="paper-broker",
                     max_age_s=paper_trading.RATE_TTL_S)).amount
            fx_used = rate
        else:
            # No rate: report the quote-currency value and say the USD figure is
            # unknown, rather than dividing by a missing number.
            value_usd = None
            fx_used = None
    else:
        value_usd = value_quote
        fx_used = 1.0
    return {**pos.__dict__, "last_price": last, "pnl_pct": pnl_pct,
            "value_usd": None if value_usd is None else round(value_usd, 2),
            "value_quote": round(value_quote, 2), "fx": fx_used}


def _order_rows(broker, limit: int = 100) -> list[dict]:
    """One row per order attempt, built from the broker's own event stream.

    The Orders page used to render a hard-coded empty array and describe itself
    as "the live order book read from the Nobitex server" — a source that never
    existed. The events below are what the broker actually recorded: a `submit`
    opens a row, a `fill` prices it, `tp1`/`close` finish it, and a `reject`
    is a row of its own (an order that was refused, with the gate named).

    Nothing here is derived from a price we did not see. Slippage stays `None`
    unless a mid price was measured at submit.
    """
    pos_by_coid = {p.client_order_id: p for p in broker.positions.values()
                   if getattr(p, "client_order_id", "")}
    rows: dict[str, dict] = {}
    #: Events written before `clientOrderId` existed have nothing to pair on, so
    #: a `fill` looks for the oldest `submit` still waiting on the same
    #: symbol+side. Without this, every legacy order showed up twice: once as
    #: "submitted" and once as a fill with no head.
    dangling: dict[tuple[str, str], list[str]] = {}
    for ev in broker.orders:
        coid = ev.extra.get("clientOrderId") or ""
        if ev.kind == "reject":
            key = f"rej-{ev.ts:.6f}-{ev.symbol}"
        elif coid:
            key = coid
        elif ev.kind == "submit":
            key = f"ev-{ev.ts:.6f}-{ev.symbol}"
            dangling.setdefault((ev.symbol, ev.side), []).append(key)
        else:
            waiting = dangling.get((ev.symbol, ev.side))
            key = waiting.pop(0) if waiting else f"ev-{ev.ts:.6f}-{ev.symbol}"
        row = rows.get(key)
        if row is None:
            row = rows[key] = {
                "clientOrderId": coid or None,
                "symbol": ev.symbol, "side": ev.side,
                "type": ev.extra.get("execution") or "limit",
                "price": float(ev.price or 0.0),
                "amount": float(ev.qty or 0.0),
                "filled": 0.0, "requested": 0.0, "fee": 0.0, "pnl_usd": 0.0,
                "slip_bps": None,
                "state": "SUBMITTED", "reason": None, "source": ev.extra.get("source"),
                "opened_at": ev.ts, "closed_at": None, "close_reason": None,
                "events": [],
            }
        row["events"].append(ev.kind)
        if ev.kind == "submit":
            row["price"] = float(ev.price or 0.0)
            row["amount"] = float(ev.qty or 0.0)
            # `qty` on a submit is what the book could take, not what was asked
            # for; the difference is the part of the order that never filled.
            row["requested"] = float(ev.extra.get("requested") or ev.qty or 0.0)
            row["state"] = "SUBMITTED"
            row["mid"] = float(ev.extra.get("mid") or 0.0)
        elif ev.kind == "fill":
            row["filled"] = float(ev.qty or 0.0)
            row["fee"] = float(ev.extra.get("fee") or 0.0)
            row["state"] = "FILLED"
            # Slippage is measured only when a mid was recorded at submit;
            # otherwise it stays unknown, because a number nobody measured is a
            # lie with a decimal point.
            mid = float(row.get("mid") or 0.0)
            if mid > 0 and ev.price:
                row["slip_bps"] = round((float(ev.price) - mid) / mid * 10_000, 2)
        elif ev.kind == "tp1":
            row["filled"] = max(0.0, row["filled"] - float(ev.qty or 0.0))
            row["pnl_usd"] += float(ev.extra.get("pnl") or 0.0)
            row["state"] = "PARTIAL"
        elif ev.kind == "close":
            row["filled"] = 0.0
            row["pnl_usd"] += float(ev.extra.get("pnl") or 0.0)
            row["state"] = "CLOSED"
            row["closed_at"] = ev.ts
            row["close_reason"] = ev.extra.get("reason")
        elif ev.kind == "reject":
            row["state"] = "REJECTED"
            row["reason"] = ev.extra.get("reason")

    # The position is the authority on what is still open: an event stream that
    # says FILLED after the position was closed by a stop would be wrong.
    for row in rows.values():
        pos = pos_by_coid.get(row["clientOrderId"] or "")
        if pos is None and not row["clientOrderId"] and row["state"] in ("FILLED", "PARTIAL"):
            # Legacy row: the newest position on that market is its owner.
            pos = next((p for p in broker.positions.values()
                        if p.symbol == row["symbol"] and getattr(p, "client_order_id", "") == ""),
                       None)
        if pos is not None:
            row["state"] = pos.state
            row["filled"] = float(pos.filled_qty)
            row["price"] = float(pos.entry_price)
            row["pnl_usd"] = round(float(pos.realized_pnl), 4)
            row["closed_at"] = pos.closed_at
            row["close_reason"] = pos.close_reason
            row["execution"] = pos.execution
        row["pnl_usd"] = round(row["pnl_usd"], 4)
        if pos is not None and getattr(pos, "requested_qty", None):
            row["requested"] = float(pos.requested_qty)
    out = sorted(rows.values(), key=lambda r: r["opened_at"], reverse=True)
    for r in out[:limit]:
        r["opened_at_iso"] = datetime.fromtimestamp(r["opened_at"], UTC).isoformat()
        r["closed_at_iso"] = (datetime.fromtimestamp(r["closed_at"], UTC).isoformat()
                              if r["closed_at"] else None)
        denom = r.get("requested") or r["amount"]
        r["filled_pct"] = round(min(100.0, r["filled"] / denom * 100), 1) if denom else 0.0
    return out[:limit]


@app.get("/api/paper/orders")
async def paper_orders(limit: int = Query(100, ge=1, le=500)):
    """The order log the Orders page renders: what was asked, what filled."""
    broker = get_broker(EQUITY, client, risk)
    rows = _order_rows(broker, limit)
    counts = {
        "total": len(rows),
        "open": sum(1 for r in rows if r["state"] in ("OPEN", "PARTIAL", "FILLED", "SUBMITTED")),
        "closed": sum(1 for r in rows if r["state"] == "CLOSED"),
        "rejected": sum(1 for r in rows if r["state"] == "REJECTED"),
    }
    return {"ok": True, "rows": rows, "counts": counts,
            "source": "paper-broker", "as_of": datetime.now(UTC).isoformat()}


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
        "market_stats": {
            sym: {
                "latest": st.latest,
                "day_change": st.day_change,
                "closed": st.closed,
                "age_s": round(max(0.0, time.time() - st.updated), 1) if st.updated else None,
                "fresh": st.fresh,
            }
            for sym, st in STREAM_EXECUTOR.markets.items()
        },
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
        # Non-null only when `load()` had to restore a backup or quarantine a
        # damaged state file. Silence here would mean the account quietly
        # restarted from the configured equity.
        "state_recovery": broker.state_recovery,
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


# ---------------- Execution mode: auto / semi / manual ----------------
#: The mode is server state (`exec_mode.EXEC_MODE`), persisted in the journal's
#: KV table. The page reads it back and the executor reads the same object; a
#: chip that only changed a browser variable was a control with no effect, and
#: a hard-coded per-symbol table made the promised per-symbol choice impossible.
_EXEC_LOADED = [False]


def _exec_mode_load() -> None:
    if _EXEC_LOADED[0]:
        return
    _EXEC_LOADED[0] = True
    if os.getenv("NBX_JOURNAL", "1") == "0":
        return
    try:
        conn = signal_journal.connect()
        try:
            EXEC_MODE.load(signal_journal.get_meta(conn, "exec_mode", None))
        finally:
            conn.close()
    except Exception as e:  # defaults are a safe fallback
        log.warning("exec mode load failed: %s", e)


def _exec_mode_save() -> None:
    if os.getenv("NBX_JOURNAL", "1") == "0":
        return
    try:
        conn = signal_journal.connect()
        try:
            signal_journal.set_meta(conn, "exec_mode", EXEC_MODE.to_json())
        finally:
            conn.close()
    except Exception as e:  # the in-memory value is still right
        log.warning("exec mode save failed: %s", e)


AUTO_INTERVAL_S = max(5.0, float(os.getenv("NBX_AUTO_INTERVAL_S", "60")))
#: After any attempt (placed or refused) a symbol is left alone for this long.
#: Without it a rejected plan is retried every tick, filling the audit with the
#: same sentence and the log with the same warning.
AUTO_COOLDOWN_S = max(0.0, float(os.getenv("NBX_AUTO_COOLDOWN_S", "1800")))
_AUTO_LAST: dict[str, float] = {}
# Counters plus the last rejection: the values are mixed types, so the dict is
# annotated rather than left to be inferred as `dict[str, int | None]`.
_AUTO_STATE: dict[str, Any] = {
    "last_tick_at": None, "placed": 0, "skipped": 0, "rejected": 0,
    "last_error": None, "last_symbol": None, "last_action": None,
}


async def auto_tick(tf: str = "15m") -> dict:
    """One pass of `auto`: size, gate and place one paper order per symbol.

    Everything here is the path an approved queue row takes — `risk.evaluate`
    for the sizing, `broker.place_limit` for the ten gates — because an
    "automatic" mode with its own private rules would be a second broker with
    weaker checks. `source="AUTO"` is written into the order events, so the
    orders page says who pulled the trigger.
    """
    _exec_mode_load()
    broker = get_broker(EQUITY, client, risk)
    now = time.time()
    if EXEC_MODE.is_auto_default():
        pool = _cached.get("signals_by_tf", {}).get(tf) or _cached.get("signals") or {}
        targets = list(pool.keys())
    else:
        targets = EXEC_MODE.auto_symbols()
    out: dict[str, Any] = {"considered": 0, "placed": 0, "skipped": 0, "rejected": []}
    for sym in sorted(set(targets)):
        snap = _signal_lookup(sym, tf)
        if not snap:
            continue
        out["considered"] += 1
        if (snap.get("direction") or "").upper() != "LONG":
            out["skipped"] += 1
            continue
        if any(p.symbol == sym and getattr(p, "state", "OPEN") != "CLOSED"
               for p in broker.positions.values()):
            out["skipped"] += 1                       # one position per market
            continue
        if now - _AUTO_LAST.get(sym, 0.0) < AUTO_COOLDOWN_S:
            out["skipped"] += 1
            continue
        entry = float(snap.get("lastClose") or 0.0)
        atr = float((snap.get("risk") or {}).get("atr") or 0.0)
        eq, _start = _live_equity()
        rate = await _fx(sym)
        if rate is None:
            # Same refusal as the plan route: an auto pass without a rate would
            # size the order against a number in the wrong currency.
            out["skipped"] += 1
            out["rejected"].append({"symbol": sym, "reason": "no-fx-rate"})
            audit("paper.order.auto", actor="auto", result="blocked",
                  detail={"symbol": sym, "reason": "no-fx-rate"})
            continue
        equity_quote = usd(eq).to_irt(rate).amount
        plan = risk.evaluate(sym, "buy", entry, atr, tf, equity_quote=equity_quote,
                             preview=True)
        _AUTO_LAST[sym] = now
        if plan.blocked or not plan.size_quote or not plan.entry:
            reason = ",".join(plan.warnings or []) or "no-size"
            out["skipped"] += 1
            out["rejected"].append({"symbol": sym, "reason": reason})
            audit("paper.order.auto", actor="auto", result="blocked",
                  detail={"symbol": sym, "reason": reason})
            continue
        qty = plan.size_quote / plan.entry
        pos = await broker.place_limit(sym, "buy", qty, plan.entry, plan.stop,
                                       plan.tp1, plan.tp2, source="AUTO")
        if pos is None:
            out["rejected"].append({"symbol": sym, "reason": broker.last_error or "rejected"})
            audit("paper.order.auto", actor="auto", result="rejected",
                  detail={"symbol": sym, "reason": broker.last_error or "rejected"})
        else:
            out["placed"] += 1
            audit("paper.order.auto", actor="auto", result="ok",
                  detail={"symbol": sym, "qty": round(qty, 8), "entry": plan.entry,
                          "stop": plan.stop, "size_quote": plan.size_quote})
    _AUTO_STATE.update({
        "last_tick_at": now,
        "placed": _AUTO_STATE["placed"] + out["placed"],
        "skipped": _AUTO_STATE["skipped"] + out["skipped"],
        "rejected": _AUTO_STATE["rejected"] + len(out["rejected"]),
        "last_error": (out["rejected"][-1]["reason"] if out["rejected"] else None),
        "last_symbol": (out["rejected"][-1]["symbol"] if out["rejected"] else None),
        "last_action": ("placed" if out["placed"] else ("rejected" if out["rejected"] else "idle")),
    })
    return out


async def _auto_loop() -> None:
    """Watch the mode; run a pass when `auto` is on and the interval elapsed.

    The loop is always alive so that switching a symbol to `auto` takes effect
    without a restart, but it does nothing at all while every symbol is
    semi/manual.
    """
    _exec_mode_load()
    while True:
        try:
            active = EXEC_MODE.is_auto_default() or bool(EXEC_MODE.auto_symbols())
            last = _AUTO_STATE.get("last_tick_at") or 0.0
            if active and time.time() - last >= AUTO_INTERVAL_S:
                res = await auto_tick()
                if res["placed"] or res["rejected"]:
                    log.info("auto: placed %d, rejected %d", res["placed"], len(res["rejected"]))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # one bad pass must not kill the loop
            log.warning("auto tick failed: %s", e)
            errorlog.capture(source="task.auto-exec", exc=e,
                             message="automatic execution tick failed")
        await asyncio.sleep(min(15.0, AUTO_INTERVAL_S))


def _exec_mode_payload() -> dict:
    """The mode *and* what the executor has done with it, in one shape.

    Both the GET and the POST answer this payload: after a change the page must
    be able to show the executor's counters without a second request — and the
    counters must be the ones the loop is writing, not a placeholder.
    """
    return {
        **EXEC_MODE.snapshot(),
        "auto": {
            "enabled": EXEC_MODE.is_auto_default() or bool(EXEC_MODE.auto_symbols()),
            "interval_s": AUTO_INTERVAL_S,
            "cooldown_s": AUTO_COOLDOWN_S,
            **_AUTO_STATE,
        },
    }


@app.get("/api/exec/mode")
async def exec_mode_get():
    """What the executor will do, read from the object it acts on."""
    _exec_mode_load()
    return {"ok": True, **_exec_mode_payload(), **provenance()}


@app.post("/api/exec/mode", dependencies=[Depends(require_client)])
async def exec_mode_set(payload: dict, request: Request):
    """Change the mode. This is a control with consequences, so it is audited."""
    body = payload or {}
    _exec_mode_load()
    try:
        if body.get("symbol"):
            sym = str(body["symbol"]).upper()
            mode = EXEC_MODE.set_symbol(sym, body.get("mode"))
            action, detail = "exec.mode.symbol", {"symbol": sym, "mode": mode or "inherit"}
        elif body.get("mode"):
            mode = EXEC_MODE.set_default(str(body["mode"]))
            action, detail = "exec.mode.default", {"default": mode}
        else:
            return {"ok": False, "error": "mode or symbol required"}
    except InvalidMode as e:
        return {"ok": False, "error": str(e)}
    _exec_mode_save()
    audit(action, detail=detail, request=request)
    return {"ok": True, **_exec_mode_payload(), **provenance()}


# ---------------- Reference feed (ADR-0014, 1.0.20) ----------------
# A second venue, for developing outside Iran and for sanity-checking the
# numbers. Read-only, clearly labelled, and never a stand-in for the exchange:
# `/api/backtest/run` turns it into a REFERENCE_DATA verdict that cannot pass.
@app.get("/api/reference/status")
async def reference_status():
    """Is the fallback feed alive, and what does it say BTCUSDT is worth?"""
    try:
        return {"ok": True, **(await reference_feed.status())}
    except Exception as e:      # a status probe must never 500
        log.warning("reference status failed: %s", e)
        return {"ok": False, "reachable": False, "error": str(e)}


@app.get("/api/reference/candles/{symbol}")
async def reference_candles(symbol: str, tf: str = "4h", limit: int = 500,
                            toman_per_usdt: float | None = None):
    """Candles from the reference venue, shaped exactly like the exchange's."""
    try:
        rows, meta = await reference_feed.fetch_candles(
            symbol.upper(), tf, limit=max(10, min(int(limit), 2000)),
            fx=toman_per_usdt,
        )
    except reference_feed.ReferenceError as e:
        return {"ok": False, "error": e.code, "message": e.message, "meta": e.meta}
    return {"ok": True, "symbol": symbol.upper(), "tf": tf, "rows": rows,
            "count": len(rows), "reference": meta, **provenance()}


@app.get("/api/reference/cross-check")
async def reference_cross_check(symbol: str = "BTCUSDT"):
    """Compare the venue's last price with the reference venue's.

    Inside Iran this is the useful direction: a large, unexplained gap means one
    of the two is stale, mis-scaled, or pointed at the wrong pair — a failing no
    other panel would show, because each feed looks fine on its own.
    """
    out: dict = {"ok": True, "symbol": symbol.upper(), "venue": None, "reference": None}
    try:
        rows, _src = await client.get_candles_ex(symbol.upper(), "15m", 2)
        out["venue"] = rows[-1]["c"] if rows else None
        out["venue_source"] = _src
    except UpstreamUnavailable as e:
        out["venue_error"] = str(e)
    try:
        price, meta = await reference_feed.fetch_price(symbol.upper())
        out["reference"] = price
        out["reference_meta"] = meta
    except reference_feed.ReferenceError as e:
        out["reference_error"] = e.code
        out["reference_message"] = e.message
    if out["venue"] and out["reference"]:
        gap = (out["venue"] / out["reference"] - 1.0) * 100
        out["gap_pct"] = round(gap, 3)
        out["verdict"] = (
            "aligned" if abs(gap) <= 2.0
            else "wide" if abs(gap) <= 10.0
            else "divergent"
        )
        if out["verdict"] != "aligned":
            out["note"] = ("a gap this size is a stale or mis-scaled feed, not an "
                           "arbitrage: check both before trusting either")
    return out


def freshness_block(rows: list[dict], tf: str) -> dict:
    """The `freshness` block for a response, degrading instead of raising.

    A card that loses its numbers because the *age check* failed would be the
    worst of both worlds, so a failure to measure becomes a stale block with the
    named code — never an exception bubbling out of an otherwise good answer.
    """
    try:
        return freshness.assess(rows, tf)
    except AdaptiveRefusal as e:                   # bad timeframes and timestamps
        return {"stale": True, "reason": e.code, "age_bars": None}
    except Exception as e:                          # pragma: no cover - last resort
        return {"stale": True, "reason": type(e).__name__, "age_bars": None}


def volatility_block(rows: list[dict]) -> dict:
    """ATR percentile + its path, or a named refusal — never a broken chart.

    A strip is a *drawing of a measurement*: if the measurement is refused (too few
    bars, a flat window) the answer still has to be an object the page can print,
    with the code that says why. `applied` is false and always will be: nothing here
    feeds the signal engine, it describes what already happened.
    """
    base = {"applied": False, "note": "descriptive statistics over past bars, not a forecast"}
    try:
        df = candles_to_df(rows)
        current = adaptive.atr_percentile(df)
        path = adaptive.atr_percentile_path(df)
        return {
            **base,
            **current,
            **path,
            "regime": adaptive.regime_for(current["percentile"]),
            "cuts": {"calm_max": adaptive.CALM_MAX, "volatile_min": adaptive.VOLATILE_MIN},
        }
    except AdaptiveRefusal as e:
        return {**base, "ok": False, "error": e.code, "message": e.message, **e.meta}
    except Exception as e:                          # pragma: no cover - last resort
        return {**base, "ok": False, "error": type(e).__name__, "message": str(e)}


def freshness_block_at(bar_at: float | None, tf: str) -> dict:
    """The same block for a caller that kept only the newest bar stamp.

    Exists so the market scan can report the age of its candles without being
    asked to hold 260 rows per symbol for a number it already computed; the
    arithmetic is `freshness.assess_bar`, i.e. the same rule, not a copy of it.
    """
    try:
        return freshness.assess_bar(bar_at, tf)
    except AdaptiveRefusal as e:
        return {"stale": True, "reason": e.code, "age_bars": None}
    except Exception as e:                          # pragma: no cover - last resort
        return {"stale": True, "reason": type(e).__name__, "age_bars": None}


@app.get("/api/adaptive/thresholds/{symbol}")
async def adaptive_thresholds(
    symbol: str,
    tf: str = Query("1h"),
    source: str = Query("nobitex", pattern="^(nobitex|reference)$"),
    limit: int = Query(400, ge=120, le=2000),
    toman_per_usdt: float | None = None,
):
    """What the fixed score thresholds amount to on *this* timeframe.

    `LONG_SCORE_MIN = 68` is one number for every symbol and every timeframe, so
    "the strategy is quiet" and "68 is unreachable at 4h" look the same on the
    dashboard. This measures the difference and recommends a factor. It always
    reports `applied: false`: the number is evidence, not a behaviour change.
    """
    from adaptive import AdaptiveError
    from adaptive import report as adaptive_report

    sym = (symbol or "").upper()
    out: dict = {"ok": True, "symbol": sym, "tf": tf, "candles_source": source}
    try:
        if source == "reference":
            rows, ref_meta = await reference_feed.fetch_candles(
                sym, tf, limit=limit, fx=toman_per_usdt)
            out["reference"] = ref_meta
        else:
            rows, _src = await client.get_candles_ex(sym, tf, limit)
    except reference_feed.ReferenceError as e:
        return {**out, "ok": False, "error": e.code, "message": e.message, "meta": e.meta}
    except UpstreamUnavailable as e:
        return {**out, "ok": False, **upstream_error("candles", str(e))}
    try:
        out.update(await asyncio.to_thread(adaptive_report, rows, tf))
    except AdaptiveError as e:
        return {**out, "ok": False, "error": e.code, "message": e.message, "meta": e.meta}
    out["bars"] = len(rows)
    # A measurement describes *now* only if its newest candle is close to the
    # clock. Say so, in bars, instead of letting a three-day-old number wear a
    # present-tense sentence.
    try:
        out["freshness"] = await asyncio.to_thread(freshness.assess, rows, tf)
    except AdaptiveError as e:
        out["freshness"] = {"stale": True, "reason": e.code, "age_bars": None}
    return out


@app.get("/api/adaptive/evidence/{symbol}")
async def adaptive_evidence(
    symbol: str,
    tf: str = Query("1h"),
    source: str = Query("nobitex", pattern="^(nobitex|reference)$"),
    limit: int = Query(900, ge=120, le=2000),
    factor: float | None = Query(None, ge=0.5, le=2.0),
    toman_per_usdt: float | None = None,
):
    """Paired walk-forward: the fixed bar against the measured one.

    `adaptive.py` proposes a factor. This asks the next question in code — would
    the higher bar have been better out-of-sample? — on identical windows, with
    identical costs and one variable. It always reports `applied: false` and
    `promotable: false`: a verdict is evidence for ADR-0021, not a retune. A
    reference feed caps the verdict at `inconclusive` (ADR-0009 / ADR-0014).
    """
    from adaptive import AdaptiveError
    from adaptive_evidence import compare

    sym = (symbol or "").upper()
    out: dict = {"ok": True, "symbol": sym, "tf": tf, "candles_source": source}
    try:
        if source == "reference":
            rows, ref_meta = await reference_feed.fetch_candles(
                sym, tf, limit=limit, fx=toman_per_usdt)
            out["reference"] = ref_meta
        else:
            rows, _src = await client.get_candles_ex(sym, tf, limit)
    except reference_feed.ReferenceError as e:
        return {**out, "ok": False, "error": e.code, "message": e.message, "meta": e.meta}
    except UpstreamUnavailable as e:
        return {**out, "ok": False, **upstream_error("candles", str(e))}
    try:
        out.update(await asyncio.to_thread(
            compare, rows, tf, sym, factor=factor, source=source))
    except AdaptiveError as e:
        return {**out, "ok": False, "error": e.code, "message": e.message, "meta": e.meta}
    try:
        out["freshness"] = await asyncio.to_thread(freshness.assess, rows, tf)
    except AdaptiveError as e:
        out["freshness"] = {"stale": True, "reason": e.code, "age_bars": None}
    return out


# ---------------- Walk-Forward Backtest (ADR-0009) ----------------
@app.post("/api/backtest/run", dependencies=[Depends(require_client)])
async def backtest_run(payload: dict):
    """Walk-forward backtest: optimise on train, report out-of-sample only."""
    b = payload or {}
    symbol = (b.get("symbol") or "BTCIRT").upper()
    tf = (b.get("tf") or "1h")
    mode = (b.get("mode") or "wf").lower()
    bars = max(200, min(int(b.get("bars", 600)), 2000))
    candles_source = (b.get("candles_source") or "nobitex").lower()
    if candles_source not in ("nobitex", "reference"):
        return {"ok": False, "error": f"unknown candles_source {candles_source!r}",
                "allowed": ["nobitex", "reference"]}
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
    }
    # Only override the cost model when the caller actually asked for one:
    # sending 0.0 by default would make every route-level backtest frictionless
    # while the simulator claims otherwise, and the gate would certify it.
    if b.get("slippage_bps") is not None:
        cfg["slippage_bps"] = float(b["slippage_bps"])
    grid = b.get("grid") if isinstance(b.get("grid"), list) else None
    ref_meta: dict = {}
    try:
        if candles_source == "reference":
            # A second venue's candles, clearly labelled. `source` travels into
            # the report and the gate turns it into REFERENCE_DATA, which can
            # never be PASS.
            try:
                rows, ref_meta = await reference_feed.fetch_candles(
                    symbol, tf, limit=bars,
                    fx=b.get("toman_per_usdt"),
                )
            except reference_feed.ReferenceError as e:
                return {"ok": False, "error": e.code, "message": e.message,
                        "meta": e.meta, "candles_source": candles_source}
            src = "reference"
        else:
            rows, src = await client.get_candles_ex(symbol, tf, bars)
        res = await asyncio.to_thread(
            run_and_save, symbol, tf, rows, cfg, mode, grid, bool(b.get("auto_window", True)), src
        )
        return {
            "ok": True,
            "report": res,
            "bars_requested": bars,
            "bars_received": len(rows),
            # `source` in the body used to be provenance()'s upstream liveness
            # flag, which silently overwrote the candle origin. They answer
            # different questions, so they get different names:
            # `report.source` = where the candles came from,
            # `upstream`      = whether the exchange answered at all.
            "candles_source": candles_source,
            **({"reference": ref_meta} if ref_meta else {}),
            "slippage_bps": res.get("slippage_bps"),
            "upstream": provenance()["source"],
            "as_of": provenance()["as_of"],
            # A walk-forward on candles that stopped three days ago describes a
            # window that ended three days ago; say so in bars, next to the gate.
            "freshness": freshness_block(rows, tf),
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
    return {"ok": True, "report": res, "bars": len(rows), "source": src,
            "freshness": freshness_block(rows, tf)}


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
    custom = (b.get("grid") and dict(b["grid"])) or None
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
        res = await asyncio.to_thread(
            grid_search, symbol, tf, rows, custom,
            float(b["slippage_bps"]) if b.get("slippage_bps") is not None
            else SLIP_BPS,
        )
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


# Coin metadata is a display-only cache, never an alternative price feed.
@app.get("/api/coins")
async def coins_metadata():
    return nobitex_metadata.CACHE.merge(coin_metadata.CACHE.view())


@app.get("/api/coins/{coin_id}/logo")
async def coin_logo(coin_id: str):
    path = coin_metadata.CACHE.logo_path(coin_id)
    if path is None:
        raise HTTPException(404, "لوگوی ذخیره‌شده‌ای برای این ارز وجود ندارد")
    try:
        data = path.read_bytes()
    except OSError:
        raise HTTPException(404, "لوگوی ذخیره‌شده در دسترس نیست") from None
    media_type = coin_metadata.image_type(data)
    if media_type is None:
        raise HTTPException(404, "لوگوی ذخیره‌شده معتبر نیست")
    return Response(data, media_type=media_type,
                    headers={"Cache-Control": "private, max-age=3600",
                             "X-Content-Type-Options": "nosniff"})


# ---------------- Static Frontend Hosting ----------------
REPO_ROOT = Path(__file__).resolve().parent.parent
#: One build attempt per process, and one warning per process — not one per request.
_build_attempted = False
_stale_page_logged = False
if (REPO_ROOT / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(REPO_ROOT / "assets")), name="assets")


def _page_is_stale() -> bool:
    """Is the inlined build older than the markup or the stylesheets?"""
    standalone = REPO_ROOT / "nobitex-mobile.html"
    markup = [REPO_ROOT / "index.html", REPO_ROOT / "nbx-core.js", REPO_ROOT / "nbx-app.js"]
    css = sorted((REPO_ROOT / "assets" / "css").glob("*.css"))
    newest = max((p.stat().st_mtime for p in [*markup, *css] if p.exists()), default=0)
    return (not standalone.exists()) or standalone.stat().st_mtime < newest


def _build_page_once() -> None:
    """Rebuild the inlined page **outside** the request path.

    The old code rebuilt on demand while answering `GET /`, which meant a
    request could run `npm run css` (up to 60 s) and `build_standalone.py`.
    `uvicorn --workers 1` serves every other request from the same event loop,
    so one visitor with a stale checkout froze the whole dashboard — the health
    check included — for as long as the build took.

    A page build is a deploy-time step, not a request-time one: CI already fails
    a pull request whose `nobitex-mobile.html` is behind (`git diff --exit-code`
    in the `js` job). This function exists only for a source checkout that asks
    for the old behaviour with `NBX_AUTOBUILD_PAGE=1`, and it runs at most once
    per process, before the first request.
    """
    global _build_attempted
    if _build_attempted or os.getenv("NBX_AUTOBUILD_PAGE", "0") != "1":
        return
    if not (REPO_ROOT / "scripts" / "build_standalone.py").exists():
        return                      # a deployed tree: nothing to build with
    _build_attempted = True
    import subprocess

    css = sorted((REPO_ROOT / "assets" / "css").glob("*.css"))
    markup = [REPO_ROOT / "index.html", REPO_ROOT / "nbx-core.js", REPO_ROOT / "nbx-app.js"]
    # build_standalone.py refuses to inline a stylesheet older than the markup
    # (classes added since the last CSS build would be missing), so rebuild the
    # CSS first when the toolchain is present.
    stylesheet_behind = bool(css) and any(
        p.stat().st_mtime > max(c.stat().st_mtime for c in css) for p in markup if p.exists())
    if stylesheet_behind and (REPO_ROOT / "node_modules" / ".bin" / "tailwindcss").exists():
        try:
            subprocess.run(["npm", "run", "css"], cwd=str(REPO_ROOT),
                           check=True, capture_output=True, timeout=60)
        except Exception as exc:          # report, then try the build anyway
            log.warning("css build failed: %s", _short_proc_error(exc))
    try:
        subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "build_standalone.py")],
                       cwd=str(REPO_ROOT), check=True, capture_output=True, timeout=30)
        log.info("inlined dashboard rebuilt (NBX_AUTOBUILD_PAGE=1)")
    except Exception as exc:
        log.warning("standalone build failed (%s); serving the previous build",
                    _short_proc_error(exc))


def _self_contained_page() -> Path:
    """The dashboard as one file. Never built while answering a request.

    `index.html` links `assets/css/*.css` and `nbx-*.js`. That is right for
    development and wrong for every context that blocks subresources: a
    sandboxed preview iframe, a phone opening the file from a download folder,
    an air-gapped host. In those contexts the page arrives with no stylesheet
    and no JavaScript at all — no charts, no data, just unstyled markup.

    So `/` serves the inlined build that `npm run build` produced. When it is
    missing or older than its sources, the last good build is served and the
    reason is logged once — a stale-but-complete page beats a fresh-but-empty
    one, and neither is worth freezing the event loop for.
    `NBX_SERVE_INLINE=0` restores the plain file for anyone who wants to edit
    and reload; `NBX_AUTOBUILD_PAGE=1` rebuilds once at startup instead.
    """
    standalone = REPO_ROOT / "nobitex-mobile.html"
    if os.getenv("NBX_SERVE_INLINE", "1") == "0":
        return REPO_ROOT / "index.html"
    if _page_is_stale():
        global _stale_page_logged
        if not _stale_page_logged:
            _stale_page_logged = True
            log.warning(
                "inlined dashboard is older than its sources; serving the previous "
                "build. Run `npm run build` (or set NBX_AUTOBUILD_PAGE=1 to build "
                "once at startup).")
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
        filename="nobiTrader-mobile.html",
    )


@app.get("/download/nobitex-trader-robat.zip", include_in_schema=False)
async def download_package_zip():
    return FileResponse(
        REPO_ROOT / "nobitex-trader-robat.zip",
        media_type="application/zip",
        filename="nobitex-trader-robat.zip",
    )


