#!/usr/bin/env python3
"""Check that the FA panel labels a static profile and does not mislabel a cache hit.

    npm install && pip install playwright && playwright install chromium
    node scripts/build_standalone.py   # not needed; the page is served directly
    python3 scripts/fa_label_test.py --url http://127.0.0.1:8020/

Only /api/fa/* is stubbed, per case. Everything else comes from the running
server, so the rest of the page behaves normally. The point is that the label
must be right exactly when a provider is *not* answering — and that a cache hit
must not borrow the "static profile" label.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

def fa_row(symbol: str, source: str) -> dict:
    return {
        "symbol": symbol, "score": 70.0, "market": 70, "tokenomics": 70,
        "onchain": 70, "sentiment": 70, "macro": 70, "vetoes": [],
        "evidence": {"profile": "fallback" if source == "fallback" else "live"},
        "fresh": source == "live", "cached": source == "cached",
        "source": source, "profile": "fallback" if source == "fallback" else "live",
        "ts": "2026-10-20T00:00:00+00:00",
    }


CASES = {
    # name: (per-symbol source, expected sub-text fragment, expect chips?)
    "live": ("live", "زنده", False),
    "cached": ("cached", "زنده", False),      # a cache hit is not a static profile
    "fallback": ("fallback", "پروفایل ثابت", True),
}


async def run(url: str, case: str) -> int:
    from playwright.async_api import async_playwright

    source, needle, want_chip = CASES[case]
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1440, "height": 950})

        async def fa(route):
            sym = route.request.url.rsplit("/", 1)[-1]
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps(fa_row(sym, source)))

        await pg.route("**/api/fa/*", fa)
        await pg.goto(url, wait_until="load")
        await pg.wait_for_timeout(5000)
        await pg.click('[data-tab="fa"]')
        await pg.wait_for_timeout(4000)

        got = await pg.evaluate("""() => ({
            sub: (document.getElementById('faAvgSub') || {}).textContent || '',
            chips: [...document.querySelectorAll('#faList .chip')].map((e) => e.textContent.trim()),
            rows: document.querySelectorAll('#faList .nbx-mini-hbar').length,
            avg: (document.getElementById('faAvg') || {}).textContent || '',
        })""")
        got["allChips"] = bool(got["rows"]) and len(got["chips"]) == got["rows"]
        await b.close()

    labelled = got["allChips"] if want_chip else not got["chips"]
    ok = needle in got["sub"] and labelled and got["avg"] != "—"
    print(f"{case:9s} sub={got['sub']!r} chips={len(got['chips'])}/{got['rows']} "
          f"avg={got['avg']!r} -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8020/")
    ap.add_argument("--case", choices=[*CASES, "all"], default="all")
    a = ap.parse_args()
    cases = list(CASES) if a.case == "all" else [a.case]
    return max(asyncio.run(run(a.url, c)) for c in cases)


if __name__ == "__main__":
    sys.exit(main())
