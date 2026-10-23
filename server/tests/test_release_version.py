"""The release number must agree across backend and both dashboard builds."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_version_is_semver():
    assert re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", (ROOT / "VERSION").read_text().strip())


def test_backend_version_matches_release():
    import main

    assert main.app.version == (ROOT / "VERSION").read_text().strip()


def test_package_versions_match_release():
    version = (ROOT / "VERSION").read_text().strip()
    assert json.loads((ROOT / "package.json").read_text())["version"] == version
    lock = json.loads((ROOT / "package-lock.json").read_text())
    assert lock["version"] == lock["packages"][""]["version"] == version


def test_dashboard_versions_match_release():
    version = (ROOT / "VERSION").read_text().strip()
    for name in ("index.html", "nobitex-mobile.html"):
        html = (ROOT / name).read_text()
        badge = re.search(r'<span id="buildChip"[^>]*>(.*?)</span>', html)
        assert badge and badge.group(1) == f"v{version} · core"


def test_the_release_number_stays_inside_its_ceiling():
    """A ceiling is a contract, not a habit — see docs/releases.md.

    The 1.0 line may run its PATCH up to 30; from 1.1.0 on every field tops out at 10.
    The number of releases is irrelevant: what is capped is the value itself, so 1.0.30 is
    allowed and the release after it must be 1.1.0 rather than 1.0.31.
    """
    major, minor, patch = (int(p) for p in (ROOT / "VERSION").read_text().strip().split("."))
    patch_ceiling = 30 if (major, minor) == (1, 0) else 10
    assert major <= 10, f"MAJOR {major} is past its ceiling of 10"
    assert minor <= 10, f"MINOR {minor} is past its ceiling of 10"
    assert patch <= patch_ceiling, (
        f"PATCH {patch} is past its ceiling of {patch_ceiling}; "
        f"the next release is {major}.{minor + 1}.0")


def test_the_documented_ceilings_are_the_ones_the_test_enforces():
    """The table in the docs and the numbers here must not drift apart."""
    doc = (ROOT / "docs" / "releases.md").read_text(encoding="utf-8")
    assert "| ۳۰ |" in doc, "the 30 ceiling is no longer documented"
    assert "| ۱۰ |" in doc, "the 10 ceiling is no longer documented"
    en = (ROOT / "docs" / "releases.en.md").read_text(encoding="utf-8")
    assert "| 30 |" in en and "| 10 |" in en
