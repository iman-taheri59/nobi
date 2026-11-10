#!/usr/bin/env python3
"""Offline Chromium regression for the connection disclosure (source + standalone).

No backend/exchange: network requests outside the local source fixture are aborted.
The exact private widget functions are evaluated with deterministic display data;
no production hook or order/scan endpoint is added for testing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from browser_launch import add_arguments, launch_args

ROOT = Path(__file__).resolve().parent.parent
WIDTHS = (320, 390, 768, 1023, 1024, 1366, 1920)
APP = (ROOT / "nbx-app.js").read_text(encoding="utf-8")
WIDGET = APP[APP.index("  function addLiveStylesOnce()"):
             APP.index("  /* ---------- Mobile Bottom Navigation")]
FIXTURE = r"""({widget, direction}) => {
  LANG = direction === 'rtl' ? 'fa' : 'en';
  document.documentElement.dir = direction;
  document.documentElement.lang = LANG;
  applyStaticI18n();
  document.querySelectorAll('#sidebar .navbtn').forEach(b => b.classList.remove('hidden'));
  document.querySelectorAll('.tab-page').forEach(p => p.classList.add('hidden'));
  document.querySelector('#page-signals').classList.remove('hidden');
  const state = {mode: 'ONLINE', latency: 29, ranked: Array(27).fill({}),
    timeframe: '15m', lastScanAt: Date.now()-46000,
    wsTransport: 'rest', wsChannels: [], wsStats: 0, wsCandle: 0};
  const make = new Function('live', `
    const $ = id => document.getElementById(id);
    const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, ch =>
      ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const faNum = n => String(n);
    ${widget}
    addLiveStylesOnce(); renderLiveBadge(); return renderLiveBadge;
  `);
  window.__widgetState = state;
  window.__renderWidget = make(state);
}"""
PROBE = """() => {
 const box = e => {const r=e.getBoundingClientRect();
   return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
 const badge = document.querySelector('#liveBadge'), pop = document.querySelector('#apiStatusPop');
 const main = document.querySelector('main'), nav = document.querySelector('#sidebar');
 const overlap = (a,b) => Math.min(a.right,b.right)-Math.max(a.left,b.left)>1 &&
                         Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1;
 return {width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,
   inMain:main.contains(badge),fixed:getComputedStyle(badge).position==='fixed',
   padding:parseFloat(getComputedStyle(badge).paddingInlineStart),
   background:getComputedStyle(badge).backgroundColor,
   badge:box(badge),pop:box(pop),main:box(main),nav:box(nav),
   navOverlap:overlap(box(pop),box(nav)),
   nextPageTop:document.querySelector('#page-signals').getBoundingClientRect().top,
   expanded:document.querySelector('#liveBadgeBtn').getAttribute('aria-expanded'),
   popHidden:pop.hidden};
}"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    parser.add_argument("--screenshots", type=Path, help="optional local evidence directory")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright

    assert APP in (ROOT / "nobitex-mobile.html").read_text(encoding="utf-8"), "Rebuild standalone JS first"
    cases = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch_args(args.channel, args.executable))
        try:
            for source in ("index.html", "nobitex-mobile.html"):
                for direction in ("rtl", "ltr"):
                    for width in WIDTHS:
                        for height in (480, 768):
                            page = browser.new_page(viewport={"width": width, "height": height})
                            page.add_init_script("window.setInterval = () => 0;")

                            def serve(route, *, source=source):
                                url = urlparse(route.request.url)
                                path = unquote(url.path).lstrip("/") or source
                                target = (ROOT / path).resolve()
                                if (url.netloc == "layout.test" and ROOT in target.parents
                                        and target.is_file() and target.suffix in
                                        {".html", ".css", ".js", ".json"}):
                                    route.fulfill(path=str(target))
                                else:
                                    route.abort()

                            page.route("**/*", serve)
                            page.goto("https://layout.test/", wait_until="load")
                            page.locator("#liveBadge").wait_for(state="attached")
                            page.evaluate(FIXTURE, {"widget": WIDGET, "direction": direction})
                            button = page.locator("#liveBadgeBtn")
                            # The old implementation fails here: its badge floats over the nav.
                            closed = page.evaluate(PROBE)
                            context = (source, direction, width, height)
                            assert closed["padding"] >= 8, (context, closed)
                            assert closed["background"] == "rgb(16, 17, 20)", (context, closed)
                            assert closed["inMain"] and not closed["fixed"], (context, closed)
                            assert button.get_attribute("aria-expanded") == "false", context
                            button.click()
                            opened = page.evaluate(PROBE)
                            assert opened["expanded"] == "true" and not opened["popHidden"], (context, opened)
                            assert opened["scroll"] <= opened["width"] + 1, (context, opened)
                            assert opened["pop"]["left"] >= -1, (context, opened)
                            assert opened["pop"]["right"] <= width + 1, (context, opened)
                            assert opened["nextPageTop"] >= opened["badge"]["bottom"] - 1, (context, opened)
                            if width >= 1024:
                                assert not opened["navOverlap"], (context, opened)
                            # A health refresh must not collapse the panel or steal focus.
                            button.focus()
                            page.evaluate("__widgetState.latency=31; __renderWidget();")
                            assert button.get_attribute("aria-expanded") == "true", context
                            assert button.evaluate("e => document.activeElement === e"), context
                            # Long/untrusted API strings must remain text and not widen the page.
                            page.evaluate("__widgetState.timeframe='<img src=x onerror=alert(1)>'+'X'.repeat(180); __renderWidget();")
                            assert page.locator("#apiStatusPop img").count() == 0, context
                            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1"), context
                            page.evaluate("__widgetState.timeframe='15m'; __renderWidget();")
                            if width < 1024:
                                page.locator("#menuBtn").click()
                                page.wait_for_function("document.querySelector('#sidebar').classList.contains('side-open')")
                                page.wait_for_function("getComputedStyle(document.querySelector('#sidebar')).transform === 'matrix(1, 0, 0, 1, 0, 0)'")
                                nav_button = page.locator('#sidebar [data-tab="signals"]')
                                assert nav_button.evaluate("e => {const r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2));}"), context
                                page.locator("#menuClose").click()
                            if args.screenshots and source == "nobitex-mobile.html" and height == 768 and width in (390, 1366):
                                args.screenshots.mkdir(parents=True, exist_ok=True)
                                page.screenshot(path=str(args.screenshots / f"connection-{direction}-{width}.png"))
                            button.focus()
                            page.keyboard.press("Escape")
                            assert button.get_attribute("aria-expanded") == "false", context
                            assert page.locator("#apiStatusPop").is_hidden(), context
                            button.press("Enter")
                            assert button.get_attribute("aria-expanded") == "true", context
                            page.close()
                            cases += 1
        finally:
            browser.close()
    print(json.dumps({"status": "PASS", "browser_layouts": cases,
                      "source_and_standalone": True, "directions": ["rtl", "ltr"],
                      "live_server_or_exchange": False}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
