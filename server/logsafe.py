"""Keep secrets out of the logs.

`httpx` logs every request at INFO with the full URL. For Telegram the bot token
*is* part of the URL (`/bot<token>/getMe`), so a perfectly normal INFO line
writes the secret to stdout, journald, Docker logs and any log shipper — where
it outlives the process and is readable by anyone with log access.

This context manager silences the `httpx` logger only for the duration of a
token-bearing request. General request logging stays on.
"""
from __future__ import annotations

import contextlib
import logging

_HTTPX = logging.getLogger("httpx")


@contextlib.contextmanager
def quiet_httpx():
    prev = _HTTPX.level
    _HTTPX.setLevel(logging.WARNING)
    try:
        yield
    finally:
        _HTTPX.setLevel(prev)


def redact(url: str) -> str:
    """`.../bot123:ABC/getMe` -> `.../bot***/getMe` (for our own log lines)."""
    if "/bot" not in url:
        return url
    head, _, tail = url.partition("/bot")
    rest = tail
    slash = rest.find("/")
    return f"{head}/bot***{rest[slash:] if slash != -1 else ''}"
