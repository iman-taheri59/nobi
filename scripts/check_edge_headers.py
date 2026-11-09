#!/usr/bin/env python3
"""Read-only public edge-header check for a Nobi deployment.

This script does not log in, does not send credentials, and does not place
orders. It fetches a few public endpoints and checks the browser-security
header contract that nginx and the app must present together:

* one HSTS header on HTTPS,
* one copy of each security header,
* frame protection,
* no-store on the dashboard and authenticated/API JSON.

Run it from an operator machine, or on the server itself:

    python3 scripts/check_edge_headers.py --base https://capoy.ir
    python3 scripts/check_edge_headers.py --base https://capoy.ir --json edge.json

Exit code 0 means the checked public responses match the contract. Exit code 1
means at least one check failed. Exit code 2 means the script could not run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from email.message import Message
from pathlib import Path

SECURITY_HEADERS = (
    "strict-transport-security",
    "x-content-type-options",
    "referrer-policy",
    "x-frame-options",
    "content-security-policy",
)

CHECK_PATHS = (
    "/",
    "/api/health",
    "/api/auth/me",
)


@dataclass
class Check:
    path: str
    name: str
    status: str
    detail: str


def fetch(url: str, timeout: float) -> tuple[int | None, Message, str | None]:
    """Return status, response headers, and an error name without credentials."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "nobi-edge-header-check/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.headers, None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers or Message(), None
    except Exception as exc:  # report type only, not URL/secret text
        return None, Message(), type(exc).__name__


def values(headers: Message, name: str) -> list[str]:
    return [str(value) for value in headers.get_all(name, [])]


def one(headers: Message, name: str) -> str | None:
    items = values(headers, name)
    return items[0] if len(items) == 1 else None


def add(checks: list[Check], path: str, name: str, ok: bool, detail: str) -> None:
    checks.append(Check(path, name, "pass" if ok else "fail", detail))


def evaluate(path: str, status: int | None, headers: Message, scheme: str) -> list[Check]:
    checks: list[Check] = []
    if status is None:
        add(checks, path, "fetch", False, "no response")
        return checks

    add(checks, path, "fetch", True, f"HTTP {status}")

    for name in SECURITY_HEADERS:
        count = len(values(headers, name))
        if name == "strict-transport-security" and scheme != "https":
            add(checks, path, name, True, "not required on plain HTTP")
            continue
        add(checks, path, f"{name} count", count == 1, f"{count} cop{'y' if count == 1 else 'ies'}")

    if scheme == "https":
        hsts = one(headers, "strict-transport-security") or ""
        match = re.search(r"max-age=(\d+)", hsts, re.IGNORECASE)
        max_age = int(match.group(1)) if match else 0
        add(checks, path, "hsts max-age", max_age >= 31536000, f"max-age={max_age}")

    xcto = (one(headers, "x-content-type-options") or "").lower()
    add(checks, path, "nosniff", xcto == "nosniff", xcto or "missing/duplicate")

    referrer = (one(headers, "referrer-policy") or "").lower()
    add(checks, path, "referrer-policy", referrer == "same-origin", referrer or "missing/duplicate")

    frame = (one(headers, "x-frame-options") or "").upper()
    add(checks, path, "x-frame-options", frame in {"DENY", "SAMEORIGIN"}, frame or "missing/duplicate")

    csp = (one(headers, "content-security-policy") or "").lower()
    add(checks, path, "frame-ancestors", "frame-ancestors 'none'" in csp, csp or "missing/duplicate")

    cache_values = values(headers, "cache-control")
    cache = cache_values[0].lower() if len(cache_values) == 1 else ""
    if path == "/":
        add(checks, path, "dashboard cache-control", len(cache_values) == 1 and "no-store" in cache,
            "; ".join(cache_values) or "missing")
    elif path.startswith("/api/"):
        add(checks, path, "api cache-control", len(cache_values) == 1 and "no-store" in cache,
            "; ".join(cache_values) or "missing")

    return checks


def self_test() -> int:
    """Offline tests for the header contract logic."""
    good = Message()
    good["Strict-Transport-Security"] = "max-age=31536000"
    good["X-Content-Type-Options"] = "nosniff"
    good["Referrer-Policy"] = "same-origin"
    good["X-Frame-Options"] = "DENY"
    good["Content-Security-Policy"] = "frame-ancestors 'none'"
    good["Cache-Control"] = "no-store"
    assert all(c.status == "pass" for c in evaluate("/", 200, good, "https"))

    api = Message()
    for key, value in good.items():
        if key.lower() != "cache-control":
            api[key] = value
    api["Cache-Control"] = "private, no-store, max-age=0"
    assert all(c.status == "pass" for c in evaluate("/api/health", 200, api, "https"))

    bad = Message()
    for key, value in good.items():
        bad[key] = value
    bad["Cache-Control"] = "no-store"
    del bad["Strict-Transport-Security"]
    bad["X-Content-Type-Options"] = "nosniff"
    results = evaluate("/", 200, bad, "https")
    assert any(c.status == "fail" and c.name == "hsts max-age" for c in results)
    assert any(c.status == "fail" and c.name == "dashboard cache-control" for c in results)
    assert any(c.status == "fail" and c.name == "x-content-type-options count" for c in results)
    print("self-test: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://capoy.ir", help="Public base URL")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--json", type=Path, help="Write machine-readable results here")
    parser.add_argument("--self-test", action="store_true", help="Run offline logic tests and exit")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    base = args.base.rstrip("/")
    scheme = "https" if base.startswith("https://") else "http"
    all_checks: list[Check] = []
    for path in CHECK_PATHS:
        status, headers, error = fetch(base + path, args.timeout)
        if error:
            all_checks.append(Check(path, "fetch", "fail", error))
            continue
        all_checks.extend(evaluate(path, status, headers, scheme))

    failed = [c for c in all_checks if c.status == "fail"]
    for check in all_checks:
        print(f"{check.status.upper():4} {check.path:16} {check.name:32} {check.detail}")

    if args.json:
        args.json.write_text(
            json.dumps({"base": base, "checks": [asdict(c) for c in all_checks]}, indent=2),
            encoding="utf-8",
        )

    print(f"\n{len(all_checks) - len(failed)}/{len(all_checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(2) from None
