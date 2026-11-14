#!/usr/bin/env python3
"""Offline browser regression for the authenticated mobile header.

Run: python scripts/mobile_header_test.py
Tests the canonical responsive page at mobile widths, RTL/LTR, and short/long account labels.
The account controls are DOM fixtures, not authentication/API tests. All remote
requests are blocked; the script never connects to a deployed trading server.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from browser_launch import (
    add_arguments,
    launch_args,
    launch_failed_message,
    missing_package_message,
)

ROOT = Path(__file__).resolve().parent.parent
WIDTHS = (320, 360, 375, 390, 480, 640, 768, 1023, 1024, 1440)
#: The sidebar transform is a CSS transition, judged on a runner that also has a
#: browser, a scan and 80 layout cases in flight. Playwright's default 30s was
#: exceeded by one `ui` job while every measured value in that same run was
#: correct, so the ceiling is raised rather than the assertion.
#:
#: Measured since: 2.1.3 raised it to 120s and one `ui` run then timed out *at*
#: 120s — the sidebar opened and never closed. Removing a class is synchronous,
#: so a wait that long means the click never reached `closeSide`, not that the
#: box was slow. Raising the number only made the failure four times slower, so
#: it is back near the default and the case is named in the log instead.
#: The animation is judged by where it *ends*, not by the value it passes
#: through. The fully-open matrix is only reached on the very last frame of a
#: 300 ms curve, and `getComputedStyle` during the transition returns the
#: interpolated matrix, so on a loaded runner the sample can land on any partial
#: value. The wait therefore asks "is the panel open and in place?", which is true
#: for the whole transition and stays true afterwards.
SIDEBAR_TIMEOUT_MS = 30_000
SETTLED = (
    "(() => {"
    "  const el = document.querySelector('#sidebar');"
    "  if (!el || !el.classList.contains('side-open')) return false;"
    "  const t = getComputedStyle(el).transform;"
    "  if (t === 'none') return true;"
    "  const m = t.match(/^matrix[(]([^)]+)[)]$/);"
    "  if (!m) return false;"
    "  const v = m[1].split(',').map(Number);"
    "  return Math.abs(v[4]) < 1 && Math.abs(v[5]) < 1;"
    "})()"
)

PROBE = """() => {
  const h = document.querySelector('body > header > div');
  return {width: document.documentElement.clientWidth,
          scroll: document.documentElement.scrollWidth,
          header: h.scrollWidth, headerWidth: h.clientWidth, x: scrollX};
}"""
FIXTURE = """({direction, name}) => {
  document.documentElement.dir = direction;
  document.querySelector('#userName').textContent = name;
  document.querySelector('#userRole').textContent = direction === 'rtl' ? '· مالک' : '· owner';
  const chip = document.querySelector('#userChip');
  chip.classList.remove('hidden'); chip.classList.add('inline-flex');
  document.querySelector('#userName').classList.remove('hidden');
  document.querySelector('#userRole').classList.remove('hidden');
}"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    args = parser.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(missing_package_message(), file=sys.stderr)
        return 2

    cases = 0
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(**launch_args(args.channel, args.executable))
        except Exception as exc:
            print(launch_failed_message(exc), file=sys.stderr)
            return 2
        try:
            for source in ("index.html",):
                for direction in ("rtl", "ltr"):
                    for width in WIDTHS:
                        for name in ("ADMIN", "LONG_OWNER_USERNAME_123456789"):
                            page = browser.new_page(
                                viewport={"width": width, "height": 812},
                                is_mobile=True, has_touch=True, device_scale_factor=3.25,
                            )

                            def serve(route, _request, *, source=source):
                                url = urlparse(route.request.url)
                                path = unquote(url.path).lstrip("/") or source
                                target = (ROOT / path).resolve()
                                if (url.netloc == "layout.test" and ROOT in target.parents
                                        and target.is_file()
                                        and target.suffix in {".html", ".css", ".js", ".json"}
                                        and not path.startswith("api/")):
                                    route.fulfill(path=str(target))
                                else:
                                    route.abort()

                            page.route("**/*", serve)
                            page.goto("https://layout.test/", wait_until="load")
                            page.locator("#userMenuBtn").wait_for(state="attached")
                            page.evaluate(FIXTURE, {"direction": direction, "name": name})
                            data = page.evaluate(PROBE)
                            context = (source, direction, width, name, data)
                            # Printed as the case runs, not only on failure: a
                            # red job whose log cannot be fetched (this happens)
                            # then still shows which layout broke and by how much.
                            print(f"  {source} {direction} {width}px {name!r}: "
                                  f"scroll={data['scroll']} width={data['width']} "
                                  f"header={data['header']}/{data['headerWidth']}",
                                  flush=True)
                            assert data["scroll"] <= data["width"] + 1, context
                            assert data["header"] <= data["headerWidth"] + 1, context
                            assert abs(data["x"]) <= 1, context
                            if width < 1024:
                                for close in ("button", "overlay"):
                                    # Named as it runs: a `ui` job failed inside
                                    # this loop with no way to tell which of the
                                    # two closes, on which source, at which width,
                                    # and the log was the only witness.
                                    print(f"  {source} {direction} {width}px "
                                          f"{name!r}: sidebar close via {close}",
                                          flush=True)
                                    page.locator("#menuBtn").click()
                                    try:
                                        page.wait_for_function(SETTLED,
                                                               timeout=SIDEBAR_TIMEOUT_MS)
                                    except PlaywrightTimeoutError:
                                        # Name the value it was stuck on. A bare
                                        # timeout on a runner with no log access
                                        # cost three red `ui` jobs before the
                                        # number that was actually on screen
                                        # could be seen at all.
                                        stuck = page.evaluate(
                                            "() => { const el = document.querySelector('#sidebar');"
                                            " return [el ? getComputedStyle(el).transform : 'no #sidebar',"
                                            " el ? el.classList.contains('side-open') : null,"
                                            " document.documentElement.dir,"
                                            " document.querySelector('#sidebar')"
                                            "   ? getComputedStyle(document.querySelector('#sidebar'))"
                                            "     .transitionDuration : null]; }"
                                        )
                                        raise AssertionError(
                                            f"the sidebar never settled open: transform={stuck[0]!r} "
                                            f"side-open={stuck[1]} dir={stuck[2]} "
                                            f"transition={stuck[3]} ({source} {direction} {width}px "
                                            f"{name!r})"
                                        ) from None
                                    assert page.locator("#sidebar").evaluate(
                                        "e => e.classList.contains('side-open')"
                                    ), context
                                    if close == "button":
                                        page.locator("#menuClose").click()
                                    else:
                                        page.locator("#sideOverlay").click(position={
                                            "x": 5 if direction == "rtl" else width - 5,
                                            "y": 300,
                                        })
                                    page.wait_for_function(
                                        "!document.querySelector('#sidebar').classList.contains('side-open')",
                                        timeout=SIDEBAR_TIMEOUT_MS,
                                    )
                                    assert page.locator("#sideOverlay").is_hidden(), context
                            cases += 1
                            page.close()
        finally:
            browser.close()
    print(f"PASS: {cases} header layouts; mobile close button and overlay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
