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
    fatal into a decision the operator can make. The decision itself is git's:
    the guard runs the very merge the update would run and reads its answer,
    instead of guessing from a file list. Measured on capoy.ir, 17 Nov: the
    guess diffed the whole 2.5.6..2.5.8 range and announced 760 files as "an
    edit on the box" over a single modified file.
    """
    text = _text()
    assert 'status --porcelain' in text
    assert "refusing to update a checkout with local changes" in text
    # The question is asked of git, and the answer is variable, not a file list.
    assert 'merge --ff-only --no-commit --no-stat "$REMOTE/$BRANCH"' in text
    assert "GUARD_DIRTY=1" in text
    # A guess that only names files this update rewrites must not come back.
    assert "INCOMING=" not in text and "AT_RISK=" not in text
    # The refusal must come before the merge that does the work.
    assert text.find("refusing to update a checkout with local changes") < \
        text.find('merge --ff-only "origin/$BRANCH" >/dev/null')


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
    assert "untracked and harmless" in code
    # The collision case (an untracked path the incoming tree would write) needs
    # no separate scan any more: git refuses that merge itself, and its answer is
    # what the guard reads.
    assert "merge --ff-only --no-commit --no-stat" in code
    assert "cat-file -e" not in code, (
        "re-implementing git's overwrite rules is how the guard grew a second, "
        "wrong opinion about which files are at risk"
    )


def test_a_real_install_with_only_a_cache_directory_still_updates(tmp_path):
    """Behavioural, against a real clone: `.cache/` alone must not stop the run.

    The script itself demands root, so its guard is lifted out and run against a
    real repository: once with an untracked cache directory (the state a live
    install is in within hours of booting), then with a modified tracked file,
    then with an untracked path origin would write over.
    """
    if os.name != "posix" or shutil.which("bash") is None:
        pytest.skip("the update script is bash; running it needs a POSIX shell")

    text = _text()
    marker = 'STATUS="$(runas git -C "$APP_DIR" status --porcelain'
    assert marker in text
    start = text.index(marker)
    end = text.index("\nfi\n", text.index('LEFTOVER="$(', start)) + len("\nfi\n")
    guard = text[start:end]

    upstream = tmp_path / "upstream"
    upstream.mkdir()
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

    def head() -> str:
        return subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8").stdout.strip()

    def run_guard() -> str:
        harness = (
            'runas() { "$@"; }\n'
            'warn() { printf "WARN %s\\n" "$*"; }\n'
            'die() { printf "DIE %s\\n" "$*" >&2; exit 1; }\n'
            f'APP_DIR={str(clone)!r}\nBRANCH=main\nREMOTE=origin\nDO_RESET=0\n'
            "ALLOW_DIRTY=0\nASSUME_YES=1\nSTASH_EDITS=0\n" + guard
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

    # the same clone with a *tracked* modification must still be refused —
    # unless the incoming change leaves that file alone, in which case git
    # fast-forwards and keeping the edit is correct.
    (clone / "keep.txt").write_text("edited by hand\n", encoding="utf-8")
    second = run_guard()
    assert "DIE" not in second, second
    assert "does not rewrite (kept)" in second, second
    assert "keep.txt" in second, "the run still has to say what it found"
    assert head() == subprocess.run(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], capture_output=True,
        text=True, encoding="utf-8").stdout.strip(), (
        "the guard asked git a question; it must not have answered it by moving HEAD"
    )

    # the same edit, now to a file origin rewrites: this is the case that blocks
    (upstream / "keep.txt").write_text("from origin\n", encoding="utf-8")
    _git(upstream, "add", "-A")
    _git(upstream, "-c", "user.email=t@e.st", "-c", "user.name=t",
         "commit", "--quiet", "-m", "two")
    _git(upstream, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(clone, "fetch", "--quiet", "origin")
    blocked = run_guard()
    assert "refusing to update a checkout with local changes" in blocked, blocked
    assert "keep.txt" in blocked, "the refusal must name the file it is protecting"
    assert "git said:" in blocked, "the operator needs git's own words, not mine"

    # an untracked path that origin would write is refused by git itself, and the
    # operator is told which path — from git, not from a second opinion
    _git(clone, "checkout", "--", "keep.txt")
    (upstream / "landing.txt").write_text("from origin\n", encoding="utf-8")
    _git(upstream, "add", "-A")
    _git(upstream, "-c", "user.email=t@e.st", "-c", "user.name=t",
         "commit", "--quiet", "-m", "three")
    _git(upstream, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(clone, "fetch", "--quiet", "origin")
    (clone / "landing.txt").write_text("mine\n", encoding="utf-8")
    third = run_guard()
    assert "landing.txt" in third, third


@pytest.mark.skipif(os.name != "posix", reason="the update script is bash")
def test_a_repointed_tag_is_named_as_the_reason_a_fetch_failed(tmp_path):
    """A failed fetch has causes that need different answers; do not guess.

    Measured on capoy.ir, 17 Nov: the dry run died in `git fetch` and the message
    offered credentials for a private repository. The repository is not private —
    the tag `v2.5.8` had been re-pointed on the remote, and git refuses to
    overwrite an existing tag in the clone. The operator was sent to fix the one
    thing that was already fine.

    The helper below is lifted out of the script and run against a fake `git`
    that prints each failure mode, so the classification is executed, not grepped.
    """
    if os.name != "posix" or shutil.which("bash") is None:
        pytest.skip("the update script is bash; running it needs a POSIX shell")

    text = _text()
    assert "fetch_or_die()" in text
    assert 'FETCH_OUT="$(runas git -C "$APP_DIR" "$@" 2>&1)"' in text, (
        "the failure has to be classified from git's own output, not from a guess"
    )
    helper = text[text.index("fetch_or_die() {"):text.index('fetch_or_die fetch --quiet')]

    def classify(message: str) -> str:
        fake = tmp_path / "bin"
        fake.mkdir(exist_ok=True)
        (fake / "git").write_text(
            "#!/bin/sh\nprintf '%s\\n' " + repr(message).replace("'", '"') + " >&2\nexit 1\n",
            encoding="utf-8")
        (fake / "git").chmod(0o755)
        harness = (
            'runas() { "$@"; }\n'
            'warn() { printf "WARN %s\\n" "$*"; }\n'
            'die() { printf "DIE %s\\n" "$*" >&2; exit 1; }\n'
            "APP_DIR=/tmp\nSERVICE_USER=nobi\nBRANCH=main\n" + helper
            + '\nfetch_or_die fetch --tags origin main\n'
        )
        proc = subprocess.run(["bash", "-c", harness], capture_output=True, text=True,
                              encoding="utf-8", timeout=60,
                              env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"})
        return proc.stdout + proc.stderr

    clobber = classify("! [rejected]        v2.5.8     -> v2.5.8  (would clobber existing tag)")
    assert "re-pointed" in clobber or "no longer matches" in clobber, clobber
    assert "--tags --force" in clobber, "the fix has to be the one that applies"
    assert "credentials" not in clobber, "the repository was never private"

    # Authentication is *not* the same answer as unreachable, and the old message
    # treated them as one. Measured on capoy.ir: the box could not reach
    # github.com at all (the ordinary state of an Iranian host), and the log said
    # "if the repository is private, the service user needs credentials" — advice
    # for a case that was not the one that failed.
    timeout = classify("fatal: unable to access 'https://github.com/x': Failed to connect to github.com port 443: Connection timed out")
    assert "network problem" in timeout, timeout
    assert "credentials" not in timeout, timeout

    auth = classify("fatal: Authentication failed for 'https://example/nobi.git/'")
    assert "refused this host's credentials" in auth, auth
    assert "network problem" not in auth, auth

    other = classify("fatal: something nobody predicted")
    assert "not one this" in other, other
    assert "| fatal: something nobody predicted" in other, (
        "git's own words belong in the log either way"
    )


def test_a_describe_suffix_is_not_a_version() -> None:
    """`--always` answers a commit id where no tag is reachable.

    On capoy.ir the checkout was exactly the running release and the log said
    "the service runs 2.5.8 but the checkout is at v2.5.8-8-ga5d559b — a
    previous update may have half-applied": the string comparison treated a
    commit id as a version. A non-tag describe ends in `-N-g<sha>`.
    """
    text = _text()
    assert '*-g[0-9a-f]*) DESCRIBED=""' in text, (
        "a `-N-g<sha>` describe is a commit id and must not be compared to a version"
    )
    assert "may have half-applied" in text, "the real mismatch must still be reported"


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
    # The checkout is only moved after the --check exit — and the guard's own
    # merge is `--no-commit` + `--abort`, so the question can be asked without
    # answering it. A plain `merge --ff-only` there fast-forwarded a clean
    # checkout before `--check` printed "nothing was changed".
    assert text.find("--check: nothing was changed") < text.find('merge --ff-only "origin/$BRANCH"')
    assert "merge --ff-only --no-commit" in text
    assert "merge --abort" in text


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


def test_the_release_tag_can_pin_the_checkout():
    """A validated tag must be the thing installed, not whatever main is.

    The workflow checked out `inputs.tag`, proved the tag sits on main and that
    VERSION matched it — and then called this script, which fetched and moved
    to `origin/$BRANCH`. Two releases landed while main moved, so the code that
    reached production was not necessarily the commit that had been validated.
    """
    code = _code_without_comments()
    assert "--ref)" in code, "no way to pin the checkout to a tag"
    assert "REF" in code
    assert "checkout --quiet --detach" in code, "the ref never changes HEAD"
    assert 'if [ -n "$REF" ] && [ "$NEW_SHA" != "$REMOTE_SHA" ]' in code
    # The tag is fetched by name; the extra `--tags` came off, because on a host
    # whose tags have been re-pointed it fails with "would clobber existing tag"
    # and takes the release down with it.
    assert '"+refs/tags/$REF:refs/tags/$REF"' in code
    assert "fetch_or_die" in code, "a failed fetch has to be classified, not guessed at"


def test_a_version_mismatch_stops_the_deploy_workflow():
    """`|| true` made the VERSION check decorative in .github/workflows/deploy.yml."""
    wf = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert 'test "$(cat VERSION)" = "${GITHUB_REF_NAME#v}" || true' not in wf
    assert 'if [ "$have" != "$want" ]' in wf
    # The tag reaches the box as the remote script's argument (`$1`) and is
    # quoted into the flag list, because the script on the box may predate
    # `--ref` and because ssh does not forward the runner's environment
    # (see test_deploy_bootstrap.py).
    assert 'tag="$1"' in wf
    assert "--ref $tag" in wf


def test_the_status_listing_matches_its_sudoers_rule():
    """sudo compares the whole argv: a flag the rule does not name is a refusal.

    The listing changed to `-lt` so `head -40` stops cutting off the newest
    files, but the sudoers rule still said `-l`. The step is
    `continue-on-error`, so the failure would have looked like "the listing was
    empty" — the same class of silent absence that started this.
    """
    wf = (ROOT / ".github" / "workflows" / "status.yml").read_text(encoding="utf-8")
    rule = (ROOT / "deploy" / "nobi-status.sudoers").read_text(encoding="utf-8")
    assert "-lt --time-style" in wf
    assert "/usr/bin/ls -lt --time-style" in rule.replace("\\\\", "")
    assert "head -40 | tac" in wf


def test_a_unit_in_the_service_writable_checkout_is_not_installed_as_root_silently():
    """The checkout is chowned to the service user; installing root files from it
    is a privilege escalation, and the installer itself already says so.

    `deploy_arvan.sh`: "Templates MUST NOT be read from the checkout: it is
    chowned to the service user below. A service-writable systemd unit copied to
    /etc as root is a root escalation." `update_arvan.sh` then did exactly that
    on every deploy, from the same checkout.
    """
    code = _code_without_comments()
    assert "--accept-unit-change" in code
    assert "ACCEPT_UNIT_CHANGE" in code
    # The gate must cover the unit AND the root helpers, or the helper is the
    # way around it.
    assert code.count("ACCEPT_UNIT_CHANGE") >= 3
    # A diff is shown before the decision, not after.
    assert "diff -u" in code
    # And the deploy workflow has to opt in explicitly.
    wf = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "--accept-unit-change" in wf


def test_a_host_without_an_installed_unit_can_still_be_installed():
    """The gate must not deadlock a fresh host: with nothing to overwrite there
    is nothing to protect, so the unit is installed."""
    code = _code_without_comments()
    assert "there is no installed unit to overwrite" in _text()
    assert 'unit_differs" -eq 2' in code


def test_the_deploy_workflow_does_not_hand_new_flags_to_an_old_script():
    """The flag list is a consequence of a question asked on the box.

    Three released failures, one shape: the step assembled a command line from
    what it *believed* about the script that would run.

      * `--ref` (14 Nov) and `--keep-local-edits` (first 2.5.8 attempt) were sent
        to a parser that answers `unknown argument`, and the release read as
        broken code;
      * then `--keep-local-edits` was put in the base list *before* the single
        check, so the step's warning and its command line disagreed;
      * then the check itself was a `grep -q` in a pipe (141 on a match under
        `pipefail`), and a heredoc — which is stdin, readable once — could not
        both inspect and run;
      * and the script being checked was `/opt/nobi/scripts/…`, the *previous*
        release's, because only a successful deploy replaces it. Four red runs
        were spent editing a file the box never read.

    So the step now: stages this run's script where the deploy account can write,
    asks that file which `case` arms it has (one ssh, output captured), and gives
    the answers to the remote script as arguments. Nothing reaches the command
    line that the probe did not say `yes` to.
    """
    wf = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    # staged where the account may write; never installed over the root-owned file
    assert "~/nobi-deploy/scripts/update_arvan.sh" in wf
    assert "install -o root -g root" not in wf, (
        "the deploy account cannot rewrite /opt/nobi; that produces"
        " 'sudo: a password is required' and a red run"
    )
    # asked of the staged script, with the answer captured on the runner
    assert 'caps="$(ssh prod' in wf
    assert "for arm in ref accept-unit-change keep-local-edits; do" in wf
    assert 'grep -q -- "--$arm)"' in wf, "the `case` arm is detected, not the word"
    assert "CAP_KEEP=yes" in wf and "CAP_REF=yes" in wf and "CAP_UNIT=yes" in wf
    # and passed to the box as arguments, because `env:` does not cross ssh and a
    # backslash inside `$(...)` is a literal backslash (three answers, one arg)
    assert "ssh prod \"bash -s -- '$TAG' $CAP_REF $CAP_UNIT $CAP_KEEP '$RUN_SCRIPT'\"" in wf
    assert 'cap_keep="$4"' in wf and 'script="$5"' in wf
    # the command line only ever grows from that answer
    assert 'flags="--yes --backup --verify"' in wf
    assert 'flags="--check"' in wf
    assert wf.index('if [ "$cap_keep" = yes ]') < wf.index('flags="$flags --keep-local-edits"')
    assert wf.index('if [ "$cap_ref" = yes ]') < wf.index('flags="$flags --ref $tag"')
    # --reset is the destructive path and has no business in a workflow
    assert "--reset" not in wf
    # The probe's own pipeline must not be `grep -q` on the *deciding* side; the
    # runner parses its answer with `case`.
    assert 'while IFS= read -r line; do' in wf


def test_the_fallback_still_proves_which_commit_landed():
    """Without `--ref` the script installs origin/main, so the version string is
    no longer proof: a merge that carries the same VERSION would pass.

    The commit the run validated is recorded up front and compared with the
    checkout on the box afterwards.
    """
    wf = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert 'echo "sha=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"' in wf
    assert 'want="${{ steps.tag.outputs.sha }}"' in wf
    assert "git -C /opt/nobi rev-parse HEAD" in wf
    # An unreadable checkout must be a warning, not a green tick.
    assert "could not read the checkout's commit" in wf


def test_the_deploy_checks_the_tag_it_was_given_not_the_ref_it_was_triggered_on():
    """`workflow_dispatch` has no tag: `GITHUB_REF_NAME` is the branch.

    On 14 Nov the `Deploy v2.5.8` run died in its first step with
    `VERSION says 2.5.8 but the tag is main` — the check compared the installed
    version against the *branch* the workflow was dispatched from, so every
    manual deploy failed before the SSH step ever ran, and the only thing the
    failure proved was that the workflow could not start.
    """
    wf = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert 'want="${GITHUB_REF_NAME#v}"' not in wf, (
        "the tag version must come from the `tag` input, not from the ref"
    )
    assert 'want="${TAG#v}"' in wf
    # The input has to reach the step that reads it.
    step = wf[wf.index("- name: Tag must point at main"):wf.index("- name: SSH setup")]
    assert "TAG: ${{ inputs.tag }}" in step
    assert "if [ \"$have\" != \"$want\" ]" in step


def test_product_code_the_update_rewrites_is_not_overwritten_even_with_yes(tmp_path):
    """A deploy must not overwrite an edit to the code it is deploying.

    Measured on capoy.ir, 14 Nov: `/opt/nobi` held `M server/paper_trading.py` —
    the file that monitors open positions — while the incoming release rewrote
    it. `--yes` is consent to run unattended, not consent to lose that.

    The question "would this fast-forward lose the edit?" is asked of git, not
    inferred from a diff: on 17 Nov the inferred version announced 760 files as
    "an edit on the box" because 2.5.6..2.5.8 rewrote most of the tree, which is
    a message that sends the operator to the wrong file. git's answer, and the
    file it names, are what this pins.
    """
    if os.name != "posix" or shutil.which("bash") is None:
        pytest.skip("the update script is bash; running it needs a POSIX shell")

    text = _text()
    start = text.index('STATUS="$(runas git -C "$APP_DIR" status --porcelain')
    end = text.index("\nfi\n", text.index('LEFTOVER="$(', start)) + len("\nfi\n")
    guard = text[start:end]
    assert "GUARD_DIRTY=1" in guard
    assert 'merge --ff-only --no-commit --no-stat "$REMOTE/$BRANCH"' in guard
    assert "--keep-local-edits" in _code_without_comments(), (
        "an unattended run needs a way to save the edit instead of dying on it"
    )

    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "-c", "init.defaultBranch=main", "init", "--quiet", ".")
    (upstream / "server").mkdir()
    (upstream / "server" / "paper_trading.py").write_text("equity = 1\n", encoding="utf-8")
    (upstream / "VERSION").write_text("2.1.3\n", encoding="utf-8")
    _git(upstream, "add", "-A")
    _git(upstream, "-c", "user.email=t@e.st", "-c", "user.name=t", "commit", "--quiet", "-m", "one")
    _git(upstream, "update-ref", "refs/remotes/origin/main", "HEAD")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", str(upstream), str(clone)],
                   check=True, capture_output=True)
    # the incoming commit touches exactly the file somebody edited here
    (upstream / "server" / "paper_trading.py").write_text("equity = 2\n", encoding="utf-8")
    _git(upstream, "add", "-A")
    _git(upstream, "-c", "user.email=t@e.st", "-c", "user.name=t", "commit", "--quiet", "-m", "two")
    _git(upstream, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(clone, "fetch", "--quiet", "origin")
    (clone / "server" / "paper_trading.py").write_text("equity = 1  # hand edit\n", encoding="utf-8")

    data = tmp_path / "data"
    data.mkdir()

    def run_guard(*, reset: int = 0, force: int = 0, keep: int = 0) -> str:
        harness = (
            'runas() { "$@"; }\n'
            'warn() { printf "WARN %s\\n" "$*"; }\n'
            'die() { printf "DIE %s\\n" "$*" >&2; exit 1; }\n'
            f'APP_DIR={str(clone)!r}\nBRANCH=main\nREMOTE=origin\nDO_RESET={reset}\n'
            f'ALLOW_DIRTY={force}\nASSUME_YES=1\nSTASH_EDITS={keep}\n'
            f'DATA_DIR={str(data)!r}\nSERVICE_USER={os.environ.get("USER", "user")!r}\n' + guard
        )
        proc = subprocess.run(["bash", "-c", harness], capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
        return proc.stdout + proc.stderr

    # `--yes` is not consent to overwrite product code: this must stop, and name
    # the file it stopped for.
    refused = run_guard()
    assert "DIE" in refused, refused
    assert "paper_trading.py" in refused
    assert "keep-local-edits" in refused, "the refusal has to offer the way out"

    # --keep-local-edits saves the edit to a patch, previews it, and stashes it.
    kept = run_guard(keep=1)
    assert "DIE" not in kept, kept
    patches = list(data.glob("local-edits-*.patch"))
    assert patches, kept
    assert "hand edit" in patches[0].read_text(encoding="utf-8")
    assert "hand edit" in kept, "the diff belongs in the deploy log, not only on disk"
    status = subprocess.run(["git", "-C", str(clone), "status", "--porcelain"],
                            capture_output=True, text=True, encoding="utf-8").stdout
    assert "paper_trading.py" not in status, "the edit is stashed, not still in the way"
    # put the checkout back the way it was for the two paths below
    subprocess.run(["git", "-C", str(clone), "stash", "drop", "--quiet"], capture_output=True)
    (clone / "server" / "paper_trading.py").write_text("equity = 1  # hand edit\n", encoding="utf-8")

    # --force is informed consent, and it says what it is doing.
    forced = run_guard(force=1)
    assert "DIE" not in forced, forced
    assert "--force was given" in forced, forced

    # --reset is the fork/branch path: it destroys, and has to say so.
    reset = run_guard(reset=1)
    assert "DIE" not in reset, reset
    assert "DESTROY" in reset, reset


def test_a_backtest_report_does_not_block_a_deploy(tmp_path):
    """Artefacts a running install writes are not "local work".

    `server/backtest_reports/*.json` is written by the Backtest page. The
    checkout is the service user's, so the web process can create them any time
    — and on capoy.ir they were there next to the hand edit that actually
    mattered. A guard that cannot tell them apart either blocks every deploy or
    overwrites a fix; this one asks the incoming diff which files are at risk.
    """
    if os.name != "posix" or shutil.which("bash") is None:
        pytest.skip("the update script is bash; running it needs a POSIX shell")

    text = _text()
    start = text.index('STATUS="$(runas git -C "$APP_DIR" status --porcelain')
    end = text.index("\nfi\n", text.index("LEFTOVER=", start)) + len("\nfi\n")
    guard = text[start:end]

    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "-c", "init.defaultBranch=main", "init", "--quiet", ".")
    (upstream / "server").mkdir()
    (upstream / "server" / "paper_trading.py").write_text("equity = 1\n", encoding="utf-8")
    (upstream / "server" / "backtest_reports").mkdir()
    (upstream / "server" / "backtest_reports" / "old.json").write_text("{}\n", encoding="utf-8")
    _git(upstream, "add", "-A")
    _git(upstream, "-c", "user.email=t@e.st", "-c", "user.name=t", "commit", "--quiet", "-m", "one")
    _git(upstream, "update-ref", "refs/remotes/origin/main", "HEAD")
    old_sha = subprocess.run(["git", "-C", str(upstream), "rev-parse", "HEAD"],
                             capture_output=True, text=True, encoding="utf-8").stdout.strip()

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", str(upstream), str(clone)],
                   check=True, capture_output=True)
    report = clone / "server" / "backtest_reports" / "RUNUSDT-1h-123.json"
    report.write_text('{"trades": []}\n', encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "-c", "user.email=t@e.st", "-c", "user.name=t", "commit", "--quiet", "-m", "report")

    # the incoming change touches nothing the box changed
    (upstream / "server" / "paper_trading.py").write_text("equity = 2\n", encoding="utf-8")
    _git(upstream, "add", "-A")
    _git(upstream, "-c", "user.email=t@e.st", "-c", "user.name=t", "commit", "--quiet", "-m", "two")
    _git(upstream, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(clone, "fetch", "--quiet", "origin")
    new_sha = subprocess.run(["git", "-C", str(upstream), "rev-parse", "HEAD"],
                             capture_output=True, text=True, encoding="utf-8").stdout.strip()

    harness = (
        'runas() { "$@"; }\n'
        'warn() { printf "WARN %s\\n" "$*"; }\n'
        'die() { printf "DIE %s\\n" "$*" >&2; exit 1; }\n'
        f'APP_DIR={str(clone)!r}\nBRANCH=main\nDO_RESET=0\nALLOW_DIRTY=0\nASSUME_YES=1\n'
        f'OLD_SHA={old_sha}\nREMOTE_SHA={new_sha}\n' + guard
    )
    proc = subprocess.run(["bash", "-c", harness], capture_output=True, text=True,
                          encoding="utf-8", timeout=120)
    out = proc.stdout + proc.stderr
    assert "DIE" not in out, out
    # A committed report is not an edit at all, so the run says nothing about it.
    assert "backtest_reports" not in out, out


@pytest.mark.skipif(os.name != "posix", reason="the gate is run by a bash step on Linux")
def test_the_npm_gate_fails_on_an_undocumented_high_advisory(tmp_path) -> None:
    """A gate that cannot pass is a gate people learn to ignore.

    17 Nov: `picomatch <=4.0.7` was high in CI, `npm audit fix` moved the tree to
    `picomatch@4.0.7` — the newest published version — and the advisory was
    widened to `picomatch *` in the same window. The old step (`npm audit
    --audit-level=high`) could then only be satisfied by uninstalling the
    toolchain, so the release was red for a reason no commit could fix.

    The gate now allows an advisory that is *written down* as unfixable, with a
    reason and a version to re-check, and nothing else. This runs both ways.
    """
    import json
    import shutil

    gate = ROOT / "scripts" / "audit_gate.py"
    assert gate.exists()
    if shutil.which("python3") is None:
        pytest.skip("needs python3 on PATH")

    advisory = "GHSA-fc5q-39xc-vjpf"
    report: dict[str, dict] = {"vulnerabilities": {
        "picomatch": {"severity": "high", "via": [
            {"source": 1, "name": "picomatch",
             "url": f"https://github.com/advisories/{advisory}"}]},
        "@parcel/watcher": {"severity": "high", "via": ["picomatch"]},
        "eslint": {"severity": "low", "via": ["junk"]},
    }}
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    def run() -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(gate), str(path)],
                              capture_output=True, text=True, encoding="utf-8")

    ok = run()
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert advisory in ok.stdout, "the exception has to be visible in the log"
    # an indirect package inherits the advisory: the report is per package
    assert "@parcel/watcher" in ok.stdout

    report["vulnerabilities"]["picomatch"]["via"][0]["url"] = (
        "https://github.com/advisories/GHSA-aaaa-bbbb-cccc")
    path.write_text(json.dumps(report), encoding="utf-8")
    bad = run()
    assert bad.returncode == 1, bad.stdout
    assert "GHSA-aaaa-bbbb-cccc" in bad.stdout
