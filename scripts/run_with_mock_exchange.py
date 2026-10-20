#!/usr/bin/env python3
"""Run the dashboard against the exchange fixture, optionally with fundamentals down.

Built to verify the "static profile" path end to end — the FA engine must fall
back to a hard-coded profile **and** the page must say so. This is a fixture,
not a product component (same shelf as ``mock_upstream.py``).

The exchange is not the subject of that test, so it comes from the mock
upstream and is read through the client in-process. ``--keep-fa`` is the
control run: same wiring, providers left alone.

    python3 scripts/mock_upstream.py --port 8010          # in one shell
    python3 scripts/run_with_mock_exchange.py --port 8020 --exchange http://127.0.0.1:8010
    python3 scripts/run_with_mock_exchange.py --port 8021 --keep-fa
    open http://127.0.0.1:8020/   and   http://127.0.0.1:8021/
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import fa_engine  # noqa: E402


async def _down(*_a, **_kw):
    raise RuntimeError("provider unreachable (simulated)")


#: Only the shape is fixed here; every price comes from the exchange fixture.
UNIVERSE = ("BTCIRT", "ETHIRT", "SOLIRT", "XRPIRT", "ADAIRT", "DOGEIRT")


def _wire_exchange(base: str) -> None:
    """Feed the app's client from the exchange fixture, without HTTP to itself.

    The real client would call ``apiv2.nobitex.ir`` and trip its circuit
    breaker during startup, before this server is listening. Patching the two
    reads it needs keeps the test about the FA path.
    """
    import main

    async def get_market_stats() -> dict:
        out = {}
        async with httpx.AsyncClient(timeout=10) as c:
            for i, sym in enumerate(UNIVERSE):
                r = await c.get(f"{base}/api/candles/{sym}", params={"tf": "1h", "count": 2})
                rows = (r.json() or {}).get("rows") or []
                if not rows:
                    continue
                last, prev = rows[-1]["c"], rows[0]["c"]
                out[sym] = {
                    "latest": last,
                    "valueDst": 1e10 * (len(UNIVERSE) - i),
                    "dayChange": ((last / prev) - 1) * 100 if prev else 0,
                }
        if not out:
            raise RuntimeError("exchange fixture returned no candles")
        return {"status": "ok", "stats": out}

    async def get_udf_history(symbol: str, resolution: str, from_ts: int, to_ts: int) -> dict:
        tf = {"60": "1h", "240": "4h", "15": "15m"}.get(resolution, "1h")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{base}/api/candles/{symbol}", params={"tf": tf, "count": 400})
        rows = (r.json() or {}).get("rows") or []
        if rows:
            # The engine asks for a window ending now; the fixture's series may
            # end earlier. Shift it forward so it lands inside that window.
            now = (int(time.time()) // 60) * 60
            shift = now - rows[-1]["t"]
            rows = [{**x, "t": x["t"] + shift} for x in rows]
        return {"s": "ok", "t": [int(x["t"] / 1000) for x in rows],
                "o": [x["o"] for x in rows], "h": [x["h"] for x in rows],
                "l": [x["l"] for x in rows], "c": [x["c"] for x in rows],
                "v": [x["v"] for x in rows]}

    main.client.get_market_stats = get_market_stats
    main.client.get_udf_history = get_udf_history
    print(f"exchange reads served from {base}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8020)
    ap.add_argument("--exchange", default="",
                    help="base URL of the exchange fixture (default: 127.0.0.1:8010)")
    ap.add_argument("--keep-fa", action="store_true",
                    help="leave the fundamentals providers alone (control run)")
    ap.add_argument("--journal-db", default="",
                    help="use a scratch journal database instead of server/journal.db")
    ap.add_argument("--force-veto", metavar="NAME", default="",
                    help="make every symbol raise this fundamental veto, so the "
                         "cap/verdict path can be seen in the UI")
    a = ap.parse_args()

    if a.journal_db:
        import os
        os.environ["NBX_JOURNAL_DB"] = a.journal_db
        print(f"journal -> {a.journal_db}")

    if a.keep_fa:
        print("fundamentals providers left live")
    else:
        fa_engine.FAEngine._cg_overview = _down
        fa_engine.FAEngine._llama_protocols = _down
        fa_engine.FAEngine._listen_prices = _down
        print("fundamentals providers forced down")

    import main  # imported here so the patches land before the app is served
    import uvicorn

    _wire_exchange(a.exchange or "http://127.0.0.1:8010")

    if a.force_veto:
        # fa_engine is imported at module scope; re-importing it here would make
        # the name local to main() and break the patches above.

        real_score = fa_engine.FAEngine.score

        async def score_with_veto(self, symbol, **kw):
            res = await real_score(self, symbol, **kw)
            if a.force_veto not in res.vetoes:
                res.vetoes.append(a.force_veto)
            return res

        fa_engine.FAEngine.score = score_with_veto  # covers main.FA_ENGINE too
        print(f"every symbol now raises {a.force_veto!r}")

    # Pass the app object, not the "main:app" string: uvicorn would import the
    # module afresh and drop every patch above.
    uvicorn.run(main.app, host="0.0.0.0", port=a.port, log_level="warning",
                access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
