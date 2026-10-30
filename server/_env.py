"""Load `server/.env` before anything reads `os.environ`.

Why this module exists
----------------------
`.env.example` shipped with the project and every doc said "copy it to .env".
Nothing ever called `load_dotenv()`, so a user who followed the instructions got
no tokens at all and no error — the dashboard simply said "not configured".
That is the worst kind of configuration bug: silent.

Import order matters. `notify.TOKEN`, `security.API_TOKEN` and
`ws_execution.WS_URL` are read at *module import* time, so this has to run
before those modules are imported — `import _env` is the first import in
`main.py`.

Rules
-----
* **Never override** a real environment variable. A value exported in the shell
  (or injected by systemd / Docker) outranks the file.
* **Never raise.** A malformed `.env` must not stop the server; it is recorded
  in `STATUS` and reported by `/api/settings` instead of being swallowed.
"""
from __future__ import annotations

import os
from pathlib import Path

from runtime_settings import parse_managed

ENV_PATH = Path(__file__).resolve().with_name(".env")
MANAGED_ENV_PATH = Path(os.getenv("NBX_MANAGED_ENV") or Path(__file__).resolve().with_name("managed.env"))

SOURCES = {key: "process_environment" for key, value in os.environ.items() if value}

STATUS: dict = {
    "path": str(ENV_PATH),
    "exists": ENV_PATH.is_file(),
    "loaded": False,
    "vars": 0,
    "error": None,
}


def _parse(text: str) -> dict[str, str]:
    """Minimal .env parser (KEY=value, # comments, optional quotes).

    Deliberately not a dependency: `python-dotenv` is in requirements but this
    keeps the load path visible and testable without it.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:  # strip a trailing comment only when it is clearly one
            hash_at = value.find(" #")
            if hash_at != -1:
                value = value[:hash_at].rstrip()
        out[key] = value
    return out


def load(path: Path | None = None) -> dict:
    """Read the file into `os.environ` without overriding existing values."""
    target = path or ENV_PATH
    # Reset first: without this, a second call with a missing file kept
    # reporting `loaded: True, vars: N` from the previous successful load —
    # the UI would have said ".env loaded" while reading nothing.
    STATUS.update(path=str(target), exists=target.is_file(),
                  loaded=False, vars=0, error=None)
    if not STATUS["exists"]:
        return STATUS
    try:
        values = _parse(target.read_text(encoding="utf-8"))
    except OSError as exc:                      # unreadable / encoding
        STATUS["error"] = f"{type(exc).__name__}: {exc}"
        return STATUS
    applied = 0
    for key, value in values.items():
        if key in os.environ and os.environ[key] != "":
            continue                            # real env wins
        os.environ[key] = value
        SOURCES[key] = "server/.env"
        applied += 1
    STATUS.update(loaded=True, vars=applied, error=None)
    return STATUS


STATUS = load()


def load_managed(path: Path | None = None) -> dict:
    """Load the wizard's non-secret file below real env and server/.env."""
    target = path or MANAGED_ENV_PATH
    status = {"path": str(target), "exists": target.is_file(), "loaded": False,
              "vars": 0, "error": None}
    if not target.is_file():
        return status
    try:
        values = parse_managed(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
        return status
    applied = 0
    for key, value in values.items():
        if key not in os.environ or os.environ[key] == "":
            os.environ[key] = value
            SOURCES[key] = "managed.env"
            applied += 1
    status.update(loaded=True, vars=applied)
    return status


MANAGED_STATUS = load_managed()
