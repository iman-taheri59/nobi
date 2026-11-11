"""The responsive dashboard has one source: ``index.html``.

Both supported page routes serve those exact bytes. Browser-layout tests use
that same file at phone and desktop viewport sizes; there is no generated HTML
variant to keep in parity.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

import main

ROOT = Path(__file__).resolve().parents[2]
CLIENT = TestClient(main.app)


def test_root_and_explicit_index_serve_the_canonical_source() -> None:
    expected = (ROOT / "index.html").read_bytes()
    for route in ("/", "/index.html"):
        response = CLIENT.get(route)
        assert response.status_code == 200
        assert response.content == expected
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers.get("cache-control") == "no-store, must-revalidate"


def test_removed_standalone_page_route_is_not_served() -> None:
    assert CLIENT.get("/nobitex-mobile.html").status_code == 404


def test_canonical_html_keeps_responsive_viewport_and_loads_local_assets() -> None:
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert re.search(
        r'<meta\s+name="viewport"\s+content="width=device-width,\s*initial-scale=1\.0',
        html,
    )
    styles = re.findall(r'<link\s+rel="stylesheet"\s+href="([^"]+)"', html)
    assert styles and all(path.startswith("assets/css/") for path in styles)
    scripts = re.findall(r'<script\s+src="([^"]+)"', html)
    assert {"nbx-core.js", "nbx-app.js", "nbx-alerts.js"} <= set(scripts)

    for asset in (*styles, "nbx-core.js", "nbx-app.js", "nbx-alerts.js"):
        response = CLIENT.get("/" + asset)
        assert response.status_code == 200, f"canonical asset /{asset} is unavailable"
        expected = (ROOT / asset).read_bytes()
        assert response.content == expected, f"/{asset} does not serve its source bytes"


def test_canonical_dashboard_requests_server_coin_metadata() -> None:
    """The cosmetic server layer must stay wired without gating app boot."""
    app = (ROOT / "nbx-app.js").read_text(encoding="utf-8")
    page = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "function loadCoinMetadata" in app
    assert "window.setCoinMetadata(data.coins" in app
    assert "/coins" in app
    assert re.search(r'<script src="nbx-app\.js(?:\?[^"]*)?"', page)
    assert "window.setCoinMetadata = function setCoinMetadata" in page
    assert re.search(r"setInterval\(loadCoinMetadata,\s*60\s*\*\s*60\s*\*\s*1000\)", app)
    assert "try {\n      await NBX.boot();\n      loadCoinMetadata();" in app


def test_tailwind_scans_only_product_sources() -> None:
    """Documentation wording must not affect the compiled utility CSS."""
    source = (ROOT / "assets" / "css" / "tailwind.src.css").read_text(encoding="utf-8")
    assert '@import "tailwindcss" source(none);' in source
    assert source.count('@import "tailwindcss"') == 1
    files = re.findall(r'@source\s+"([^"]+)"', source)
    assert sorted(files) == sorted(
        ["../../index.html", "../../nbx-app.js", "../../nbx-core.js", "../../nbx-alerts.js"]
    )
    compiled = (ROOT / "assets" / "css" / "tailwind.css").read_text(encoding="utf-8")
    assert compiled.startswith("/*! tailwindcss v4")
