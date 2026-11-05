"""Which commit is this *process* running? Read once at start-up, never again.

Health reports `version`, but a version string does not prove which commit is
live (review P2: "health proves the version, not the commit"). The commit is
resolved exactly once when the module is imported — i.e. when the process
started — so a later `git fetch`/checkout on disk cannot make a running
process claim code it is not executing.

Resolution order:
1. `NBX_BUILD_COMMIT` (set by a packaging step or the unit file);
2. the checkout's `.git/HEAD`, followed through a symbolic ref, including
   `packed-refs`. No subprocess, no git binary required;
3. `None` — reported honestly as unknown, never guessed.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_SHA = re.compile(r"^[0-9a-f]{40}$")


def _ref_target(git_dir: Path, ref: str) -> str | None:
    loose = git_dir / ref
    if loose.is_file():
        value = loose.read_text(encoding="utf-8").strip()
        return value if _SHA.match(value) else None
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref and _SHA.match(parts[0]):
                return parts[0]
    return None


def commit_from_checkout(root: Path) -> str | None:
    git = root / ".git"
    if git.is_file():  # worktree pointer: "gitdir: <path>"
        text = git.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir:"):
            return None
        git = (root / text.split(":", 1)[1].strip()).resolve()
    head = git / "HEAD"
    if not head.is_file():
        return None
    value = head.read_text(encoding="utf-8").strip()
    if value.startswith("ref:"):
        return _ref_target(git, value.split(":", 1)[1].strip())
    return value if _SHA.match(value) else None


def resolve(root: Path | None = None, env: dict[str, str] | None = None) -> dict[str, str | None]:
    """`{"commit": <short>, "commit_full": <sha|None>, "source": env|git|None}`."""
    environ = os.environ if env is None else env
    raw = (environ.get("NBX_BUILD_COMMIT") or "").strip().lower()
    if raw and re.fullmatch(r"[0-9a-f]{7,40}", raw):
        return {"commit": raw[:12], "commit_full": raw if len(raw) == 40 else None, "source": "env"}
    sha = commit_from_checkout(root or Path(__file__).resolve().parent.parent)
    if sha:
        return {"commit": sha[:12], "commit_full": sha, "source": "git"}
    return {"commit": None, "commit_full": None, "source": None}


#: Frozen at process start on purpose; see module docstring.
BUILD: dict[str, str | None] = resolve()
