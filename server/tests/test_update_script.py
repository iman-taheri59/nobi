# mypy: check_untyped_defs = False
"""The ArvanCloud update script must do the boring things a human forgets.

`scripts/deploy_arvan.sh` installs the dashboard and leaves the update as three
commands in the guide. `scripts/update_arvan.sh` is those three commands plus
the guards around them. Each guard below was added because a measured run got
it wrong, so each one is pinned by a test that fails if the guard is removed.

The tests are text checks and `bash -n` — the same approach as
`test_deploy_tls_order.py` — because the script needs root, a systemd host and a
git remote, none of which the test machine has. Where a guard's *behaviour* was
verified, the commit message records the measured run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "update_arvan.sh"
INSTALLER = ROOT / "scripts" / "deploy_arvan.sh"
ENV_FILE = "nobi.env"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _code_without_comments() -> str:
    """Comments describe intent; only executable lines count."""
    return "\n".join(
        line for line in _text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


# --------------------------------------------------------------------------- #
# It is a real shell script.
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="`bash -n` needs a POSIX shell",
)
def test_the_script_is_valid_bash() -> None:
    out = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True,
                         text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr


def test_the_script_is_executable() -> None:
    assert SCRIPT.stat().st_mode & 0o111


def test_it_follows_the_installers_shape() -> None:
    """Same helpers, same --help, same 'fail before touching anything' opening.

    A second ops script that reads differently from the first is a script
    nobody trusts enough to run unattended.
    """
    installer = INSTALLER.read_text(encoding="utf-8")
    for marker in ("set -euo pipefail", "step()", "warn()", "die()", "have()"):
        assert marker in _text(), f"{marker} disappeared from the update script"
    # --help prints the header comment, and only the header comment.
    assert "sed -n '2,20p'" in _text()
    assert _text().count("sed -n '2,20p'") == installer.count("sed -n '2,20p'") == 1
    # The precondition block comes before anything that writes.
    assert _text().find('step "checking the machine"') < _text().find('step "updating the checkout"')


def test_help_prints_only_the_header_comment() -> None:
    """The header is 19 comment lines; a 20th would leak `set -euo pipefail`."""
    out = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True,
                         text=True, encoding="utf-8", check=True)
    assert out.returncode == 0
    assert "set -euo pipefail" not in out.stdout
    assert "APP_DIR=" not in out.stdout
    assert "sudo bash scripts/update_arvan.sh" in out.stdout


def test_an_unknown_argument_is_refused() -> None:
    out = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True,
                         text=True, encoding="utf-8")
    assert out.returncode != 0
    assert "unknown argument" in out.stderr


# --------------------------------------------------------------------------- #
# What it must never touch.
# --------------------------------------------------------------------------- #

def test_it_never_writes_the_env_file() -> None:
    """The token lives in nobi.env; an update that rewrites it locks everyone out.

    The installer is explicit about this too ("The .env is never overwritten"),
    and the update must be at least as careful — it runs far more often.
    """
    written = [
        line for line in _code_without_comments().splitlines()
        if "ENV_FILE" in line and (">" in line or ">>" in line or "tee " in line)
    ]
    assert not written, f"the env file must only be read: {written}"
    assert "ENV_FILE=" in _text(), "the env file is not mentioned — did it get renamed?"


def test_it_never_touches_the_data_directory() -> None:
    """journal.db, auth.db and the caches live outside the checkout on purpose.

    `git clean -fd` is the dangerous one: without the excludes it would delete
    them, and `--reset` runs it.
    """
    text = _text()
    assert "clean -fd" in text, "the --reset path should clean untracked files"
    start = next(i for i, ln in enumerate(text.splitlines()) if "clean -fd" in ln)
    # the command spans a backslash continuation; take it whole
    clean_cmd = text.splitlines()[start]
    i = start
    while clean_cmd.rstrip().endswith("\\") and i + 1 < len(text.splitlines()):
        i += 1
        clean_cmd += " " + text.splitlines()[i].strip().lstrip("-e ")
    for keep in ("*.db", "*.db-*", "*.jsonl", "*.token"):
        assert keep in clean_cmd, f"git clean would delete {keep}"
    for line in _code_without_comments().splitlines():
        assert not line.lstrip().startswith("rm -rf"), (
            "an update script has no reason to rm -rf anything"
        )


def test_the_backup_is_opt_in_not_automatic() -> None:
    """A silent tar of a growing database is a surprise, not a safety net."""
    assert "--backup" in _text()
    assert "DO_BACKUP=0" in _text(), "backup must default to off"


# --------------------------------------------------------------------------- #
# The guards, each one earned by a failure.
# --------------------------------------------------------------------------- #

def test_a_dirty_checkout_is_refused_not_warned_about() -> None:
    """`git merge --ff-only` fails on a dirty tree, so the update dies anyway.

    Saying why — and offering stash, commit, or --reset — turns a confusing
    fatal into a decision the operator can make.
    """
    text = _text()
    assert 'status --porcelain' in text
    assert "refusing to update a dirty checkout" in text
    # The refusal must come before the merge, not after it.
    assert text.find("refusing to update a dirty checkout") < text.find("merge --ff-only")


def test_local_commits_are_counted_before_reset_discards_them() -> None:
    """Measured: a diverged checkout with one local commit.

    `--reset` dropped that commit with no warning at all, because
    `status --porcelain` only sees *uncommitted* work. `rev-list --count
    origin/BRANCH..HEAD` sees the rest.
    """
    text = _text()
    assert "rev-list --count" in text
    assert "origin/$BRANCH..HEAD" in text
    assert "will DISCARD the commit(s) listed above" in text


def test_the_fast_forward_refusal_names_the_way_out() -> None:
    text = _text()
    assert "fast-forward refused" in text
    assert "--reset" in text


def test_a_diverged_history_cannot_be_fixed_by_ff_only() -> None:
    """The documented fork/branch path is fetch + reset, not pull."""
    text = _text()
    assert "reset --hard" in text
    assert "merge --ff-only" in text


def test_the_systemd_unit_is_refreshed_when_the_checkout_changes_it() -> None:
    """A hardening fix to nobi.service is invisible to `git pull`.

    The running unit is the copy in /etc, not the one in the checkout, so
    without this step such a fix never reaches a deployed host.
    """
    text = _text()
    assert "cmp -s" in text, "the installed unit must be compared, not assumed current"
    assert "UNIT_DIR" in text
    assert "daemon-reload" in text


def test_the_health_check_happens_after_the_restart() -> None:
    text = _text()
    restart = text.find("systemctl restart")
    loop = text.find('for i in $(seq 1 "$HEALTH_WAIT")')
    assert restart != -1 and loop != -1
    assert restart < loop, "the health probe must come after the restart, not before"
    assert "did not come back" in text


def test_a_failed_start_prints_the_rollback_with_the_recorded_sha() -> None:
    """The one thing an operator needs at 2am is the exact command back."""
    text = _text()
    assert "OLD_SHA=" in text
    assert "reset --hard $OLD_SHA" in text
    assert "journalctl" in text, "the log must be printed, not just suggested"


def test_the_version_the_service_reports_is_compared_to_the_checkout() -> None:
    """A half-applied update looks fine until you ask the service what it runs."""
    text = _text()
    assert "reports version" in text
    assert "may have half-applied" in text


def test_check_shows_what_would_land_and_changes_nothing() -> None:
    text = _text()
    assert "--check" in text
    assert "nothing was changed" in text
    # The checkout is only moved after the --check exit.
    assert text.find("--check: nothing was changed") < text.find("merge --ff-only")


def test_an_already_current_install_is_a_noop() -> None:
    """Restarting for nothing is a minute of downtime for no reason."""
    text = _text()
    assert "already up to date" in text
    assert text.find("already up to date") < text.find("merge --ff-only")


def test_a_missing_service_says_what_it_did_find() -> None:
    """The first run on a real host failed with "is this the right host?".

    That is true but useless: the operator cannot act on it. The check now looks
    in three places (unit files, loaded units, the file in UNIT_DIR) and, when
    none of them has it, prints the units that *do* look like nobi so the name
    can be passed with SERVICE= instead of guessed at.
    """
    text = _text()
    assert "list-unit-files" in text
    assert "list-units" in text
    assert "no %s.service on this host" in text
    assert "units that do look like nobi" in text
    assert "SERVICE=<name>" in text


def test_the_service_check_looks_in_three_places_not_one() -> None:
    """`list-unit-files` alone misses a unit installed as a symlink or loaded at runtime."""
    text = _text()
    assert text.count("systemctl list-unit-files") >= 2
    assert "systemctl list-units --all" in text
    assert "UNIT_DIR/${SERVICE}.service" in text


def test_pip_runs_as_the_service_user_not_as_root() -> None:
    """A root-owned file in the venv breaks the *next* update with EACCES.

    That reads as "the update broke" when the cause is ownership, so the script
    repairs it up front instead.
    """
    text = _text()
    assert "runas" in text
    assert '! -user "$SERVICE_USER"' in text
    assert "an earlier pip ran as root" in text
