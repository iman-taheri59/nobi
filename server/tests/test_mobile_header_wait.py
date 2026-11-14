# mypy: check_untyped_defs = False
"""The sidebar assertion waits for a *settled* panel, and says what it saw.

The `ui` job has now gone red four times on the same step — 9 Nov, 9 Nov, 10 Nov
and 14 Nov — each time at `mobile_header_test.py`'s wait for
`transform === 'matrix(1, 0, 0, 1, 0, 0)'`, each time between 60 and 70 seconds
into a step whose timeout is 30, each time on `ltr … ADMIN … via button`, and
each time at a *different* width: 375, 390 and 480 px.

The assertion was the problem, not the layout. `#sidebar` is moved by a 300 ms
CSS transition:

    #sidebar { transition: transform .3s cubic-bezier(.22,1,.36,1); }
    #sidebar.side-open { transform: translateX(0) !important; }

`getComputedStyle().transform` during a transition returns the **interpolated**
matrix, so the exact open value only exists on the final frame. `wait_for_function`
polls with requestAnimationFrame, and on a loaded 2-core runner a frame can
arrive after the transition has already been replaced by the next one — for
example when the overlay's fade-out runs while the panel is already open. The
old wait could then sit at `matrix(1, 0, 0, 1, 35.829, 0)` for the whole 30 s and
fail on a sidebar that was visibly open.

Two assertions pin the fix: the wait is a settled-value predicate (which is true
throughout the transition, not only on its last frame), and a failure prints the
matrix it was stuck on, which is what three of those runs could not show.
"""

from __future__ import annotations

import ast
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mobile_header_test.py"
TEXT = SCRIPT.read_text(encoding="utf-8")


def _constant(name: str) -> str:
    """Read a module-level string constant without importing playwright."""
    tree = ast.parse(TEXT)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is gone from mobile_header_test.py")


def test_the_open_predicate_does_not_demand_the_final_matrix():
    """`=== matrix(1, 0, 0, 1, 0, 0)` is one frame of a 300 ms animation.

    The old string is kept out of the file: it was the assertion, and it is the
    reason a correct layout failed. This test is what stops it coming back.
    """
    assert "matrix(1, 0, 0, 1, 0, 0)" not in TEXT


def test_the_wait_accepts_an_interpolated_but_settled_transform():
    """The predicate has to be true for the whole open state, not its last frame."""
    predicate = _constant("SETTLED")
    assert "side-open" in predicate, "the predicate no longer checks the open state"
    assert "matrix" in predicate
    assert "v[4]" in predicate and "v[5]" in predicate, (
        "the translation components are the thing being settled; a predicate that "
        "ignores them would pass a half-open panel"
    )
    assert "getComputedStyle" in predicate


def test_a_failure_reports_what_is_on_screen_and_what_is_under_the_pointer():
    """A timeout must print the state it was stuck in.

    Evidence from the 14 November rerun, once the first version of this report
    landed: `transform='matrix(1, 0, 0, 1, -288, 0)' side-open=False ltr` — the
    panel was closed, not half-open. That one line moved the question from
    "which animation frame?" to "why did the click not open it?", which no log
    line had been able to ask before. The report therefore has to carry enough
    to answer that too: where the button is, what element is actually under the
    pointer, and whether the width is on the mobile side of the 1024 breakpoint.
    """
    for probe in ("the sidebar never settled open", "the sidebar never closed via",
                  "menuBtnCoveredBy", "innerWidth", "mediaMobile", "activeElement"):
        assert probe in TEXT, probe
    assert "def diagnose(" in TEXT
    # And it is used by both waits, not just the open one.
    assert TEXT.count("AssertionError(diagnose(") == 2


def test_the_settle_window_is_a_number_someone_can_check():
    """No magic 30 000 re-derived inside the loop."""
    assert "SIDEBAR_TIMEOUT_MS = 30_000" in TEXT
    assert TEXT.count("timeout=SIDEBAR_TIMEOUT_MS") == 2, (
        "open and close both need the same ceiling"
    )
