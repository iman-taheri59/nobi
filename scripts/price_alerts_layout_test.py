#!/usr/bin/env python3
"""Offline Chromium layout/access checks for the ADR-0049 alert dialog.

Only local source files and deterministic GET /api/alerts fixtures are served.
Every other request is aborted, no credentials are used, and no mutation/order
route exists in the fixture. The bell itself is clicked; the test never invokes
the module's private refresh functions to manufacture a successful UI result.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from browser_launch import add_arguments, launch_args

ROOT = Path(__file__).resolve().parent.parent
WIDTHS = (320, 390, 768, 1366)
RULE = {
    "id": 1, "symbol": "BTCIRT", "metric": "last", "operator": "above",
    "threshold": 100, "enabled": True, "armed": True,
    "last_status": "armed", "last_value": 90, "last_observed_at": 1700000000,
}
EVENT = {
    "id": 8, "rule_id": None, "symbol": "BTCIRT", "metric": "last",
    "operator": "above", "threshold": 100, "previous_value": 90, "value": 110,
    "observed_at": 1700000001, "source": "fixture", "delivery_sent": False,
    "delivery_reason": "http-400",
}
PROBE = """() => {
  const box = id => { const r = document.getElementById(id).getBoundingClientRect();
    return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}; };
  const dialog = box('alertsDialog');
  const controls = ['alertsRefresh', 'alertsClose'].map(id => ({...box(id),
    intentionallyHidden:document.getElementById(id).classList.contains('hidden')}));
  return {viewport:innerWidth, viewportHeight:innerHeight, scroll:document.documentElement.scrollWidth,
    dialog, controls, hidden:document.getElementById('alertsModal').classList.contains('hidden'),
    accessHidden:document.getElementById('alertsAccess').classList.contains('hidden'),
    contentHidden:document.getElementById('alertsContent').classList.contains('hidden')};
}"""


def assert_inside(probe: dict, context: tuple) -> None:
    assert probe["scroll"] <= probe["viewport"] + 1, (context, probe)
    dialog = probe["dialog"]
    assert dialog["left"] >= -1 and dialog["right"] <= probe["viewport"] + 1, (context, probe)
    assert dialog["top"] >= -1 and dialog["bottom"] <= probe["viewportHeight"] + 1, (context, probe)
    for control in probe["controls"]:
        if control["intentionallyHidden"]:
            assert control["width"] == 0, (context, probe)
            continue
        assert control["width"] > 0 and control["left"] >= dialog["left"] - 1, (context, probe)
        assert control["right"] <= dialog["right"] + 1, (context, probe)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    parser.add_argument("--screenshots", type=Path, help="optional local evidence directory")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright

    assert '<script src="nbx-alerts.js"></script>' in (ROOT / "index.html").read_text(encoding="utf-8")
    cases = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch_args(args.channel, args.executable))
        try:
            for source in ("index.html",):
                for language, direction in (("fa", "rtl"), ("en", "ltr")):
                    for width in WIDTHS:
                        height = 568 if width < 768 else 900
                        context = (source, language, width, height)
                        page = browser.new_page(viewport={"width": width, "height": height})
                        page.add_init_script("window.setInterval = () => 0;")
                        alert_gets = 0
                        errors: list[str] = []
                        page.on("pageerror", lambda exc, bucket=errors: bucket.append(str(exc)))

                        def serve(route, *, source=source):
                            nonlocal alert_gets
                            request = route.request
                            url = urlparse(request.url)
                            if url.netloc == "layout.test" and url.path == "/api/alerts/rules" and request.method == "GET":
                                alert_gets += 1
                                route.fulfill(status=200, content_type="application/json",
                                              body=json.dumps({"ok": True, "rules": [RULE], "n": 1}))
                                return
                            if url.netloc == "layout.test" and url.path == "/api/alerts/events" and request.method == "GET":
                                alert_gets += 1
                                route.fulfill(status=200, content_type="application/json",
                                              body=json.dumps({"ok": True, "events": [EVENT], "n": 1}))
                                return
                            path = unquote(url.path).lstrip("/") or source
                            target = (ROOT / path).resolve()
                            if (url.netloc == "layout.test" and ROOT in target.parents
                                    and target.is_file() and target.suffix in
                                    {".html", ".css", ".js", ".json", ".png", ".svg", ".woff2"}):
                                route.fulfill(path=str(target))
                            else:
                                route.abort()

                        page.route("**/*", serve)
                        page.goto("https://layout.test/", wait_until="load")
                        page.wait_for_function("window.NBXAlerts && typeof NBXAlerts.open === 'function'")
                        page.wait_for_timeout(250)
                        page.evaluate("""({language, direction}) => {
                          document.documentElement.lang = language;
                          document.documentElement.dir = direction;
                          NBXAlerts.setUser({user:{id:1,username:'fixture-owner',role:'owner'},can_mutate:true});
                          NBXAlerts.render();
                        }""", {"language": language, "direction": direction})
                        page.locator("#alertsBtn").click()
                        page.wait_for_function("document.querySelector('#alertsRules').textContent.includes('BTCIRT')")
                        page.wait_for_timeout(350)
                        assert alert_gets == 2, (context, "open must make exactly two GETs", alert_gets)
                        probe = page.evaluate(PROBE)
                        assert not probe["hidden"] and not probe["contentHidden"], (context, probe)
                        assert_inside(probe, context)
                        assert page.locator("#alertsEvents").inner_text().find("http-400") >= 0, context
                        assert page.locator("#alertsBadge").is_visible(), context

                        if args.screenshots and width in (390, 1366):
                            args.screenshots.mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(args.screenshots / f"alerts-{language}-{width}.png"))

                        page.locator("#alertsClose").click()
                        assert page.locator("#alertsModal").is_hidden(), context
                        before_viewer = alert_gets
                        page.evaluate("NBXAlerts.setUser({user:{id:3,username:'fixture-viewer',role:'viewer'},can_mutate:false})")
                        page.locator("#alertsBtn").click()
                        page.wait_for_timeout(150)
                        viewer = page.evaluate(PROBE)
                        assert not viewer["hidden"] and not viewer["accessHidden"] and viewer["contentHidden"], (context, viewer)
                        assert_inside(viewer, context)
                        assert alert_gets == before_viewer, (context, "Viewer made an alert request")
                        page.keyboard.press("Escape")
                        assert page.locator("#alertsModal").is_hidden(), context
                        assert page.locator("#alertsBtn").evaluate("e => document.activeElement === e"), context
                        assert not errors, (context, errors)
                        page.close()
                        cases += 1
        finally:
            browser.close()
    print(json.dumps({"status": "PASS", "browser_layouts": cases,
                      "canonical_page": True, "directions": ["rtl", "ltr"],
                      "viewer_zero_requests": True, "live_server_or_exchange": False}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
