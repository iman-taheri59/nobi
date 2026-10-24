"""The signals list draws a first page, not the whole scan — and says so.

Until 1.1.2 the renderer held a bare `.slice(0, 12)`:

    list.innerHTML = ranked.slice(0, 12).map(...)

so the strip above the list could read "39 symbols scanned" while rows 13..39
had no row at all — no score, no detail, no link — even though the backend had
scored them and the shadow gates had already judged them. Nobody could tell
whether the scan was small or the page was short.

These tests guard the shape of the fix, not its pixels: the limit is a named
constant, the renderer uses it, there is a control that lifts it and says how
many rows are hidden, and both languages can say it. A rendered-DOM test would
be better and is not attempted here — `nbx-app.js` expects the whole dashboard
around it, and a half-mocked page would pass while the real one broke.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "nbx-app.js"


@pytest.fixture(scope="module")
def source() -> str:
    return APP.read_text(encoding="utf-8")


def test_the_limit_is_a_named_constant(source):
    assert re.search(r"const SIG_ROW_LIMIT = \d+;", source), (
        "the first-page size must be a named constant, not a literal in the renderer"
    )


def test_the_renderer_no_longer_slices_with_a_magic_number(source):
    """The exact defect: a literal 12 between `ranked` and `.map(`."""
    assert not re.search(r"list\.innerHTML = ranked\s*\n?\s*\.slice\(0, \d+\)", source)
    assert "ranked.slice(0, SIG_ROW_LIMIT)" in source


def test_the_hidden_count_is_computed_not_guessed(source):
    assert re.search(r"const hidden = Math\.max\(0, ranked\.length - SIG_ROW_LIMIT\);", source)


def test_there_is_a_control_that_lifts_the_limit_and_names_the_total(source):
    assert "window.sigShowAll" in source
    assert "inFa('sg.showAll').replace('{n}', faNum(ranked.length))" in source, (
        "the control must say how many rows exist, otherwise it hides the same fact"
    )


def test_show_all_prefetches_the_sparklines_it_reveals(source):
    """Rows past the first page had no chart data: the chart is fixed, not left blank."""
    body = source[source.index("window.sigShowAll"):]
    body = body[:body.index("function renderSignalsLive")]
    assert "prefetchSparkCandles(rest, live.timeframe)" in body


def _code_only(text: str) -> str:
    """Source with comments removed — the comment here *quotes* the old defect."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def test_the_sparkline_budget_follows_the_same_constant(source):
    code = _code_only(source)
    assert ".slice(0, 12)" not in code, "a second copy of the number will drift from the first"
    assert code.count("slice(0, SIG_ROW_LIMIT)") >= 3


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_both_languages_can_say_it(source, lang):
    block = source[source.index(f"    {lang}: {{"):]
    block = block[:block.index("\n    },")]
    for key in ("'sg.showAll'", "'sg.showLess'"):
        assert key in block, f"{lang} is missing {key} — a missing key renders its own name"
