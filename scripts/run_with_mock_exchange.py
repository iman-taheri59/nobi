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
from units import scale_stats  # noqa: E402


async def _down(*_a, **_kw):
    raise RuntimeError("provider unreachable (simulated)")


#: Only the shape is fixed here; every price comes from the exchange fixture.
#: BTCUSDT and USDTIRT are included because the premium needs all three legs.
UNIVERSE = ("BTCIRT", "ETHIRT", "SOLIRT", "XRPIRT", "ADAIRT", "DOGEIRT",
            "BTCUSDT", "USDTIRT")


#: Derived price per symbol, so the candle series can be put on the same scale
#: as the market board. Filled by the stats read, which always runs first.
_PRICES: dict[str, float] = {}


def to_udf(rows: list[dict]) -> dict:
    """Fixture rows -> the UDF payload the exchange documents.

    `t` is epoch **seconds** on both sides. An earlier version divided it by
    1000, which put every candle in January 1970: the chart's axis, the
    \"last candle\" age and the approval queue's TTL were all off by a factor of
    1000 while every number on screen still looked plausible.
    """
    return {"s": "ok", "t": [int(x["t"]) for x in rows],
            "o": [x["o"] for x in rows], "h": [x["h"] for x in rows],
            "l": [x["l"] for x in rows], "c": [x["c"] for x in rows],
            "v": [x["v"] for x in rows]}


def _wire_exchange(base: str) -> None:
    """Feed the app's client from the exchange fixture, without HTTP to itself.

    The real client would call ``apiv2.nobitex.ir`` and trip its circuit
    breaker during startup, before this server is listening. Patching the two
    reads it needs keeps the test about the FA path.
    """
    import main

    async def get_market_stats() -> dict:
        """Serve the fixture's own tickers, not a re-derivation of them.

        This used to rebuild every row from two candles and invent
        ``valueDst = 1e10 * (n - i)``, which is a turnover figure with no unit:
        a USDT pair then claimed ten billion dollars a day. The fixture now
        publishes a coherent table (prices, 24 h turnover in the pair's quote
        currency, 24 h change), so take it and only fix prices if the fixture
        contradicts itself.
        """
        async with httpx.AsyncClient(timeout=10) as c:
            u = await c.get(f"{base}/api/universe")
            uni = (u.json() or {}) if u.status_code == 200 else {}
            rows = uni.get("symbols") or []

        out: dict[str, dict] = {}
        if rows:
            usd_irt = next((float(r["last"]) for r in rows
                            if r.get("symbol") == "USDTIRT" and r.get("last")), 0.0)
            for r in rows:
                sym = str(r.get("symbol") or "")
                if not sym or not r.get("last"):
                    continue
                out[sym] = {
                    "latest": float(r["last"]),
                    "valueDst": float(r.get("vol24") or 0.0),
                    "dayChange": float(r.get("change24") or 0.0),
                }
        else:
            # No fixture table: fall back to candles, but never invent a volume.
            async with httpx.AsyncClient(timeout=10) as c:
                for sym in UNIVERSE:
                    r = await c.get(f"{base}/api/candles/{sym}",
                                    params={"tf": "1h", "count": 25})
                    rows_c = (r.json() or {}).get("rows") or []
                    if not rows_c:
                        continue
                    last, prev = rows_c[-1]["c"], rows_c[0]["c"]
                    out[sym] = {"latest": last, "valueDst": 0.0,
                                "dayChange": ((last / prev) - 1) * 100 if prev else 0.0}
            usd_irt = 0.0

        if not out:
            raise RuntimeError("exchange fixture returned no tickers")

        # The fixture's table is coherent; rebuild an IRT price from its USDT
        # twin only when the fixture disagrees with itself, so the two files
        # cannot drift apart silently.
        if usd_irt > 0:
            for sym, row in out.items():
                if sym.endswith("IRT") and sym != "USDTIRT":
                    twin = out.get(sym.replace("IRT", "USDT"))
                    if twin:
                        row["latest"] = twin["latest"] * usd_irt
        _PRICES.update({sym: row["latest"] for sym, row in out.items()})

        # Hand the *documented* shape back to the client — `btc-rls` keys and
        # Rial quotes — so the fixture exercises the same conversion path the
        # real API does. A fixture that pre-divides by ten cannot catch a
        # boundary that forgets to.
        wire = {"status": "ok", "stats": {}}
        for sym, row in out.items():
            if sym.endswith("USDT"):
                key, k = sym[:-4].lower() + "-usdt", 1.0
            elif sym.endswith("IRT"):
                key, k = sym[:-3].lower() + "-rls", 10.0
            else:
                continue
            wire["stats"][key] = {"latest": row["latest"] * k,
                                  "volumeDst": row["valueDst"] * k,
                                  "dayChange": row["dayChange"],
                                  "isClosed": False}
        return scale_stats(wire)

    async def get_udf_history(symbol: str, resolution: str, from_ts: int, to_ts: int,
                              count: int | None = None) -> dict:
        tf = {"60": "1h", "240": "4h", "15": "15m"}.get(resolution, "1h")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{base}/api/candles/{symbol}", params={"tf": tf, "count": 400})
        rows = (r.json() or {}).get("rows") or []
        if rows:
            # The engine asks for a window ending now; the fixture's series may
            # end earlier. Shift it forward so it lands inside that window — and
            # land it on the timeframe's own boundary: a 15-minute series whose
            # candles sit one minute apart cannot be merged with a live candle,
            # and its axis is a lie even before that.
            step = {"15": 900, "60": 3600, "240": 14400, "D": 86400}.get(resolution, 900)
            now = int(time.time()) // step * step
            shift = now - rows[-1]["t"]
            rows = [{**x, "t": x["t"] + shift} for x in rows]
            # Put the series on the market board's scale, otherwise the chart
            # quotes a different price than the ticker next to it.
            target = _PRICES.get(symbol.upper())
            if target and rows[-1]["c"] > 0:
                k = target / rows[-1]["c"]
                rows = [{**x, "o": x["o"] * k, "h": x["h"] * k,
                         "l": x["l"] * k, "c": x["c"] * k} for x in rows]
        return to_udf(rows)

    async def get_orderbook(symbol: str) -> dict:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{base}/api/orderbook/{symbol}")
        body = r.json() or {}
        # The exchange speaks [price, amount] arrays; the rest of the app speaks
        # {p, q}. Translate here, at the boundary, like the real client does.
        # The real payload carries `status: "ok"`; the health probe checks it.
        # Omitting it made `/api/health` report "exchange unreachable" while the
        # board was visibly fetching prices from this same fixture.
        return {"status": "ok",
                "bids": [[l["p"], l["q"]] for l in body.get("bids") or []],
                "asks": [[l["p"], l["q"]] for l in body.get("asks") or []]}

    # `/v2/options` is the exchange's own statement of minimums and tick sizes.
    # The fixture answers it locally so the live run exercises the same code path
    # without reaching the network.
    _real_get = main.client.get

    async def get(path, params=None, attempts=1):
        if path == "/v2/options":
            return {"status": "ok", "nobitex": {
                "minOrders": {"rls": "3000000", "usdt": "11"},
                "amountPrecisions": {"BTCIRT": "0.000001", "XRPIRT": "1"},
                "pricePrecisions": {"BTCIRT": "10"},
            }}
        return await _real_get(path, params=params, attempts=attempts)

    main.client.get = get
    main.client.get_market_stats = get_market_stats
    main.client.get_udf_history = get_udf_history
    main.client.get_orderbook = get_orderbook
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
