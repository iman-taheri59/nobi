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
import sys
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


@pytest.mark.skipif(
    os.name != "posix",
    reason="git does not carry the exec bit on Windows; the mode is a POSIX idea",
)
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


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="the update script is bash; running it needs a POSIX shell",
)
def test_help_prints_only_the_header_comment() -> None:
    """The header is 19 comment lines; a 20th would leak `set -euo pipefail`."""
    out = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True,
                         text=True, encoding="utf-8", check=True)
    assert out.returncode == 0
    assert "set -euo pipefail" not in out.stdout
    assert "APP_DIR=" not in out.stdout
    assert "sudo bash scripts/update_arvan.sh" in out.stdout


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="the update script is bash; running it needs a POSIX shell",
)
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


def test_untracked_files_are_not_mistaken_for_a_dirty_checkout() -> None:
    """A pip or pytest cache is not work, and `git pull` does not care about it.

    Measured on a scratch clone: a checkout holding `.cache/` and `untracked.txt`
    fast-forwards cleanly, while a *modified tracked* file aborts the merge with
    "Please commit your changes or stash them". `status --porcelain` prints both
    with the same command, so the guard has to separate them — otherwise the
    first cache directory an install grows blocks every update after it.
    """
    code = _code_without_comments()
    assert "grep -v '^?? '" in code, "tracked changes must be told from untracked ones"
    assert "ls-files --others --exclude-standard" in code
    # The collision test is the one case an untracked path *does* block: when
    # origin's tree already holds that path, the merge cannot write it.
    assert "cat-file -e" in code
    assert "untracked and harmless" in code


def test_a_real_install_with_only_a_cache_directory_still_updates(tmp_path):
    """Behavioural, against a real clone: `.cache/` alone must not stop the run.

    The source assertions above describe the guard; this one runs the guard's
    **own text**, lifted out of the script, against an actual repository holding
    an untracked `.cache/` and then a modified tracked file, and checks which of
    the two it refuses. The script itself demands root, so `runas` is stubbed to
    a plain exec — which is exact here, because the test already runs as the
    user it would be asking for.
    """
    if os.name != "posix" or shutil.which("bash") is None:
        pytest.skip("the update script is bash; running it needs a POSIX shell")

    text = _text()
    marker = 'STATUS="$(runas git -C "$APP_DIR" status --porcelain'
    assert marker in text, (
        "the dirty guard must read `git status --porcelain` into its own variable "
        "and split the `??` lines out of it; without that split an untracked cache "
        "directory is reported as work"
    )
    start = text.index(marker)
    end_marker = "\nfi\n"
    leftover = text.index("LEFTOVER=", start)
    end = text.index(end_marker, leftover) + len(end_marker)
    guard = text[start:end]

    upstream = tmp_path / "upstream"
    upstream.mkdir()
    # the guard asks for `origin/$BRANCH`, so the upstream's default branch has to
    # actually be `main` — a scratch repo otherwise starts on `master` and the
    # collision test would silently test nothing.
    _git(upstream, "-c", "init.defaultBranch=main", "init", "--quiet", ".")
    (upstream / "VERSION").write_text("2.1.3\n", encoding="utf-8")
    (upstream / "keep.txt").write_text("original\n", encoding="utf-8")
    _git(upstream, "add", "-A")
    _git(upstream, "-c", "user.email=t@e.st", "-c", "user.name=t", "commit", "--quiet", "-m", "one")
    _git(upstream, "tag", "v2.1.3")
    _git(upstream, "update-ref", "refs/remotes/origin/main", "HEAD")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", str(upstream), str(clone)],
                   check=True, capture_output=True)

    def run_guard() -> str:
        harness = (
            'runas() { "$@"; }\n'
            'warn() { printf "WARN %s\\n" "$*"; }\n'
            'die() { printf "DIE %s\\n" "$*" >&2; exit 1; }\n'
            f'APP_DIR={str(clone)!r}\nBRANCH=main\nDO_RESET=0\n'
            "ALLOW_DIRTY=0\nASSUME_YES=1\n" + guard
        )
        proc = subprocess.run(["bash", "-c", harness], capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
        return proc.stdout + proc.stderr

    # a cache directory, exactly what a live install grows within hours
    (clone / ".cache").mkdir()
    (clone / ".cache" / "junk").write_text("x", encoding="utf-8")
    first = run_guard()
    assert "DIE" not in first, first
    assert "untracked and harmless" in first, first

    # the same clone with a *tracked* modification must still be refused
    _git(clone, "reset", "--hard", "--quiet", "HEAD")
    (clone / "keep.txt").write_text("edited by hand\n", encoding="utf-8")
    second = run_guard()
    assert "refusing to update a dirty checkout" in second, second
    assert "keep.txt" in second, "the refusal must name the file it is protecting"

    # and an untracked path that origin would overwrite is the third case
    _git(clone, "reset", "--hard", "--quiet", "HEAD")
    (upstream / "landing.txt").write_text("from origin\n", encoding="utf-8")
    _git(upstream, "add", "-A")
    _git(upstream, "-c", "user.email=t@e.st", "-c", "user.name=t",
         "commit", "--quiet", "-m", "two")
    _git(upstream, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(clone, "fetch", "--quiet", "origin")
    (clone / "landing.txt").write_text("mine\n", encoding="utf-8")
    third = run_guard()
    assert "refusing to update a dirty checkout" in third, third
    assert "landing.txt" in third, "a path origin would overwrite must block too"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


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

    That is true but useless: the operator cannot act on it. The check now asks
    systemd directly and, when systemd does not know the unit, prints the units
    that *do* look like nobi so the name can be passed with SERVICE= instead of
    guessed at.
    """
    text = _text()
    assert "list-unit-files" in text
    assert "systemd does not know" in text
    assert "units that do look like nobi" in text
    assert "SERVICE=<name>" in text


def test_the_service_check_uses_no_pipeline() -> None:
    """`set -o pipefail` + `grep -q` = exit 141, and a present unit looks absent.

    Measured: `grep -q` exits the moment it matches, the writer upstream dies of
    SIGPIPE, and `pipefail` promotes that 141 to the pipeline's status. So the
    check failed on hosts that *had* the unit — which is exactly the "no
    nobi.service" a real run produced. `systemctl show` takes no pipeline and
    returns non-zero when systemd does not know the unit, which is both the
    existence test and the answer.
    """
    text = _code_without_comments()
    # No executable `grep -q` anywhere: an early exit is the failure mode.
    for line in text.splitlines():
        assert "grep -q" not in line, f"an early-exiting grep is back: {line.strip()}"
    assert 'systemctl show "${SERVICE}.service"' in text
    # The candidate list is captured before it is filtered, for the same reason.
    assert 'UNIT_LIST="$(systemctl list-unit-files' in text
    assert "UNIT_LIST" in text


def test_a_stale_service_is_not_reported_as_up_to_date() -> None:
    """A current checkout is not the same as a current *service*.

    On a host reset to a new commit but never restarted, the process served
    2.0.3 while the code on disk said 2.0.10 — and the script answered "already
    up to date" and exited 0. That is the one answer that is both true and
    useless, so the early exit is conditional on the service agreeing.
    """
    text = _text()
    assert "STALE_SERVICE" in text
    assert "the checkout is current, but the service is not" in text
    assert "HEAD:VERSION" in text, "the comparison needs the checkout's own VERSION"
    # the early exit must sit inside the else, not before it
    code = _code_without_comments()
    assert code.find('step "already up to date"') > code.find("the checkout is current")


def test_the_service_check_asks_systemd_not_a_grep() -> None:
    """One authoritative call beats three guesses.

    The previous version searched `list-unit-files`, `list-units --all` and the
    file in UNIT_DIR — three places because `list-unit-files` alone misses a unit
    installed as a symlink or loaded at runtime. `systemctl show` answers the
    same question with no pipeline at all, which is also what removes the
    SIGPIPE failure mode.
    """
    text = _text()
    assert 'systemctl show "${SERVICE}.service"' in text
    assert "-p Id --value" in text
    # the file in UNIT_DIR is still a fallback, for a unit not yet loaded
    assert '[ ! -f "$UNIT_DIR/${SERVICE}.service" ]' in text


def test_pip_runs_as_the_service_user_not_as_root() -> None:
    """A root-owned file in the venv breaks the *next* update with EACCES.

    That reads as "the update broke" when the cause is ownership, so the script
    repairs it up front instead.
    """
    text = _text()
    assert "runas" in text
    assert '! -user "$SERVICE_USER"' in text
    assert "an earlier pip ran as root" in text


def _bash_snippet(snippet: str, tmp_path, env: dict | None = None) -> subprocess.CompletedProcess:
    script = tmp_path / "snippet.sh"
    script.write_text(
        "set -u\n"
        "warn() { printf '  !  %s\\n' \"$*\"; }\n"
        "die() { printf '  x  %s\\n' \"$*\" >&2; exit 1; }\n"
        "step() { printf '\\n==> %s\\n' \"$*\"; }\n"
        "runas() { \"$@\"; }\n" + snippet,
        encoding="utf-8",
    )
    return subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, **(env or {})}, timeout=60,
    )


def _lift(text: str, start_marker: str, end_marker: str) -> str:
    start = text.index(start_marker)
    return text[start:text.index(end_marker, start) + len(end_marker)]


def test_a_matching_service_version_is_not_reported_as_half_applied(tmp_path) -> None:
    """`git describe` says `v2.3.2`, health says `2.3.2`: that is a match.

    The comparison prefixed another `v`, so every healthy install was warned
    about a half-applied update — seen on the production host before 2.5.0.
    """
    if os.name != "posix" or shutil.which("bash") is None:
        pytest.skip("bash required")
    text = _text()
    compare = _lift(text, 'if [ "$RUNNING_VER" != "(service not answering)" ]', "\nfi\n")
    for old_ver, running in (("v2.3.2", "2.3.2"), ("2.3.2", "2.3.2"), ("v2.3.2", "v2.3.2")):
        result = _bash_snippet(
            f'OLD_VER="{old_ver}"\nRUNNING_VER="{running}"\n' + compare, tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert "half-applied" not in result.stdout, (old_ver, running, result.stdout)
        assert "matches the checkout" in result.stdout
    result = _bash_snippet('OLD_VER="v2.3.2"\nRUNNING_VER="2.5.0"\n' + compare, tmp_path)
    assert "half-applied" in result.stdout


def test_the_backup_is_the_documented_sqlite_backup_not_a_tar_of_live_files(tmp_path) -> None:
    """A tar of the data directory fails while the service writes its candle
    cache (`file changed as we read it`), and a raw copy of a live SQLite file
    is not a consistent snapshot anyway. The guide's own procedure is
    `state_backup.py`, which uses SQLite's online backup API. That is what
    `--backup` must run — and a missing database must still stop the update.
    """
    if os.name != "posix" or shutil.which("bash") is None:
        pytest.skip("bash required")
    text = _text()
    assert "tar -C \"$DATA_DIR\"" not in text, "do not tar a live data directory"
    assert "state_backup.py" in text
    backup = _lift(text, 'if [ "$DO_BACKUP" -eq 1 ]; then', "\nfi\n")

    import sqlite3
    data = tmp_path / "data"
    data.mkdir()
    for name in ("journal.db", "auth.db", "live_execution.db"):
        with sqlite3.connect(data / name) as conn:
            conn.execute("CREATE TABLE t (x)")
            conn.execute("INSERT INTO t VALUES (1)")
    (data / "candle-cache").mkdir()
    (data / "candle-cache" / "busy.json").write_text("{}", encoding="utf-8")
    env_file = tmp_path / "nobi.env"
    env_file.write_text(
        f"NBX_JOURNAL_DB={data}/journal.db\nNBX_AUTH_DB={data}/auth.db\n"
        f"NBX_LIVE_DB={data}/live_execution.db\nNBX_API_TOKEN=must-not-leak\n",
        encoding="utf-8",
    )
    app = tmp_path / "app"
    (app / ".venv" / "bin").mkdir(parents=True)
    (app / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "state_backup.py", app / "scripts" / "state_backup.py")
    os.symlink(sys.executable, app / ".venv" / "bin" / "python")
    prelude = (
        f'APP_DIR="{app}"\nDATA_DIR="{data}"\nENV_FILE="{env_file}"\n'
        f'BACKUP_DIR="{tmp_path / "backups"}"\nSERVICE_USER="$(id -un)"\nDO_BACKUP=1\n'
    )
    result = _bash_snippet(prelude + backup, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    archives = list((tmp_path / "backups").glob("*.zip"))
    assert len(archives) == 1, result.stdout
    assert "must-not-leak" not in result.stdout + result.stderr
    import zipfile
    names = zipfile.ZipFile(archives[0]).namelist()
    assert not any("candle" in n for n in names), "the cache is rebuildable, not state"
    assert any("journal" in n for n in names) and any("auth" in n for n in names)

    # A database the env file points at but which is missing must refuse.
    (data / "live_execution.db").unlink()
    result = _bash_snippet(prelude + backup, tmp_path)
    assert result.returncode != 0
    assert "stopping before the update" in result.stderr
