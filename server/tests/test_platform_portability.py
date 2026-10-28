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

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = Path(__file__).resolve().parent

#: Attributes that exist on POSIX and not on Windows. Only ones that are cheap to
#: remove are listed: anything pytest itself might need would be a false alarm.
POSIX_ONLY = ("geteuid", "getegid", "fork", "forkpty", "killpg", "setsid")

# platformdirs now ships an auto-loaded pytest plugin. On this simulated OS,
# sys.platform is still Linux, so importing that unrelated plugin tries to import
# the deliberately removed geteuid. Real Windows selects its Windows backend.
# Block only that plugin in the two child harnesses; all project modules and
# helper assertions still collect/run with POSIX attributes absent.


def test_the_suite_collects_without_posix_only_calls():
    code = (
        "import os, sys, pytest\n"
        f"for attr in {POSIX_ONLY!r}:\n"
        "    if hasattr(os, attr):\n"
        "        delattr(os, attr)\n"
        "sys.exit(pytest.main(['-q', '--collect-only', '-p', 'no:cacheprovider', '-p', 'no:platformdirs',"
        f" {str(TESTS)!r}]))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert out.returncode == 0, (
        "a test module needs a POSIX-only os attribute at import time; on Windows "
        "this aborts collection of the entire suite.\n"
        + (out.stdout[-3000:] + out.stderr[-3000:])
    )


#: Directories that are not this repository's code: a virtualenv on Windows lives
#: *inside* the checkout (`server\.venv`), so without this the scan reported fifty
#: offenders in pip, numpy and pytest.
SKIP_DIRS = {".venv", "venv", "site-packages", "node_modules", "__pycache__",
             "build", "dist", ".git", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def _python_sources() -> list[Path]:
    out: list[Path] = []
    for base in ((ROOT / "server"), (ROOT / "scripts")):
        for p in base.rglob("*.py"):
            if SKIP_DIRS & set(p.parts):
                continue
            out.append(p)
    return out


def _has_encoding(call: ast.Call) -> bool:
    """`encoding=` keyword, or a positional encoding as the first argument."""
    if any(k.arg == "encoding" for k in call.keywords):
        return True
    return bool(call.args) and isinstance(call.args[0], ast.Constant) and isinstance(
        call.args[0].value, str)


#: `.read_text()` / `.write_text()` without an encoding use the *locale* codec, which
#: on Windows is cp1252: `UnicodeDecodeError: 'charmap' codec can't decode byte 0x81`
#: on any file with Persian in it. The advisory Windows job found exactly that in
#: `test_release_version` and `test_github_release`, and `subprocess(..., text=True)`
#: had it too — a UTF-8 em dash came back as mojibake.
def test_no_file_is_read_or_written_with_the_locale_codec():
    offenders: list[str] = []
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            attr = getattr(node.func, "attr", "")
            if attr in ("read_text", "write_text") and not _has_encoding(node):
                what = ".read_text()" if attr == "read_text" else ".write_text()"
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: {what} without encoding=")
            if attr in ("run", "check_output", "Popen", "call", "check_call") and any(
                    k.arg == "text" and isinstance(k.value, ast.Constant) and k.value.value
                    for k in node.keywords) and not any(k.arg == "encoding" for k in node.keywords):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: "
                                 f"subprocess(text=True) without encoding=")
    assert not offenders, "decoded with the locale codec (cp1252 on Windows):\n" + "\n".join(offenders)


def test_helper_transport_suite_runs_without_posix_only_calls():
    """The installer-helper tests must execute, not just collect, on Windows.

    ``test_the_suite_collects_without_posix_only_calls`` only checks
    *collection*. The helper tests used ``monkeypatch.setattr(os, "geteuid")``
    at runtime, which passed on Linux and failed all seven of them on the
    Windows runner. Re-run that module with the POSIX-only attributes deleted.
    """
    code = (
        "import os, sys, pytest\n"
        f"for attr in {POSIX_ONLY!r}:\n"
        "    if hasattr(os, attr):\n"
        "        delattr(os, attr)\n"
        "sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider', '-p', 'no:platformdirs', "
        f"{str(TESTS / 'test_helper_transport.py')!r}]))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                         capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert out.returncode == 0, (
        "helper tests need a POSIX-only os attribute at runtime:\n"
        + out.stdout[-3000:] + out.stderr[-3000:]
    )
