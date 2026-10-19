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

import asyncio
import logging
import os
from datetime import datetime, timezone

from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from nobitex_client import ClientError, NobitexClient
from ta_engine import candles_to_df, ta_score
from fa_engine import FA_ENGINE, FAResult
from risk_engine import RiskEngine, RiskConfig
from paper_trading import get_broker, PAPER_BROKER, PaperBroker
from backtest import EventBacktester, WalkForwardRunner, run_and_save, grid_search, DEFAULT_GRID
from ws_execution import STREAM_EXECUTOR, streams_start

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nbx-app")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Nobitex Trader Bot — Phase 2 (FA + Risk)", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

TIMEFRAMES = ["15m", "1h", "4h", "D"]
client = NobitexClient()
EQUITY = float(os.getenv("NBX_EQUITY_USD", "100000"))  # default: 100k USD test capital
risk = RiskEngine(RiskConfig(equity=EQUITY))
_cached: dict = {"universe": [], "signals": {}, "signals_by_tf": {}, "premium": None, "last_scan": None}


def norm(s: str) -> str:
    return (s or "").strip().upper()


@app.on_event("startup")
async def _boot():
    await client.connect()
    asyncio.create_task(load_universe())
    asyncio.create_task(rescan("15m"))


@app.on_event("shutdown")
async def _stop():
    await client.aclose()


@app.get("/api/cache/stats")
async def cache_stats():
    from candle_cache import CANDLE_CACHE
    return CANDLE_CACHE.stats() | {"ok": True}


@app.get("/api/health")
async def health():
    ok = False
    try:
        r = await asyncio.wait_for(client.get_orderbook("BTCIRT"), timeout=6)
        ok = bool(r and r.get("status") == "ok")
    except Exception:
        pass
    return {
        "ok": ok,
        "backend": "python-fastapi-phase1",
        "ts": datetime.now(timezone.utc).isoformat(),
        "stats": {
            "total": client.stats.total,
            "ok": client.stats.ok,
            "fail": client.stats.fail,
            "success_rate": round(client.stats.success_rate(), 1),
            "last_latency_ms": client.stats.last_latency_ms,
        },
    }


async def load_universe():
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
        usdtirt = next((r for r in rows if r["symbol"] == "USDTIRT"), None)
        _cached["premium"] = usdtirt["last"] if usdtirt else None
        log.info("universe: %d rows loaded", len(_cached["universe"]))
    except ClientError as e:
        log.warning("load_universe: %s", e)
        _cached["universe"] = []
    return _cached["universe"]


@app.get("/api/universe")
async def universe():
    if not _cached["universe"]:
        await load_universe()
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "premium_usdtirt": _cached["premium"],
        "count": len(_cached["universe"]),
        "symbols": _cached["universe"],
    }


async def rescan(tf: str = "15m", top: int = 40):
    if not _cached["universe"]:
        await load_universe()
    sem = asyncio.Semaphore(6)
    out: dict = {}

    async def one(u):
        try:
            async with sem:
                rows = await client.get_candles(u["symbol"], tf, 260)
            df = candles_to_df(rows)
            sig = ta_score(df)
            vol = u["vol24"]
            import math
            liq = round(min(100, max(0, 50 + math.log10(vol + 1) * 4)), 0)
            fa_stub = 50
            composite = round(0.55 * sig["score"] + 0.25 * fa_stub + 0.2 * liq, 1)
            base = {**u, **sig, "liquidity_score": liq, "fa_score": fa_stub, "composite": composite}
            out[u["symbol"]] = base
        except ClientError as e:
            log.warning("scan %s: %s", u["symbol"], e)

    await asyncio.gather(*(one(u) for u in _cached["universe"][:top]))
    # Phase 2: add FA scores for top-10 liquid symbols
    for s in list(out.values())[:10]:
        try:
            fa: FAResult = await FA_ENGINE.score(s["symbol"], usdt_premium_dev=0, nobil_depth_score=s["liquidity_score"])
            composite = round(0.55 * s["score"] + 0.25 * fa.score + 0.2 * s["liquidity_score"], 1)
            s["fa"] = {"score": fa.score, "vetoes": fa.vetoes, "evidence": fa.evidence}
            s["fa_score"] = fa.score
            s["composite"] = composite
        except Exception as e:
            log.warning("FA failed %s: %s", s["symbol"], e)
    _cached["signals"] = out
    _cached["signals_by_tf"][tf] = out
    _cached["last_scan"] = datetime.now(timezone.utc).isoformat()
    return out


@app.get("/api/signals")
async def signals(tf: str = Query("15m", pattern="^(15m|1h|4h|D)$")):
    if tf not in _cached["signals_by_tf"] or not _cached["signals_by_tf"][tf]:
        await rescan(tf)
    tf_signals = _cached["signals_by_tf"].get(tf) or _cached["signals"]
    ranked = sorted(tf_signals.values(), key=lambda x: x["composite"], reverse=True)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tf": tf,
        "count": len(ranked),
        "premium_usdtirt": _cached["premium"],
        "scored": ranked,
    }


@app.get("/api/candles/{symbol}")
async def candles(symbol: str, tf: str = Query("15m", pattern="^(15m|1h|4h|D)$")):
    rows = await client.get_candles(symbol, tf, 260)
    return {"symbol": symbol.upper(), "tf": tf, "count": len(rows), "rows": rows}


@app.get("/api/fa/{symbol}")
async def fa(symbol: str):
    s = _cached["signals"].get(symbol.upper())
    liq = s["liquidity_score"] if s else 50
    res = await FA_ENGINE.score(symbol.upper(), usdt_premium_dev=0, nobil_depth_score=liq)
    return {"symbol": symbol.upper(), **res.__dict__, "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/risk/plan")
async def risk_plan(symbol: str, side: str = "buy", tf: str = Query("15m", pattern="^(15m|1h|4h|D)$")):
    s = _cached["signals"].get(symbol.upper())
    if not s:
        await rescan(tf, top=60)
        s = _cached["signals"].get(symbol.upper())
    if not s:
        return {"error": "symbol not loaded"}
    plan = risk.evaluate(symbol.upper(), side, s["lastClose"], s["risk"]["atr"], tf)
    risk.open_heat -= plan.risk_pct  # preview only; don't actually consume heat
    return {"symbol": symbol.upper(), "side": side, **plan.__dict__, "healthHeat": risk.open_heat}


@app.get("/api/risk/state")
async def risk_state():
    return {
        "equity": risk.cfg.equity,
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
    }


# ---------------- Paper Trading (ADR-0009/0012/0013) ----------------
@app.post("/api/paper/order")
async def paper_order(payload: dict):
    body = payload or {}
    side = (body.get("side") or "buy").lower()
    symbol = (body.get("symbol") or "").upper()
    qty = float(body.get("qty") or 0)
    if not symbol or not qty:
        return {"error": "symbol & qty required"}
    size = body.get("size_quote") and float(body["size_quote"]) or None
    plan = body.get("risk_plan")
    broker = get_broker(EQUITY, client, risk)
    try:
        if plan:
            pass  # plan stub preserved; risk gates applied inside evaluate when enabled
        src = (body.get("source") or "SEMI").upper()
        price = body.get("price") and float(body["price"]) or None
        pos = await broker.place_limit(symbol, side, qty, price, body.get("stop") and float(body["stop"]), body.get("tp1") and float(body["tp1"]), body.get("tp2") and float(body["tp2"]), src)
        return {"ok": bool(pos), "position": pos.__dict__ if pos else None, "metrics": broker.metrics()}
    except Exception as e:
        log.exception("paper order failed")
        return {"ok": False, "error": str(e)}


@app.get("/api/paper/positions")
async def paper_positions():
    broker = get_broker(EQUITY, client, risk)
    await streams_start(broker, client)
    await broker.evaluate_positions()
    return {"positions": [p.__dict__ for p in broker.positions.values()], "metrics": broker.metrics()}


@app.get("/api/ws/status")
async def ws_status():
    return {
        "enabled": STREAM_EXECUTOR.enabled,
        "subscriptions": sorted(list(getattr(STREAM_EXECUTOR, "_subs", []))),
        "books": {
            s: {
                "best_bid": b.best_bid,
                "best_ask": b.best_ask,
                "mid": b.mid,
                "last_update": b.last_update,
                "latency": b.latency_ms,
                "fresh": b.fresh,
            }
            for s, b in STREAM_EXECUTOR.books.items()
        },
    }


@app.get("/api/paper/metrics")
async def paper_metrics():
    broker = get_broker(EQUITY, client, risk)
    await broker.evaluate_positions()
    return {"metrics": broker.metrics(), "curve": [list(x) for x in broker.equity_curve[-200:]]}


@app.post("/api/paper/halt")
async def paper_halt():
    broker = get_broker(EQUITY, client, risk)
    broker.halted = True
    risk.halted = True
    return {"halted": broker.halted}


# ---------------- Walk-Forward Backtest (ADR-0009) ----------------
@app.post("/api/backtest/run")
async def backtest_run(payload: dict):
    b = payload or {}
    symbol = (b.get("symbol") or "BTCIRT").upper()
    tf = (b.get("tf") or "1h")
    cfg = {
        "initial_equity": float(b.get("equity", 50000)),
        "risk_pct": float(b.get("risk_pct", 0.75)),
        "atr_stop_k": float(b.get("atr_stop_k", 2.0)),
        "max_concurrent": int(b.get("max_concurrent", 5)),
        "train_bars": int(b.get("train_bars", 240)),
        "eval_bars": int(b.get("eval_bars", 120)),
    }
    try:
        rows = await client.get_candles(symbol, tf, 400)
        res = await asyncio.to_thread(run_and_save, symbol, tf, rows, cfg)
        return {"ok": True, "report": res}
    except Exception as e:
        log.exception("backtest failed")
        import traceback
        return {"ok": False, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/backtest/quick")
async def backtest_quick(symbol: str = "BTCIRT", tf: str = "1h"):
    try:
        rows = await client.get_candles(symbol, tf, 400)
    except Exception as e:
        return {"ok": False, "error": f"candle fetch failed: {e}"}
    if not rows:
        return {"ok": False, "error": "no candles returned — check connectivity to apiv2.nobitex.ir"}
    cfg = {"initial_equity": 50000, "risk_pct": 0.75, "atr_stop_k": 2.0, "max_concurrent": 5, "train_bars": 240, "eval_bars": 120}
    res = await asyncio.to_thread(run_and_save, symbol.upper(), tf, rows, cfg)
    return {"ok": True, "report": res}


@app.post("/api/grid-multi")
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
        except Exception as e:
            return {"symbol": sym, "error": str(e)}

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


@app.post("/api/backtest/grid")
async def backtest_grid(payload: dict):
    b = payload or {}
    symbol = (b.get("symbol") or "BTCIRT").upper()
    tf = b.get("tf") or "1h"
    custom = b.get("grid") and dict(b["grid"]) or None
    try:
        rows = await client.get_candles(symbol, tf, 400)
        if not rows:
            return {"ok": False, "error": "no candles fetched"}
        res = await asyncio.to_thread(grid_search, symbol, tf, rows, custom)
        return {"ok": True, **res}
    except Exception as e:
        log.exception("grid failed")
        return {"ok": False, "error": str(e)}


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
    a = latest["aggregate"]
    checks = {
        "profit_factor_ge_1_3": {"value": a.get("profit_factor", 0), "pass": (a.get("profit_factor", 0) or 0) >= 1.3, "required": 1.3},
        "max_drawdown_lt_15": {"value": a.get("max_drawdown", 0), "pass": (a.get("max_drawdown", 0) or 0) < 15, "required": 15},
        "trades_ge_100": {"value": a.get("trades", 0), "pass": (a.get("trades", 0) or 0) >= 100, "required": 100},
    }
    passed = all(c["pass"] for c in checks.values())
    return {
        "ok": True,
        "gate": "PASS" if passed else "FAIL",
        "symbol": latest.get("symbol"),
        "tf": latest.get("tf"),
        "config_hash": latest.get("config_hash"),
        "checks": checks,
        "note": "Paper-trading evidence is still required before any live deployment (ADR-0009).",
    }


# ---------------- Static Frontend Hosting ----------------
REPO_ROOT = Path(__file__).resolve().parent.parent
if (REPO_ROOT / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(REPO_ROOT / "assets")), name="assets")


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
async def serve_index():
    return FileResponse(REPO_ROOT / "index.html")


@app.get("/version-check.html", include_in_schema=False)
async def serve_version_check():
    return FileResponse(REPO_ROOT / "version-check.html")


@app.get("/nbx-core.js", include_in_schema=False)
async def serve_nbx_core():
    return FileResponse(REPO_ROOT / "nbx-core.js", media_type="application/javascript")


@app.get("/nbx-app.js", include_in_schema=False)
async def serve_nbx_app():
    return FileResponse(REPO_ROOT / "nbx-app.js", media_type="application/javascript")


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


