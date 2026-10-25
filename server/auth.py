"""Accounts, sessions and invites — one shared trading account, several people.

Why this exists
---------------
Until now the only credential was `NBX_API_TOKEN`, and the browser had nowhere
to put it: the deployment guide told people to open the console and type
`localStorage.setItem(...)`. Reads were unauthenticated by design. That is fine
for one person on a laptop and wrong for a dashboard that can place orders.

This module adds the missing concept: a *person*. Several people can sign in to
the same trading account (position state stays global and shared — see
ADR-0018); what changes is that every request now has an identity, every
mutation has an actor, and the door is closed by default once the first account
exists.

Design decisions that are security, not style
---------------------------------------------
* **scrypt from the standard library.** No new dependency, and the hash string
  carries its own parameters (``scrypt$n$r$p$salt$hash``) so they can be raised
  later without a rewrite.
* **Sessions are stored hashed.** The cookie holds 32 random bytes; the table
  holds ``sha256(token)``. A leaked database does not log anyone in.
* **CSRF is a second lock.** The session cookie is ``SameSite=Lax`` and
  ``HttpOnly``, which already blocks cross-site POSTs in current browsers. A
  per-session CSRF token in a readable cookie plus an ``X-CSRF-Token`` header
  adds the case SameSite does not cover (sub-domains, older clients) — and it
  is testable, which "the browser handles it" is not.
* **Failure is indistinguishable.** Unknown user and wrong password return the
  same message, and an unknown user still pays for one scrypt comparison so the
  response time does not reveal which accounts exist.
* **Invites are single-use and role-bound.** The role is decided when the
  invite is created, not when it is redeemed, so a viewer invite cannot become
  an owner by editing a request.
* **No self-service escalation.** Registration cannot choose a role; only an
  owner can change one.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

#: Where accounts live. Separate from `journal.db` on purpose: the journal is a
#: trading record that may be rebuilt or compacted, and losing it must not lock
#: everybody out. On a server both belong on the persistent volume.
AUTH_DB = Path(os.getenv("NBX_AUTH_DB") or Path(__file__).with_name("auth.db"))

#: How long a login lasts. Sliding: extended on use, at most hourly.
SESSION_DAYS = max(1, int(os.getenv("NBX_SESSION_DAYS", "14")))

#: Failed logins allowed per (address, username) inside `LOGIN_WINDOW_S`.
LOGIN_ATTEMPTS = max(3, int(os.getenv("NBX_LOGIN_ATTEMPTS", "10")))
LOGIN_WINDOW_S = max(60, int(os.getenv("NBX_LOGIN_WINDOW_S", "900")))

ROLES = ("owner", "operator", "viewer")
_RANK = {"viewer": 1, "operator": 2, "owner": 3}

MIN_PASSWORD = 12
MAX_PASSWORD = 200

# scrypt cost: ~40 ms on a modern core. Deliberately not tunable by env — a
# deployment that lowers this is a deployment that silently weakens the hashes.
_N, _R, _P, _DKLEN = 2**14, 8, 1, 32

#: Obvious passwords, rejected outright. Length does the real work; this list
#: only catches the ones people type when they are being careless.
_BLOCKLIST = {
    "password", "password1", "password123", "passw0rd", "123456789012",
    "qwertyuiop12", "administrator", "letmein12345", "changeme1234",
    "nobi", "nobitex", "tradingbot", "qwerty123456",
}


class AuthError(Exception):
    """A refusal with a message meant for the person who tried."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: str
    active: bool
    created_at: int
    last_login: int | None

    @property
    def can_mutate(self) -> bool:
        return self.active and _RANK.get(self.role, 0) >= _RANK["operator"]

    @property
    def is_owner(self) -> bool:
        return self.active and self.role == "owner"

    def public(self) -> dict:
        return {"id": self.id, "username": self.username, "role": self.role,
                "active": self.active, "created_at": self.created_at,
                "last_login": self.last_login}


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
_ready: set[str] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    username_key  TEXT NOT NULL UNIQUE,       -- lowercased, for case-insensitive login
    pw_hash       TEXT NOT NULL,
    role          TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL,
    created_by    TEXT,
    last_login    INTEGER
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,             -- sha256(token) hex, never the token
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    csrf        TEXT NOT NULL,
    ip          TEXT,
    ua          TEXT
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS live_policies (
    user_id             INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    enabled             INTEGER NOT NULL DEFAULT 0,
    max_order_usdt      REAL NOT NULL,
    max_daily_usdt      REAL NOT NULL,
    max_daily_orders    INTEGER NOT NULL,
    allowed_symbols     TEXT NOT NULL,
    allowed_execution   TEXT NOT NULL,
    updated_at          INTEGER NOT NULL,
    updated_by          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS live_order_usage (
    client_order_id TEXT PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    notional_usdt   REAL NOT NULL,
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_live_usage_user_time ON live_order_usage(user_id, created_at);

CREATE TABLE IF NOT EXISTS user_activity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    username    TEXT NOT NULL,
    action      TEXT NOT NULL,
    result      TEXT NOT NULL DEFAULT 'ok',
    detail      TEXT,
    ip          TEXT,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_user_activity_user_time ON user_activity(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS invites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code_hash   TEXT NOT NULL UNIQUE,
    role        TEXT NOT NULL,
    note        TEXT,
    created_at  INTEGER NOT NULL,
    created_by  TEXT,
    expires_at  INTEGER,
    used_at     INTEGER,
    used_by     TEXT
);
"""


def connect() -> sqlite3.Connection:
    """Open the auth database, creating the schema once per path per process."""
    path = Path(AUTH_DB)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) not in _ready:
        conn.executescript(_SCHEMA)
        conn.commit()
        _ready.add(str(path))
        with contextlib.suppress(OSError):     # hashes are not world-readable
            path.chmod(0o600)
    return conn


def _row_to_user(row: sqlite3.Row) -> User:
    return User(id=row["id"], username=row["username"], role=row["role"],
                active=bool(row["active"]), created_at=row["created_at"],
                last_login=row["last_login"])


def reset_caches() -> None:
    """Forget the "schema is ready" flags. Tests only."""
    _ready.clear()
    _failures.clear()


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    """`scrypt$n$r$p$salt_b64$derived_b64`, self-describing."""
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AuthError(f"password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                             n=_N, r=_R, p=_P, dklen=_DKLEN,
                             maxmem=64 * 1024 * 1024)
    salt_b64 = base64.b64encode(salt).decode()
    derived_b64 = base64.b64encode(derived).decode()
    return f"scrypt${_N}${_R}${_P}${salt_b64}${derived_b64}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time-ish check; a malformed hash is a failed login, not a 500."""
    try:
        scheme, n_s, r_s, p_s, salt_b64, dk_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        derived = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                                 n=int(n_s), r=int(r_s), p=int(p_s),
                                 dklen=len(expected), maxmem=64 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, expected)


#: One scrypt comparison for accounts that do not exist, so timing does not
#: answer "does this username exist?".
_DUMMY = hash_password(secrets.token_urlsafe(18))


def check_password_quality(password: str, username: str) -> None:
    """Raise `AuthError` with something the person can act on."""
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"رمز عبور باید دست‌کم {MIN_PASSWORD} کاراکتر باشد")
    if len(password) > MAX_PASSWORD:
        raise AuthError(f"رمز عبور نباید بیش از {MAX_PASSWORD} کاراکتر باشد")
    low = password.lower()
    if low in _BLOCKLIST:
        raise AuthError("این رمز در فهرست رمزهای رایج است؛ رمز دیگری انتخاب کنید")
    if username and username.lower() in low:
        raise AuthError("رمز نباید بخشی از نام کاربری باشد")


# --------------------------------------------------------------------------- #
# Login throttle (in-process, like the rate limiter in security.py)
# --------------------------------------------------------------------------- #
_failures: dict[str, deque[float]] = {}


def _throttle_key(ip: str, username: str) -> str:
    return f"{ip}|{username.lower()}"


def attempts_left(ip: str, username: str) -> int:
    window = _failures.get(_throttle_key(ip, username), deque())
    cutoff = time.monotonic() - LOGIN_WINDOW_S
    live = [t for t in window if t > cutoff]
    return max(0, LOGIN_ATTEMPTS - len(live))


def _note_failure(ip: str, username: str) -> None:
    window = _failures.setdefault(_throttle_key(ip, username), deque())
    window.append(time.monotonic())
    cutoff = time.monotonic() - LOGIN_WINDOW_S
    while window and window[0] < cutoff:
        window.popleft()


def _clear_failures(ip: str, username: str) -> None:
    _failures.pop(_throttle_key(ip, username), None)


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
def normalise_username(username: str) -> str:
    name = (username or "").strip()
    if not 3 <= len(name) <= 32:
        raise AuthError("نام کاربری باید بین ۳ و ۳۲ کاراکتر باشد")
    if not all(ch.isalnum() or ch in "._-" for ch in name):
        raise AuthError("نام کاربری فقط حروف، رقم، نقطه، خط تیره و زیرخط")
    if not name[0].isalnum():
        raise AuthError("نام کاربری باید با حرف یا رقم شروع شود")
    return name


def create_user(username: str, password: str, *, role: str = "viewer",
                created_by: str = "cli") -> User:
    name = normalise_username(username)
    if role not in ROLES:
        raise AuthError(f"role must be one of {', '.join(ROLES)}")
    check_password_quality(password, name)
    pw_hash = hash_password(password)
    conn = connect()
    try:
        try:
            cur = conn.execute(
                "INSERT INTO users(username, username_key, pw_hash, role, created_at, created_by) "
                "VALUES (?,?,?,?,?,?)",
                (name, name.lower(), pw_hash, role, int(time.time()), created_by),
            )
        except sqlite3.IntegrityError:
            raise AuthError("این نام کاربری قبلاً گرفته شده است", status=409) from None
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()
    return _row_to_user(row)


def authenticate(username: str, password: str, *, ip: str = "") -> User:
    """Username + password -> User, or AuthError. Throttled per address."""
    name = (username or "").strip()
    if attempts_left(ip, name) <= 0:
        raise AuthError(
            f"تلاش‌های ناموفق زیاد بود؛ {LOGIN_WINDOW_S // 60} دقیقه بعد دوباره امتحان کنید",
            status=429,
        )
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE username_key = ?",
                           (name.lower(),)).fetchone()
    finally:
        conn.close()
    stored = row["pw_hash"] if row else _DUMMY
    ok = verify_password(password or "", stored) and row is not None
    if not ok:
        _note_failure(ip, name)
        raise AuthError("نام کاربری یا رمز عبور درست نیست", status=401)
    user = _row_to_user(row)
    if not user.active:
        raise AuthError("این حساب غیرفعال شده است", status=403)
    _clear_failures(ip, name)
    conn = connect()
    try:
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (int(time.time()), user.id))
        conn.commit()
    finally:
        conn.close()
    return User(**{**user.__dict__, "last_login": int(time.time())})


def get_user(user_id: int) -> User | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_user(row) if row else None


def get_user_by_name(username: str) -> User | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE username_key = ?",
                           ((username or "").strip().lower(),)).fetchone()
    finally:
        conn.close()
    return _row_to_user(row) if row else None


def list_users() -> list[User]:
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    finally:
        conn.close()
    return [_row_to_user(r) for r in rows]


def record_activity(user: User | None, action: str, *, username: str = "",
                    result: str = "ok", detail: str = "", ip: str = "") -> None:
    """Append a bounded, secret-free account activity event."""
    who = user.username if user else normalise_username(username) if username else "unknown"
    user_id = user.id if user else None
    with connect() as conn:
        conn.execute(
            "INSERT INTO user_activity(user_id,username,action,result,detail,ip,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (user_id, who, action[:64], result[:16], detail[:300], ip[:80], int(time.time())),
        )


def list_activity(*, limit: int = 100, user_id: int | None = None) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    with connect() as conn:
        if user_id is None:
            rows = conn.execute(
                "SELECT * FROM user_activity ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM user_activity WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
    return [dict(row) for row in rows]


def get_live_policy(user_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM live_policies WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["enabled"] = bool(out["enabled"])
    out["allowed_symbols"] = json.loads(out["allowed_symbols"])
    out["allowed_execution"] = json.loads(out["allowed_execution"])
    return out


def set_live_policy(user_id: int, policy: dict, *, actor: str) -> dict:
    user = get_user(user_id)
    if user is None:
        raise AuthError("کاربر پیدا نشد", status=404)
    if user.role != "operator":
        raise AuthError("مجوز ثبت واقعی فقط برای operator قابل تخصیص است", status=409)
    symbols = sorted(set(policy["allowed_symbols"]))
    executions = sorted(set(policy["allowed_execution"]))
    now = int(time.time())
    with connect() as conn:
        conn.execute(
            """INSERT INTO live_policies(user_id,enabled,max_order_usdt,max_daily_usdt,
               max_daily_orders,allowed_symbols,allowed_execution,updated_at,updated_by)
               VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
               enabled=excluded.enabled,max_order_usdt=excluded.max_order_usdt,
               max_daily_usdt=excluded.max_daily_usdt,max_daily_orders=excluded.max_daily_orders,
               allowed_symbols=excluded.allowed_symbols,allowed_execution=excluded.allowed_execution,
               updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
            (user_id, int(policy["enabled"]), policy["max_order_usdt"],
             policy["max_daily_usdt"], policy["max_daily_orders"],
             json.dumps(symbols, separators=(",", ":")),
             json.dumps(executions, separators=(",", ":")), now, actor),
        )
    return get_live_policy(user_id) or {}


def authorize_live_order(user: User, *, client_order_id: str, symbol: str,
                         execution: str, notional_usdt: float) -> dict:
    """Atomically enforce and reserve an operator's daily real-order budget."""
    if user.is_owner:
        return {"owner_override": True}
    if user.role != "operator" or not user.active:
        raise AuthError("ثبت سفارش واقعی برای این نقش مجاز نیست", status=403)
    now = int(time.time())
    day_start = now - (now % 86400)
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        policy = conn.execute("SELECT * FROM live_policies WHERE user_id=?", (user.id,)).fetchone()
        if policy is None or not policy["enabled"]:
            raise AuthError("مجوز live_place برای این operator فعال نیست", status=403)
        symbols = set(json.loads(policy["allowed_symbols"]))
        executions = set(json.loads(policy["allowed_execution"]))
        if symbol not in symbols or execution not in executions:
            raise AuthError("نماد یا نوع سفارش خارج از policy است", status=403)
        amount = float(notional_usdt)
        if amount <= 0 or amount > float(policy["max_order_usdt"]):
            raise AuthError("مبلغ سفارش از سقف operator بیشتر است", status=409)
        existing = conn.execute(
            "SELECT user_id,notional_usdt FROM live_order_usage WHERE client_order_id=?",
            (client_order_id,),
        ).fetchone()
        if existing is not None:
            if existing["user_id"] != user.id or abs(float(existing["notional_usdt"]) - amount) > 1e-9:
                raise AuthError("idempotency key با رزرو قبلی تعارض دارد", status=409)
            conn.commit()
            return {"reserved": False, "idempotent": True}
        count, spent = conn.execute(
            "SELECT count(*),coalesce(sum(notional_usdt),0) FROM live_order_usage "
            "WHERE user_id=? AND created_at>=?", (user.id, day_start),
        ).fetchone()
        if int(count) >= int(policy["max_daily_orders"]):
            raise AuthError("سقف تعداد سفارش روزانه operator پر شده است", status=409)
        if float(spent) + amount > float(policy["max_daily_usdt"]):
            raise AuthError("سقف مبلغ روزانه operator پر شده است", status=409)
        conn.execute(
            "INSERT INTO live_order_usage(client_order_id,user_id,notional_usdt,created_at) VALUES(?,?,?,?)",
            (client_order_id, user.id, amount, now),
        )
        conn.commit()
        return {"reserved": True, "daily_orders": int(count) + 1,
                "daily_usdt": round(float(spent) + amount, 8)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def any_users() -> bool:
    """Has anybody claimed this installation? Called on every API request."""
    conn = connect()
    try:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    finally:
        conn.close()


def count_owners() -> int:
    conn = connect()
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'owner' AND active = 1").fetchone()[0])
    finally:
        conn.close()


def set_role(user_id: int, role: str, *, actor: str) -> User:
    if role not in ROLES:
        raise AuthError(f"role must be one of {', '.join(ROLES)}")
    target = get_user(user_id)
    if target is None:
        raise AuthError("کاربر پیدا نشد", status=404)
    if target.role == "owner" and role != "owner" and count_owners() <= 1:
        raise AuthError("آخرین مالک را نمی‌توان تنزل داد؛ اول مالک دیگری بسازید", status=409)
    conn = connect()
    try:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        conn.commit()
    finally:
        conn.close()
    return get_user(user_id)  # type: ignore[return-value]


def set_active(user_id: int, active: bool, *, actor: str) -> User:
    target = get_user(user_id)
    if target is None:
        raise AuthError("کاربر پیدا نشد", status=404)
    if not active and target.role == "owner" and count_owners() <= 1:
        raise AuthError("آخرین مالک فعال را نمی‌توان غیرفعال کرد", status=409)
    conn = connect()
    try:
        conn.execute("UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user_id))
        if not active:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return get_user(user_id)  # type: ignore[return-value]


def change_password_as_admin(user_id: int, new: str) -> None:
    """Set a password without knowing the old one — for the CLI only.

    Kept out of the API on purpose: over HTTP, "I am the owner" is not the same
    as "I am the person whose password this is", and resetting someone else's
    password is a takeover primitive. A shell on the box already has the file.
    """
    user = get_user(user_id)
    if user is None:
        raise AuthError("کاربر پیدا نشد", status=404)
    check_password_quality(new, user.username)
    conn = connect()
    try:
        conn.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(new), user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def change_password(user_id: int, current: str, new: str) -> None:
    """Change your own password and sign out every other device."""
    user = get_user(user_id)
    if user is None:
        raise AuthError("کاربر پیدا نشد", status=404)
    conn = connect()
    try:
        row = conn.execute("SELECT pw_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    if row is None or not verify_password(current, row["pw_hash"]):
        raise AuthError("رمز فعلی درست نیست", status=401)
    check_password_quality(new, user.username)
    conn = connect()
    try:
        conn.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(new), user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def _session_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user: User, *, ip: str = "", ua: str = "") -> tuple[str, str, int]:
    """Returns `(token, csrf, expires_at)`.

    Neither secret is stored as given: the table keeps `sha256` of both, so the
    database alone cannot be replayed as a cookie *and* a header.
    """
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = int(time.time())
    expires = now + SESSION_DAYS * 86400
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id, user_id, created_at, expires_at, last_seen, csrf, ip, ua) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (_session_id(token), user.id, now, expires, now, _session_id(csrf),
             ip[:64], ua[:200]),
        )
        conn.commit()
    finally:
        conn.close()
    return token, csrf, expires


@dataclass(frozen=True)
class Session:
    user: User
    csrf_hash: str        # sha256 of the token handed to the browser
    expires_at: int


def session_from_token(token: str) -> Session | None:
    """Resolve a cookie to a Session, extending it at most once an hour."""
    if not token or len(token) > 200:
        return None
    sid = _session_id(token)
    now = int(time.time())
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        if row is None:
            return None
        if row["expires_at"] < now:
            conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            conn.commit()
            return None
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
        if user_row is None or not user_row["active"]:
            conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            conn.commit()
            return None
        if now - row["last_seen"] > 3600:            # one write per hour, not per request
            conn.execute("UPDATE sessions SET last_seen = ?, expires_at = ? WHERE id = ?",
                         (now, now + SESSION_DAYS * 86400, sid))
            conn.commit()
        return Session(user=_row_to_user(user_row), csrf_hash=row["csrf"],
                       expires_at=now + SESSION_DAYS * 86400)
    finally:
        conn.close()


def delete_session(token: str) -> None:
    if not token:
        return
    conn = connect()
    try:
        conn.execute("DELETE FROM sessions WHERE id = ?", (_session_id(token),))
        conn.commit()
    finally:
        conn.close()


def delete_user_sessions(user_id: int) -> int:
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def active_sessions() -> int:
    conn = connect()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM sessions WHERE expires_at > ?",
                                (int(time.time()),)).fetchone()[0])
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Invites
# --------------------------------------------------------------------------- #
def create_invite(role: str, *, created_by: str, note: str = "",
                  ttl_hours: int = 72) -> str:
    """Create a single-use code. The plaintext is returned once and never stored."""
    if role not in ROLES:
        raise AuthError(f"role must be one of {', '.join(ROLES)}")
    code = secrets.token_urlsafe(24)
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO invites(code_hash, role, note, created_at, created_by, expires_at) "
            "VALUES (?,?,?,?,?,?)",
            (_session_id(code), role, note[:200], int(time.time()), created_by,
             int(time.time()) + ttl_hours * 3600),
        )
        conn.commit()
    finally:
        conn.close()
    return code


def redeem_invite(code: str, username: str, password: str) -> User:
    """Consume a code and create the account it describes, atomically."""
    name = normalise_username(username)
    check_password_quality(password, name)
    pw_hash = hash_password(password)          # before the lock: scrypt is slow
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM invites WHERE code_hash = ?",
                           (_session_id(code or ""),)).fetchone()
        now = int(time.time())
        if row is None:
            raise AuthError("کد دعوت معتبر نیست", status=403)
        if row["used_at"] is not None:
            raise AuthError("این کد دعوت قبلاً استفاده شده است", status=409)
        if row["expires_at"] and row["expires_at"] < now:
            raise AuthError("این کد دعوت منقضی شده است", status=409)
        try:
            cur = conn.execute(
                "INSERT INTO users(username, username_key, pw_hash, role, created_at, created_by) "
                "VALUES (?,?,?,?,?,?)",
                (name, name.lower(), pw_hash, row["role"], now, f"invite:{row['created_by']}"),
            )
        except sqlite3.IntegrityError:
            raise AuthError("این نام کاربری قبلاً گرفته شده است", status=409) from None
        conn.execute("UPDATE invites SET used_at = ?, used_by = ? WHERE id = ?",
                     (now, name, row["id"]))
        conn.commit()
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return _row_to_user(user_row)


def list_invites() -> list[dict]:
    """Metadata only — the code itself is not recoverable by design."""
    conn = connect()
    try:
        rows = conn.execute("SELECT id, role, note, created_at, created_by, expires_at, "
                            "used_at, used_by FROM invites ORDER BY id DESC LIMIT 100").fetchall()
    finally:
        conn.close()
    now = int(time.time())
    out = []
    for r in rows:
        state = ("used" if r["used_at"] else
                 "expired" if (r["expires_at"] or 0) < now else "open")
        out.append({**{k: r[k] for k in r.keys()}, "state": state})  # noqa: SIM118
    return out


def revoke_invite(invite_id: int) -> bool:
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM invites WHERE id = ? AND used_at IS NULL", (invite_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


__all__ = [
    "AUTH_DB",
    "MIN_PASSWORD",
    "ROLES",
    "SESSION_DAYS",
    "AuthError",
    "Session",
    "User",
    "active_sessions",
    "any_users",
    "attempts_left",
    "authenticate",
    "authorize_live_order",
    "change_password",
    "change_password_as_admin",
    "check_password_quality",
    "connect",
    "count_owners",
    "create_invite",
    "create_session",
    "create_user",
    "delete_session",
    "delete_user_sessions",
    "get_live_policy",
    "get_user",
    "get_user_by_name",
    "hash_password",
    "list_activity",
    "list_invites",
    "list_users",
    "normalise_username",
    "record_activity",
    "redeem_invite",
    "reset_caches",
    "revoke_invite",
    "session_from_token",
    "set_active",
    "set_live_policy",
    "set_role",
    "verify_password",
]
