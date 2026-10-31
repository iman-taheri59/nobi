#!/usr/bin/env python3
"""Offline browser regression for the authenticated mobile header.

Run: python scripts/mobile_header_test.py
Tests source + generated standalone, RTL/LTR and short/long account labels.
The account controls are DOM fixtures, not authentication/API tests. All remote
requests are blocked; the script never connects to a deployed trading server.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from browser_launch import (
    add_arguments,
    launch_args,
    launch_failed_message,
    missing_package_message,
)

ROOT = Path(__file__).resolve().parent.parent
WIDTHS = (320, 360, 375, 390, 480, 640, 768, 1023, 1024, 1440)
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
  document.querySelector('#logoutBtn').classList.remove('hidden');
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
            for source in ("index.html", "nobitex-mobile.html"):
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
                            page.locator("#mobilePkgBtn").wait_for(state="attached")
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
                                    page.locator("#menuBtn").click()
                                    page.wait_for_function(
                                        "getComputedStyle(document.querySelector('#sidebar')).transform"
                                        " === 'matrix(1, 0, 0, 1, 0, 0)'"
                                    )
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
                                        "!document.querySelector('#sidebar').classList.contains('side-open')"
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
