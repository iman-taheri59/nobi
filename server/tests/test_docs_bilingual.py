"""A documented pair exists in both languages, or says it does not.

The repository claims that every ADR is written in Persian *and* English, and the
README/CHANGELOG/release guide now claim the same. Both claims were false at some
point in ways nobody noticed: ADR-0017 had no English text at all, and ADR-0013
had two English files — a full translation plus a second file holding only an
amendment — while the Persian side kept the amendment inside the one file.

These tests are the difference between "we intend to keep them in sync" and
"the repository fails when we don't". They check structure (a pair exists, has a
switcher, has no duplicates) and the two version strings that a release claims.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FA = ROOT / "docs" / "adr" / "fa"
EN = ROOT / "docs" / "adr" / "en"

# Every flat document that is promised in two languages. The twin is the same
# path with `.en.md` instead of `.md`.
PAIRS = (
    ("README.md", "README.en.md"),
    ("docs/README.md", "docs/README.en.md"),
    ("docs/releases.md", "docs/releases.en.md"),
    ("docs/upgrade-2.0.1.md", "docs/upgrade-2.0.1.en.md"),
    ("CHANGELOG.md", "CHANGELOG.en.md"),
    ("docs/testing.md", "docs/testing.en.md"),
    ("docs/coin-metadata.md", "docs/coin-metadata.en.md"),
    ("docs/financial-display.md", "docs/financial-display.en.md"),
    ("docs/evidence.md", "docs/evidence.en.md"),
)

ADR_RE = re.compile(r"^ADR-(\d{4})-")


def _adr_numbers(folder: Path) -> list[str]:
    return sorted(m.group(1) for p in folder.glob("ADR-*.md") if (m := ADR_RE.match(p.name)))


def _plain_name(path: Path) -> str:
    """The filename as real Unicode, whatever the filesystem encoding is.

    Under a non-UTF-8 locale (a Linux box with LANG=C) `Path.name` returns
    surrogate-escaped characters, so comparing it against text read from the file
    fails for a reason that has nothing to do with the link being missing. Windows
    stores names as UTF-16 and was never affected; this makes the test honest on
    both.
    """
    return path.name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _adr_files(folder: Path) -> dict[str, Path]:
    return {m.group(1): p for p in folder.glob("ADR-*.md") if (m := ADR_RE.match(p.name))}


def test_every_adr_has_exactly_one_file_per_language():
    fa, en = _adr_numbers(FA), _adr_numbers(EN)
    assert len(fa) == len(_adr_files(FA)), f"duplicate ADR number in fa: {fa}"
    assert len(en) == len(_adr_files(EN)), f"duplicate ADR number in en: {en}"
    assert fa == en, (
        "ADR numbers differ between languages: "
        f"missing in en={sorted(set(fa) - set(en))}, missing in fa={sorted(set(en) - set(fa))}"
    )


def test_each_adr_links_to_its_other_language():
    fa, en = _adr_files(FA), _adr_files(EN)
    for number in sorted(fa):
        fa_text, en_text = fa[number].read_text(encoding="utf-8"), en[number].read_text(encoding="utf-8")
        assert _plain_name(en[number]) in fa_text, f"{_plain_name(fa[number])} does not link to its English twin"
        assert _plain_name(fa[number]) in en_text, f"{_plain_name(en[number])} does not link to its Persian twin"


def test_documented_pairs_exist_and_link_both_ways():
    for persian, english in PAIRS:
        fa_path, en_path = ROOT / persian, ROOT / english
        assert fa_path.exists(), f"missing {persian}"
        assert en_path.exists(), f"missing {english}"
        assert _plain_name(Path(english)) in fa_path.read_text(encoding="utf-8"), (
            f"{persian} does not link to {english}"
        )
        assert _plain_name(Path(persian)) in en_path.read_text(encoding="utf-8"), (
            f"{english} does not link to {persian}"
        )


def test_current_version_is_documented_in_both_languages():
    """The release-facing pages state the version; the changelogs head a section with it."""
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    for english in ("README.en.md", "docs/README.en.md", "docs/releases.en.md"):
        assert f"**{version}**" in (ROOT / english).read_text(encoding="utf-8"), (
            f"{english} does not carry the current version **{version}**"
        )
    for changelog in ("CHANGELOG.md", "CHANGELOG.en.md"):
        assert f"\n## {version}\n" in (ROOT / changelog).read_text(encoding="utf-8"), (
            f"{changelog} has no section for {version}"
        )


def test_persian_only_documents_are_declared_as_pending():
    """A new Persian-only doc must be admitted in the coverage table, not ignored.

    Adding `docs/new-thing.md` without an English twin is allowed — a translation
    that drifts is worse than none — but the documentation map has to say so.
    """
    declared = (ROOT / "docs" / "README.en.md").read_text(encoding="utf-8")
    missing = []
    for path in sorted((ROOT / "docs").glob("*.md")):
        if path.name == "README.md":
            continue
        if path.with_suffix(".en.md").exists():
            continue
        if f"`{path.name}`" not in declared:
            missing.append(path.name)
    assert not missing, (
        "these documents have no English twin and are not listed in the coverage "
        f"table of docs/README.en.md: {missing}"
    )
