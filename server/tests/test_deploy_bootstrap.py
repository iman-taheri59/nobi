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


def _dispatch(tmp_path: Path, installed_script: str, step: str = "Update") -> tuple[str, int, str]:
    """Run the remote script with `sudo` replaced by a recorder."""
    box = tmp_path / "box"
    box.mkdir(exist_ok=True)
    installed = box / "update_arvan.sh"
    installed.write_text(installed_script, encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    log = tmp_path / "sudo.log"
    (fake_bin / "sudo").write_text(
        f'#!/bin/sh\necho "$*" >> {log}\nexit 0\n', encoding="utf-8")
    (fake_bin / "sudo").chmod(0o755)
    script = _remote_script(step).replace(INSTALLED, str(installed))
    path = tmp_path / "remote.sh"
    path.write_text(script, encoding="utf-8")
    # The box does not inherit the runner's environment: the only way the tag
    # can arrive is the way the ssh command line sends it, as `$1`.
    env = {"PATH": f"{fake_bin}:/usr/bin:/bin", "HOME": str(tmp_path)}
    out = subprocess.run(["bash", str(path), "v2.5.8"], capture_output=True,
                         text=True, env=env, encoding="utf-8")
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
    assert called == f"-n bash {tmp_path / 'box' / 'update_arvan.sh'} --yes --backup --verify", log
    assert "predates --ref" in log, "the fallback has to say it fell back"


@pytest.mark.skipif(os.name != "posix", reason="the remote side is a POSIX shell")
def test_a_script_that_knows_the_flags_gets_them(tmp_path):
    called, code, log = _dispatch(tmp_path, NEW_SCRIPT)
    assert code == 0, log
    assert "--accept-unit-change --ref v2.5.8" in called, called


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
    assert "--check --ref v2.5.8" in called, called


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
