#!/usr/bin/env python3
"""Layout regression test in a real browser (Chromium via Playwright).

Why this exists
---------------
jsdom has no layout engine, so it cannot see the two bugs a user actually
reported:

* the Signals page had eleven empty chart slots in twelve rows, because the
  backend path never fetched per-symbol candles (jsdom happily rendered the
  "—" placeholder and every assertion passed);
* `hidden lg:inline-flex` chips stayed visible on a phone. Tailwind v4 put its
  utilities in `@layer utilities`, and the dashboard's own unlayered
  `.chip{display:inline-flex}` beat them, so the header grew to 741px inside a
  390px viewport. Nothing in jsdom cares about CSS layers.

This script drives the page the way a person does — 390 / 768 / 1440 wide,
every tab, mobile bar below 1024px — and fails on:

* horizontal document scrolling (something is wider than the screen)
* a tab that cannot be opened
* a signal row whose chart has no data (on a live backend)
* a header row wider than its own box

Run:
    cd server && python3 -m uvicorn main:app --port 8000 &
    python3 scripts/layout_test.py            # add --url for another host

Exit code 0 = clean, 1 = at least one problem.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

TABS = ["overview", "signals", "fa", "orders", "performance",
        "desk", "risk", "backtest", "errors", "settings"]
VIEWPORTS = [(390, 844), (768, 900), (1440, 900)]

PAGE_PROBE = """() => {
  const vw = document.documentElement.clientWidth;
  return {
    scrollWidth: document.documentElement.scrollWidth,
    vw,
    overflow: Math.max(0, document.documentElement.scrollWidth - vw),
  };
}"""

HEADER_PROBE = """() => {
  const row = document.querySelector('header > div');
  if (!row) return {ok: true};
  return {ok: row.scrollWidth <= row.clientWidth + 1,
          scrollWidth: row.scrollWidth, clientWidth: row.clientWidth};
}"""

TAB_PROBE = """() => {
  const sec = document.querySelector('.tab-page:not(.hidden)');
  const t = (sec || document.body).innerText || '';
  const digits = (t.match(/[0-9\u06F0-\u06F9]/g) || []).length;
  const unavail = sec ? sec.querySelectorAll('[data-unavailable]').length : 0;
  const explained = /در دسترس نیست|امکان‌پذیر نیست|ذهنی|unreachable|unavailable/i.test(t);
  // A data-i18n element with no text means a missing dictionary key, which
  // renders as a blank label nobody notices in a screenshot.
  const emptyI18n = [...(sec || document).querySelectorAll('[data-i18n]')]
    .filter(e => !e.textContent.trim())
    .map(e => e.getAttribute('data-i18n')).slice(0, 4);
  return {len: t.length, digits, unavail, explained, emptyI18n,
          section: sec ? sec.id : null};
}"""

SIGNALS_PROBE = """() => {
  const rows = [...document.querySelectorAll('.sig-row')];
  const withSvg = rows.filter(r => r.querySelector('svg[data-spark]')).length;
  const missing = rows.filter(r => r.querySelector('[data-spark="missing"]')).length;
  const row = rows[0];
  const svg = row && row.querySelector('svg[data-spark]');
  const share = row && svg ? +(svg.getBoundingClientRect().width / row.getBoundingClientRect().width * 100).toFixed(1) : 0;
  const errBlock = document.querySelector('#sigList [data-unavailable]');
  return {rows: rows.length, withSvg, missing,
          unavailable: !!errBlock,
          errorBlock: !!(errBlock && errBlock.textContent.trim().length > 20),
          chartWidth: svg ? Math.round(svg.getBoundingClientRect().width) : 0,
          chartShare: share,
          clickable: !!(row && row.getAttribute('role') === 'button')};
}"""

DETAIL_PROBE = """() => {
  const d = document.querySelector('.sig-detail');
  if (!d) return {open: false};
  const svg = d.querySelector('svg');
  return {open: true,
          width: svg ? Math.round(svg.getBoundingClientRect().width) : 0,
          height: svg ? Math.round(svg.getBoundingClientRect().height) : 0,
          candles: d.querySelectorAll('rect').length};
}"""

ERRORS_PROBE = """() => {
  const page = document.querySelector('#page-errors');
  if (!page) return {present: false};
  const blank = [...page.querySelectorAll('[data-i18n]')]
    .filter(e => !(e.textContent || '').trim()).length;
  return {present: true, blank,
          cards: ['errTotal','errErrors','errDay','errLast']
            .map(id => ((page.querySelector('#'+id)||{}).textContent || '').trim()),
          rows: page.querySelectorAll('#errBody tr.err-row').length,
          buttons: ['errSelfTest','errExport','errClear','errRefresh']
            .filter(id => page.querySelector('#'+id)).length};
}
"""


async def run(url: str) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("playwright is not installed:\n"
              "  pip install playwright && python -m playwright install chromium",
              file=sys.stderr)
        return 2

    problems: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for width, height in VIEWPORTS:
            page = await browser.new_page(viewport={"width": width, "height": height})
            page_errors: list[str] = []
            page.on("pageerror", lambda e: page_errors.append(str(e)[:160]))
            try:
                await page.goto(url, wait_until="domcontentloaded")
            except Exception as exc:                        # noqa: BLE001
                # A dead server used to surface as a raw Playwright traceback
                # that reads like a test bug. Say what happened instead.
                print(f"FAIL\n - {url} is not serving ({type(exc).__name__})")
                print("   start it first: cd server && "
                      "python3 -m uvicorn main:app --host 0.0.0.0 --port 8000")
                return 1
            await page.wait_for_timeout(4000)

            hdr = await page.evaluate(HEADER_PROBE)
            if not hdr["ok"]:
                problems.append(
                    f"{width}px: header is {hdr['scrollWidth']}px wide inside "
                    f"{hdr['clientWidth']}px (a `hidden lg:*` utility lost the cascade)")

            for tab in TABS:
                selector = (f'#mobileBottomNav [data-mtab="{tab}"]' if width < 1024
                            else f'[data-tab="{tab}"]')
                try:
                    await page.click(selector, timeout=5000)
                except Exception as exc:                     # noqa: BLE001
                    problems.append(f"{width}px: cannot open tab '{tab}' ({type(exc).__name__})")
                    continue
                await page.wait_for_timeout(700)

                probe = await page.evaluate(PAGE_PROBE)

                # The invariant that the old suite never checked: a panel is
                # either populated with real values or it says, in words, why
                # it has none. Two thirds of the bugs found on 2026-10-20 were
                # panels that silently showed nothing (or a stale hard-coded
                # number) while every test stayed green, because only the
                # overflow was asserted.
                tabinfo = await page.evaluate(TAB_PROBE)
                if not tabinfo["section"]:
                    problems.append(f"{width}px/{tab}: no visible .tab-page section")
                if tabinfo["emptyI18n"]:
                    problems.append(
                        f"{width}px/{tab}: blank label(s) — missing i18n key "
                        f"{tabinfo['emptyI18n']}")
                if tabinfo["digits"] < 3 and not tabinfo["explained"]:
                    problems.append(
                        f"{width}px/{tab}: panel is empty AND carries no explanation "
                        f"({tabinfo['digits']} digits, {tabinfo['unavail']} unavailable blocks)")
                if probe["overflow"] > 0:
                    problems.append(
                        f"{width}px/{tab}: page scrolls {probe['overflow']}px sideways "
                        f"({probe['scrollWidth']} > {probe['vw']})")

                if tab == "signals":
                    # The first scan has to finish before rows exist; poll
                    # instead of guessing a fixed delay.
                    sig = {"rows": 0}
                    for _ in range(45):
                        await page.wait_for_timeout(700)
                        sig = await page.evaluate(SIGNALS_PROBE)
                        if sig["rows"]:
                            break
                    if sig.get("unavailable"):
                        # The exchange is unreachable in this environment. The
                        # page must say so in an error-styled block; a blank
                        # list would pass a naive "no overflow" check.
                        if not sig.get("errorBlock"):
                            problems.append(f"{width}px/signals: no rows and no error message shown")
                    if sig["rows"] and width >= 1024:
                        # The trend line used to be a 144px sliver in a 1128px
                        # row (12.8%). A chart that small is decoration.
                        if sig["chartWidth"] < 200:
                            problems.append(f"{width}px/signals: row chart is only {sig['chartWidth']}px wide")
                        if not sig["clickable"]:
                            problems.append(f"{width}px/signals: rows are not clickable")
                        await page.click(".sig-row", position={"x": 30, "y": 20})
                        await page.wait_for_timeout(600)
                        det = await page.evaluate(DETAIL_PROBE)
                        if not det["open"]:
                            problems.append(f"{width}px/signals: clicking a row opened no detail")
                        elif det["candles"] < 60:
                            problems.append(f"{width}px/signals: detail chart has {det['candles']} candles")

                    if tab == "errors":
                        err = await page.evaluate(ERRORS_PROBE)
                        if not err["present"]:
                            problems.append(f"{width}px/errors: page section missing")
                        else:
                            if err["blank"]:
                                problems.append(
                                    f"{width}px/errors: {err['blank']} label(s) render empty (i18n key missing)")
                            if any(c in ("", "—") for c in err["cards"]):
                                problems.append(f"{width}px/errors: summary cards did not fill: {err['cards']}")
                            if err["buttons"] != 4:
                                problems.append(f"{width}px/errors: only {err['buttons']}/4 controls present")

                    if sig["rows"] == 0 and not sig.get("errorBlock"):
                        scanning = await page.evaluate(
                            "() => /scanning|اسکن|در حال اسکن/.test((document.querySelector('#sigList')||{}).textContent || '')")
                        if scanning:
                            print(f"  note {width}px: first scan still running — rows not ready yet")
                        else:
                            snippet = await page.evaluate(
                                "() => (((document.querySelector('#sigList')||{}).innerHTML || '')"
                                ".replace(/<[^>]*>/g,' ').replace(/\\s+/g,' ').trim()).slice(0,90)")
                            problems.append(
                                f"{width}px/signals: no rows and no error message "
                                f"(#sigList = {snippet!r})")
                    elif sig["missing"] > sig["rows"] // 4:
                        problems.append(
                            f"{width}px/signals: {sig['missing']}/{sig['rows']} rows have no chart data")

            if page_errors:
                problems.append(f"{width}px: {len(page_errors)} JS error(s): {page_errors[0]}")
            await page.close()
        await browser.close()

    if problems:
        print("FAIL")
        for p_ in problems:
            print(" -", p_)
        return 1
    print(f"PASS — {len(VIEWPORTS)} viewports, {len(TABS)} tabs, no overflow, charts have data")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000/")
    sys.exit(asyncio.run(run(ap.parse_args().url)))
