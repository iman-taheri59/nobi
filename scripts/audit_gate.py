#!/usr/bin/env python3
"""Fail on an npm advisory that is high and that nobody has written down as unfixable.

`npm audit --audit-level=high` is the right instinct and the wrong gate: an
advisory can be widened (a fixed package becomes vulnerable again) or published
against a version that has no newer release, and then *every* run is red for a
reason no commit can fix. That happened on 17 Nov with `picomatch`, and a gate
that cannot pass is a gate people learn to ignore.

So the gate is: every high advisory must either be fixable (`npm audit fix` moves
the tree) or be listed in `UNFIXABLE` below with the reason and the version at
which it is worth re-checking. Anything else exits 1 and prints the advisories.

Usage: audit_gate.py <npm-audit-json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: Advisory source -> why it cannot be fixed from this repository, plus the
#: version at which to look again. Removing an entry is how you make the gate
#: strict again; it is deliberately not a wildcard.
UNFIXABLE: dict[str, str] = {
    "GHSA-fc5q-39xc-vjpf": (
        "picomatch ReDoS, widened to `picomatch *` after 4.0.7 — the latest "
        "release — was still flagged. Only reachable through @tailwindcss/cli's "
        "dev-time file watcher, never in the served bundle. Re-check when "
        "@parcel/watcher publishes a picomatch 5.x resolution or tailwind drops it."
    ),
}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        report = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::error::could not read the audit report: {exc}")
        return 1

    # `npm audit` reports one entry per affected package: the package that
    # carries the advisory (with a GHSA id in `url`) and every package that
    # depends on it, whose `via` is just the name of the one below. Resolving
    # those names to the advisory they inherit is what makes the exception list
    # about advisories rather than about package names.
    vulns = report.get("vulnerabilities", {})

    def advisory_ids(name: str, seen: set[str] | None = None) -> set[str]:
        seen = seen or set()
        if name in seen or name not in vulns:
            return set()
        seen.add(name)
        ids: set[str] = set()
        for via in vulns[name].get("via", []):
            if isinstance(via, dict):
                url = via.get("url") or ""
                if "/advisories/" in url:
                    ids.add(url.rsplit("/", 1)[-1])
            else:
                ids |= advisory_ids(str(via), seen)
        return ids

    blocking: list[str] = []
    excepted: list[str] = []
    for name, vuln in sorted(vulns.items()):
        if vuln.get("severity") != "high":
            continue
        ids = sorted(advisory_ids(name))
        seen = ", ".join(ids) or "no advisory id"
        if ids and all(i in UNFIXABLE for i in ids):
            excepted.append(f"  {name}: {seen}")
            for i in ids:
                print(f"::notice::excepted {i}: {UNFIXABLE[i]}")
        else:
            blocking.append(f"  {name}: {seen}")

    if excepted:
        print("excepted advisories (documented in scripts/audit_gate.py):")
        print("\n".join(excepted))
    if blocking:
        print("::error::high npm advisories that are neither fixed nor excepted:")
        print("\n".join(blocking))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
