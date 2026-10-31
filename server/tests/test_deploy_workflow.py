"""The deploy path stays gated, verifiable and free of the old shortcuts.

Nothing here talks to a server or to GitHub. These tests assert the *shape* of
the deployment — what triggers it, what it refuses to do, and what it proves
before it calls itself finished — because those are the properties that are
expensive to discover after the fact.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/deploy.yml"
REMOTE = ROOT / "scripts/deploy_remote.sh"


@pytest.fixture(scope="module")
def workflow() -> dict:
    # BaseLoader: GitHub's own expressions stay opaque strings, so a test
    # cannot accidentally assert on a value the parser has already substituted.
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


@pytest.fixture(scope="module")
def remote() -> str:
    return REMOTE.read_text(encoding="utf-8")


def test_deploy_workflow_is_present() -> None:
    assert WORKFLOW.is_file(), "the deploy workflow is gone — this is how it was lost before"


def test_only_a_tag_or_a_human_can_trigger_a_deploy(workflow) -> None:
    triggers = list(workflow[True] if True in workflow else workflow["on"])
    assert triggers == ["workflow_dispatch", "push"]
    push = workflow["on"]["push"]
    assert list(push) == ["tags"], "a push to a branch must not deploy"
    assert push["tags"] == ["v[0-9]*.[0-9]*.[0-9]*"]
    assert "branches" not in push


def test_a_tag_push_does_not_deploy_by_itself(workflow) -> None:
    """Publishing a tag is the normal end of a release, not a deployment order."""
    gate = workflow["jobs"]["gate"]
    assert gate["outputs"]["deploy"] == "${{ steps.decide.outputs.deploy }}"
    body = "\n".join(step["run"] for step in gate["steps"] if "run" in step)
    assert "vars.DEPLOY_AUTO" in body
    assert "workflow_dispatch" in body
    # An unapproved tag push must leave the server alone, and must not fail:
    # a red X on every release teaches people to ignore this workflow.
    assert 'echo "deploy=false"' in body
    assert "exit 1" not in body


def test_deploy_job_is_guarded_by_the_gate(workflow) -> None:
    deploy = workflow["jobs"]["deploy"]
    assert deploy["needs"] == "gate"
    assert deploy["if"] == "needs.gate.outputs.deploy == 'true'"
    # Least privilege: the server is pulled from, not handed a token.
    assert workflow["permissions"] == {"contents": "read"}


def test_the_checked_out_tag_is_the_one_that_deploys(workflow) -> None:
    deploy = workflow["jobs"]["deploy"]
    checkout = next(s for s in deploy["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["ref"] == "${{ needs.gate.outputs.tag }}"
    assert checkout["with"]["fetch-depth"] == "0"  # BaseLoader keeps scalars as strings
    runs = "\n".join(s["run"] for s in deploy["steps"] if "run" in s)
    assert "git tag --points-at HEAD" in runs
    assert "--verify-tag" not in runs  # that flag belongs to `gh release create`


# --------------------------------------------------------------------------- #
# The shortcuts the previous version of this workflow took, and must not again.
# --------------------------------------------------------------------------- #

def _executed(workflow) -> str:
    """Only what a runner would actually execute.

    The header comment names each shortcut this file refuses to take, so a test
    that searched the whole file for `ssh-keyscan` would fail on the very
    documentation that explains why it is not there.
    """
    return "\n".join(
        step["run"] for step in workflow["jobs"]["deploy"]["steps"] if "run" in step
    )


def test_the_host_key_is_never_scraped(workflow) -> None:
    """`ssh-keyscan ... || true` accepts anything that answers on port 22."""
    text = _executed(workflow)
    assert "ssh-keyscan" not in text
    assert "StrictHostKeyChecking=yes" in text
    assert "SSH_KNOWN_HOSTS" in text
    # Without an expected key the run stops rather than deploying blind.
    assert "refusing to trust a scraped host key" in text


def test_the_private_key_is_never_left_in_a_well_known_place(workflow) -> None:
    text = _executed(workflow)
    assert "~/.ssh" not in text and "$HOME/.ssh" not in text
    assert "id_rsa" not in text
    assert "RUNNER_TEMP" in text
    # The cleanup runs even when the deploy step failed, or a failed deploy
    # leaves the key behind — which is the one time nobody is watching.
    guarded = [s for s in workflow["jobs"]["deploy"]["steps"] if s.get("if")]
    assert guarded, "no step is conditional on failure"
    assert all(s["if"] == "always()" for s in guarded)


def test_ssh_is_non_interactive(workflow) -> None:
    text = _executed(workflow)
    for option in ("BatchMode=yes", "IdentitiesOnly=yes", "ConnectTimeout"):
        assert option in text, option


def test_nothing_is_built_on_the_server(workflow) -> None:
    """CI already refuses to release a stale dashboard; compiling twice invites drift."""
    text = _executed(workflow)
    for forbidden in ("npm run build", "npm ci", "pip install", "git pull"):
        assert forbidden not in text, f"the server must not run: {forbidden}"


# --------------------------------------------------------------------------- #
# The server-side half.
# --------------------------------------------------------------------------- #

def test_remote_script_exists_is_executable_and_is_the_one_used(workflow) -> None:
    assert REMOTE.is_file()
    assert "scripts/deploy_remote.sh" in _executed(workflow)


def _index_mode(path: Path) -> str | None:
    """The permission bits git recorded for a file, or None if untracked.

    `stat()` cannot answer this on Windows, which does not carry the exec bit
    at all — that is what the advisory Windows job caught the first time. The
    index is the platform-independent truth about how the file was committed.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "--stage", "--", path.name],
            cwd=path.parent, capture_output=True, text=True, check=True,
        ).stdout.split("\t")
        return out[0].split()[0] if out and out[0].strip() else None
    except (OSError, subprocess.CalledProcessError, IndexError):
        return None


def test_remote_script_is_committed_executable() -> None:
    mode = _index_mode(REMOTE)
    if mode is None:  # an untracked checkout has no index entry to read
        if sys.platform == "win32":
            pytest.skip("no index entry and no exec bit on Windows")
        assert REMOTE.stat().st_mode & 0o111, "deploy_remote.sh must be executable"
        return
    assert mode == "100755", f"git records deploy_remote.sh as {mode}, not 100755"


def test_remote_script_deploys_a_tag_not_a_branch(remote) -> None:
    assert "checkout --quiet --detach" in remote
    assert "git pull" not in remote
    # A branch checkout would let the next fetch move the code the service runs.
    assert "refs/tags/" in remote


def test_remote_script_rejects_a_malformed_tag(remote) -> None:
    """The tag reaches systemd, a URL and a filename; vX.Y.Z or nothing."""
    assert re.search(r"\^v\[0-9\]\+\\\.\[0-9\]\+\\\.\[0-9\]\+\$", remote)


def test_remote_script_verifies_the_version_it_actually_deployed(remote) -> None:
    """"The service restarted" is not "the service is running this code"."""
    assert "service_version" in remote
    assert "wait_healthy" in remote
    assert "/api/health" in remote
    assert "EXPECT_VERSION" in remote
    # The rollback path is the same code as the forward path, so it is real.
    assert "--rollback" in remote
    assert "rolled back to" in remote


def test_remote_script_records_a_known_good_commit_before_touching_anything(remote) -> None:
    assert "last-good-commit" in remote
    # Written *after* the checkout succeeded: recording it earlier would let a
    # failed deploy overwrite the only known-good pointer with a broken one.
    assert remote.index("NEW_COMMIT=") < remote.index('> "$LAST_COMMIT_FILE"')


def test_remote_script_refuses_to_run_as_a_normal_user(remote) -> None:
    assert 'id -u)" -eq 0' in remote


# --------------------------------------------------------------------------- #
# The rest of the pipeline has to agree with this one.
# --------------------------------------------------------------------------- #

def test_release_workflow_publishes_the_tag_this_deploys() -> None:
    release = yaml.load(
        (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    tags = release[True if True in release else "on"]["push"]["tags"]
    assert tags == ["v[0-9]*.[0-9]*.[0-9]*"]


def test_the_panel_count_is_published_not_hardcoded() -> None:
    """The step name used to say 45 while the probe verified 44 (rkAtr was removed).

    A number written into a step name is a number nobody updates, so it is gone:
    ci.yml publishes no count and the probe's own JSON is the only source. This
    test is what stops it creeping back.
    """
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    probe = (ROOT / "scripts/probe_live.py").read_text(encoding="utf-8")
    match = re.search(r"^PANELS[^=]*= (\{.*?\n\})", probe, re.S | re.M)
    assert match, "PANELS is no longer a literal in probe_live.py"
    expected = sum(len(tabs) for tabs in ast.literal_eval(match.group(1)).values())

    assert "every panel explains itself" in ci
    assert "panel explains itself" not in ci.replace(
        "every panel explains itself", ""
    ), "a hardcoded count is back in the step name"
    # The count is published from the probe's JSON, not from the workflow.
    assert "--json probe-panels.json" in ci
    assert "publish the panel count to the run summary" in ci
    assert "GITHUB_STEP_SUMMARY" in ci
    # Sanity: the probe really does count what the report claims.
    assert expected == 44, "the probe's own count moved — check the summary wording"
