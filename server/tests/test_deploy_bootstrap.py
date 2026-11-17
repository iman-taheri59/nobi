# mypy: check_untyped_defs = False
"""The deploy workflow must not hand a flag to the script that the script refuses.

`deploy.yml` runs `update_arvan.sh` **from the box**, and on the day a release is
deployed that file is whatever the *previous* release left there. So a workflow
cannot introduce a flag by using it: the 2.5.8 workflow called

    update_arvan.sh --yes --backup --verify --accept-unit-change --ref v2.5.8

and on a host still running 2.5.6 that is the old case-loop, which ends in

    *) die "unknown argument: $1 (--help lists them)" ;;

Measured, with the argument loop lifted verbatim out of `1c2556b`'s script:

    [--yes --backup --verify]                                   -> exit 0
    [--yes --backup --verify --accept-unit-change --ref v2.5.8] -> exit 1
        die: unknown argument: --accept-unit-change (--help lists them)

So the *deploy* step of the first release that adds a flag always fails, and it
fails before reading the tag. The tests below pin the run-time choice of flag set
and execute it for real with `sudo` stubbed, because the failure mode is a shell
one: a branch that is never taken cannot be caught by grepping for it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
INSTALLED = "/opt/nobi/scripts/update_arvan.sh"


def _step(name: str) -> dict:
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = data["jobs"]["deploy"]["steps"]
    found = [s for s in steps if s.get("name") == name]
    assert found, f"the {name} step is gone"
    return found[0]


def _update_step() -> dict:
    return _step("Update")


def _remote_command(name: str = "Update") -> str:
    """The remote command line — what `ssh prod` actually asks the box to run.

    Only the last non-heredoc line of the step is that command; earlier lines
    are the runner's own shell (argument checks, quoting) and the heredoc that
    follows is stdin. Skipping this distinction is how `$TAG` ended up *inside*
    the heredoc: the step looked right and the box never received a value.
    """
    lines = [ln for ln in _step(name)["run"].rstrip("\n").split("\n") if ln.strip()]
    assert lines[-1].strip() == "REMOTE", lines[-1]
    start = max(i for i, ln in enumerate(lines) if ln.startswith("ssh prod"))
    return lines[start]


def _remote_script(name: str = "Update") -> str:
    """The heredoc body that runs on the box, without the ssh wrapper."""
    lines = [ln for ln in _step(name)["run"].rstrip("\n").split("\n") if ln.strip()]
    assert lines[-1].strip() == "REMOTE", lines[-1]
    start = max(i for i, ln in enumerate(lines) if ln.startswith("ssh prod"))
    return "\n".join(lines[start + 1:-1]) + "\n"


def _probe_script(name: str = "Update") -> str:
    """The heredoc body of the capability probe (`ssh prod 'bash -s' <<'PROBE'`)."""
    lines = [ln for ln in _step(name)["run"].rstrip("\n").split("\n") if ln.strip()]
    end = max(i for i, ln in enumerate(lines) if ln.strip() == "PROBE")
    start = max(i for i, ln in enumerate(lines) if ln.startswith("caps=\"$(ssh prod"))
    return "\n".join(lines[start + 1:end]) + "\n"


def _run_probe(tmp_path: Path, installed_script: str, *, readable: bool = True) -> str:
    """Execute the probe on a path the deploy account may or may not read."""
    box = tmp_path / "probe-box"
    box.mkdir(parents=True, exist_ok=True)
    installed = box / "update_arvan.sh"
    installed.write_text(installed_script, encoding="utf-8")
    if not readable:
        installed.chmod(0o600)
    fake_bin = tmp_path / "probe-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    (fake_bin / "sudo").write_text(
        "#!/bin/sh\n"
        'if [ "$4" = "cat" ]; then\n'
        f'  if [ {1 if readable else 0} -eq 1 ]; then cat "$5"; exit 0; fi\n'
        '  echo "sudo: a password is required" >&2; exit 1\n'
        "fi\nexit 0\n", encoding="utf-8")
    (fake_bin / "sudo").chmod(0o755)
    path = tmp_path / "probe.sh"
    path.write_text(_probe_script().replace(INSTALLED, str(installed)), encoding="utf-8")
    out = subprocess.run(["bash", str(path)], capture_output=True, text=True,
                         encoding="utf-8", env={"PATH": f"{fake_bin}:/usr/bin:/bin",
                                                "HOME": str(tmp_path)})
    return out.stdout + out.stderr


def _tag_argument(name: str = "Update") -> str:
    """What the ssh command sends as the remote script's `$1`.

    `ssh prod "bash -s -- '$TAG'"` -> `'$TAG'`: the local shell expands it and
    the remote shell receives one argument. The quotes are kept in the answer so
    a call that would let the shell split or glob the tag is visible here.
    """
    command = _remote_command(name)
    assert "-- " in command, command
    sent = command.split("-- ", 1)[1]
    assert sent.startswith("'"), sent
    return sent[: sent.index("'", 1) + 1]


def _names(script: str, *arms: str) -> dict[str, str]:
    """Which of these `case` arms the given script text has: `yes` / `no`."""
    return {arm: ("yes" if f"--{arm})" in script else "no") for arm in arms}


def _probe_answer(script: str) -> str:
    """The probe step's output for a script, exactly as the runner reads it.

    The probe is a separate ssh call that answers `has <arm>` / `missing <arm>`;
    the runner turns those lines into `CAP_*`. Building that answer here means
    the tests exercise the same text the workflow does, not a shortcut.
    """
    return "".join(
        f"has {arm}\n" if f"--{arm})" in script else f"missing {arm}\n"
        for arm in ("ref", "accept-unit-change", "keep-local-edits")
    )


def _dispatch(tmp_path: Path, installed_script: str, step: str = "Update",
              *, readable: bool = True,
              caps: tuple[str, str, str] | None = None) -> tuple[str, int, str]:
    """Run the remote script with `sudo` replaced by a recorder.

    `sudo -u nobi cat` is part of the real step (the checkout belongs to the
    service user, so the deploy account may not be able to read it), so the stub
    answers it the way `sudo` would: the file's contents when it is allowed,
    nothing and a non-zero status when `readable=False`.
    """
    if caps is None:
        probe = _probe_answer(installed_script)
        caps = (
            "yes" if "has ref" in probe else "no",
            "yes" if "has accept-unit-change" in probe else "no",
            "yes" if "has keep-local-edits" in probe else "no",
        )
    ref, unit, keep = caps
    box = tmp_path / "box"
    box.mkdir(parents=True, exist_ok=True)
    installed = box / "update_arvan.sh"
    installed.write_text(installed_script, encoding="utf-8")
    if not readable:
        installed.chmod(0o600)  # the deploy account is not the owner
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "sudo.log"
    (fake_bin / "sudo").write_text(
        "#!/bin/sh\n"
        # `sudo -n -u nobi cat FILE` -> the contents (or a refusal)
        'if [ "$4" = "cat" ]; then\n'
        f'  if [ {1 if readable else 0} -eq 1 ]; then cat "$5"; exit 0; fi\n'
        '  echo "sudo: a password is required" >&2; exit 1\n'
        'fi\n'
        f'echo "$*" >> {log}\n'
        "exit 0\n",
        encoding="utf-8")
    (fake_bin / "sudo").chmod(0o755)
    script = _remote_script(step).replace(INSTALLED, str(installed))
    path = tmp_path / "remote.sh"
    path.write_text(script, encoding="utf-8")
    # The box does not inherit the runner's environment: the only way the tag
    # can arrive is the way the ssh command line sends it, as `$1`.
    env = {"PATH": f"{fake_bin}:/usr/bin:/bin", "HOME": str(tmp_path)}
    out = subprocess.run(["bash", str(path), "v2.5.8", ref, unit, keep],
                         capture_output=True, text=True, env=env, encoding="utf-8")
    called = log.read_text(encoding="utf-8").strip() if log.exists() else ""
    return called, out.returncode, out.stdout + out.stderr


NEW_SCRIPT = (ROOT / "scripts" / "update_arvan.sh").read_text(encoding="utf-8")
#: The v2.5.6 file, verbatim: comments and all. Its prose does not mention either
#: flag, but the point of keeping it whole is that the detector is asked about the
#: file a real box has, not about a synthetic stub.
OLD_SCRIPT = subprocess.run(
    ["git", "-C", str(ROOT), "show", "1c2556b:scripts/update_arvan.sh"],
    capture_output=True, text=True, encoding="utf-8",
).stdout
if not OLD_SCRIPT:  # a shallow clone has no history to show
    OLD_SCRIPT = "#!/usr/bin/env bash\n"


@pytest.mark.skipif(os.name != "posix", reason="the remote side is a POSIX shell")
def test_a_new_flag_is_not_sent_to_an_old_script(tmp_path):
    called, code, log = _dispatch(tmp_path, OLD_SCRIPT)
    assert code == 0, log
    # The old parser gets only what it understands — a run that adds a flag to
    # the list before checking for it is exactly how `unknown argument:
    # --keep-local-edits` happened on 2.5.8's step 8.
    assert called == f"-n bash {tmp_path / 'box' / 'update_arvan.sh'} --yes --backup --verify", log
    assert "predates --ref" in log, "the fallback has to say it fell back"


@pytest.mark.skipif(os.name != "posix", reason="the remote side is a POSIX shell")
def test_a_script_that_knows_the_flags_gets_them(tmp_path):
    called, code, log = _dispatch(tmp_path, NEW_SCRIPT)
    assert code == 0, log
    assert "--accept-unit-change" in called and "--ref v2.5.8" in called, called


@pytest.mark.skipif(os.name != "posix", reason="the remote side is a POSIX shell")
def test_a_comment_mentioning_a_flag_is_not_an_argument_parser(tmp_path):
    """The detector greps for the `case` arm, not for the word.

    This repository's own comments explain `--accept-unit-change` at length; a
    future edit that adds that sentence to the *old* script's header would have
    made a bare `grep -- "--accept-unit-change"` answer "the box knows it" about
    a file whose parser still dies on it.
    """
    script = "#!/usr/bin/env bash\n# never install without --accept-unit-change\n"
    called, code, log = _dispatch(tmp_path, script)
    assert code == 0, log
    assert "--accept-unit-change" not in called, (
        "the fallback was skipped because a comment looked like a parser"
    )


@pytest.mark.skipif(os.name != "posix", reason="the remote side is a POSIX shell")
def test_the_dry_run_does_not_die_where_the_update_would(tmp_path):
    """A dry run that cannot run is worse than none: it reports nothing and looks
    like a failure of the release. It carried the same `--ref`."""
    called, code, log = _dispatch(tmp_path, OLD_SCRIPT, step="Dry run")
    assert code == 0, log
    assert called == f"-n bash {tmp_path / 'box' / 'update_arvan.sh'} --check", called
    called, code, log = _dispatch(tmp_path, NEW_SCRIPT, step="Dry run")
    assert code == 0, log
    assert "--check --keep-local-edits --ref v2.5.8" in called, called


def test_the_tag_never_travels_through_the_shell_twice():
    """`${{ inputs.tag }}` is a dispatch input: it goes in through `env:`, so the
    remote script sees a variable and not a nested quoted expression."""
    step = _update_step()
    assert step["env"] == {"TAG": "${{ inputs.tag }}"}
    run = step["run"]
    assert "${{" not in run, "a GitHub expression inside the remote heredoc is a quoting trap"
    assert 'tag="$1"' in run


@pytest.mark.skipif(os.name != "posix", reason="the remote side is a POSIX shell")
def test_the_remote_script_never_reads_an_environment_variable_it_cannot_have():
    """An `env:` block on the step does not cross the ssh boundary.

    21:10Z on 14 Nov: the `Update` step ran
    `ssh prod 'bash -s' <<'REMOTE'` with `set -eu` and `tag="$TAG"`, and died in
    two seconds with `TAG: unbound variable`. The job never reached the update
    script, the box was never touched, and the release read as broken because of
    a quoting decision. The tag now travels as the remote script's first
    argument — the one channel ssh really carries.
    """
    for name in ("Update", "Dry run"):
        body = _remote_script(name)
        assert "$1" in body, f"{name} never reads the argument the ssh line sends"
        assert "$TAG" not in body, (
            f"{name} reads an environment variable ssh does not forward; "
            "that is the `TAG: unbound variable` failure"
        )
        # What is sent after `--` is quoted, so the remote shell receives one
        # argument even if the tag ever contains something the shell likes.
        assert _tag_argument(name) == "'$TAG'", _tag_argument(name)

    # The old shape is reproduced, so this guard is not decoration: the same
    # body with the tag taken from the environment dies before doing anything.
    env = {k: v for k, v in os.environ.items() if k != "TAG"}
    out = subprocess.run(["bash", "-c", 'set -eu\ntag="$TAG"\necho reached'],
                         capture_output=True, text=True, encoding="utf-8", env=env)
    assert out.returncode != 0, "the shape this test forbids was supposed to fail"
    assert "TAG: unbound variable" in out.stderr, out.stderr
    assert "reached" not in out.stdout


@pytest.mark.skipif(os.name != "posix", reason="the remote side is a POSIX shell")
def test_the_command_line_and_the_warning_never_disagree(tmp_path):
    """The flag list is built from the checks, never from optimism.

    14 Nov, 03:1x: step 8 printed

        Warning: the installed update script predates --ref; this run installs
        origin/main.
        running: update_arvan.sh --yes --backup --verify --keep-local-edits
        x  unknown argument: --keep-local-edits (--help lists them)

    The warning said the run had fallen back to the contract the old script
    understands; the command line had `--keep-local-edits` on it anyway, because
    it was appended to the base list *before* the one check the step ran. The
    release then died in the old script's case loop, two lines in — the same
    failure as --ref, one flag later.
    """
    # The 2.5.6 parser, verbatim: it must reject every flag it does not know.
    old = _dispatch(tmp_path, OLD_SCRIPT)[0]
    assert "--keep-local-edits" not in old, old
    assert "--ref" not in old, old
    assert "--accept-unit-change" not in old, old

    # A script that knows everything gets everything, in one place.
    new = _dispatch(tmp_path / "new", NEW_SCRIPT)[0]
    assert "--keep-local-edits" in new and "--ref v2.5.8" in new, new


@pytest.mark.skipif(os.name != "posix", reason="the remote side is a POSIX shell")
def test_a_checkout_the_deploy_account_cannot_read_is_asked_through_sudo(tmp_path):
    """The detector must not answer "old script" because it was not allowed to look.

    `/opt/nobi` is chowned to the service user by `deploy_arvan.sh`, and the
    workflow's account is `deploy`. A bare `grep` on a file it cannot read exits
    non-zero, which the step used to read as "the script is old" — a decision
    made from a permissions error. The file is read as the service user
    (`sudo -u nobi cat`), and only if that is refused does the plain read get a
    turn.
    """
    probe = _probe_script("Update")
    assert "sudo -n -u nobi cat" in probe, (
        "the installed script has to be read as its owner, or the answer is a"
        " permissions error dressed up as an old script"
    )
    # A file only the service user can read, and a file anybody can read: both
    # must classify the same way, and neither may answer "old script".
    for readable in (False, True):
        answer = _run_probe(tmp_path / f"r{int(readable)}", NEW_SCRIPT, readable=readable)
        assert "has keep-local-edits" in answer, answer
        assert "has ref" in answer, answer

    called, code, log = _dispatch(tmp_path / "d", NEW_SCRIPT, readable=False)
    assert code == 0, log
    assert "--accept-unit-change" in called and "--ref v2.5.8" in called, called
