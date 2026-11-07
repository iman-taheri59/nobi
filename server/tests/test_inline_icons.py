"""Every icon the dashboard names must exist in the inline set.

The page used to load `lucide@latest` from unpkg — an unpinned, un-SRI'd script
from a third party, i.e. remote code execution by whoever controls that path.
It is gone; `refreshIcons()` renders from `OFFLINE_ICONS` only. The cost of
that is a missing icon silently becoming a circle, so this test pins the set:
each `data-lucide="…"` in the markup and the JS bundles must have an entry.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ("index.html", "nbx-core.js", "nbx-app.js", "nbx-alerts.js")
NAME = r"([a-z0-9][a-z0-9-]*)"


def _used() -> set[str]:
    used: set[str] = set()
    for name in SOURCES:
        text = (ROOT / name).read_text(encoding="utf-8")
        used |= set(re.findall(r"data-lucide=\\?[\"']" + NAME, text))
        used |= set(re.findall(r"setAttribute\(['\"]data-lucide['\"],\s*['\"]" + NAME, text))
    return used


def _inline() -> set[str]:
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    start = html.index("OFFLINE_ICONS = {")
    block = html[start:html.index("};", start)]
    return set(re.findall(r"^\s*'" + NAME + r"':", block, re.M))


def test_every_named_icon_is_inline() -> None:
    missing = sorted(_used() - _inline())
    assert not missing, f"icons without an inline definition (would render as a circle): {missing}"


def test_no_icon_script_is_loaded_from_a_cdn() -> None:
    for name in ("index.html", "nobitex-mobile.html"):
        html = (ROOT / name).read_text(encoding="utf-8")
        assert "unpkg.com/lucide" not in html and "lucide@" not in html, name
