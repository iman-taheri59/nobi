# mypy: check_untyped_defs = False
"""The deploy workflow and the script on the box must agree, or the run dies.

`deploy.yml` is read from `main` on every dispatch; `/opt/nobi/scripts/update_arvan.sh`
is whatever the last successful deploy (or the original install) left there. The
script *demands uid 0* and, past that, does its privileged work as the service
user. The runner reaches the box as `deploy`, which is not root — by design.

So the workflow may not hand the script a flag its parser rejects, and it may not
have root run a file `deploy` can write (17 Nov 2026: `sudo: a password is
required` — the passwordless rule covers the SYSTEM script only, not a copy
staged by the deploy account; that boundary is deliberate). Both halves are
pinned here: the flag list is built from the script's own case arms (probed on
the box and parsed with `case`), and the root call is exactly

    sudo -n /opt/nobi/scripts/update_arvan.sh <the two shapes the sudoers rule allows>

`deploy/nobi-deploy.sudoers` lists the 16 argv shapes the workflow can produce —
a `--check` (dry run) or `--yes --backup --verify` (update) plus any subsequence
of `--keep-local-edits [--ref v*] [--accept-unit-change]` in that fixed order.
`--reset` and `--force` are not in any rule, so a compromised deploy key cannot
hard-reset the checkout from CI. The tests below execute the remote body with
`ssh`/`sudo` stubbed, because the failure modes are shell ones.
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
        installed.chmod(0o000)
    path = tmp_path / "probe.sh"
    # the step passes the script to inspect as the probe's first argument
    path.write_text(_probe_script(), encoding="utf-8")
    out = subprocess.run(["bash", str(path), str(installed)], capture_output=True, text=True,
                         encoding="utf-8", env={"PATH": "/usr/bin:/bin",
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



def _dispatch(tmp_path: Path, installed_script: str, step: str = "Update",
              *, readable: bool = True,
              caps: tuple[str, str, str] | None = None) -> tuple[str, int, str]:
    """Run the remote script with `sudo` replaced by a recorder.

    The probe reads the script with a plain `cat`; the update path calls
    `sudo -n <script> <flags>`. The stub strips the `-n` and records the
    command line, which is what the release is being judged on. An unreadable
    script (`readable=False`, mode 000) must degrade to "every flag is
    missing", never to a crash in the probe.
    """
    if caps is None:
        # Derive the caps by running the real probe on the script, exactly as
        # the runner does over ssh — so `readable=False` (mode 000) answers
        # "every flag is missing" instead of greping the text behind its back.
        probe = _run_probe(tmp_path / "probe", installed_script, readable=readable)
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
        # 000, not 600: the file is owned by the test process, and only the
        # missing read bit makes `cat` fail for it.
        installed.chmod(0o000)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "sudo.log"
    (fake_bin / "sudo").write_text(
        "#!/bin/sh\n"
        # `sudo -n <script> <flags>` -> strip the -n, record the command line,
        # which is what the release is being judged on.
        'if [ "$1" = "-n" ]; then shift; fi\n'
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
    # `sudo -n bash -c "bash <script> <flags>"` — the stub records the inner
    # command line, which is what the release is judged on.
    assert called == f"{tmp_path / 'box' / 'update_arvan.sh'} --yes --backup --verify", log
    assert "the script has no --ref" in log, "the fallback has to say it fell back"


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
    assert called == f"{tmp_path / 'box' / 'update_arvan.sh'} --check", called
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
def test_a_checkout_the_deploy_account_cannot_read_degrades_to_missing_flags(tmp_path):
    """The detector must not answer "old script" because it was not allowed to look.

    A bare `grep` on a file it cannot read exits non-zero, which the step used
    to read as "the script is old" — a decision made from a permissions error.
    The probe now reads with a plain `cat` and treats a failure as "every flag
    is missing": the run then uses the two argv shapes that are safe against
    any parser, says so in a warning, and the post-deploy commit check is the
    backstop. This test pins those three answers.
    """
    probe = _probe_script("Update")
    assert "sudo -n -u nobi cat" not in probe, (
        "the probe must not need a sudoers rule that does not exist; a plain "
        "read is the only one the deploy account can be assumed to have"
    )
    unreadable = _run_probe(tmp_path / "r0", NEW_SCRIPT, readable=False)
    assert "every flag is treated as missing" in unreadable, unreadable
    assert "has ref" not in unreadable, unreadable

    called, code, log = _dispatch(tmp_path / "d", NEW_SCRIPT, readable=False)
    assert code == 0, log
    assert called.endswith("update_arvan.sh --yes --backup --verify"), called
    assert "--ref" not in called, called


@pytest.mark.skipif(os.name != "posix", reason="the runner side is a shell")
def test_the_whole_step_runs_with_the_capability_answers_expanded(tmp_path):
    """Run the step *as the runner runs it*, with `ssh` and `sudo` stubbed.

    The two tests above call the remote body directly, which is exactly how a
    quoting bug survives them: on 17 Nov the step read

        ssh prod "bash -s -- '$TAG' \\$CAP_REF \\$CAP_UNIT \\$CAP_KEEP"

    and the box answered `bash: line 2: $2: unbound variable` — a backslash there
    is a literal backslash, so all three answers arrived as one argument. The
    probe had said `yes yes yes`, the command line said the opposite, and the
    release died two lines in. Only running the text of the step can catch that.
    """
    box = tmp_path / "box"
    box.mkdir(parents=True)
    installed = box / "update_arvan.sh"
    installed.write_text(NEW_SCRIPT, encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    # `ssh prod "<command string>"` hands one argument to the remote shell, so the
    # fake does the same: shift past the host and run the rest as a command line.
    (fake_bin / "ssh").write_text('#!/bin/sh\nshift\nexec sh -c "$*"\n', encoding="utf-8")
    # The update path is `sudo -n <script> <flags>`; the stub strips -n and
    # records the command line. The script is never executed here.
    log = tmp_path / "sudo.log"
    (fake_bin / "sudo").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-n" ]; then shift; fi\n'
        f'echo "$*" >> {log}\n'
        "exit 0\n", encoding="utf-8")
    for stub in (fake_bin / "ssh", fake_bin / "sudo"):
        stub.chmod(0o755)

    fake_home = tmp_path / "deploy-home"
    fake_home.mkdir()

    for step in ("Update", "Dry run"):
        text = _step(step)["run"].replace(INSTALLED, str(installed))
        path = tmp_path / f"{step.replace(' ', '_')}.sh"
        path.write_text(text, encoding="utf-8")
        env = {"PATH": f"{fake_bin}:/usr/bin:/bin", "HOME": str(fake_home),
               "TAG": "v2.5.8"}
        log.write_text("", encoding="utf-8")
        out = subprocess.run(["/bin/bash", "-c", f"cd {tmp_path} && /bin/bash {path}"],
                             capture_output=True, text=True, encoding="utf-8", env=env)
        assert out.returncode == 0, out.stdout + out.stderr
        assert "unbound variable" not in out.stderr, out.stderr
        called = log.read_text(encoding="utf-8").strip()
        # The script on the box knows every flag, so the run must use them — and
        # `--ref` must be followed by its tag, or the tag becomes somebody else's
        # argument. The call must be the SYSTEM script via sudo, not a staged copy.
        assert str(installed) in called, called
        assert "--keep-local-edits" in called, called
        assert "--ref v2.5.8" in called, called
        assert "unknown argument" not in out.stdout + out.stderr


def test_the_root_call_is_the_sudoers_pinned_one() -> None:
    """The privilege boundary is the point of the design; pin it exactly.

    `deploy` reaches the box and may not become root except for the one script
    the sudoers rule names — 17 Nov's `sudo: a password is required` is that
    boundary working. So the workflow must call `sudo -n` on the SYSTEM path,
    and any file the deploy account can write must never be on a root command
    line. `deploy/nobi-deploy.sudoers` enumerates the argv shapes; this test
    keeps the workflow and the rule agreeing.
    """
    sudoers = (ROOT / "deploy" / "nobi-deploy.sudoers").read_text(encoding="utf-8")
    for name in ("Update", "Dry run"):
        run = _step(name)["run"]
        assert 'sudo -n "$script" $flags' in run, f"{name} does not use the pinned sudo call"
        assert "/opt/nobi/scripts/update_arvan.sh" in run
        # nothing the deploy account can stage or rewrite may be root-run
        assert "~/nobi-deploy" not in run, f"{name} stages a deploy-writable root target"
        assert "install -o root" not in run
    # the rule covers exactly the two base shapes, and never --reset/--force
    base = [ln for ln in sudoers.splitlines() if ln.startswith("deploy ALL=")]
    assert len(base) == 16, base
    for line in base:
        assert "--reset" not in line and "--force" not in line, line
    assert "/opt/nobi/scripts/update_arvan.sh --check" in sudoers
    assert "/opt/nobi/scripts/update_arvan.sh --yes --backup --verify" in sudoers
    # both base shapes plus every subsequence of the three trailing flags,
    # in the fixed order the workflow builds them
    subsets = ["", " --keep-local-edits", " --ref v*", " --keep-local-edits --ref v*",
               " --accept-unit-change", " --keep-local-edits --accept-unit-change",
               " --ref v* --accept-unit-change",
               " --keep-local-edits --ref v* --accept-unit-change"]
    for suffix in subsets:
        assert f"update_arvan.sh --check{suffix}\n" in sudoers + "\n", suffix
        assert f"update_arvan.sh --yes --backup --verify{suffix}\n" in sudoers + "\n", suffix
