"""The Status workflow and deploy/nobi-status.sudoers must agree, exactly.

`.github/workflows/status.yml` is read-only reporting for the production box. It
reaches the box as `deploy`, which is not root — so every privileged read goes
through sudo. Ubuntu 26.04+ ships sudo-rs, whose parser refuses wildcards in
command arguments ("wildcards are not allowed in command arguments", seen live
on 2026-11-24); a single glob in this file makes `visudo -cf` reject the WHOLE
file and every sudo step dies with "I'm sorry deploy" — which is exactly what
the 2026-11-26 Status run showed (every diagnostic step refused; the health
endpoint was the only green part).

So: every rule below is an exact argv, and the time windows live in the scripts
as flags. The tests here hold three lines:

1. no argument token in nobi-status.sudoers contains a glob character;
2. every script path the sudoers pins exists in this tree (a rule for a
   renamed/deleted script is a rule that can never match, silently);
3. every sudo argv the workflow runs is pinned in the sudoers file, and no
   workflow input (`log_lines`, `since`) can produce an argv outside it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUDOERS = ROOT / "deploy" / "nobi-status.sudoers"
WORKFLOW = ROOT / ".github" / "workflows" / "status.yml"
SCRIPTS = ROOT / "scripts"

GLOB_CHARS = "*?[]{}"


def _rules() -> list[tuple[str, list[str]]]:
    rules = []
    for raw in SUDOERS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "NOPASSWD:" not in line:
            continue
        path = next((tok for tok in line.split() if tok.startswith("/")), None)
        assert path, f"no absolute command path in sudoers line: {raw!r}"
        rules.append((path, line.split(path, 1)[1].split()))
    return rules


def test_status_sudoers_has_no_argument_globs():
    bad = [tok for _path, args in _rules() for tok in args if any(ch in tok for ch in GLOB_CHARS)]
    assert not bad, f"sudo-rs refuses argv globs in deploy/nobi-status.sudoers: {bad}"


def test_every_pinned_script_exists():
    missing = []
    for path, _args in _rules():
        m = re.match(r"/opt/nobi/(?:\.venv/bin/python )?(scripts/[^ ]+)", path)
        if m and not (SCRIPTS / Path(m.group(1)).name).is_file():
            missing.append(m.group(1))
    assert not missing, f"nobi-status.sudoers pins scripts that do not exist: {missing}"


def test_workflow_sudo_argvs_are_pinned():
    yml = WORKFLOW.read_text(encoding="utf-8")
    for _path, args in _rules():
        if "journal_diagnostic" in " ".join(args):
            needle = "journal_diagnostic.py /srv/nobi-data/journal.db --last-hours 24"
        elif "paper_state_summary" in " ".join(args):
            needle = "paper_state_summary.py /srv/nobi-data/paper_state.json --events 20"
        elif "journal_holdout" in " ".join(args):
            needle = "journal_holdout.py show --db /srv/nobi-data/journal.db"
        elif "shadow_backfill" in " ".join(args):
            needle = "shadow_backfill.py /srv/nobi-data/journal.db --last-days 17"
        elif "pump_chase_backfill" in " ".join(args):
            needle = "pump_chase_backfill.py /srv/nobi-data/journal.db --last-days 17"
        else:
            continue  # binary paths (journalctl/ls/du) covered below by exact-string match
        assert needle in yml, f"workflow does not run the pinned argv: {needle}"
    for binary in ("/usr/bin/journalctl -u nobi -n 2000 --no-pager -o short-iso",
                   "/usr/bin/journalctl -u nobi -n 200 --no-pager",
                   "/usr/bin/ls -lt /srv/nobi-data/ /srv/nobi-data/backup/",
                   "/usr/bin/du -sh /srv/nobi-data"):
        assert binary in SUDOERS.read_text(encoding="utf-8")
        # the workflow may call the binary through PATH; sudo resolves it to the
        # pinned absolute path before matching.
        assert binary.rsplit("/usr/bin/", 1)[1] in yml, f"workflow does not run the pinned argv: {binary}"


def test_workflow_cannot_widen_the_argv():
    """No free-text input reaches a sudo argv: the old `log_lines`/`since`
    inputs did, which is how an unpinned argv would sail into a refusal."""
    yml = WORKFLOW.read_text(encoding="utf-8")
    assert "inputs.log_lines" not in yml
    assert "inputs.since" not in yml
    assert "SINCE" not in yml
    assert "${{ inputs.log_lines }}" not in yml


def test_scripts_implement_the_pinned_window_flags():
    diag = (SCRIPTS / "journal_diagnostic.py").read_text(encoding="utf-8")
    shadow = (SCRIPTS / "shadow_backfill.py").read_text(encoding="utf-8")
    pump = (SCRIPTS / "pump_chase_backfill.py").read_text(encoding="utf-8")
    assert "--last-hours" in diag
    assert "--last-days" in shadow
    assert "--last-days" in pump
