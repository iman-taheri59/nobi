#!/usr/bin/env python3
"""mock_upstream.py — a contract-shaped stand-in for the Nobitex feed.

This is **not** a demo mode and it is not wired into the app. It exists so the
"the exchange is up" half of the UI can be exercised on a machine that cannot
reach `apiv2.nobitex.ir` (which includes this sandbox and most CI).

    python3 scripts/mock_upstream.py --port 8010
    python3 scripts/probe_live.py --url http://127.0.0.1:8010

It answers the same URLs with the same JSON shapes the real backend returns
when it has live data (see `server/main.py`). Prices are deterministic and
obviously synthetic (`symbol` is echoed, `source` is "mock") so a number that
leaks into a screenshot is identifiable.

Nothing here should ever ship in front of a user.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
#: One coherent price table. The USDT legs are the anchor and every IRT price
#: is derived from them at a single toman rate, because a fixture whose
#: BTCIRT/USDTIRT ratio implies a 76 % premium (or an ETH worth $3 m) wastes
#: the reader's time on numbers that could never come from one exchange.
USD_IRT = 101_200.0
BASE_USDT = {"BTC": 88_400.0, "ETH": 3_100.0, "TON": 7.8, "SOL": 148.0,
             "XRP": 0.62, "ADA": 0.41, "DOGE": 0.092, "TRX": 0.215}

#: Kept for callers that name an IRT pair directly.
BASE_PX = {f"{k}IRT": v * USD_IRT for k, v in BASE_USDT.items()}
BASE_PX["USDTIRT"] = USD_IRT
TF_SEC = {"15m": 900, "1h": 3600, "4h": 14400, "D": 86400}


def price(sym: str) -> float:
    base = base_price(sym)
    # a slow deterministic wiggle so candles are not a flat line
    return base * (1 + 0.004 * math.sin(time.time() / 600.0))


def base_price(sym: str) -> float:
    sym = sym.upper()
    if sym in BASE_PX:
        return BASE_PX[sym]
    if sym.endswith("USDT") and sym[:-4] in BASE_USDT:
        return BASE_USDT[sym[:-4]]
    if sym.endswith("IRT") and sym[:-3] in BASE_USDT:
        return BASE_USDT[sym[:-3]] * USD_IRT
    # Unknown symbol: a stable, obviously-fake price is better than a random
    # one, but the caller should not be reading this in the first place.
    seed = sum(ord(c) for c in sym)
    return 500.0 + seed * 37.0


def candles(sym: str, tf: str, n: int) -> list[dict]:
    step = TF_SEC.get(tf, 900)
    now = int(time.time()) // step * step
    p = base_price(sym)
    rnd = random.Random(f"{sym}:{tf}")     # deterministic per symbol+tf
    out = []
    for i in range(n):
        drift = math.sin((n - i) / 18.0) * 0.0025
        o = p
        c = o * (1 + drift + rnd.uniform(-0.0015, 0.0015))
        hi = max(o, c) * (1 + abs(rnd.uniform(0, 0.0012)))
        lo = min(o, c) * (1 - abs(rnd.uniform(0, 0.0012)))
        out.append({"t": now - (n - 1 - i) * step, "o": round(o, 6),
                    "h": round(hi, 6), "l": round(lo, 6), "c": round(c, 6),
                    "v": round(rnd.uniform(10, 900), 3)})
        p = c
    # The series must end where the quote says it ends, otherwise the probe's
    # UI-vs-API cross-check flags a mismatch that is the fixture's fault, not
    # the dashboard's.
    target = price(sym)
    scale = target / out[-1]["c"] if out[-1]["c"] else 1.0
    for r in out:
        for k in ("o", "h", "l", "c"):
            r[k] = round(r[k] * scale, 6)
    return out


SYMBOLS = list(BASE_PX)


def universe() -> dict:
    rows = []
    for i, s in enumerate(SYMBOLS):
        px = price(s)
        rows.append({"symbol": s, "last": round(px, 6), "vol24": 1e12 / (i + 3),
                     "change24": round(math.sin(i) * 2.4, 2), "rank": i + 1})
    return {"ok": True, "count": len(rows), "symbols": rows,
            "source": "mock", "premium_usdtirt": 1_012_000,
            "premium_pct": 1.9, "as_of": datetime.now(timezone.utc).isoformat()}


def signals(tf: str) -> dict:
    scored = []
    for i, u in enumerate(universe()["symbols"][:10]):
        scored.append({
            "symbol": u["symbol"], "tf": tf, "price": u["last"],
            "score": round(78.4 - i * 3.1, 1),
            "composite": round(82.0 - i * 2.7, 1),
            "composite_basis": "ta+liquidity",
            "direction": "LONG" if i < 6 else "NEUTRAL",
            "liquidity_score": round(70 - i, 1),
            "fa_score": None, "fa": None,
            "atr_pct": 1.9, "stop_pct": 3.8,
            "components": {"trend": 80, "momentum": 74, "volume": 61, "structure": 77},
            "data_source": "mock",
        })
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat(), "tf": tf,
            "count": len(scored), "age_s": 4.0, "stale": False,
            "refreshing": False, "premium_usdtirt": 1_012_000,
            "premium_pct": 1.9, "scored": scored, "source": "mock",
            "fa_coverage": {"scored": 10, "live": 0, "fallback": 10,
                            "failed": 0, "total": 10}}


def fa(sym: str) -> dict:
    return {"ok": True, "symbol": sym, "score": 64.0,
            "vetoes": [], "evidence": {"tvl": 4.2e9},
            "source": "mock", "unavailable": False}


class Handler(BaseHTTPRequestHandler):
    server_version = "nobi-mock/1.0"

    def log_message(self, *a):                     # keep the console readable
        pass

    def _send(self, obj, code=200, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                              # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path

        if p in ("/", "/index.html"):
            html = (ROOT / "index.html").read_bytes()
            return self._send(html, ctype="text/html")
        # Static assets the page loads (scripts, styles). Without these the
        # dashboard never boots and every panel reads as blank, which looks
        # like a UI bug rather than a fixture bug.
        if p.endswith((".js", ".css", ".svg", ".png", ".woff2")) or p.startswith("/assets/"):
            rel = p.lstrip("/")
            target = (ROOT / rel).resolve()
            try:
                target.relative_to(ROOT.resolve())          # no path traversal
            except ValueError:
                return self._send({"ok": False, "error": "forbidden"}, 403)
            if target.is_file():
                ctype = {".js": "application/javascript", ".css": "text/css",
                         ".svg": "image/svg+xml", ".png": "image/png",
                         ".woff2": "font/woff2"}.get(target.suffix, "text/plain")
                return self._send(target.read_bytes(), ctype=ctype)
            return self._send({"ok": False, "error": "not_found", "path": p}, 404)

        if p == "/api/health":
            return self._send({"ok": True, "backend": "nobi-mock", "version": "2.2.0",
                               "ts": datetime.now(timezone.utc).isoformat(),
                               "upstream": {"ok": True, "latency_ms": 41, "age_s": 2,
                                            "error": None, "degraded": False},
                               "auth": {"mode": "mock", "token_required": False},
                               "provenance": {"source": "mock"},
                               "stats": {"total": 12, "ok": 12, "fail": 0,
                                         "success_rate": 1.0, "last_latency_ms": 41}})
        if p == "/api/universe":
            return self._send(universe())
        if p == "/api/signals":
            return self._send(signals(q.get("tf", ["15m"])[0]))
        if p.startswith("/api/candles/"):
            sym = p.rsplit("/", 1)[-1].upper()
            tf = q.get("tf", ["15m"])[0]
            n = int(q.get("count", ["160"])[0])
            rows = candles(sym, tf, max(60, min(n, 600)))
            return self._send({"ok": True, "symbol": sym, "tf": tf,
                               "count": len(rows), "rows": rows, "source": "mock",
                               "as_of": datetime.now(timezone.utc).isoformat()})
        if p.startswith("/api/fa/"):
            return self._send(fa(p.rsplit("/", 1)[-1].upper()))
        if p.startswith("/api/orderbook/"):
            sym = p.rsplit("/", 1)[-1].upper()
            mid = price(sym)
            rnd = random.Random("book:" + sym)
            # {p, q} is what /api/orderbook returns after normalising the
            # exchange's [price, amount] arrays. The fixture speaks the backend's
            # contract, not the exchange's — mixing the two is how a panel ends
            # up drawing fifteen em-dashes.
            bids, asks = [], []
            for i in range(8):
                bids.append({"p": round(mid * (1 - 0.0006 * (i + 1)), 6),
                             "q": round(rnd.uniform(0.2, 4.0), 4)})
                asks.append({"p": round(mid * (1 + 0.0006 * (i + 1)), 6),
                             "q": round(rnd.uniform(0.2, 4.0), 4)})
            return self._send({"ok": True, "symbol": sym, "bids": bids, "asks": asks,
                               "levels": len(bids) + len(asks),
                               "best_bid": bids[0]["p"], "best_ask": asks[0]["p"],
                               "mid": round(mid, 6),
                               "spread": round(asks[0]["p"] - bids[0]["p"], 6),
                               "spread_pct": round((asks[0]["p"] - bids[0]["p"]) / mid * 100, 4),
                               "bid_depth": round(sum(x["p"] * x["q"] for x in bids), 2),
                               "ask_depth": round(sum(x["p"] * x["q"] for x in asks), 2),
                               "imbalance": 3.2, "last_update": int(time.time() * 1000),
                               "source": "mock"})
        if p == "/api/risk/state":
            return self._send({"ok": True, "equity": 100000.0, "halted": False,
                               "openHeat": 0.0, "dailyPnl": 0.0,
                               "caps": {"maxConcurrent": 5, "maxHeatPct": 6,
                                        "maxSingleAssetPct": 25, "minCashPct": 10},
                               "kill": {"dailyPnlPct": -3.0},
                               "source": "mock"})
        if p == "/api/paper/positions":
            return self._send({"ok": True, "positions": [], "source": "mock"})
        if p == "/api/paper/metrics":
            now = int(time.time())
            curve = [[now - (60 - i) * 86400, 100000.0 + i * 12.5] for i in range(60)]
            # Shape must match server/main.py exactly: the browser reads
            # `metrics` and only uses the response if that key is present.
            return self._send({"metrics": {"equity": 100000.0, "start_equity": 100000.0,
                                           "cash": 100000.0, "trades": 0,
                                           "profit_factor": None, "hit_rate": None,
                                           "expectancy_r": None, "max_drawdown": 0.0},
                               "curve": curve, "equity_quote": "USD",
                               "source": "mock"})
        if p == "/api/journal/summary":
            return self._send({"ok": True, "kpi": {"hit": None, "exp": None, "vpf": None,
                                                   "ic": None, "sig": 0, "res": 0,
                                                   "issued": 0, "pending": 0, "void": 0},
                               "signals": [], "horizons": [], "families": [],
                               "calibration": [], "origins": {}, "data_sources": {},
                               "resolved_sources": {}, "msi": None,
                               "generated_at": datetime.now(timezone.utc).isoformat()})
        if p == "/api/errors":
            return self._send({"errors": [], "n": 0,
                               "counts": {"total": 0, "error": 0, "critical": 0,
                                          "warning": 0, "info": 0, "last_24h": 0,
                                          "last_ts": None, "sources": {}},
                               "filters": {}})
        if p == "/api/audit":
            return self._send({"ok": True, "rows": [], "n": 0})
        if p == "/api/settings":
            return self._send({"ok": True, "read_only": True,
                               "config_hash": "mock000000",
                               "weights": {"ta_components": {"source": "ta_engine",
                                                             "values": {"trend": 0.4,
                                                                        "momentum": 0.25,
                                                                        "volume": 0.15,
                                                                        "structure": 0.2}},
                                           "composite": {"values": {"ta": 0.55, "fa": 0.25,
                                                                    "liq": 0.2}}},
                               "flags": [], "source": "mock"})
        if p == "/api/notify/status":
            return self._send({"ok": True, "configured": False, "sent": 0, "failed": 0})
        if p == "/api/backtest/reports":
            return self._send({"ok": True, "reports": []})
        return self._send({"ok": False, "error": "mock_unknown_route", "path": p}, 404)

    def do_POST(self):                             # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        return self._send({"ok": False, "unavailable": True,
                           "error": "mock_read_only"}, 200)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8010)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    print(f"mock upstream on http://0.0.0.0:{a.port}  "
          f"(serving {ROOT/'index.html'})\n"
          f"now run: python3 scripts/probe_live.py --url http://127.0.0.1:{a.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
