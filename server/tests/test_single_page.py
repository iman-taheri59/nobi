"""There is one dashboard, and one generated artifact. Prove it, and keep it so.

The repository ships two HTML files:

    index.html            the dashboard: markup + links to 5 stylesheets and 2 scripts
    nobitex-mobile.html   the same dashboard with those 7 files inlined

The second is *generated* by ``scripts/build_standalone.py`` and exists because
some contexts block subresources entirely — a phone opening the file from a
download folder, an air-gapped host, a sandboxed preview iframe. It is not a
second layout and not a second implementation, so a difference between the two
files is a defect by definition: either someone edited the generated file by
hand (their change is erased by the next build) or someone forked the markup
(the two pages now drift, and only one of them gets fixed).

These tests compare the artifact against its sources directly, without running
the build, so they hold in any checkout that has no node_modules and no
tailwind binary.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "index.html"
STANDALONE = ROOT / "nobitex-mobile.html"
CSS_FILES = ("layers", "tailwind", "base", "components", "dashboard")
JS_FILES = ("nbx-core.js", "nbx-app.js")

pytestmark = pytest.mark.skipif(
    not (INDEX.is_file() and STANDALONE.is_file()),
    reason="the generated page is not in this checkout",
)


def _deinline(page: str) -> str:
    """Undo exactly what the builder does — nothing more."""
    page = re.sub(r'<style id="nbx-standalone-css">.*?</style>\n?', "", page, flags=re.S)
    for js in JS_FILES:
        page = re.sub(
            rf'<script id="nbx-inline-{js.replace(".", "-")}">.*?</script>',
            f'<script src="{js}"></script>',
            page,
            flags=re.S,
        )
    for name in CSS_FILES:
        page = page.replace(
            f'<link rel="stylesheet" href="assets/css/{name}.css">\n', ""
        )
        page = page.replace(f'<link rel="stylesheet" href="assets/css/{name}.css">', "")
    page = re.sub(
        r'<link rel="stylesheet" href="https://cdn\.jsdelivr\.net/gh/rastikerdar'
        r'/vazirmatn[^>]*>\n?',
        "",
        page,
    )
    page = page.replace('<link rel="manifest" href="manifest.json">\n', "")
    page = page.replace('<link rel="manifest" href="manifest.json">', "")
    return page


def _normalise(page: str) -> str:
    # Removing seven link tags leaves runs of blank lines behind. Those runs are
    # the only difference between the two files, and they carry no meaning.
    return re.sub(r"\n{2,}", "\n", page.strip())


def test_the_standalone_file_is_the_dashboard_with_its_assets_inlined() -> None:
    """The only difference allowed is the inlining itself."""
    source = _normalise(_deinline(INDEX.read_text(encoding="utf-8")))
    artifact = _normalise(_deinline(STANDALONE.read_text(encoding="utf-8")))
    assert artifact == source, (
        "nobitex-mobile.html has diverged from index.html. It is generated — "
        "run: python3 scripts/build_standalone.py (never edit it by hand)"
    )


def test_the_inlined_stylesheets_are_exactly_the_five_source_files() -> None:
    """A hand-edited rule inside the artifact is invisible until the next build."""
    page = STANDALONE.read_text(encoding="utf-8")
    block = re.search(r'<style id="nbx-standalone-css">\n(.*)\n</style>', page, re.S)
    assert block, "the artifact has no inlined stylesheet block"
    expected = "\n\n".join(
        f"/* ---- {name}.css ---- */\n"
        + (ROOT / "assets" / "css" / f"{name}.css").read_text(encoding="utf-8")
        for name in CSS_FILES
    )
    assert block.group(1) == expected, (
        "the inlined CSS does not match assets/css/*.css — rebuild the page"
    )


def test_the_inlined_javascript_is_exactly_the_two_engines() -> None:
    """The engines must be byte-identical, or the two pages compute differently."""
    page = STANDALONE.read_text(encoding="utf-8")
    for js in JS_FILES:
        code = (ROOT / js).read_text(encoding="utf-8")
        marker = f'<script id="nbx-inline-{js.replace(".", "-")}">\n{code}\n</script>'
        assert marker in page, f"{js} is missing, truncated or edited inside the artifact"


def test_the_page_ships_no_mobile_only_markup() -> None:
    """One responsive page: no second layout hiding behind a width check.

    `index.html` is the source for both form factors — the Tailwind `md:`/`sm:`
    variants and `injectMobileNavAndDownload()` adapt it. A second, hand-written
    mobile document would be a second thing to fix, and only one of them would
    get fixed.
    """
    source = INDEX.read_text(encoding="utf-8")
    assert "md:" in source and "sm:" in source, "index.html has no responsive variants"
    assert "injectMobileNavAndDownload" in (ROOT / "nbx-app.js").read_text(encoding="utf-8")
    # The artifact may only contain what its sources contain.
    assert _normalise(_deinline(STANDALONE.read_text(encoding="utf-8"))) == _normalise(
        _deinline(source)
    )


def test_the_dashboard_asks_for_online_coin_metadata() -> None:
    """Both layers of the icon system are live on the same page.

    The bundled SVGs (assets/coins, inlined as data URIs) cover the common coins
    offline. The online layer lives behind the server's disk-only cache — the
    endpoint is `/api/coins` and the images are `/api/coins/{id}/logo` — and it
    is what covers a curated coin with no bundled logo (TON, SHIB, NEAR, AXS,
    EGLD, HBAR, APT, ARB, OP, PEPE today). If the call disappears, those coins
    silently fall back to a three-letter badge and nobody notices: a cosmetic
    regression with no error attached to it.
    """
    app = (ROOT / "nbx-app.js").read_text(encoding="utf-8")
    page = INDEX.read_text(encoding="utf-8")
    assert "function loadCoinMetadata" in app
    assert "window.setCoinMetadata(data.coins" in app
    assert "/coins" in app
    # Defined here, consumed there: the page must actually load the script.
    assert '<script src="nbx-app.js"></script>' in page
    assert "window.setCoinMetadata = function setCoinMetadata" in page
    # Polled, so a coin added to the registry appears without a redeploy... and
    # slow enough that it is not a request per tab switch.
    assert re.search(r"setInterval\(loadCoinMetadata,\s*60\s*\*\s*60\s*\*\s*1000\)", app)
    # Cosmetic: it must never gate the market data boot path.
    assert "try {\n      await NBX.boot();\n      loadCoinMetadata();" in app
