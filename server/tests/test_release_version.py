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
