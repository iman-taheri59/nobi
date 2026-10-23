"""Validate a release tag and emit its changelog section for GitHub Releases."""
from __future__ import annotations

import sys

#: The CHANGELOG is Persian. On Windows the console encoding is cp1252, so printing
#: one of those lines raised `UnicodeEncodeError: 'charmap' codec can't encode` and
#: the release check "failed" for a reason that had nothing to do with the release.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def extract_notes(changelog: str, version: str) -> str:
    match = re.search(
        rf"^## {re.escape(version)}\s*\n(.*?)(?=^## |\Z)",
        changelog,
        re.MULTILINE | re.DOTALL,
    )
    if not match or not match.group(1).strip():
        raise ValueError(f"Missing changelog notes for {version}")
    return match.group(1).strip() + "\n"


def release_notes(root: Path, tag: str | None = None) -> str:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise ValueError("VERSION must be a stable semantic version")
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"Tag {tag!r} does not match VERSION v{version}")
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
    if not package["version"] == lock["version"] == lock["packages"][""]["version"] == version:
        raise ValueError("Package versions do not match VERSION")
    for name in ("index.html", "nobitex-mobile.html"):
        badge = re.search(
            r'<span id="buildChip"[^>]*>(.*?)</span>',
            (root / name).read_text(encoding="utf-8"),
        )
        if not badge or badge.group(1) != f"v{version} · core":
            raise ValueError(f"Stale version badge in {name}")
    return extract_notes((root / "CHANGELOG.md").read_text(encoding="utf-8"), version)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="GitHub release tag, e.g. v1.0.1")
    args = parser.parse_args()
    try:
        print(release_notes(ROOT, args.tag), end="")
    except ValueError as exc:
        parser.exit(1, f"Release validation failed: {exc}\n")


if __name__ == "__main__":
    main()
