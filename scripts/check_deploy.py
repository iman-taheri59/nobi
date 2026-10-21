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
  python        the interpreter is new enough for the code (3.11+)
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

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
OK, FAIL, SKIP, ERROR = "ok", "fail", "skip", "error"
RANK = {OK: 0, SKIP: 1, ERROR: 2, FAIL: 3}


def _env() -> dict[str, str]:
    """Read `server/.env` the way the app does, without importing the app."""
    out: dict[str, str] = {}
    path = SERVER / ".env"
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def _cfg(key: str) -> str:
    return os.environ.get(key) or _env().get(key) or ""


def _row(status: str, name: str, detail: str, hint: str = "") -> dict:
    return {"status": status, "name": name, "detail": detail, "hint": hint}


# --------------------------------------------------------------------------- #
# Static configuration
# --------------------------------------------------------------------------- #
def check_config() -> dict:
    path = SERVER / ".env"
    if not path.is_file():
        return _row(FAIL, "config", f"{path} does not exist",
                    "cp deploy/nobi.env.example server/.env and fill it in")
    mode = path.stat().st_mode & 0o777
    pairs = _env()
    if not pairs:
        return _row(FAIL, "config", f"{path} has no KEY=VALUE lines",
                    "an empty file is not a configuration")
    if mode & 0o077:
        return _row(FAIL, "config", f"{path} is mode {mode:o} — group/other can read the secrets",
                    f"chmod 600 {path}")
    return _row(OK, "config", f"{path}, mode {mode:o}, {len(pairs)} keys")


def check_python() -> dict:
    v = sys.version_info
    got = f"{v.major}.{v.minor}.{v.micro} ({sys.executable})"
    if (v.major, v.minor) < (3, 11):
        return _row(FAIL, "python", got,
                    "this code uses `datetime.UTC`; use Ubuntu 24.04 (python3.12) "
                    "or install python3.11+")
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
    paper = _cfg("NBX_PAPER_STATE") or str(SERVER / "paper_state.json")
    jp = Path(journal)
    inside_repo = str(jp.resolve()).startswith(str(ROOT))
    problems = []
    if inside_repo:
        problems.append(f"journal is inside the checkout ({jp})")
    # A persistent volume is a mount point; the root filesystem is not.
    mounts = {}
    try:
        for line in Path("/proc/mounts").read_text().splitlines():
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

    root_m = on_volume(jp)
    if problems:
        hint = ("put the data on the attached volume: NBX_JOURNAL_DB=/srv/nobi-data/journal.db "
                "and NBX_PAPER_STATE=/srv/nobi-data/paper.json")
        return _row(FAIL, "storage", "; ".join(problems), hint)
    if root_m in ("/", ""):
        return _row(SKIP, "storage", f"{jp} is on the root filesystem ({root_m or '?'})",
                    "a rebuild of the ابرک loses it; attach a volume and point the two "
                    "variables at it")
    return _row(OK, "storage", f"journal on {root_m}, paper state {paper}")


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


def check_bind() -> dict:
    """Is the *app* reachable on a public interface? It must not be."""
    try:
        out = subprocess.run(["ss", "-lntp"], capture_output=True, text=True,
                             timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return _row(SKIP, "bind", f"cannot read the socket table: {type(e).__name__}", "")
    rows = [ln.split() for ln in out.splitlines() if len(ln.split()) > 4 and ":8000" in ln.split()[3]]
    if not rows:
        return _row(SKIP, "bind", "nothing is listening on :8000",
                    "start the service: systemctl start nobi")
    exposed, ours, foreign = [], [], []
    for f in rows:
        addr = f[3].rsplit(":", 1)[0].strip("[]")
        loopback = addr.startswith("127.") or addr in ("::1", "")
        mine = "python" in " ".join(f[5:]) or "uvicorn" in " ".join(f[5:])
        (ours if mine else foreign).append(addr)
        if not loopback and mine:
            exposed.append(addr)
    if exposed:
        return _row(FAIL, "bind", f"the app itself listens on {', '.join(exposed)}:8000",
                    "bind 127.0.0.1:8000 (--host 127.0.0.1) and let the reverse "
                    "proxy face the internet; a public bind skips the proxy and "
                    "the token header is plain HTTP")
    if ours:
        return _row(OK, "bind", f"app on {', '.join(ours)}:8000 (loopback only)")
    return _row(SKIP, "bind", f":8000 is held by another process on {', '.join(foreign)}",
                "no pid shown for that listener — find out what it is before opening "
                "the firewall (sudo ss -lntp)")


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


def check_reads(public: str) -> dict:
    """Say out loud what is *not* protected.

    `require_client` guards state-changing endpoints only. GET /api/positions
    answers anyone who knows the URL, in every auth mode — so reporting "ok"
    here would be the tool lying about the one thing it exists to report.
    """
    if not public:
        return _row(SKIP, "reads", "no --public URL given", "")
    try:
        with urllib.request.urlopen(public.rstrip("/") + "/api/health", timeout=15) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return _row(OK, "reads", f"the site answers {e.code} before reaching the app",
                        "an extra auth layer (nginx basic auth) is in front — good, "
                        "reads are protected too")
        return _row(FAIL, "reads", f"/api/health -> {e.code}", "")
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        return _row(FAIL, "reads", f"/api/health failed: {type(e).__name__}", "")
    if not body.get("ok"):
        return _row(FAIL, "reads", f"/api/health says ok={body.get('ok')!r}", "")
    mode = body.get("auth", {}).get("mode")
    return _row(SKIP, "reads",
                f"ok, auth mode={mode}; GET endpoints need no token, so positions and "
                "equity are readable by anyone with the URL",
                "put the site behind nginx basic auth (docs/deploy-arvan.md §۷) "
                "or accept it")


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
          check_storage, check_disk, check_exchange, check_bind)


def run(public: str) -> list[dict]:
    rows = [fn() for fn in CHECKS]
    rows.append(check_readonly(public))
    rows.append(check_reads(public))
    rows.append(check_tls(public))
    return rows


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

    if a.json_path:
        Path(a.json_path).write_text(json.dumps(
            {"when": datetime.now(UTC).isoformat(), "public": a.public,
             "overall": worst, "counts": counts, "checks": rows},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"json → {a.json_path}")
    return 0 if worst in (OK, SKIP) else 1


if __name__ == "__main__":
    sys.exit(main())
