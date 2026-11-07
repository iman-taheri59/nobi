"""The exec bit must live in Git, not only in one working tree.

`c4a2b73` dropped mode 100755 from every tracked file and the clean-clone
tests only noticed through `os.stat`, which a local `chmod` silences. This
test asks the index instead: a script that must run from a fresh clone must
be recorded as 100755 in `git ls-files -s`.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MUST_BE_EXECUTABLE = (
    "scripts/update_arvan.sh",
    "scripts/deploy_arvan.sh",
    "scripts/resolve_fa.py",
    "deploy/nobi-privileged-helper",
    "deploy/nobi-helper-client",
)


def _index_modes() -> dict[str, str] | None:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        return None
    out = subprocess.run(["git", "ls-files", "-s", *MUST_BE_EXECUTABLE], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    modes: dict[str, str] = {}
    for line in out.splitlines():
        meta, _, path = line.partition("\t")
        modes[path] = meta.split()[0]
    return modes


def test_operational_scripts_are_executable_in_the_git_index() -> None:
    modes = _index_modes()
    if modes is None:
        pytest.skip("not a git checkout")
    wrong = {p: modes.get(p, "untracked") for p in MUST_BE_EXECUTABLE if modes.get(p) != "100755"}
    assert not wrong, f"not 100755 in the index (fix: git update-index --chmod=+x): {wrong}"
