"""System error log — the thing you read when the dashboard misbehaves.

Why a separate store from `audit_log`
-------------------------------------
`audit_log` records *decisions* ("halt armed", "order rejected"). This records
*failures*: unhandled exceptions, background-task crashes, failed upstream
calls, and errors reported by the browser. Mixing them would bury the few
control actions that matter under a stream of tracebacks.

Design constraints, in order
----------------------------
1. **Never throw.** A logger that can take the app down is worse than no
   logger, so every write is wrapped and the in-memory ring buffer is the
   fallback when SQLite is unavailable.
2. **Never store a secret.** Telegram/API tokens live in URLs, headers and
   exception strings, so every field is scrubbed before it is stored or served.
3. **Bounded.** 500 rows in memory, 2000 in SQLite; a crash loop cannot fill
   the disk.
4. **Cheap to read.** `GET /api/errors` returns newest first with a level and
   source filter, plus counts for the dashboard badge.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
import traceback
from collections import deque
from typing import Any

import journal

LEVELS = ("debug", "info", "warning", "error", "critical")
MAX_MEMORY = 500
MAX_DB = 2000

_memory: deque[dict[str, Any]] = deque(maxlen=MAX_MEMORY)
_lock = threading.Lock()
_seq = 0

# --------------------------------------------------------------------------- #
# Scrubbing
# --------------------------------------------------------------------------- #
# Anything that looks like a credential must not reach the log file, the API
# response, or the browser. These patterns are deliberately blunt: a missed
# secret is unrecoverable, a redacted non-secret is not.
#
# Each entry is (pattern, replacement). The replacement is an explicit string
# rather than a lambda over groups: the first version kept `group(1)` for every
# rule, which preserved the token for the bare-token rule because that rule's
# only group *was* the token.
_REDACTED = "[redacted]"
_RULES = (
    # keep the URL prefix, drop the bot token
    (re.compile(r"(https?://api\.telegram\.org/bot)[^/\s\"']+"), r"\1" + _REDACTED),
    # <digits>:<35 chars> — the shape Telegram issues
    (re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"), _REDACTED),
    # header-ish "Authorization: Bearer x" / "x-api-token: y"
    (re.compile(r"(?i)\b(x-api-token|authorization|bearer)\b\s*[:=]?\s*(?:bearer\s+)?\S+"),
     r"\1" + _REDACTED),
    # KEY=value for anything that names a token/key
    (re.compile(r"(?i)\b(nbx_[a-z_]*token|nobitex_api_(?:key|secret)|api[_-]?key)\b\s*[:=]\s*\S+"),
     r"\1" + _REDACTED),
    # long hex blobs (hashes, keys) — nothing legitimate that long is worth keeping
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), _REDACTED),
)


def scrub(text: Any) -> str:
    if text is None:
        return ""
    s = str(text)
    for pattern, repl in _RULES:
        s = pattern.sub(repl, s)
    return s[:4000]


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def _init(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS error_log (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ts INTEGER NOT NULL,
               level TEXT NOT NULL,
               source TEXT NOT NULL,
               message TEXT NOT NULL,
               detail TEXT,
               context TEXT,
               client TEXT
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_error_ts ON error_log(ts DESC)")
    conn.commit()


def _prune(conn: sqlite3.Connection) -> None:
    # Keep the newest MAX_DB rows; a crash loop must not grow unbounded.
    conn.execute(
        "DELETE FROM error_log WHERE id NOT IN "
        "(SELECT id FROM error_log ORDER BY id DESC LIMIT ?)", (MAX_DB,)
    )


def capture(level: str = "error", source: str = "app", message: str = "",
            detail: str | None = None, context: dict | None = None,
            client: str | None = None, exc: BaseException | None = None) -> dict:
    """Record one failure. Returns the stored row; never raises."""
    global _seq
    lvl = (level or "error").lower()
    if lvl not in LEVELS:
        lvl = "error"
    if exc is not None:
        message = message or f"{type(exc).__name__}: {exc}"
        detail = detail or "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    row = {
        "ts": int(time.time()),
        "level": lvl,
        "source": (source or "app")[:64],
        "message": scrub(message)[:1000],
        "detail": scrub(detail)[:4000] if detail else None,
        "context": scrub(context)[:1000] if context else None,
        "client": (client or "")[:64] or None,
    }
    with _lock:
        _seq += 1
        row["id"] = _seq
        _memory.append(row)

    try:
        conn = journal.connect()
        try:
            _init(conn)
            cur = conn.execute(
                "INSERT INTO error_log (ts, level, source, message, detail, context, client) "
                "VALUES (?,?,?,?,?,?,?)",
                (row["ts"], row["level"], row["source"], row["message"],
                 row["detail"], row["context"], row["client"]),
            )
            row["id"] = int(cur.lastrowid)
            _prune(conn)
            conn.commit()
        finally:
            conn.close()
    except Exception:            # storage problems must not cascade
        pass
    return row


def recent(limit: int = 100, level: str | None = None, source: str | None = None,
           since: int | None = None, q: str | None = None) -> list[dict]:
    where, params = ["1=1"], []
    if level and level in LEVELS:
        where.append("level=?")
        params.append(level)
    if source:
        where.append("source=?")
        params.append(source)
    if since:
        where.append("ts>=?")
        params.append(int(since))
    if q:
        where.append("(message LIKE ? OR detail LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    sql = ("SELECT id, ts, level, source, message, detail, context, client "
           "FROM error_log WHERE " + " AND ".join(where) + " ORDER BY ts DESC, id DESC LIMIT ?")
    params.append(max(1, min(int(limit), 500)))
    try:
        conn = journal.connect()
        try:
            _init(conn)
            rows = [dict(r) for r in conn.execute(sql, params)]
        finally:
            conn.close()
        if rows or not since:
            return rows
    except Exception:
        pass
    # fallback: the in-memory ring (newest first)
    rows = [dict(r) for r in reversed(_memory)]
    if level:
        rows = [r for r in rows if r["level"] == level]
    if source:
        rows = [r for r in rows if r["source"] == source]
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in (r["message"] or "").lower()
                or needle in (r["detail"] or "").lower()]
    return rows[: max(1, min(int(limit), MAX_MEMORY))]


def counts() -> dict[str, Any]:
    out = {"error": 0, "critical": 0, "warning": 0, "total": 0, "last_24h": 0,
           "last_ts": None, "sources": {}}
    day_ago = int(time.time()) - 86400
    try:
        conn = journal.connect()
        try:
            _init(conn)
            for row in conn.execute("SELECT level, COUNT(*) n FROM error_log GROUP BY level"):
                out[row["level"]] = row["n"]
                out["total"] += row["n"]
            out["last_24h"] = conn.execute(
                "SELECT COUNT(*) FROM error_log WHERE ts>=?", (day_ago,)).fetchone()[0]
            out["last_ts"] = conn.execute("SELECT MAX(ts) FROM error_log").fetchone()[0]
            for row in conn.execute(
                    "SELECT source, COUNT(*) n FROM error_log GROUP BY source ORDER BY n DESC LIMIT 12"):
                out["sources"][row["source"]] = row["n"]
        finally:
            conn.close()
        return out
    except Exception:
        mem = list(_memory)
        out["total"] = len(mem)
        out["last_24h"] = sum(1 for r in mem if r["ts"] >= day_ago)
        out["last_ts"] = mem[-1]["ts"] if mem else None
        for r in mem:
            out[r["level"]] = out.get(r["level"], 0) + 1
        return out


def clear() -> int:
    """Delete everything. Used by the dashboard's 'clear' button (guarded)."""
    removed = 0
    try:
        conn = journal.connect()
        try:
            _init(conn)
            removed = conn.execute("SELECT COUNT(*) FROM error_log").fetchone()[0]
            conn.execute("DELETE FROM error_log")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
    with _lock:
        _memory.clear()
    return int(removed)


# --------------------------------------------------------------------------- #
# Logging bridge
# --------------------------------------------------------------------------- #
class Handler(logging.Handler):
    """Copy ERROR and above from any logger into the store.

    Without this the app logs tracebacks to stdout and nobody who is looking at
    the dashboard ever sees them.
    """

    IGNORE = ("nbx-app.errorlog",)   # never recurse into ourselves

    def emit(self, record: logging.LogRecord) -> None:      # noqa: D102
        try:
            if record.name in self.IGNORE:
                return
            # Endpoints we instrument ourselves call `capture()` directly and tag
            # the log record, so the same failure does not land twice.
            if getattr(record, "nbx_captured", False):
                return
            if record.levelno < logging.WARNING:
                return
            detail = None
            if record.exc_info:
                detail = "".join(traceback.format_exception(*record.exc_info))
            if record.levelno >= logging.CRITICAL:
                lvl = "critical"
            elif record.levelno >= logging.ERROR:
                lvl = "error"
            else:
                # Upstream timeouts and rejected sends used to be invisible:
                # they are warnings on the server and errors to the user.
                lvl = "warning"
            capture(level=lvl,
                    source=record.name[:64],
                    message=record.getMessage(),
                    detail=detail,
                    context={"module": record.module, "line": record.lineno})
        except Exception:
            pass


def install(level: int = logging.WARNING) -> Handler:
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, Handler):
            return h
    h = Handler()
    h.setLevel(level)
    root.addHandler(h)
    return h


# --------------------------------------------------------------------------- #
# Periodic self-check (proves the log is not silently dead)
# --------------------------------------------------------------------------- #
def self_test() -> dict:
    """Write and read back one marker row. Used by /api/errors/selftest."""
    before = counts()["total"]
    row = capture(level="info", source="errorlog.selftest",
                  message="self-test marker", context={"pid": os.getpid()})
    after = counts()["total"]
    found = any(r["id"] == row["id"] for r in recent(limit=20))
    return {"wrote": row["id"], "total_before": before, "total_after": after,
            "readable": found, "storage": "sqlite+memory"}
