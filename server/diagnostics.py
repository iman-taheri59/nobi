"""Health checks the dashboard can run on itself — and prove the answers.

Why this is not "run the test suite from a web page"
---------------------------------------------------
The pytest suite answers *"is this commit correct?"*. It needs the source
tree, takes seconds, spawns processes, and belongs in CI. What the person
staring at the error log needs is a different question: *"is this running
process broken right now?"* — is the exchange reachable, is the USDT/IRT rate
fresh, does the journal open, can a marker row survive a write and a read.

Those two are `diagnostics` (here) and `devsuite` (the real gates, in
`devsuite.py`). Mixing them would put a 20-second `pytest` behind a button
labelled "check health", and the next person would conclude health checks are
slow and stop pressing the button.

Three rules, enforced by tests
------------------------------
1. **A check that raises is never "ok".** It is `error`, with the exception in
   the detail. The one thing worse than no health check is a green one that
   skipped its own body.
2. **"Cannot tell" is a first-class answer.** `skip` always carries a reason:
   the process has not talked to the exchange yet, so there is no rate to
   judge. Reporting `ok` there would be a lie; reporting `fail` would be a
   false alarm that teaches people to ignore red.
3. **Every `ok` names what was actually observed.** "error log healthy" is not
   a result; "wrote #812, read it back, 812 rows" is. The `detail` string is
   the evidence, and `data` carries the numbers for anything that wants them.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import errorlog
import journal

OK = "ok"
FAIL = "fail"
SKIP = "skip"
ERROR = "error"

#: Ordered worst-last so a summary can pick the head of the list.
SEVERITY = (OK, SKIP, ERROR, FAIL)


@dataclass
class Result:
    """What one check observed."""

    status: str
    detail: str = ""
    hint: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Ctx:
    """Everything a check may touch, handed in by `main` at call time.

    Explicit rather than imported: `diagnostics` must not import `main` (which
    imports it), and a check that can reach a global can also be fooled by a
    test that forgot to reset it.
    """

    client: Any = None
    cached: dict[str, Any] = field(default_factory=dict)
    paper_state: Callable[[], dict[str, Any]] | None = None
    risk_state: Callable[[], dict[str, Any]] | None = None
    fx: Callable[[], Awaitable[Any]] | None = None
    ws_status: Callable[[], dict[str, Any]] | None = None
    secrets: dict[str, bool] = field(default_factory=dict)
    #: Ceiling for one call to the exchange.
    exchange_timeout_s: float = 20.0
    #: Ceiling for one whole check. Generous relative to the above: the market
    #: checks make one call each, and this exists to catch a hang, not to race
    #: a slow network.
    check_timeout_s: float = 45.0


@dataclass(frozen=True)
class Spec:
    id: str
    fa: str
    en: str
    kind: str          # core | storage | market
    needs: str         # "" | "exchange"
    fn: Callable[[Ctx], Awaitable[Result]]


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _ago(seconds: float) -> str:
    s = max(0.0, seconds)
    if s < 90:
        return f"{s:.0f}s"
    if s < 5400:
        return f"{s / 60:.0f}m"
    return f"{s / 3600:.1f}h"


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
async def check_errorlog(ctx: Ctx) -> Result:
    """Write a marker row and read it back from the same store the UI reads."""
    res = errorlog.self_test()
    if not res.get("sqlite_verified"):
        return Result(FAIL, f"wrote #{res.get('wrote')} but it did not come back from recent()",
                      "SQLite round-trip not verified; memory fallback is not durable storage")
    return Result(OK, f"wrote #{res.get('wrote')}, read it back, {res.get('total_after')} rows",
                  data={"wrote": res.get("wrote"), "total": res.get("total_after")})


async def check_journal(ctx: Ctx) -> Result:
    """Read journal schema/state without migration or lifecycle evaluation."""
    conn = journal.connect_readonly()
    try:
        stage = journal.stage_snapshot(conn)
        verdict = journal.msi(conn)  # calculation only, no counter/meta writes
        rows = int(conn.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0])
        last = conn.execute("SELECT MAX(ts) FROM signal_log").fetchone()[0]
    finally:
        conn.close()
    detail = f"{rows} row(s) in signal_log"
    detail += (f", newest {_ago(time.time() - float(last))} old" if last else " — no rows")
    return Result(OK, detail, data={"rows": rows, "db": str(journal.DB_PATH),
                  "stage": stage["stage"], "msi_status": verdict["status"], "read_only": True})


async def check_paper(ctx: Ctx) -> Result:
    """The paper account must hold finite money. NaN equity breaks every cap."""
    if ctx.paper_state is None:
        return Result(SKIP, "paper account is not wired into this process", "")
    st = ctx.paper_state()
    eq, cash = _num(st.get("equity")), _num(st.get("cash"))
    positions = st.get("positions") or {}
    if eq is None or cash is None:
        return Result(FAIL, f"equity/cash is not a finite number: {st.get('equity')!r}, "
                            f"{st.get('cash')!r}", "a NaN account makes every risk cap test false")
    if eq <= 0:
        return Result(FAIL, f"equity is {eq:g}", "the account is empty — every order will be refused")
    return Result(OK, f"equity {eq:,.0f} USD, cash {cash:,.0f}, {len(positions)} position(s)",
                  data={"equity": eq, "cash": cash, "positions": len(positions)})


async def check_risk(ctx: Ctx) -> Result:
    """The caps the preflight divides by must themselves be usable."""
    if ctx.risk_state is None:
        return Result(SKIP, "risk engine is not wired into this process", "")
    st = ctx.risk_state()
    caps = st.get("caps") or {}
    bad = [k for k in ("riskPct", "maxHeatPct", "minCashPct", "maxSingleAssetPct")
           if _num(caps.get(k)) is None or not (0 < float(caps[k]) <= 100)]
    if bad:
        return Result(FAIL, f"out-of-range cap(s): {', '.join(bad)} = "
                            f"{[caps.get(k) for k in bad]}",
                      "a cap of 0 or above 100 silently disables the gate it guards")
    heat = _num(st.get("openHeat")) or 0.0
    return Result(OK, f"heat {heat:.1f}% of {caps['maxHeatPct']:g}%, "
                      f"{'HALTED' if st.get('halted') else 'armed'}",
                  data={"halted": bool(st.get("halted")), "openHeat": heat})


async def check_fx(ctx: Ctx) -> Result:
    """The USDT/IRT rate: present, positive, and younger than its own TTL.

    This is the check for the bug that shipped a `rate from usdt-irt is 1935s
    old` traceback: a stale rate is not a slow rate, it is a wrong one, and the
    only way to see it coming is to look at the age against the TTL.
    """
    if ctx.fx is None:
        return Result(SKIP, "no FX accessor wired into this process", "")
    rate = await ctx.fx()
    if rate is None:
        return Result(SKIP, "no USDT/IRT rate has been read yet",
                      "start the scan or call /api/universe once; a rate of 0 is never "
                      "substituted for a real one")
    px = _num(getattr(rate, "toman_per_usdt", None))
    age = _num(getattr(rate, "age_s", None))
    ttl = _num(getattr(rate, "max_age_s", None)) or 0.0
    if px is None or px <= 0:
        return Result(FAIL, f"rate is {px!r}", "a zero rate silently converts every IRT order to nothing")
    if age is None:
        return Result(SKIP, "rate has no timestamp, so its freshness cannot be judged", "")
    if age > ttl:
        return Result(FAIL, f"{px:,.0f} IRT/USDT is {_ago(age)} old, TTL {_ago(ttl)}",
                      "orders priced off this rate are priced off the past")
    return Result(OK, f"{px:,.0f} IRT/USDT, {_ago(age)} old (TTL {_ago(ttl)})",
                  data={"rate": px, "age_s": age, "ttl_s": ttl})


async def check_universe(ctx: Ctx) -> Result:
    rows = ctx.cached.get("universe") or []
    ts = _num(ctx.cached.get("universe_ts"))
    if not rows:
        err = ctx.cached.get("universe_error")
        return Result(SKIP, f"universe has not loaded{f' ({err})' if err else ''}",
                      "every market panel reads from it, so they will all be empty")
    age = time.time() - ts if ts else None
    return Result(OK, f"{len(rows)} symbols" + (f", {_ago(age)} old" if age is not None else ""),
                  data={"symbols": len(rows), "age_s": age})


async def check_secrets(ctx: Ctx) -> Result:
    """Which secrets exist — never what they are."""
    if not ctx.secrets:
        return Result(SKIP, "nothing to report", "")
    have = sorted(k for k, v in ctx.secrets.items() if v)
    missing = sorted(k for k, v in ctx.secrets.items() if not v)
    detail = f"{len(have)}/{len(ctx.secrets)} set"
    if missing:
        detail += f"; missing: {', '.join(missing)}"
    return Result(OK, detail, "" if not missing else "those features stay off until they are set",
                  data={"set": have, "missing": missing})


async def check_ws(ctx: Ctx) -> Result:
    if ctx.ws_status is None:
        return Result(SKIP, "websocket client is not wired into this process", "")
    st = ctx.ws_status()
    connected = bool(st.get("connected"))
    age = _num(st.get("last_msg_age_s"))
    if not connected:
        return Result(SKIP, "socket is not connected",
                      "the dashboard falls back to REST polling, which is slower but not wrong")
    return Result(OK, "connected" + (f", last message {_ago(age)} ago" if age is not None else ""),
                  data=st)


# --- market: these really call the exchange, so they are the slow ones ------
async def check_market_stats(ctx: Ctx) -> Result:
    if ctx.client is None:
        return Result(SKIP, "no exchange client in this process", "")
    t0 = time.perf_counter()
    stats = await asyncio.wait_for(ctx.client.get_market_stats(), ctx.exchange_timeout_s)
    ms = (time.perf_counter() - t0) * 1000
    rows = (stats or {}).get("stats") or {}
    if not rows:
        return Result(FAIL, "the exchange answered with no markets",
                      "nobody can trade if /market/stats is empty")
    return Result(OK, f"{len(rows)} markets in {ms:.0f} ms", data={"markets": len(rows), "ms": round(ms)})


async def check_candles(ctx: Ctx) -> Result:
    if ctx.client is None:
        return Result(SKIP, "no exchange client in this process", "")
    t0 = time.perf_counter()
    rows = await asyncio.wait_for(ctx.client.get_candles("BTCIRT", "15m", 20), ctx.exchange_timeout_s)
    ms = (time.perf_counter() - t0) * 1000
    n = len(rows or [])
    if n < 2:
        return Result(FAIL, f"BTCIRT 15m returned {n} bar(s)",
                      "the signal charts and the journal resolve from these bars")
    return Result(OK, f"{n} BTCIRT 15m bars in {ms:.0f} ms", data={"bars": n, "ms": round(ms)})


async def check_orderbook(ctx: Ctx) -> Result:
    if ctx.client is None:
        return Result(SKIP, "no exchange client in this process", "")
    ob = await asyncio.wait_for(ctx.client.get_orderbook("BTCIRT"), ctx.exchange_timeout_s)
    bids = (ob or {}).get("bids") or []
    asks = (ob or {}).get("asks") or []
    if not bids or not asks:
        return Result(FAIL, "one side of the BTCIRT book is empty",
                      "a crossed or one-sided book is how a dead feed looks")
    bid, ask = _num(bids[0][0]), _num(asks[0][0])
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return Result(FAIL, f"best bid/ask are not usable prices: {bid!r} / {ask!r}", "")
    if ask < bid:
        return Result(FAIL, f"crossed book: bid {bid:,.0f} > ask {ask:,.0f}",
                      "the feed is stale or the two sides came from different snapshots")
    spread = (ask - bid) / bid * 100
    return Result(OK, f"bid {bid:,.0f} / ask {ask:,.0f} ({spread:.2f}% spread)",
                  data={"bid": bid, "ask": ask, "spread_pct": round(spread, 3)})


REGISTRY: tuple[Spec, ...] = (
    Spec("errorlog", "لاگ خطا: نوشتن و خواندن", "error log: write + read", "storage", "",
         check_errorlog),
    Spec("journal", "ژورنال: باز شدن پایگاه داده", "journal: database opens", "storage", "",
         check_journal),
    Spec("paper", "حساب کاغذی: پول متناهی", "paper account: finite money", "core", "",
         check_paper),
    Spec("risk", "موتور ریسک: سقف‌ها", "risk engine: caps", "core", "",
         check_risk),
    Spec("fx", "نرخ USDT/IRT و تازگی آن", "USDT/IRT rate freshness", "core", "",
         check_fx),
    Spec("universe", "فهرست بازار", "market universe", "core", "",
         check_universe),
    Spec("secrets", "کلیدهای تنظیم‌شده", "configured secrets", "core", "",
         check_secrets),
    Spec("ws", "اتصال وب‌سوکت", "websocket link", "core", "",
         check_ws),
    Spec("market_stats", "صرافی: /market/stats", "exchange: /market/stats", "market", "exchange",
         check_market_stats),
    Spec("candles", "صرافی: کندل BTCIRT", "exchange: BTCIRT candles", "market", "exchange",
         check_candles),
    Spec("orderbook", "صرافی: دفتر سفارش", "exchange: order book", "market", "exchange",
         check_orderbook),
)

BY_ID = {s.id: s for s in REGISTRY}


def catalogue() -> list[dict[str, Any]]:
    """What the UI lists before anything has run. No results, no fake statuses."""
    return [{"id": s.id, "fa": s.fa, "en": s.en, "kind": s.kind, "needs": s.needs}
            for s in REGISTRY]


async def _one(spec: Spec, ctx: Ctx) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        res = await asyncio.wait_for(spec.fn(ctx), timeout=ctx.check_timeout_s)
    except TimeoutError:
        res = Result(FAIL, f"no answer within {ctx.check_timeout_s:g}s",
                     "the check hung; a hung check is a failure, not a slow pass")
    except Exception as exc:  # a check must never take the whole run down
        # Not `ok`, not `fail`: this is a bug in the check or in the code it
        # touched, and pretending otherwise is how health checks rot.
        res = Result(ERROR, f"{type(exc).__name__}: {str(exc)[:200]}",
                     "the check itself threw — read the error log for the traceback")
        errorlog.capture(level="error", source=f"diagnostics.{spec.id}", message=str(exc),
                         context={"exception": type(exc).__name__})
    if res.status not in (OK, FAIL, SKIP, ERROR):
        res = Result(ERROR, f"check returned an unknown status {res.status!r}", "")
    return {"id": spec.id, "fa": spec.fa, "en": spec.en, "kind": spec.kind,
            "needs": spec.needs, "status": res.status, "detail": res.detail,
            "hint": res.hint, "ms": round((time.perf_counter() - t0) * 1000),
            "data": res.data}


async def run(ctx: Ctx, only: list[str] | None = None,
              kinds: list[str] | None = None) -> dict[str, Any]:
    """Run the registry (or a subset) and summarise without softening anything."""
    chosen = [s for s in REGISTRY
              if (only is None or s.id in only) and (kinds is None or s.kind in kinds)]
    unknown = sorted(set(only or []) - set(BY_ID))
    if unknown:
        raise KeyError(f"unknown check(s): {', '.join(unknown)}")
    started = time.time()
    results = [await _one(spec, ctx) for spec in chosen]
    counts = {k: sum(1 for r in results if r["status"] == k) for k in SEVERITY}
    # The headline is the worst thing found, not the majority: one red check is
    # not "mostly healthy".
    overall = OK
    for status in SEVERITY:          # ok < skip < error < fail
        if counts[status]:
            overall = status
    return {
        "started_at": started,
        "duration_ms": round((time.time() - started) * 1000),
        "overall": overall,
        "counts": counts,
        "results": results,
        "unknown": unknown,
    }
