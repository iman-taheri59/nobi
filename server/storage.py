"""Where the application may write at runtime.

Two modules used to create their output directory **beside their own source, at
import time**: `candle_cache` and `backtest`. On the deployment this project
documents, the checkout is read-only (`ProtectSystem=strict` in
`deploy/nobi.service`, one writable path), and both modules are imported from
`main`, so the service refused to start with

    PermissionError: .../server/candle_cache
    PermissionError: .../server/backtest_reports

three times in a row, after which systemd gave up (`StartLimitBurst=3`) — a
deployment that looks like a crash loop and reads like a code bug.

The rule this module encodes:

* an output directory is **data**, so it is configured by the environment and
  lives on the data volume, not in the checkout;
* a directory that cannot be created must never stop the process. Caching and
  report writing are optimisations; losing them costs speed and history, not
  correctness, so falling back is right — but silently falling back is not, so
  every fallback is logged with the variable to set.

`NBX_CANDLE_CACHE` and `NBX_BT_DIR` are the two knobs; the install script sets
both to paths under `/srv/nobi-data`.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger("storage")


def writable_dir(env_name: str, default: Path, *, label: str) -> Path:
    """The first usable directory: `$env_name`, then `default`, then a temp dir.

    Returning a path is not a promise that writing will succeed — a disk can
    fill up later — but it does promise that importing this module cannot fail
    because of the filesystem, which is the failure this exists to prevent.
    """
    configured = os.getenv(env_name, "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.append(Path(default))
    candidates.append(Path(tempfile.gettempdir()) / f"nobi-{label}")
    first = candidates[0]
    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("%s: %s is not usable (%s)", label, path, exc)
            continue
        if path != first:
            log.warning(
                "%s: using %s instead of %s — put it on the data volume "
                "(%s=/srv/nobi-data/%s)", label, path, first, env_name, label)
        return path
    log.warning("%s: no writable directory; output will not be kept", label)
    return Path(tempfile.gettempdir()) / f"nobi-{label}"


__all__ = ["writable_dir"]
