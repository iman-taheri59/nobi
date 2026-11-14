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


DIAGNOSE = """() => {
  const sb = document.querySelector('#sidebar');
  const btn = document.querySelector('#menuBtn');
  const rect = el => { if (!el) return null; const b = el.getBoundingClientRect();
    return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]; };
  const covered = (el) => {
    if (!el) return null;
    const b = el.getBoundingClientRect();
    const at = document.elementFromPoint(b.x + Math.min(8, b.width / 2), b.y + b.height / 2);
    return at ? (at.id || at.tagName) : null;
  };
  return {
    transform: sb ? getComputedStyle(sb).transform : 'no #sidebar',
    sideOpen: sb ? sb.classList.contains('side-open') : null,
    transition: sb ? getComputedStyle(sb).transitionDuration : null,
    overlayHidden: document.querySelector('#sideOverlay')
      ? document.querySelector('#sideOverlay').classList.contains('hidden') : null,
    innerWidth: window.innerWidth,
    clientWidth: document.documentElement.clientWidth,
    scroll: [window.scrollX, window.scrollY],
    mediaMobile: matchMedia('(max-width:1023px)').matches,
    menuBtn: rect(btn), menuBtnCoveredBy: covered(btn),
    menuClose: rect(document.querySelector('#menuClose')),
    menuCloseCoveredBy: covered(document.querySelector('#menuClose')),
    overlay: rect(document.querySelector('#sideOverlay')),
    sidebar: rect(sb),
    activeElement: document.activeElement ? (document.activeElement.id
      || document.activeElement.tagName) : null,
    bodyOverflow: document.body.style.overflow,
    gateOpen: document.querySelector('#authGate')
      ? !document.querySelector('#authGate').hidden : null,
  };
}"""


def diagnose(page, what, source, direction, width, name):
    """Everything a red run needs to explain itself without the runner's log.

    `transform` alone was enough to see that the panel was closed; it is not
    enough to say why a click produced nothing. The pointer is included because
    a click can land on the wrong element at a boundary width, and the element
    that actually receives it is the only way to tell.
    """
    try:
        seen = page.evaluate(DIAGNOSE)
    except Exception as exc:  # the page may already be gone
        return f"{what} (the page could not be asked why: {exc})"
    details = " ".join(f"{k}={v!r}" for k, v in seen.items())
    return f"{what}: {details} ({source} {direction} {width}px {name!r})"


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
                                        # Name what is on screen. A bare timeout
                                        # on a runner whose log needed admin
                                        # rights cost three red `ui` jobs before
                                        # any of this could be seen.
                                        raise AssertionError(diagnose(
                                            page, "the sidebar never settled open",
                                            source, direction, width, name,
                                        )) from None
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
                                    try:
                                        page.wait_for_function(
                                            "!document.querySelector('#sidebar')"
                                            ".classList.contains('side-open')",
                                            timeout=SIDEBAR_TIMEOUT_MS,
                                        )
                                    except PlaywrightTimeoutError:
                                        raise AssertionError(diagnose(
                                            page, f"the sidebar never closed via {close}",
                                            source, direction, width, name,
                                        )) from None
                                    assert page.locator("#sideOverlay").is_hidden(), context
                            cases += 1
                            page.close()
        finally:
            browser.close()
    print(f"PASS: {cases} header layouts; mobile close button and overlay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
