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
2. **Redact known secret shapes and sensitive structured keys.** All text
   fields are scrubbed on capture and read. This is best effort, not a proof
   that an arbitrary unlabeled secret can never appear in free text.
3. **Bounded.** 500 rows in memory, 2000 in SQLite; a crash loop cannot fill
   the disk.
4. **Cheap to read.** `GET /api/errors` returns newest first with a level and
   source filter, plus counts for the dashboard badge.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import re
import sqlite3
import threading
import time
import traceback
from collections import deque
from pathlib import Path
from typing import Any
from uuid import uuid4

import journal

LEVELS = ("debug", "info", "warning", "error", "critical")
MAX_MEMORY = 500
MAX_DB = 2000

_memory: deque[dict[str, Any]] = deque(maxlen=MAX_MEMORY)
_lock = threading.Lock()
_io_lock = threading.RLock()  # serialize this process's reads/writes, not other workers
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
    (re.compile(r"(?i)\b(password|passwd|api[_-]?secret|[a-z_]*token|cookie|session(?:_id)?)\b\s*[:=]\s*[^\s,;}]+"), r"\1=" + _REDACTED),
    (re.compile(r"(https?://)[^/\s:@]+:[^/@\s]+@"), r"\1" + _REDACTED + "@"),
    # long hex blobs (hashes, keys) — nothing legitimate that long is worth keeping
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), _REDACTED),
)


_SECRET_KEY = re.compile(r"(?i)(token|secret|password|passwd|authorization|cookie|session|privatekey|apikey)")
_QUOTED_SECRET = re.compile(
    r"(?i)([\"']?(?:[a-z_]*token|[a-z_]*secret|password|passwd|authorization|cookie|api[_-]?key)[\"']?\s*[:=]\s*)([\"'])([^\r\n]*)")


def _text(value: Any) -> str:
    try:
        s = str(value)
    except Exception:
        return "[unprintable]"
    s = s[:16384]
    s = _QUOTED_SECRET.sub(lambda m: m[1] + m[2] + _REDACTED + m[2], s)
    for pattern, repl in _RULES:
        s = pattern.sub(repl, s)
    return s[:4000]


def scrub(value: Any) -> str:
    """Bounded, key-aware structured redaction plus legacy free-text rules.

    Legacy disk bytes are not rewritten; serving them passes through this too.
    Unknown/unlabeled secrets in arbitrary prose remain an explicit limitation.
    """
    if value is None:
        return ""
    if isinstance(value, str) and len(value)<=32768 and value.lstrip().startswith(("{", "[", "(")):
        try:
            try:
                decoded = json.loads(value)
            except ValueError:
                decoded = ast.literal_eval(value)  # bounded legacy repr, never eval()
            if isinstance(decoded, (dict, list, tuple)):
                value = decoded
        except (ValueError, TypeError, SyntaxError, RecursionError, MemoryError):
            pass
    budget = [128]
    def clean(v: Any, depth: int = 0) -> Any:
        budget[0] -= 1
        if budget[0] < 0 or depth > 6:
            return "[truncated]"
        if isinstance(v, dict):
            out: dict[str, Any] = {}
            for i, (k, x) in enumerate(v.items()):
                if i >= 64 or budget[0] <= 0:
                    out["[truncated]"] = True
                    break
                key = _text(k)
                normalized = re.sub(r"[^a-z0-9]", "", key.lower())
                out[key] = _REDACTED if _SECRET_KEY.search(normalized) else clean(x, depth+1)
            return out
        if isinstance(v, (list, tuple)):
            out_list = []
            for i, x in enumerate(v):
                if i >= 64 or budget[0] <= 0:
                    out_list.append("[truncated]")
                    break
                out_list.append(clean(x, depth+1))
            return out_list
        if v is None or isinstance(v, (bool, int, float)):
            return v
        return _text(v)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(clean(value), ensure_ascii=False, default=str)[:4000]
    return _text(value)


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


def _store_key() -> str:
    return str(Path(journal.DB_PATH).resolve())


def capture(level: str = "error", source: str = "app", message: str = "",
            detail: str | None = None, context: Any = None,
            client: str | None = None, exc: BaseException | None = None) -> dict:
    """Record an event; failed SQLite writes remain visible as memory-only."""
    global _seq
    lvl = (level or "error").lower()
    if lvl not in LEVELS:
        lvl = "error"
    if exc is not None:
        message = message or f"{type(exc).__name__}: {exc}"
        detail = detail or "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    row = {"ts": int(time.time()), "level": lvl, "source": scrub(source or "app")[:64],
           "message": scrub(message)[:1000], "detail": scrub(detail) or None,
           "context": scrub(context)[:1000] or None, "client": scrub(client)[:64] or None,
           "persisted": False}
    with _io_lock:
        row["_store"] = _store_key()
        with _lock:
            _seq += 1
            row["id"] = -_seq  # process-local memory IDs never alias SQLite IDs
            _memory.append(row)
        try:
            conn = journal.connect()
            try:
                _init(conn)
                cur = conn.execute(
                    "INSERT INTO error_log (ts,level,source,message,detail,context,client) VALUES(?,?,?,?,?,?,?)",
                    tuple(row[k] for k in ("ts","level","source","message","detail","context","client")))
                db_id = int(cur.lastrowid or 0)
                _prune(conn)
                conn.commit()
                with _lock:
                    row["id"] = db_id
                    row["persisted"] = True
            finally:
                conn.close()
        except Exception:  # diagnostic failures must not cascade into the caller
            pass
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _served(row: dict) -> dict:
    out = {k: v for k, v in row.items() if not k.startswith("_")}
    for key in ("source", "message", "detail", "context", "client"):
        out[key] = scrub(out.get(key)) or ("" if key in ("source", "message") else None)
    return out


def _counts(rows: list[dict], now: int) -> dict[str, Any]:
    out: dict[str, Any] = dict.fromkeys(LEVELS, 0)
    out.update(total=len(rows), last_24h=0, last_ts=None, sources={})
    cutoff = now - 86400
    for r in rows:
        out[r["level"]] = out.get(r["level"], 0) + 1
        out["last_24h"] += int(r["ts"] >= cutoff)
        out["last_ts"] = max(out["last_ts"] or 0, r["ts"])
        src = r["source"]
        out["sources"][src] = out["sources"].get(src, 0) + 1
    return out


def snapshot(limit: int = 100, level: str | None = None, source: str | None = None,
             since: int | None = None, q: str | None = None) -> dict[str, Any]:
    """One observed row-set for table, counts and scope, not two racing reads.

    SQL retains priority. Merge only this process's unpersisted ring events;
    do not resurrect pruned/cleared DB rows from the ring. On read failure the
    whole ring is explicitly partial. No initialization or migration on read.
    Counts are for this observed retained snapshot, never lifetime incidents.
    """
    limit = max(1, min(int(limit), 500))
    db_ok = False
    read_truncated = False
    with _io_lock:
        with _lock:
            mem = [dict(r) for r in _memory if r.get("_store") == _store_key()]
        db_rows = []
        conn = None
        try:
            conn = journal.connect_readonly()
            conn.execute("BEGIN")
            raw = conn.execute("SELECT * FROM error_log ORDER BY ts DESC,id DESC LIMIT ?", (MAX_DB+1,)).fetchall()
            read_truncated = len(raw) > MAX_DB
            db_rows = [dict(r, persisted=True) for r in raw[:MAX_DB]]
            db_ok = True
        except Exception:
            pass
        finally:
            if conn is not None:
                conn.close()
        pending = [r for r in mem if r.get("persisted") is False]
        observed = db_rows + pending if db_ok else mem
    rows = [_served(r) for r in observed]
    rows.sort(key=lambda r: (r["ts"], r["id"] if r["id"] > 0 else -r["id"]), reverse=True)
    now = int(time.time())
    counts_all = _counts(rows, now)
    selected = rows
    if level:
        selected = [r for r in selected if r["level"] == level]
    if source:
        selected = [r for r in selected if r["source"] == source]
    if since is not None:
        selected = [r for r in selected if r["ts"] >= since]
    if q:
        needle = q.casefold()
        selected = [r for r in selected if needle in (r["message"] or "").casefold()
                    or needle in (r["detail"] or "").casefold()]
    loaded = selected[:limit]
    return {"errors": loaded, "n": len(loaded), "counts": counts_all,
            "filtered_count": len(selected), "filtered_counts": _counts(selected, now),
            "truncated": len(selected) > len(loaded), "generated_at": now,
            "storage": {"backend": "sqlite+pending-memory" if db_ok and pending else "sqlite" if db_ok else "memory",
                        "sqlite_readable": db_ok, "pending_memory": len(pending),
                        "degraded": not db_ok or bool(pending) or read_truncated,
                        "db_read_truncated": read_truncated, "max_db": MAX_DB, "max_memory": MAX_MEMORY,
                        "scope": "observed-retained-snapshot", "durability_verified": False},
            "filters": {"level": level, "source": source, "since": since, "q": q, "limit": limit}}


def recent(limit: int = 100, level: str | None = None, source: str | None = None,
           since: int | None = None, q: str | None = None) -> list[dict]:
    return snapshot(limit, level, source, since, q)["errors"]


def counts() -> dict[str, Any]:
    return snapshot()["counts"]


def clear() -> int:
    """Explicit owner action. Preserve memory and propagate failure to route.

    Not cross-process/crash atomic with the separately written audit record.
    """
    with _io_lock:
        with _lock:
            pending_removed = sum(1 for r in _memory if r.get("_store") == _store_key() and r.get("persisted") is False)
        conn = journal.connect()
        try:
            _init(conn)
            removed = conn.execute("SELECT COUNT(*) FROM error_log").fetchone()[0]
            conn.execute("DELETE FROM error_log")
            conn.commit()
        finally:
            conn.close()
        with _lock:
            keep = [r for r in _memory if r.get("_store") != _store_key()]
            _memory.clear()
            _memory.extend(keep)
        return int(removed) + pending_removed


# --------------------------------------------------------------------------- #
# Logging bridge
# --------------------------------------------------------------------------- #
class Handler(logging.Handler):
    """Copy ERROR and above from any logger into the store.

    Without this the app logs tracebacks to stdout and nobody who is looking at
    the dashboard ever sees them.
    """

    IGNORE = ("nbx-app.errorlog",)   # never recurse into ourselves

    def emit(self, record: logging.LogRecord) -> None:
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
    """Explicit marker write/read from SQLite, not ID equality in fallback.

    A verified round trip is not an fsync, crash-survival or replication test.
    """
    before = counts()["total"]
    marker = "self-test marker " + str(uuid4())
    row = capture(level="info", source="errorlog.selftest", message=marker,
                  context={"pid": os.getpid()})
    verified = False
    conn = None
    try:
        if row["persisted"]:
            conn = journal.connect_readonly()
            found = conn.execute("SELECT * FROM error_log WHERE id=?", (row["id"],)).fetchone()
            verified = found is not None and all(found[k] == row[k] for k in
                ("ts", "level", "source", "message", "detail", "context", "client"))
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()
    return {"ok": verified, "wrote": row["id"], "writable": row["persisted"],
            "readable": verified, "sqlite_verified": verified, "durability_verified": False,
            "total_before": before, "total_after": counts()["total"],
            "storage": "sqlite" if verified else "sqlite-unverified" if row["persisted"] else "memory"}
