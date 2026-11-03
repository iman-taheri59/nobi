# mypy: check_untyped_defs = False
"""The analysis catalogues and review reports must stay paired and structurally aligned.

A translation is allowed to change prose, but it must not quietly drop a section,
a table row, a code block, or the link back to the source language. These checks
are deliberately structural: they do not claim to verify the trading logic or to
re-date a historical review.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]

REPORT_STEMS = (
    "nobi-review",
    "capoy-review",
    "capoy-auth-review",
    "capoy-owner-probe-guide",
    "nobi-desk-queue-review",
    "capoy-owner-probe-analysis",
)
CATALOGUE_STEMS = (
    "docs/analysis/technical-analysis-capabilities",
    "docs/analysis/fundamental-analysis-capabilities",
)
PAIRS = tuple((stem + ".md", stem + ".en.md") for stem in REPORT_STEMS + CATALOGUE_STEMS)
PERSIAN = re.compile(r"[\u0600-\u06ff]")
HEADING = re.compile(r"^(#{1,6})\s+", re.MULTILINE)
FENCE = re.compile(r"^```([^\n]*)$", re.MULTILINE)
TABLE_ROW = re.compile(r"^\|.*\|\s*$", re.MULTILINE)
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _structure(text: str) -> tuple[list[int], list[str], int]:
    """Heading levels, fence markers, and table-row count, in source order."""
    return (
        [len(m.group(1)) for m in HEADING.finditer(text)],
        [m.group(1).strip() for m in FENCE.finditer(text)],
        len(TABLE_ROW.findall(text)),
    )


@pytest.mark.parametrize("persian,english", PAIRS)
def test_analysis_pair_exists_and_links_both_ways(persian: str, english: str) -> None:
    fa_path, en_path = ROOT / persian, ROOT / english
    assert fa_path.is_file(), f"missing source document: {persian}"
    assert en_path.is_file(), f"missing English translation: {english}"
    assert Path(english).name in _read(persian), f"{persian} does not link to {english}"
    assert Path(persian).name in _read(english), f"{english} does not link to {persian}"


@pytest.mark.parametrize("persian,english", PAIRS)
def test_translation_preserves_document_structure(persian: str, english: str) -> None:
    fa, en = _structure(_read(persian)), _structure(_read(english))
    assert fa[0] == en[0], f"heading levels differ: {persian} -> {english}"
    assert fa[1] == en[1], f"code-fence markers differ: {persian} -> {english}"
    assert fa[2] == en[2], f"table-row count differs: {persian} -> {english}"


@pytest.mark.parametrize("persian,english", PAIRS)
def test_english_translation_has_no_untranslated_persian_prose(persian: str, english: str) -> None:
    text = _read(english)
    # The language-switcher label is intentionally Persian.
    text = re.sub(r"\[فارسی\]\([^)]*\)", "", text)
    assert not PERSIAN.search(text), f"untranslated Persian text remains in {english}"


@pytest.mark.parametrize("persian,english", PAIRS)
def test_local_links_in_the_pair_resolve(persian: str, english: str) -> None:
    for name in (persian, english):
        base = (ROOT / name).parent
        for target in LINK.findall(_read(name)):
            parts = urlsplit(target.strip())
            if parts.scheme or parts.netloc or not parts.path:
                continue
            resolved = (base / unquote(parts.path)).resolve()
            assert resolved.exists(), f"broken local link in {name}: {target}"


def test_english_report_index_links_every_report() -> None:
    index = _read("analysis-reports.en.md")
    for stem in REPORT_STEMS:
        assert f"]({stem}.en.md)" in index, f"index is missing {stem}.en.md"
        assert f"]({stem}.md)" in index, f"index is missing the Persian source {stem}.md"
    for stem in CATALOGUE_STEMS:
        assert f"]({stem}.en.md)" in index, f"index is missing {stem}.en.md"
