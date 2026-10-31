"""A number written into prose is a number nobody updates.

Three counts in this repository have drifted, each for the same reason: the
truth lived in code and the prose kept a copy of it.

* `rkAtr` was removed from the probe in 0bc4d3d because no element with that id
  exists in the page, taking the real count from 45 to 44 — while ci.yml's step
  name, both READMEs, the testing guides, the Windows guide and ADR-0016 all
  kept saying 45.
* The README said "CI has three jobs" while ci.yml has four, because the
  advisory Windows job was never added to the sentence.
* docs/testing said "expect 591 passed" while the suite had reached 1460.

These tests read each number from the place that cannot be wrong about it —
`probe_live.py` for panels, ci.yml for jobs — and fail when a living document
disagrees. The test-count number was removed from the docs rather than pinned,
because it grows with every test and any value would go stale again.

Dated review reports under docs/reviews/ and the changelogs are deliberately
excluded: they record what was true when they were written, and rewriting them
would falsify the record rather than correct it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "scripts" / "probe_live.py"
CI = ROOT / ".github/workflows/ci.yml"

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


# --------------------------------------------------------------------------- #
# The CI job count.
# --------------------------------------------------------------------------- #

def _ci_jobs() -> list[str]:
    workflow = yaml.load(CI.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return list(workflow["jobs"])


def test_the_readmes_name_every_ci_job() -> None:
    """The README used to say "three jobs" while ci.yml had four."""
    jobs = _ci_jobs()
    assert len(jobs) == 4, "ci.yml gained or lost a job — update both READMEs"
    for name in ("README.md", "README.en.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for job in jobs:
            assert f"`{job}" in text or "`windows (advisory)`" in text, (
                f"{name} does not mention the ci.yml job {job!r}"
            )


def test_the_readme_job_count_matches_ci() -> None:
    """Whatever the README claims, it has to be the number of jobs in ci.yml."""
    expected = len(_ci_jobs())
    # Both languages spell the number as a word here, not a digit.
    words = {2: {"two", "دو"}, 3: {"three", "سه"}, 4: {"four", "چهار"}, 5: {"five", "پنج"}}
    for name in ("README.md", "README.en.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        stated = re.findall(r"CI has (\w+) jobs?|CI (\S+) jobs? دارد", text)
        assert stated, f"{name} no longer states how many CI jobs there are"
        claimed = {w.lower() for pair in stated for w in pair if w}
        # Each README spells it in its own language, so a subset is enough —
        # but every word it does use has to be the right number.
        assert claimed and claimed <= words[expected], (
            f"{name} claims {claimed} CI jobs; ci.yml has {expected}"
        )
