#!/usr/bin/env python3
"""Where the browser comes from — one answer for every UI script.

Two different failures used to produce the same message, and the message sent
people to the wrong place:

1. The `playwright` **package** is missing *for the interpreter that runs the
   script*. A bare `python` on Windows is often not the project's venv, so
   `pip install playwright` in one shell and `python scripts/probe_live.py` in
   another reach different site-packages. The old text — "playwright is not
   installed: pip install playwright && python -m playwright install
   chromium" — answered a package problem with a browser download, and if the
   interpreter really was the problem, it downloaded into the wrong place.

2. The package is fine but has no browser of its own. `playwright install
   chromium` fetches one pinned revision from Playwright's CDN; a blocked CDN,
   a proxy, or an antivirus scanner that truncates the archive stops it. The
   script can use a browser that is already on the machine instead.

So every UI script gets the same two flags and the same honest error:

  --channel msedge|chrome|chromium   the browser already installed
  --executable PATH                  any Chromium build you unpacked yourself
                                     (NBX_CHROME also works)

Both messages print `sys.executable`, because "which python am I?" is the
question the old one could not answer.
"""

from __future__ import annotations

import argparse
import os
import sys

CHANNELS = ("chrome", "msedge", "chromium")

# The three ways out, printed identically wherever a script gives up on the
# browser. Keeping them in one string means a help text cannot drift from a
# failure message.
WAYS = (
    "  python -m playwright install chromium     # Playwright's own build\n"
    "  --channel msedge                          # Edge, already on Windows\n"
    "  --executable C:\\path\\to\\chrome.exe       # a build you unpacked"
)


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """Attach `--channel` and `--executable` to a script's parser."""
    ap.add_argument("--channel", choices=list(CHANNELS),
                    help="launch an installed browser instead of Playwright's download")
    ap.add_argument("--executable",
                    help="path to chrome.exe / chrome (default: $NBX_CHROME)")


def launch_args(channel: str | None = None,
                executable: str | None = None) -> dict[str, str]:
    """Keyword arguments for `chromium.launch()`.

    A path wins over a channel, and `$NBX_CHROME` fills in when neither flag is
    given. A path that does not exist stops the script here with the path in
    the message: a typo should say so, not resurface as a Playwright error
    three frames down.
    """
    exe = executable or os.getenv("NBX_CHROME") or ""
    if exe:
        if not os.path.isfile(exe):
            print(f"--executable does not exist: {exe}", file=sys.stderr)
            raise SystemExit(2)
        return {"executable_path": exe}
    if channel:
        return {"channel": channel}
    return {}


def missing_package_message() -> str:
    """What to print when `import playwright` fails in this interpreter."""
    return (
        "the playwright package is not installed for this interpreter:\n"
        f"  {sys.executable}\n"
        "install it with that same interpreter, then pick a browser:\n"
        f'  "{sys.executable}" -m pip install playwright\n'
        f"{WAYS}\n"
        "if the project has a venv, run the script with its python instead — on\n"
        r"Windows that is .venv\Scripts\python.exe — otherwise the package lands"
        "\nsomewhere this script never looks."
    )


def launch_failed_message(exc: BaseException) -> str:
    """What to print when the package imported but no browser would start."""
    text = str(exc).strip()
    first = text.splitlines()[0] if text else type(exc).__name__
    return (
        f"could not start a browser: {first}\n"
        "one of these fixes it:\n"
        f"{WAYS}"
    )
