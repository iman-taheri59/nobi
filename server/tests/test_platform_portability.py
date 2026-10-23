# mypy: check_untyped_defs = False
"""The suite must be *collectable* on a machine without POSIX-only calls.

Why this file exists: CI runs on Linux, so a test module that calls
`os.geteuid()` at import time passed every gate for months while aborting the
whole run on Windows —

    ERROR server/tests/test_deploy_hardening.py
    AttributeError: module 'os' has no attribute 'geteuid'

That is exactly the machine `docs/run-windows.md` documents (Laragon), and the
machine the dashboard's own "project tests" card runs on. One module raised
during collection, so `pytest` reported *one error and zero tests*: the card
went red and said nothing about the other 816 tests.

A Windows CI job is the real guard (see `.github/workflows/ci.yml`). This test
is the cheap one that works on every machine: it deletes the POSIX-only
attributes from a subprocess's `os` module and asks pytest to collect. If a
module touches one of them at import, collection fails here — on Linux — with
the same message a Windows user would have seen.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = Path(__file__).resolve().parent

#: Attributes that exist on POSIX and not on Windows. Only ones that are cheap to
#: remove are listed: anything pytest itself might need would be a false alarm.
POSIX_ONLY = ("geteuid", "getegid", "fork", "forkpty", "killpg", "setsid")


def test_the_suite_collects_without_posix_only_calls():
    code = (
        "import os, sys, pytest\n"
        f"for attr in {POSIX_ONLY!r}:\n"
        "    if hasattr(os, attr):\n"
        "        delattr(os, attr)\n"
        "sys.exit(pytest.main(['-q', '--collect-only', '-p', 'no:cacheprovider',"
        f" {str(TESTS)!r}]))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, (
        "a test module needs a POSIX-only os attribute at import time; on Windows "
        "this aborts collection of the entire suite.\n"
        + (out.stdout[-3000:] + out.stderr[-3000:])
    )
