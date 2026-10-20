"""A machine that cannot download Chromium must still be able to run the UI tests.

`python -m playwright install chromium` fetches one pinned revision from
Playwright's CDN. When a network blocks that domain, the three UI scripts
(`probe_live`, `layout_test`, `fa_label_test`) can instead use the browser the
machine already has (`--channel msedge`) or any binary the user unpacked
(`--executable`). Two things are easy to get wrong, so both are pinned here:

* the **package** and the **browser** are different failures. A bare `python`
  on Windows is often not the project's venv, so `pip install playwright` in
  one shell and `python scripts/probe_live.py` in another reach different
  site-packages. The old message answered that with a browser download —
  accurate about the missing browser, useless about the cause, and misleading
  about where to install anything.

* a help text that offers a flag the script never actually passes to
  `launch()`. `--help` would look right and the flag would do nothing.

The subprocess tests run each script with a `playwright` on `PYTHONPATH` that
raises ImportError — the exact state the bug report came from — and demand exit
code 2, the interpreter's own path, and no traceback.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
UI_SCRIPTS = ["probe_live.py", "layout_test.py", "fa_label_test.py"]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def bl() -> ModuleType:
    return _load("browser_launch")


def _run(script: str, *args: str, extra_env: dict[str, str] | None = None
         ) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("NBX_CHROME", None)         # the developer's own setting must not decide a test
    env["PYTHONIOENCODING"] = "utf-8"   # `--help` carries Persian text
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, env=env, cwd=str(SCRIPTS.parent), check=False,
    )


# --------------------------------------------------------------------------
# Where the browser comes from
# --------------------------------------------------------------------------
def test_default_launch_is_playwrights_own_download(bl: ModuleType,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NBX_CHROME", raising=False)
    assert bl.launch_args() == {}


def test_a_channel_uses_the_installed_browser(bl: ModuleType,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NBX_CHROME", raising=False)
    assert bl.launch_args("msedge") == {"channel": "msedge"}


def test_an_explicit_path_beats_a_channel(bl: ModuleType) -> None:
    assert bl.launch_args("msedge", sys.executable) == {"executable_path": sys.executable}


def test_the_environment_variable_is_enough(bl: ModuleType,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NBX_CHROME", sys.executable)
    assert bl.launch_args() == {"executable_path": sys.executable}


def test_a_path_typo_exits_2_and_names_the_path(bl: ModuleType, tmp_path: Path,
                                                capsys: pytest.CaptureFixture[str],
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo must not resurface as a Playwright error three frames down."""
    monkeypatch.delenv("NBX_CHROME", raising=False)
    missing = tmp_path / "chrome.exe"
    with pytest.raises(SystemExit) as caught:
        bl.launch_args(executable=str(missing))
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert str(missing) in err
    assert "does not exist" in err


# --------------------------------------------------------------------------
# What the two messages say
# --------------------------------------------------------------------------
def test_missing_package_message_fits_the_cause(bl: ModuleType) -> None:
    msg = bl.missing_package_message()
    # "which python am I?" — the question the old message could not answer.
    assert sys.executable in msg
    assert "pip install playwright" in msg
    # ...and the answer is not only a browser download.
    assert "--channel msedge" in msg
    assert "--executable" in msg


def test_launch_failure_message_is_one_line_plus_the_three_ways(bl: ModuleType) -> None:
    msg = bl.launch_failed_message(RuntimeError("boom\nsecond line\nthird"))
    assert "boom" in msg
    assert "second line" not in msg          # a traceback is not a diagnosis
    for way in ("playwright install chromium", "--channel msedge", "--executable"):
        assert way in msg


# --------------------------------------------------------------------------
# The three scripts, run the way a person runs them
# --------------------------------------------------------------------------
@pytest.mark.parametrize("script", UI_SCRIPTS)
def test_every_ui_script_offers_a_browser_flag(script: str) -> None:
    proc = _run(script, "--help")
    assert proc.returncode == 0, proc.stderr
    assert "--channel" in proc.stdout
    assert "--executable" in proc.stdout


@pytest.mark.parametrize("script", UI_SCRIPTS)
def test_a_missing_package_says_so_and_exits_2(script: str, tmp_path: Path) -> None:
    """The reported failure: no `playwright` importable in this interpreter."""
    stub = tmp_path / "stub"
    (stub / "playwright").mkdir(parents=True)
    (stub / "playwright" / "__init__.py").write_text(
        "raise ImportError('simulated: not installed for this interpreter')\n",
        encoding="utf-8")

    proc = _run(script, extra_env={"PYTHONPATH": str(stub)})
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert "Traceback" not in proc.stderr
    assert sys.executable in proc.stderr
    assert "pip install playwright" in proc.stderr


@pytest.mark.parametrize("script", UI_SCRIPTS)
def test_no_script_launches_a_browser_without_the_flags(script: str) -> None:
    """A bare `chromium.launch()` cannot be redirected to an installed browser."""
    src = (SCRIPTS / script).read_text(encoding="utf-8")
    assert ".launch()" not in src
    assert "launch(**launch_args(" in src
