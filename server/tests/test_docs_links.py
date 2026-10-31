"""A living document may not point at a file that does not exist.

Two such references were found by sweeping the docs: `docs/deploy-arvan.md`
told the reader to run `preflight_arvan.sh` before buying a server, and
ADR-0025 said new symbol mappings are resolved by `scripts/resolve_fa.py`.
Neither script has ever existed in this repository — `git log --all` on both
paths is empty. The first sends a reader looking for a tool that is not there;
the second describes a workflow nobody can follow.

This test reads every backticked path in the living documents and fails if the
file is missing. Historical records are excluded on purpose: `CHANGELOG*.md`
and `docs/reviews/` describe what was true when they were written, and a path
that was correct then is not a defect now.

A small allowlist covers references that are legitimately absent:
`server/.env` and friends are created by the operator, `paper_state.json` is
written at runtime, and the evidence filename is a template with a date in it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Documents that describe the repository as it is now.
LIVING_DOCS = sorted(
    p for p in list(ROOT.glob("*.md"))
    + list(ROOT.glob("docs/*.md"))
    + list(ROOT.glob("docs/adr/**/*.md"))
    + [ROOT / "server" / "README.md"]
    if "reviews" not in p.parts and not p.name.startswith("CHANGELOG")
)

#: Paths that are legitimately absent from a checkout.
ALLOWED_ABSENT = {
    "server/.env",                  # the operator creates it
    "server/paper_state.json",      # written at runtime
    "nobi-installer.socket/.service",  # two unit names written as one shorthand
}

#: A backticked path with at least one directory component and a known extension.
PATH_RE = re.compile(
    r"`([A-Za-z0-9_][A-Za-z0-9_./-]*/[A-Za-z0-9_./-]*"
    r"\.(?:py|js|cjs|sh|html|json|yml|yaml|css|service|socket|conf|env|sudoers|md))`"
)


def _referenced_paths(text: str) -> set[str]:
    refs = set()
    for match in PATH_RE.finditer(text):
        ref = match.group(1)
        if ref.startswith(("http", "/")) or "*" in ref or "{" in ref:
            continue
        # A template filename, e.g. adaptive-evidence-YYYY-MM-DD.json
        if re.search(r"YYYY|MM|DD|<|>", ref):
            continue
        refs.add(ref)
    return refs


@pytest.mark.parametrize("doc", LIVING_DOCS, ids=lambda p: p.name)
def test_no_living_document_references_a_missing_file(doc: Path) -> None:
    missing = sorted(
        ref for ref in _referenced_paths(doc.read_text(encoding="utf-8"))
        if ref not in ALLOWED_ABSENT
        and not (doc.parent / ref).exists()
        and not (ROOT / ref).exists()
    )
    assert not missing, (
        f"{doc.relative_to(ROOT)} references files that do not exist: {missing}. "
        "Either the file was never written — fix the document — or it was "
        "removed and the reference should go with it."
    )


def test_the_sweep_actually_finds_something() -> None:
    """A test over a list of documents must not pass because the list is empty."""
    assert len(LIVING_DOCS) > 20, "the living-document list looks too short"
    refs = {r for doc in LIVING_DOCS for r in _referenced_paths(doc.read_text(encoding="utf-8"))}
    assert len(refs) > 50, "too few path references are being seen"
