"""The documented panel count is the one the probe actually verifies.

This number has drifted twice. `rkAtr` was removed from the probe in 0bc4d3d
because no element with that id exists in the page, which took the real count
from 45 to 44 — and the step name in ci.yml kept saying "45 panels" until run
after run was checking a number nobody had updated. The same stale 45 was still
sitting in the README, the testing guides, the Windows guide and ADR-0016.

A count written into prose is a count nobody updates, so this test reads the
count from `probe_live.py` — the only place that can be wrong about how many
panels exist — and fails if any living document disagrees.

Dated review reports under docs/reviews/ and the changelogs are deliberately
excluded: they record what was true when they were written, and rewriting them
would falsify the record rather than correct it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "scripts" / "probe_live.py"

#: Documents that describe the repository as it is now.
LIVING_DOCS = (
    "README.md",
    "README.en.md",
    "docs/run-windows.md",
    "docs/testing.md",
    "docs/testing.en.md",
    "docs/adr/en/ADR-0016-static-gates-and-ci.md",
    "docs/adr/fa/ADR-0016-دروازه‌های-ایستا-و-CI.md",
    ".github/workflows/ci.yml",
)

#: How each language spells "45 panels" — the stale value to hunt for.
STALE = ("45 panels", "۴۵ پنل")


def _panel_count() -> int:
    match = re.search(r"^PANELS[^=]*= (\{.*?\n\})", PROBE.read_text(encoding="utf-8"), re.S | re.M)
    assert match, "PANELS is no longer a literal in probe_live.py"
    panels = ast.literal_eval(match.group(1))
    return sum(len(tabs) for tabs in panels.values())


def test_the_probe_still_declares_its_panels_as_a_literal() -> None:
    """If PANELS stops being a literal, every count below becomes unverifiable."""
    assert re.search(
        r"^PANELS[^=]*= \{", PROBE.read_text(encoding="utf-8"), re.M
    ), "PANELS is computed, so its count can no longer be read statically"


def test_a_living_document_states_the_count_at_all() -> None:
    """Otherwise the assertion below would pass on documents that say nothing."""
    stated = [d for d in LIVING_DOCS if re.search(r"\d+ panels|[\u06f0-\u06f9]+ پنل",
                                                  (ROOT / d).read_text(encoding="utf-8"))]
    assert stated, "no living document states a panel count any more"


@pytest.mark.parametrize("name", LIVING_DOCS)
def test_no_living_document_carries_the_stale_count(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    found = [phrase for phrase in STALE if phrase in text]
    assert not found, (
        f"{name} still says {found}; the probe verifies {_panel_count()} panels. "
        "Update the document, not the probe — unless a panel really was removed."
    )


def test_the_documented_count_matches_the_probe() -> None:
    expected = _panel_count()
    assert expected == 44, "the probe's own count moved — check these documents"
    # Persian documents spell the number in Persian digits, English ones in
    # Latin; either spelling is accepted, the stale one is not.
    fa_digits = str(expected).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    accepted = (f"{expected} panels", f"{fa_digits} پنل")
    for name in LIVING_DOCS:
        text = (ROOT / name).read_text(encoding="utf-8")
        if not re.search(r"\d+ panels|[\u06f0-\u06f9]+ پنل", text):
            continue
        assert any(want in text for want in accepted), (
            f"{name} states a panel count but not {accepted[0]} / {accepted[1]}"
        )
