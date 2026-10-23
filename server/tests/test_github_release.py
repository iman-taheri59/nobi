"""Release preparation stays deterministic and never calls GitHub in tests."""
from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_notes.py"


def test_release_notes_match_current_version():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--tag", f"v{version}"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    assert result.stdout.strip()
    assert "## " not in result.stdout
    assert result.stdout.strip() in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_mismatched_tag_is_rejected():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--tag", "v999.0.0"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 1
    assert "does not match VERSION" in result.stderr
    assert not result.stdout


@pytest.mark.parametrize("text", ["", "## 1.2.3\n\n", "## 1.2.2\nother notes\n"])
def test_missing_release_notes_rejected(text):
    extract = runpy.run_path(str(SCRIPT))["extract_notes"]
    with pytest.raises(ValueError, match="Missing changelog"):
        extract(text, "1.2.3")


def test_extract_only_requested_section():
    extract = runpy.run_path(str(SCRIPT))["extract_notes"]
    assert extract("# Changes\n## 1.2.3\nnew notes\n\n## 1.2.2\nold\n", "1.2.3") == "new notes\n"


def test_release_workflow_requires_validation_before_publishing():
    workflow = yaml.load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert list(workflow["on"]) == ["push"]
    assert "tags" in workflow["on"]["push"]
    assert workflow["permissions"] == {"contents": "read"}
    publish = workflow["jobs"]["publish"]
    assert publish["needs"] == "validate"
    assert publish["permissions"] == {"contents": "write"}
    command = publish["steps"][-1]["run"]
    assert "--verify-tag" in command
    assert "--notes-file" in command
    assert "nobitrader-mobile.html" in command


def test_current_version_is_documented():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    for name in ("README.md", "docs/README.md", "docs/releases.md"):
        assert f"**{version}**" in (ROOT / name).read_text(encoding="utf-8")
