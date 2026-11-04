#!/usr/bin/env python3
"""Preflight for a public deployment — run it before opening the firewall.

The dashboard has one property that makes a bad deployment expensive: it can
place paper orders, halt trading, and (in a source checkout) run the test
suite. A misconfigured install does not look broken; it looks like a dashboard
that works. This script is the difference between "I followed the steps" and
"the dangerous path is actually closed".

Every check below answers a question with evidence, in the same four words the
diagnostics tab uses (`ok` / `fail` / `skip` / `error`), and the process exits
non-zero if any check failed. Nothing here is a guess: no number is printed
that was not read from a file, a socket, or an HTTP response.

    python scripts/check_deploy.py --public https://nobi.example.ir
    python scripts/check_deploy.py --json deploy-check.json

Checks
------
  config        .env exists where the service reads it, and parses
  python        the interpreter can install the pins (3.12+; numpy 2.5 needs it)
  packages      runtime dependencies import
  token         NBX_API_TOKEN is set  (mandatory behind a proxy)
  proxy         NBX_TRUSTED_PROXIES describes the reverse proxy in front
  storage       the journal/paper state live on a persistent mount, not in $HOME
  disk          free space where the data actually is
  exchange      apiv2.nobitex.ir answers (the app is useless without it)
  bind          the app listens on loopback only, not 0.0.0.0
  readonly      POST without a token is refused  ← the one that matters
  tls           the public URL answers with a valid certificate

Exit: 0 = ready, 1 = at least one `fail`, 2 = could not run (nothing to check).
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

#: Persian and em dashes on a console that is not UTF-8 (Windows cp1252, POSIX C):
#: printing them raised `UnicodeEncodeError` and the script "failed" for a reason
#: that had nothing to do with what it was checking. Reconfigure, do not escape.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
OK, FAIL, SKIP, ERROR = "ok", "fail", "skip", "error"
RANK = {OK: 0, SKIP: 1, ERROR: 2, FAIL: 3}

#: Files that could not be read, so the summary can say so instead of silently
#: checking a smaller configuration than the service actually runs with.
_unreadable: list[str] = []


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        if not path.is_file():
            return out
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        # The unit's file is root-owned, and `deploy_arvan.sh` sets it 0640
        # root:nobi — but an install that predates that, or one hardened to 0600,
        # leaves the service user unable to read it. A checker that dies here
        # reports nothing at all, which is worse than reporting less: the operator
        # sees a traceback instead of a verdict. Degrade to what is readable.
        _unreadable.append(f"{path}: {type(exc).__name__}")
        return out
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


#: The file systemd loads. `deploy/nobi.service` says it plainly: "systemd's
#: environment outranks server/.env ... Keeping the secret here means a checkout
#: without .env still runs correctly." A checker that reads only `server/.env`
#: therefore reports the *defaults* of a correctly configured install — which is
#: how a live host came to be told its journal was "inside the checkout" while
#: the file was sitting on the attached volume the whole time.
UNIT_ENV = Path(os.getenv("NBX_UNIT_ENV") or "/etc/nobi/nobi.env")


def _env() -> dict[str, str]:
    """Read the configuration the service actually runs with.

    Both files, in the order systemd resolves them: `server/.env` first, then the
    unit's EnvironmentFile on top. Reading only the first is what produced two
    false FAILs on a host whose journal and token were correctly set in the
    second — and the app was healthy throughout.
    """
    out = _read_env_file(SERVER / ".env")
    out.update(_read_env_file(UNIT_ENV))
    return out


def _cfg(key: str) -> str:
    return os.environ.get(key) or _env().get(key) or ""


def _row(status: str, name: str, detail: str, hint: str = "") -> dict:
    return {"status": status, "name": name, "detail": detail, "hint": hint}


# --------------------------------------------------------------------------- #
# Static configuration
# --------------------------------------------------------------------------- #
def check_config() -> dict:
    """Validate the files systemd actually reads, not only the optional override.

    `deploy/nobi.service` always loads `/etc/nobi/nobi.env`; `server/.env` is an
    optional local override. Requiring the latter produced a false FAIL on a
    healthy host and encouraged an empty placeholder file. Both files still get
    permission checks: the unit may be group-readable by root:nobi (0640), but
    neither may be readable by other users.
    """
    server_env = SERVER / ".env"
    existing = [path for path in (server_env, UNIT_ENV) if path.is_file()]
    if not existing:
        return _row(FAIL, "config", f"neither {server_env} nor {UNIT_ENV} exists",
                    "configure /etc/nobi/nobi.env for systemd, or copy "
                    "deploy/nobi.env.example to server/.env for local development")
    pairs = _env()
    if not pairs:
        return _row(FAIL, "config", "configuration files have no KEY=VALUE lines",
                    "an empty file is not a configuration")
    if server_env.is_file():
        mode = server_env.stat().st_mode & 0o777
        if mode & 0o077:
            return _row(FAIL, "config",
                        f"{server_env} is mode {mode:o} — group/other can read the secrets",
                        f"chmod 600 {server_env}")
    if UNIT_ENV.is_file():
        mode = UNIT_ENV.stat().st_mode & 0o777
        if mode & 0o007:
            return _row(FAIL, "config",
                        f"{UNIT_ENV} is mode {mode:o} — other users can read the secrets",
                        f"chmod 640 {UNIT_ENV} (or 600)")
    sources = ", ".join(str(path) for path in existing)
    return _row(OK, "config", f"{sources}; {len(pairs)} keys")


#: The floor is set by the pins, not by the syntax: `numpy==2.5.4` publishes
#: `Requires-Python >=3.12`, so on 3.11 `pip install -r server/requirements.txt`
#: fails before the app is ever started. The previous floor here was 3.11 (the
#: version `datetime.UTC` needs), which passed a machine that could not install.
MIN_PYTHON = (3, 12)


def check_python(version: tuple[int, int, int] | None = None) -> dict:
    v = version or (sys.version_info.major, sys.version_info.minor, sys.version_info.micro)
    got = f"{v[0]}.{v[1]}.{v[2]} ({sys.executable})"
    if (v[0], v[1]) < MIN_PYTHON:
        return _row(FAIL, "python", got,
                    f"the pinned requirements need Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ "
                    "(numpy 2.5 publishes Requires-Python >=3.12); use Ubuntu 24.04 "
                    f"(python3.12) or install python{MIN_PYTHON[0]}.{MIN_PYTHON[1]}+")
    return _row(OK, "python", got)


def check_packages() -> dict:
    missing = []
    for mod in ("fastapi", "uvicorn", "httpx", "pandas"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if missing:
        return _row(FAIL, "packages", f"cannot import: {', '.join(missing)}",
                    "python -m pip install -r server/requirements.txt")
    return _row(OK, "packages", "fastapi, uvicorn, httpx, pandas import")


def check_token() -> dict:
    token = _cfg("NBX_API_TOKEN")
    if not token:
        return _row(FAIL, "token", "NBX_API_TOKEN is empty",
                    "generate one with: python -c \"import secrets;"
                    "print(secrets.token_urlsafe(32))\" and put it in server/.env. "
                    "Behind a proxy the app refuses to start without it")
    if len(token) < 16:
        return _row(FAIL, "token", f"NBX_API_TOKEN is only {len(token)} characters",
                    "use at least 32 random bytes: secrets.token_urlsafe(32)")
    return _row(OK, "token", f"set, {len(token)} characters")


def check_proxy() -> dict:
    raw = _cfg("NBX_TRUSTED_PROXIES")
    if not raw:
        return _row(SKIP, "proxy", "NBX_TRUSTED_PROXIES is empty",
                    "behind nginx/Caddy set it to 127.0.0.1 — otherwise the app "
                    "treats every visitor as remote and (correctly) refuses, but "
                    "the error log fills up and the rate limit keys on the proxy")
    bad = [p for p in (x.strip() for x in raw.split(",")) if p and not re.match(
        r"^[0-9a-fA-F:.]+(/[0-9]{1,3})?$", p)]
    if bad:
        return _row(FAIL, "proxy", f"not an IP or CIDR: {', '.join(bad)}",
                    "fix the entries; the app ignores them and logs a warning")
    return _row(OK, "proxy", raw)


def check_storage() -> dict:
    journal = _cfg("NBX_JOURNAL_DB") or str(SERVER / "journal.db")
    auth_db = _cfg("NBX_AUTH_DB") or str(SERVER / "auth.db")
    paper = _cfg("NBX_PAPER_STATE") or str(SERVER / "paper_state.json")
    jp = Path(journal)
    inside_repo = str(jp.resolve()).startswith(str(ROOT))
    problems = []
    if inside_repo:
        problems.append(f"journal is inside the checkout ({jp})")
    # A persistent volume is a mount point; the root filesystem is not.
    mounts = {}
    try:
        for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) > 1:
                mounts[parts[1]] = parts[0]
    except OSError:
        pass

    def on_volume(p: Path) -> str:
        best = ""
        for m in mounts:
            if str(p).startswith(m) and len(m) > len(best):
                best = m
        return best

    if str(Path(auth_db).resolve()).startswith(str(ROOT)):
        problems.append(f"accounts are inside the checkout ({auth_db}) — a service "
                        "with ProtectSystem=strict cannot even create it")
    root_m = on_volume(jp)
    if problems:
        hint = ("put the data on the attached volume: NBX_JOURNAL_DB=/srv/nobi-data/journal.db, "
                "NBX_PAPER_STATE=/srv/nobi-data/paper.json and NBX_AUTH_DB=/srv/nobi-data/auth.db")
        return _row(FAIL, "storage", "; ".join(problems), hint)
    if root_m in ("/", ""):
        return _row(SKIP, "storage", f"{jp} is on the root filesystem ({root_m or '?'})",
                    "a rebuild of the ابرک loses it; attach a volume and point the two "
                    "variables at it")
    return _row(OK, "storage", f"journal on {root_m}, paper state {paper}, "
                               f"accounts {auth_db}")


def check_disk() -> dict:
    journal = _cfg("NBX_JOURNAL_DB") or str(SERVER / "journal.db")
    target = Path(journal).parent
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        usage = shutil.disk_usage(target)
    except OSError as e:
        return _row(ERROR, "disk", f"{target}: {e}")
    free_gb = usage.free / 1e9
    used_pct = usage.used / usage.total * 100
    if usage.free < 1_000_000_000:
        return _row(FAIL, "disk", f"{free_gb:.2f} GB free at {target}",
                    "the journal grows with every scan; free space or attach a volume")
    return _row(OK, "disk", f"{free_gb:.1f} GB free at {target}, {used_pct:.0f}% used")


def check_accounts() -> dict:
    """Who can get in: a named account, or only the shared token?

    Both are legitimate. What is not legitimate is a half-configured install —
    an auth database with no active owner cannot mint invites, so nobody new
    can ever be added and the person who set it up may not be able to get back
    in either.
    """
    path = Path(_cfg("NBX_AUTH_DB") or str(SERVER / "auth.db"))
    if not path.is_file():
        return _row(SKIP, "accounts", "no accounts yet — the API is in token/loopback mode",
                    "create the first one: python scripts/manage_users.py create NAME --role owner")
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            users = conn.execute("SELECT role, active FROM users").fetchall()
        finally:
            conn.close()
    except Exception as e:
        return _row(FAIL, "accounts", f"{path} is not readable: {type(e).__name__}",
                    "check the file permissions and that the service can write to it")
    owners = sum(1 for role, active in users if role == "owner" and active)
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        return _row(FAIL, "accounts", f"{path} is mode {mode:o} — password hashes are readable",
                    f"chmod 600 {path} (the service creates it that way)")
    if not users:
        return _row(SKIP, "accounts", f"{path} exists but has no users",
                    "python scripts/manage_users.py create NAME --role owner")
    if owners == 0:
        return _row(FAIL, "accounts", f"{len(users)} account(s) but no active owner",
                    "nobody can create invites or change roles; restore one with "
                    "python scripts/manage_users.py role NAME owner")
    return _row(OK, "accounts", f"{len(users)} account(s), {owners} owner(s)")


def check_exchange() -> dict:
    url = "https://apiv2.nobitex.ir/v2/orderbook/USDTIRT"
    try:
        with urllib.request.urlopen(url, timeout=12) as r:
            body = json.loads(r.read().decode("utf-8"))
        if body.get("status") != "ok":
            return _row(FAIL, "exchange", f"Nobitex answered with status={body.get('status')!r}",
                        "the bot cannot price anything without the exchange")
        bids = body.get("bids") or []
        return _row(OK, "exchange", f"USDTIRT book answered, {len(bids)} bid level(s)")
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        return _row(FAIL, "exchange", f"{type(e).__name__}: {str(e)[:120]}",
                    "check outbound HTTPS from this server; ArvanCloud's default "
                    "firewall already allows all outbound")


def classify_bind(addr: str) -> str:
    """What does it mean for uvicorn to listen on `addr`?

    loopback  reachable only from this machine — the only correct answer
    private   reachable from this VPC/subnet but not the internet: legitimate
              when nginx runs on a second cloud VM (see docs/deploy-arvan.md §VPC),
              and then the firewall/security group is the perimeter instead
    wildcard  0.0.0.0 / :: — every interface, including the public one
    public    a specific public address
    other     link-local, malformed, empty — not a deliberate choice
    """
    a = (addr or "").strip().strip("[]")
    if a in ("0.0.0.0", "::", "*", ""):
        return "wildcard"
    try:
        ip = ipaddress.ip_address(a)
    except ValueError:
        return "other"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "other"
    return "private" if ip.is_private else "public"


def check_bind() -> dict:
    """Is the *app* reachable from the internet? It must not be."""
    try:
        out = subprocess.run(["ss", "-lntp"], capture_output=True, text=True, encoding="utf-8",
                             timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return _row(SKIP, "bind", f"cannot read the socket table: {type(e).__name__}", "")
    rows = [ln.split() for ln in out.splitlines() if len(ln.split()) > 4 and ":8000" in ln.split()[3]]
    if not rows:
        return _row(SKIP, "bind", "nothing is listening on :8000",
                    "start the service: systemctl start nobi")
    seen: list[tuple[str, str]] = []
    for f in rows:
        mine = "python" in " ".join(f[5:]) or "uvicorn" in " ".join(f[5:])
        if not mine:
            continue
        addr = f[3].rsplit(":", 1)[0]
        seen.append((addr, classify_bind(addr)))
    if not seen:
        return _row(SKIP, "bind", ":8000 is held by a process that hides its pid (not uvicorn?)",
                    "sudo ss -lntp — find out what it is before opening the firewall")
    kinds = {k for _, k in seen}
    addrs = ", ".join(a for a, _ in seen)
    if "wildcard" in kinds or "public" in kinds:
        return _row(FAIL, "bind", f"the app listens on {addrs} — reachable from the internet",
                    "bind 127.0.0.1:8000 (or the private VPC address if nginx is on "
                    "another ابرک) and let the proxy face the public side")
    if "private" in kinds:
        if _cfg("NBX_TRUSTED_PROXIES").strip():
            return _row(OK, "bind", f"app on {addrs} (private) with a proxy configured",
                        "")
        return _row(SKIP, "bind", f"app on {addrs} (private address, reachable inside the VPC)",
                    "fine only if nginx runs on another ابرک in this VPC and nothing else "
                    "routes to this subnet; then set NBX_TRUSTED_PROXIES to that proxy's "
                    "private IP (see docs/deploy-arvan.md §VPC)")
    if "other" in kinds:
        return _row(SKIP, "bind", f"app on {addrs} — neither loopback nor a private range",
                    "an odd bind; make it explicit")
    return _row(OK, "bind", f"app on {addrs} (loopback only)")


# --------------------------------------------------------------------------- #
# The one that matters: does a stranger get in?
# --------------------------------------------------------------------------- #
def check_readonly(public: str) -> dict:
    """POST a halt without a token. Anything but 401/403 is a breach.

    200 means the request went through: the dashboard halted trading for
    whoever asked. That is the exact failure the loopback rule exists to
    prevent, so it is checked against the *public* URL rather than assumed.
    """
    if not public:
        return _row(SKIP, "readonly", "no --public URL given",
                    "pass --public https://your.domain to test the real entry point")
    host = urllib.parse.urlparse(public).hostname or ""
    if host == "localhost" or host.startswith("127.") or host == "::1":
        return _row(SKIP, "readonly", f"{public} is loopback — that path is allowed by design",
                    "run this against the public URL; testing 127.0.0.1 can never "
                    "distinguish a proxy that strips the client address from one that does not")
    url = public.rstrip("/") + "/api/paper/halt"
    req = urllib.request.Request(url, method="POST", data=b"", headers={
        "Content-Type": "application/json", "User-Agent": "nobi-deploy-check/1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            code, body = r.status, r.read(200).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read(200).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return _row(ERROR, "readonly", f"cannot reach {url}: {type(e).__name__}",
                    "is the proxy up and the certificate valid?")
    if code in (401, 403):
        return _row(OK, "readonly", f"anonymous POST -> {code} (refused)")
    if code == 200:
        return _row(FAIL, "readonly", "anonymous POST -> 200: ANYONE CAN HALT TRADING",
                    "the app is behind a proxy it does not know about. Set "
                    "NBX_TRUSTED_PROXIES=127.0.0.1 and NBX_API_TOKEN, then restart")
    return _row(SKIP, "readonly", f"anonymous POST -> {code} ({body[:60]!r})",
                "expected 401/403; check the proxy is forwarding to the app")


def _anonymous_get(url: str) -> tuple[int, bytes]:
    """One unauthenticated GET; HTTP refusals are results, not transport errors."""
    req = urllib.request.Request(url, headers={"User-Agent": "nobi-deploy-check/1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            # Health includes freshness for the whole symbol universe and is
            # larger than a small error-body buffer. Bound the read without
            # truncating a normal production payload into invalid JSON.
            return response.status, response.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(512)


def check_reads(public: str) -> dict:
    """Probe anonymous reads instead of inferring access from the auth mode.

    Once an account exists, `require_login_for_api` closes every non-public API
    GET. The old checker never called those routes and therefore warned on a
    correctly closed deployment. Health remains public for monitors; account,
    position, and equity data must refuse anonymous callers.
    """
    if not public:
        return _row(SKIP, "reads", "no --public URL given", "")
    base = public.rstrip("/")
    try:
        health_code, health_raw = _anonymous_get(base + "/api/health")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return _row(FAIL, "reads", f"/api/health failed: {type(exc).__name__}", "")
    if health_code in (401, 403):
        return _row(OK, "reads", f"the site answers {health_code} before reaching the app",
                    "an extra auth layer (nginx basic auth) is in front — reads are protected")
    if health_code != 200:
        return _row(FAIL, "reads", f"/api/health -> {health_code}",
                    "health must stay public while sensitive API reads require login")
    try:
        health = json.loads(health_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _row(FAIL, "reads", "/api/health returned invalid JSON", "")
    if not health.get("ok"):
        return _row(FAIL, "reads", f"/api/health says ok={health.get('ok')!r}", "")

    accounts = health.get("auth", {}).get("accounts")
    paths = ("/api/paper/positions", "/api/paper/metrics", "/api/risk/state")
    outcomes: list[str] = []
    for path in paths:
        try:
            code, _ = _anonymous_get(base + path)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return _row(ERROR, "reads", f"anonymous GET {path}: {type(exc).__name__}",
                        "could not establish whether account data is protected")
        outcomes.append(f"{path}={code}")
        if code == 200 and accounts is False:
            return _row(SKIP, "reads",
                        f"anonymous GET {path} -> 200 because no account has been claimed",
                        "create the owner account before exposing the dashboard, or put "
                        "nginx authentication in front of it")
        if code == 200:
            return _row(FAIL, "reads",
                        f"anonymous GET {path} -> 200: account data is exposed",
                        "the login middleware is not protecting reads; do not accept this "
                        "as a cosmetic warning")
        if code not in (401, 403):
            return _row(FAIL, "reads", f"anonymous GET {path} -> {code}",
                        "expected 401/403 for sensitive data; check proxy and app versions")
    return _row(OK, "reads", "; ".join(outcomes) + "; /api/health=200 (public)")


def check_tls(public: str) -> dict:
    if not public.startswith("https://"):
        return _row(SKIP, "tls", "not an https:// URL", "")
    host = urllib.parse.urlparse(public).hostname or ""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=12) as raw, \
                ctx.wrap_socket(raw, server_hostname=host) as ss:
            cert = ss.getpeercert()
        not_after = cert.get("notAfter", "?")
        return _row(OK, "tls", f"{host} certificate valid until {not_after}")
    except (ssl.SSLError, OSError) as e:
        return _row(FAIL, "tls", f"{host}: {type(e).__name__}: {str(e)[:100]}", "")


CHECKS = (check_config, check_python, check_packages, check_token, check_proxy,
          check_storage, check_disk, check_accounts, check_exchange, check_bind)


def run(public: str) -> list[dict]:
    rows = [fn() for fn in CHECKS]
    rows.append(check_readonly(public))
    rows.append(check_reads(public))
    rows.append(check_tls(public))
    return rows


def _unreadable_note() -> list[str]:
    """The lines to print about config files this user could not read.

    Empty when the NBX_* values were handed over in the environment, which is
    what `update_arvan.sh --verify` does: the file being unreadable *to that
    user* is then not a gap, and reporting it is noise on a green preflight.

    `NBX_UNIT_ENV` is the path override for that same file, not configuration
    the service runs with, so it does not count as "supplied" — it starts with
    NBX_ and would otherwise silence the note on every host.
    """
    if not _unreadable:
        return []
    supplied = [k for k in os.environ if k.startswith("NBX_") and k != "NBX_UNIT_ENV"]
    if supplied:
        return []
    lines = [f"  !  {len(set(_unreadable))} config file(s) unreadable by this user:"]
    lines += [f"       {what}" for what in dict.fromkeys(_unreadable)]
    lines.append("     the checks above used what was readable — run as root, or pass the"
                 " NBX_* values in the environment, for the full picture")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--public", default="", help="https://your.domain — tested, not assumed")
    ap.add_argument("--json", dest="json_path")
    a = ap.parse_args()

    rows = run(a.public)
    worst = OK
    for r in rows:
        if RANK[r["status"]] > RANK[worst]:
            worst = r["status"]
    mark = {OK: "ok  ", FAIL: "FAIL", SKIP: "skip", ERROR: "ERR "}
    print(f"Nobi deployment preflight · {ROOT}")
    for r in rows:
        print(f"  [{mark[r['status']]}] {r['name']:<9} {r['detail']}")
        if r["hint"] and r["status"] in (FAIL, SKIP, ERROR):
            print(f"           -> {r['hint']}")
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in (OK, SKIP, ERROR, FAIL)}
    print(f"\n{counts['ok']} ok · {counts['skip']} skip · {counts['error']} error · "
          f"{counts['fail']} fail   →  {worst.upper()}")
    for line in _unreadable_note():
        print(line)

    if a.json_path:
        Path(a.json_path).write_text(json.dumps(
            {"when": datetime.now(UTC).isoformat(), "public": a.public,
             "overall": worst, "counts": counts, "checks": rows},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"json → {a.json_path}")
    return 0 if worst in (OK, SKIP) else 1


if __name__ == "__main__":
    sys.exit(main())
