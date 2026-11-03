#!/usr/bin/env python3
"""Owner-capable, read-only review probe for nobiTrader / capoy.ir.

Run this YOURSELF against a deployment you own or are authorized to review.
It can sign in as owner, operator, or viewer, but it only sends:

  * GET requests to a fixed, reviewed allowlist; and
  * POST /api/auth/login and POST /api/auth/logout.

It never places/cancels orders, halts/resumes trading, changes settings,
creates users/invites, runs diagnostics/devsuite/backtests, uploads files,
or calls backup/restore/restart endpoints.

The output is a sanitized JSON review artifact. It deliberately omits
passwords, cookie values, CSRF values, API keys, raw logs, raw audit details,
usernames, IP addresses, user agents, and absolute server paths. Financial
and operational metrics may still be sensitive; inspect the file before sharing.

Example (owner, interactive hidden password prompt):

    python3 capoy_owner_probe.py --role owner --output capoy-owner-probe.json

For a temporary viewer account:

    python3 capoy_owner_probe.py --role viewer --output capoy-viewer-probe.json

Optional non-interactive password source (less safe than a hidden prompt):

    NBX_PASSWORD='...' python3 capoy_owner_probe.py --role owner

Do not send the password, invite code, cookies, or raw HAR to the reviewer.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import http.cookiejar
import json
import math
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_VERSION = "2.0.0"
TARGET_APP_VERSION = "2.1.8"
DEFAULT_BASE = "https://capoy.ir"
MAX_RESPONSE_BYTES = 2_000_000
ALLOWED_POST_PATHS = {"/api/auth/login", "/api/auth/logout"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

SECRET_KEY_RE = re.compile(
    r"(password|passwd|secret|api[_-]?key|authorization|cookie|csrf|"
    r"setup[_-]?token|invite[_-]?code|private[_-]?key|credential|otp)", re.I)
PERSONAL_KEY_RE = re.compile(
    r"(^username$|^user$|^actor$|^created_by$|^used_by$|^updated_by$|"
    r"^ip$|^client$|^ua$|^user_agent$|^email$|^chat_id$|^note$)", re.I)
PATH_KEY_RE = re.compile(
    r"(^|_)(path|dir|directory|file|root|db|database)($|_)|"
    r"^(backup_directory|databases|env_file)$|^NBX_.*_(DB|DIR|CACHE)$", re.I)
OPAQUE_TEXT_KEY_RE = re.compile(
    r"^(message|detail|stack|context|output|error|reason|configuration_problem)$", re.I)
LONG_SECRET_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9])")
HEX_RE = re.compile(r"(?<![A-Fa-f0-9])[A-Fa-f0-9]{32,}(?![A-Fa-f0-9])")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
ABS_PATH_RE = re.compile(r"(?<![\w])/(?:[^\s\"'<>/:]+/)+[^\s\"'<>/:]*")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def fingerprint(value: Any) -> str:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def finite_number(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    return value


@dataclass
class Response:
    status: int
    headers: list[tuple[str, str]]
    body: bytes
    final_url: str
    error: str = ""

    def header(self, name: str) -> str:
        for k, v in self.headers:
            if k.lower() == name.lower():
                return v
        return ""

    def headers_all(self, name: str) -> list[str]:
        return [v for k, v in self.headers if k.lower() == name.lower()]

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8", "replace"))
        except Exception:
            return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class Probe:
    base: str
    role: str
    timeout: float
    delay: float
    max_requests: int
    sample_symbol: str
    tf: str
    days: int
    limit: int
    include_compute: bool
    skip_upstream: bool
    username: str = ""
    password: str = field(default="", repr=False)
    jar: http.cookiejar.CookieJar = field(default_factory=http.cookiejar.CookieJar)
    opener: urllib.request.OpenerDirector | None = None
    results: list[dict] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    request_count: int = 0
    started: float = field(default_factory=time.monotonic)
    login_cookie_headers: list[str] = field(default_factory=list, repr=False)
    known_secret_values: list[str] = field(default_factory=list, repr=False)
    user_refs: dict[int, str] = field(default_factory=dict)
    openapi_inventory: list[dict] = field(default_factory=list)
    executed_get_paths: set[str] = field(default_factory=set)
    aborted: bool = False
    logged_in: bool = False

    def __post_init__(self) -> None:
        ctx = ssl.create_default_context()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx),
            urllib.request.HTTPCookieProcessor(self.jar),
            NoRedirect(),
        )
        self.opener.addheaders = [
            ("User-Agent", f"nobi-readonly-owner-review/{SCRIPT_VERSION}"),
            ("Accept", "application/json, text/plain;q=0.9, */*;q=0.5"),
        ]

    # ------------------------------------------------------------ transport
    def request(self, method: str, path: str, body: dict | None = None) -> Response:
        method = method.upper()
        parsed = urllib.parse.urlsplit(path)
        if parsed.scheme or parsed.netloc or not path.startswith("/"):
            raise ValueError("only same-origin absolute paths are allowed")
        if method != "GET" and not (method == "POST" and parsed.path in ALLOWED_POST_PATHS):
            raise ValueError(f"blocked non-read request: {method} {parsed.path}")
        if self.request_count >= self.max_requests:
            raise RuntimeError("request budget exhausted")
        if self.request_count and self.delay > 0:
            time.sleep(self.delay)
        self.request_count += 1
        url = self.base + path
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:  # type: ignore[union-attr]
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
                truncated = len(raw) > MAX_RESPONSE_BYTES
                if truncated:
                    raw = raw[:MAX_RESPONSE_BYTES]
                return Response(resp.status, list(resp.headers.items()), raw, resp.geturl(),
                                "response truncated" if truncated else "")
        except urllib.error.HTTPError as exc:
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
            truncated = len(raw) > MAX_RESPONSE_BYTES
            if truncated:
                raw = raw[:MAX_RESPONSE_BYTES]
            return Response(exc.code, list(exc.headers.items()), raw, exc.geturl(),
                            "response truncated" if truncated else "")
        except Exception as exc:
            # Do not include exception text: it can contain URLs, headers, or TLS details.
            return Response(0, [], b"", url, type(exc).__name__)

    # ------------------------------------------------------------ reporting
    def check(self, name: str, status: str, detail: str = "") -> None:
        if status not in {"pass", "warn", "fail", "info"}:
            raise ValueError(status)
        self.checks.append({"name": name, "status": status, "detail": self.redact_text(detail)})

    def redact_text(self, text: str) -> str:
        out = str(text)
        for secret in sorted(self.known_secret_values, key=len, reverse=True):
            if secret:
                out = out.replace(secret, "[REDACTED]")
        out = URL_RE.sub(lambda m: urllib.parse.urlunsplit(
            (*urllib.parse.urlsplit(m.group(0))[:2], "", "", "")), out)
        out = EMAIL_RE.sub("[REDACTED_EMAIL]", out)
        out = IPV4_RE.sub("[REDACTED_IP]", out)
        out = ABS_PATH_RE.sub("[REDACTED_PATH]", out)
        out = HEX_RE.sub("[REDACTED_HEX]", out)
        out = LONG_SECRET_RE.sub("[REDACTED_TOKEN]", out)
        return out[:1000]

    def sanitize(self, value: Any, key: str = "", depth: int = 0) -> Any:
        if depth > 8:
            return "[MAX_DEPTH]"
        if SECRET_KEY_RE.search(key):
            return value if isinstance(value, bool) else "[REDACTED]"
        if PERSONAL_KEY_RE.search(key):
            return "[REDACTED]"
        if PATH_KEY_RE.search(key) and isinstance(value, str):
            return "[REDACTED_PATH]" if value else ""
        if OPAQUE_TEXT_KEY_RE.search(key) and isinstance(value, str):
            return {"fingerprint": fingerprint(value), "length": len(value)}
        if isinstance(value, dict):
            items = list(value.items())
            out = {str(k): self.sanitize(v, str(k), depth + 1) for k, v in items[:120]}
            if len(items) > 120:
                out["_truncated_keys"] = len(items) - 120
            return out
        if isinstance(value, list):
            out = [self.sanitize(v, key, depth + 1) for v in value[:50]]
            if len(value) > 50:
                out.append({"_truncated_items": len(value) - 50})
            return out
        if isinstance(value, str):
            return self.redact_text(value)
        return finite_number(value)

    def cookie_summary(self) -> list[dict]:
        rows = []
        for raw in self.login_cookie_headers:
            parts = [p.strip() for p in raw.split(";")]
            if not parts or "=" not in parts[0]:
                continue
            name = parts[0].split("=", 1)[0]
            attrs = {}
            for part in parts[1:]:
                if "=" in part:
                    k, v = part.split("=", 1)
                    attrs[k.strip().lower()] = v.strip()
                else:
                    attrs[part.strip().lower()] = True
            rows.append({
                "name": name,
                "secure": bool(attrs.get("secure")),
                "httponly": bool(attrs.get("httponly")),
                "samesite": attrs.get("samesite", ""),
                "path": attrs.get("path", ""),
                "has_domain_attribute": "domain" in attrs,
                "has_max_age": "max-age" in attrs,
                "has_expires": "expires" in attrs,
            })
        return rows

    def security_headers(self, resp: Response) -> dict:
        names = (
            "strict-transport-security", "x-content-type-options", "referrer-policy",
            "content-security-policy", "x-frame-options", "permissions-policy",
            "cache-control", "cross-origin-opener-policy", "cross-origin-resource-policy",
        )
        out = {}
        for name in names:
            value = resp.header(name)
            if name == "content-security-policy" and value:
                out[name] = {"present": True, "fingerprint": fingerprint(value),
                             "has_frame_ancestors": "frame-ancestors" in value.lower()}
            else:
                out[name] = self.redact_text(value) if value else None
        return out

    def record_endpoint(self, section: str, path: str, resp: Response,
                        expected: tuple[int, ...], summarizer: Callable[[Any], Any] | None,
                        optional: bool = False) -> Any:
        payload = resp.json()
        entry = {
            "section": section, "method": "GET", "path": path,
            "status": resp.status, "expected": list(expected),
            "content_type": resp.header("content-type"),
            "bytes": len(resp.body), "error": resp.error,
            "cache_control": resp.header("cache-control") or None,
            "security_headers": self.security_headers(resp),
        }
        if resp.status in expected:
            entry["outcome"] = "ok"
        elif resp.status == 404:
            entry["outcome"] = "absent"
        elif resp.status in (401, 403):
            entry["outcome"] = "permission_or_session_problem"
        elif resp.status >= 500 or resp.status == 0:
            entry["outcome"] = "error"
        else:
            entry["outcome"] = "unexpected"
        if summarizer and payload is not None:
            try:
                entry["summary"] = summarizer(payload)
            except Exception as exc:
                entry["summary_error"] = type(exc).__name__
        elif payload is not None:
            entry["summary"] = self.sanitize(payload)
        else:
            entry["summary"] = {"non_json": True, "fingerprint": fingerprint(resp.body)}
        if isinstance(payload, dict) and payload.get("ok") is False:
            entry["application_ok"] = False
            entry["application_error"] = self.sanitize(payload.get("error") or payload.get("reason") or "unknown")
        self.results.append(entry)
        self.executed_get_paths.add(urllib.parse.urlsplit(path).path)
        return payload

    def get(self, section: str, path: str, expected: tuple[int, ...] = (200,),
            summarizer: Callable[[Any], Any] | None = None, optional: bool = False) -> Any:
        resp = self.request("GET", path)
        return self.record_endpoint(section, path, resp, expected, summarizer, optional)

    # ------------------------------------------------------------ summaries
    def generic_json(self, payload: Any) -> Any:
        return self.sanitize(payload)

    def me_summary(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            return {}
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        caps = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else {}
        return {
            "auth_required": payload.get("auth_required"),
            "method": payload.get("method"),
            "role": user.get("role"),
            "active": user.get("active"),
            "can_mutate": payload.get("can_mutate"),
            "is_owner": payload.get("is_owner"),
            "capabilities": {str(k): bool(v) for k, v in caps.items()},
            "live_policy_present": payload.get("live_policy") is not None,
            "preferences": self.sanitize(payload.get("preferences") or {}),
        }

    def users_summary(self, payload: Any) -> dict:
        users = payload.get("users") if isinstance(payload, dict) else None
        if not isinstance(users, list):
            return {}
        roles: dict[str, int] = {}
        active = 0
        refs = []
        for i, u in enumerate(users[:100], 1):
            if not isinstance(u, dict):
                continue
            uid = u.get("id")
            if isinstance(uid, int):
                self.user_refs[uid] = f"account-{i}"
            role = str(u.get("role") or "unknown")
            roles[role] = roles.get(role, 0) + 1
            active += 1 if u.get("active") else 0
            policy = u.get("live_policy") if isinstance(u.get("live_policy"), dict) else None
            refs.append({
                "ref": self.user_refs.get(uid, f"account-{i}"),
                "role": role, "active": bool(u.get("active")),
                "live_policy_enabled": bool(policy and policy.get("enabled")),
            })
        return {"count": len(users), "active": active, "roles": roles,
                "sessions": payload.get("sessions"), "accounts": refs}

    def events_summary(self, payload: Any) -> dict:
        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list):
            return self.sanitize(payload)
        actions: dict[str, int] = {}
        results: dict[str, int] = {}
        timestamps = []
        for e in events[:200]:
            if not isinstance(e, dict):
                continue
            a = str(e.get("action") or e.get("event") or "unknown")
            r = str(e.get("result") or "unknown")
            actions[a] = actions.get(a, 0) + 1
            results[r] = results.get(r, 0) + 1
            ts = e.get("at") or e.get("ts") or e.get("created_at")
            if isinstance(ts, (int, float)):
                timestamps.append(ts)
        return {"count": len(events), "actions": actions, "results": results,
                "oldest_ts": min(timestamps) if timestamps else None,
                "newest_ts": max(timestamps) if timestamps else None}

    def sessions_summary(self, payload: Any) -> dict:
        sessions = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(sessions, list):
            return {}
        return {"count": len(sessions),
                "current": sum(1 for s in sessions if isinstance(s, dict) and s.get("current")),
                "expires_min": min((s.get("expires_at") for s in sessions if isinstance(s, dict) and isinstance(s.get("expires_at"), (int, float))), default=None),
                "expires_max": max((s.get("expires_at") for s in sessions if isinstance(s, dict) and isinstance(s.get("expires_at"), (int, float))), default=None)}

    def invites_summary(self, payload: Any) -> dict:
        invites = payload.get("invites") if isinstance(payload, dict) else None
        if not isinstance(invites, list):
            return {}
        states: dict[str, int] = {}
        roles: dict[str, int] = {}
        for inv in invites:
            if isinstance(inv, dict):
                states[str(inv.get("state"))] = states.get(str(inv.get("state")), 0) + 1
                roles[str(inv.get("role"))] = roles.get(str(inv.get("role")), 0) + 1
        return {"count": len(invites), "states": states, "roles": roles}

    def errors_summary(self, payload: Any) -> dict:
        rows = payload.get("errors") or payload.get("items") or payload.get("rows") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return {"keys": sorted(payload.keys()) if isinstance(payload, dict) else []}
        levels: dict[str, int] = {}
        sources: dict[str, int] = {}
        fingerprints: dict[str, int] = {}
        for row in rows[:200]:
            if not isinstance(row, dict):
                continue
            levels[str(row.get("level"))] = levels.get(str(row.get("level")), 0) + 1
            sources[str(row.get("source"))] = sources.get(str(row.get("source")), 0) + 1
            msg = str(row.get("message") or "")
            fp = fingerprint(msg)
            fingerprints[fp] = fingerprints.get(fp, 0) + 1
        return {"count": len(rows), "levels": levels, "sources": sources,
                "unique_message_fingerprints": len(fingerprints),
                "top_fingerprints": sorted(fingerprints.items(), key=lambda x: -x[1])[:10],
                "filters": self.sanitize(payload.get("filters") or {})}

    def settings_summary(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            return {}
        secrets = payload.get("secrets")
        secret_presence = []
        if isinstance(secrets, list):
            for s in secrets:
                if isinstance(s, dict):
                    secret_presence.append({"name": s.get("name"), "present": bool(s.get("present"))})
        elif isinstance(secrets, dict):
            secret_presence = [{"name": k, "present": bool(v)} for k, v in secrets.items()]
        providers = payload.get("providers") if isinstance(payload.get("providers"), dict) else {}
        provider_status = {}
        for name, p in providers.items():
            if isinstance(p, dict):
                provider_status[name] = {k: p.get(k) for k in ("configured", "ok", "partial", "stale", "age_s") if k in p}
        return {
            "read_only": payload.get("read_only"),
            "config_hash_fingerprint": fingerprint(payload.get("config_hash")) if payload.get("config_hash") else None,
            "weights": self.sanitize(payload.get("weights") or {}),
            "thresholds": self.sanitize(payload.get("thresholds") or {}),
            "risk_limits": self.sanitize(payload.get("risk_limits") or {}),
            "flags": self.sanitize(payload.get("flags") or {}),
            "secret_presence": secret_presence,
            "providers": provider_status,
            "environment": payload.get("environment"),
            "env_file_present": bool(payload.get("env")),
        }

    def install_settings_summary(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            return {}
        values = payload.get("values") if isinstance(payload.get("values"), dict) else {}
        safe_values = {}
        for k, v in values.items():
            safe_values[k] = "[REDACTED_PATH]" if PATH_KEY_RE.search(k) else self.sanitize(v, k)
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        safe_fields = {}
        for k, f in fields.items():
            if isinstance(f, dict):
                safe_fields[k] = {
                    "configured": f.get("effective") not in (None, ""),
                    "managed_present": f.get("managed") is not None,
                    "source": f.get("source"),
                    "pending_or_overridden": f.get("pending_or_overridden"),
                }
        return {"values": safe_values, "fields": safe_fields,
                "allowed": payload.get("allowed"), "restart_required": payload.get("restart_required")}

    def wizard_summary(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            return {}
        steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
        runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
        return {
            "steps": [{"step": s.get("step"), "state": s.get("state"), "updated_at": s.get("updated_at")} for s in steps if isinstance(s, dict)],
            "runs": [{"id": r.get("id"), "action": r.get("action"), "state": r.get("state"),
                      "started_at": r.get("started_at"), "finished_at": r.get("finished_at"),
                      "rollback_available": r.get("rollback_available")} for r in runs if isinstance(r, dict)],
            "actions": payload.get("actions"), "helper": self.sanitize(payload.get("helper") or {}),
        }

    def operations_summary(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            return {}
        archives = payload.get("archives") if isinstance(payload.get("archives"), list) else []
        return {"archive_count": len(archives),
                "archive_bytes_total": sum(a.get("bytes", 0) for a in archives if isinstance(a, dict)),
                "helper_available": payload.get("helper_available"),
                "restore_available": payload.get("restore_available"),
                "actions": payload.get("actions"),
                "databases_configured": {k: bool(v) for k, v in (payload.get("databases") or {}).items()}}

    def devsuite_summary(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            return {}
        last = payload.get("last") if isinstance(payload.get("last"), dict) else None
        return {"available": payload.get("available"), "busy": payload.get("busy"),
                "gates": payload.get("gates"),
                "last": {k: last.get(k) for k in ("id", "state", "started_at", "finished_at", "duration_ms", "commit", "dirty", "results")} if last else None}

    def diagnostics_summary(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            return {}
        checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
        return {"check_count": len(checks),
                "checks": [{"id": c.get("id"), "kind": c.get("kind"), "label": c.get("label")} for c in checks if isinstance(c, dict)],
                "devsuite": self.sanitize(payload.get("devsuite") or {})}

    def list_summary(self, payload: Any, list_key: str) -> dict:
        if not isinstance(payload, dict):
            return {}
        rows = payload.get(list_key)
        if not isinstance(rows, list):
            return self.sanitize(payload)
        return {"count": len(rows), "sample": self.sanitize(rows[: self.limit])}

    def signals_summary(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            return {}
        scored = payload.get("scored") if isinstance(payload.get("scored"), list) else []
        sample = []
        for row in scored[: self.limit]:
            if isinstance(row, dict):
                sample.append({k: row.get(k) for k in ("symbol", "tf", "composite", "ta_score", "fa_score", "direction", "verdict") if k in row})
        return {"ok": payload.get("ok"), "tf": payload.get("tf"), "count": payload.get("count"),
                "age_s": payload.get("age_s"), "stale": payload.get("stale"),
                "refreshing": payload.get("refreshing"), "freshness": self.sanitize(payload.get("freshness") or {}),
                "gates": self.sanitize(payload.get("gates") or {}), "sample": sample}

    def candles_summary(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            return {}
        rows = payload.get("candles") or payload.get("rows") or []
        if not isinstance(rows, list):
            rows = []
        first = rows[0] if rows else None
        last = rows[-1] if rows else None
        def bar_point(b: Any) -> Any:
            if isinstance(b, dict):
                return {k: b.get(k) for k in ("t", "ts", "time", "c", "close") if k in b}
            if isinstance(b, list) and len(b) >= 5:
                return {"t": b[0], "c": b[4]}
            return None
        return {"ok": payload.get("ok"), "symbol": payload.get("symbol"), "tf": payload.get("tf"),
                "count": len(rows), "source": payload.get("source"), "first": bar_point(first), "last": bar_point(last)}

    def orderbook_summary(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            return {}
        bids = payload.get("bids") or []
        asks = payload.get("asks") or []
        def best(rows: Any) -> Any:
            if isinstance(rows, list) and rows:
                r = rows[0]
                if isinstance(r, dict):
                    return r.get("p")
                if isinstance(r, list) and r:
                    return r[0]
            return None
        bb, ba = best(bids), best(asks)
        spread = None
        if isinstance(bb, (int, float)) and isinstance(ba, (int, float)) and bb > 0:
            spread = (ba - bb) / bb * 100
        return {"ok": payload.get("ok"), "symbol": payload.get("symbol"),
                "bid_levels": len(bids) if isinstance(bids, list) else None,
                "ask_levels": len(asks) if isinstance(asks, list) else None,
                "best_bid": bb, "best_ask": ba, "spread_pct": spread}

    def live_orders_summary(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            return {}
        rows = payload.get("orders") or payload.get("rows") or []
        if not isinstance(rows, list):
            return self.sanitize(payload)
        states: dict[str, int] = {}
        sample = []
        for r in rows[:200]:
            if isinstance(r, dict):
                state = str(r.get("state") or r.get("status") or "unknown")
                states[state] = states.get(state, 0) + 1
        for r in rows[: self.limit]:
            if isinstance(r, dict):
                sample.append({k: r.get(k) for k in ("state", "status", "symbol", "side", "execution", "amount", "price", "matched", "created_at", "updated_at") if k in r})
        return {"count": len(rows), "states": states, "sample": sample}

    def openapi_summary(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            return {}
        paths = payload.get("paths") if isinstance(payload.get("paths"), dict) else {}
        inventory = []
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, op in methods.items():
                if method.lower() in {"get", "post", "put", "patch", "delete"}:
                    inventory.append({"method": method.upper(), "path": path,
                                      "operation_id": op.get("operationId") if isinstance(op, dict) else None,
                                      "tags": op.get("tags") if isinstance(op, dict) else None})
        self.openapi_inventory = inventory
        gets = [x for x in inventory if x["method"] == "GET"]
        return {"path_count": len(paths), "operation_count": len(inventory),
                "get_count": len(gets), "inventory": inventory}

    # ------------------------------------------------------------ auth
    def login(self) -> bool:
        self.known_secret_values.extend([self.password, self.username])
        resp = self.request("POST", "/api/auth/login", {"username": self.username, "password": self.password})
        self.password = ""
        os.environ.pop("NBX_PASSWORD", None)
        self.login_cookie_headers = resp.headers_all("set-cookie")
        payload = resp.json() if isinstance(resp.json(), dict) else {}
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        actual_role = user.get("role")
        self.check("login HTTP status", "pass" if resp.status == 200 else "fail", f"status={resp.status}")
        self.check("login role matches --role", "pass" if actual_role == self.role else "fail",
                   f"expected={self.role}; actual={actual_role}")
        cookies = self.cookie_summary()
        session = next((c for c in cookies if c["name"] == "nbx_session"), None)
        csrf = next((c for c in cookies if c["name"] == "nbx_csrf"), None)
        is_https = urllib.parse.urlsplit(self.base).scheme == "https"
        if session:
            self.check("session cookie HttpOnly", "pass" if session["httponly"] else "fail")
            self.check("session cookie SameSite", "pass" if str(session["samesite"]).lower() in {"lax", "strict"} else "warn", str(session["samesite"]))
            self.check("session cookie Secure on HTTPS", "pass" if session["secure"] else "fail" if is_https else "info")
            self.check("session cookie host-only", "pass" if not session["has_domain_attribute"] else "warn")
        else:
            self.check("session cookie present", "fail")
        if csrf:
            self.check("CSRF cookie readable by JS", "pass" if not csrf["httponly"] else "warn")
        else:
            self.check("CSRF cookie present", "warn")
        for c in cookies:
            value = ""
            for raw in self.login_cookie_headers:
                if raw.startswith(c["name"] + "="):
                    value = raw.split(";", 1)[0].split("=", 1)[1]
            if value:
                self.known_secret_values.append(value)
        if resp.status != 200 or actual_role != self.role:
            self.results.append({"section": "auth", "method": "POST", "path": "/api/auth/login",
                                 "status": resp.status, "outcome": "ok" if resp.status == 200 else "error",
                                 "summary": self.me_summary(payload)})
            if resp.status == 200:
                self.logged_in = True
                self.logout()
            return False
        self.results.append({"section": "auth", "method": "POST", "path": "/api/auth/login",
                             "status": 200, "outcome": "ok", "summary": self.me_summary(payload),
                             "cookie_flags": cookies})
        self.logged_in = True
        return True

    def logout(self) -> None:
        try:
            resp = self.request("POST", "/api/auth/logout")
            self.check("logout HTTP status", "pass" if resp.status == 200 else "fail", f"status={resp.status}")
            self.results.append({"section": "auth", "method": "POST", "path": "/api/auth/logout",
                                 "status": resp.status, "outcome": "ok" if resp.status == 200 else "error"})
            me = self.request("GET", "/api/auth/me").json()
            cleared = isinstance(me, dict) and me.get("user") is None
            self.check("session cleared after logout", "pass" if cleared else "fail")
        except Exception as exc:
            self.check("logout completed", "fail", type(exc).__name__)
        finally:
            self.jar.clear()
            self.login_cookie_headers = []
            self.logged_in = False

    # ------------------------------------------------------------ coverage
    def run(self) -> dict:
        owner = self.role == "owner"
        operator = self.role in {"owner", "operator"}
        owner_expected = (200,) if owner else (403,)
        operator_expected = (200,) if operator else (403,)
        try:
            # Public surface, before credentials are used.
            self.get("public", "/api/health")
            self.get("public", "/api/setup/status")
            anon_me = self.get("public", "/api/auth/me")
            self.check("anonymous cannot see a user", "pass" if isinstance(anon_me, dict) and anon_me.get("user") is None else "fail")
            root = self.request("GET", "/")
            self.results.append({"section": "public", "method": "GET", "path": "/", "status": root.status,
                                 "outcome": "ok" if root.status == 200 else "error",
                                 "bytes": len(root.body), "security_headers": self.security_headers(root),
                                 "summary": {"fingerprint": fingerprint(root.body)}})
            for h, status in (("strict-transport-security", "warn"), ("x-content-type-options", "warn"),
                              ("content-security-policy", "warn"), ("referrer-policy", "warn")):
                self.check(f"edge header {h}", "pass" if root.header(h) else status)
            self.check("frame protection", "pass" if root.header("x-frame-options") or "frame-ancestors" in root.header("content-security-policy").lower() else "warn")
            self.get("public", "/manifest.json")
            self.get("public", "/robots.txt", expected=(200, 404))
            self.get("public", "/version-check.html", expected=(200, 404))
            self.get("public", "/openapi.json", expected=(401, 403))
            self.get("public", "/docs", expected=(401, 403))

            if not self.login():
                self.aborted = True
                return self.report()

            # Account and owner administration.
            me = self.get("account", "/api/auth/me", summarizer=self.me_summary)
            if isinstance(me, dict):
                self.check("authenticated role", "pass" if (me.get("user") or {}).get("role") == self.role else "fail")
                self.check("authenticated API no-store", "warn" if "no-store" not in (self.results[-1].get("cache_control") or "").lower() else "pass")
            self.get("account", "/api/auth/me/preferences")
            self.get("account", "/api/auth/me/sessions", summarizer=self.sessions_summary)
            self.get("account", "/api/auth/me/activity?limit=20", summarizer=self.events_summary)
            users = self.get("account", "/api/auth/users", expected=owner_expected, summarizer=self.users_summary)
            self.get("account", "/api/auth/activity?limit=20", expected=owner_expected, summarizer=self.events_summary)
            self.get("account", "/api/auth/invites", expected=owner_expected, summarizer=self.invites_summary)
            if owner and isinstance(users, dict):
                operators = [u for u in users.get("users", []) if isinstance(u, dict) and u.get("role") == "operator"]
                for u in operators[:5]:
                    uid = u.get("id")
                    if isinstance(uid, int):
                        self.get("account", f"/api/auth/users/{uid}/live-policy", expected=(200,))
                if len(operators) > 5:
                    self.check("operator live-policy coverage", "info", f"sampled 5 of {len(operators)} operators")

            # Settings, operations, logs.
            self.get("settings", "/api/settings", expected=owner_expected, summarizer=self.settings_summary)
            self.get("settings", "/api/install/settings", expected=owner_expected, summarizer=self.install_settings_summary)
            self.get("settings", "/api/install/wizard/status", expected=owner_expected, summarizer=self.wizard_summary)
            self.get("settings", "/api/operations", expected=owner_expected, summarizer=self.operations_summary)
            self.get("settings", "/api/cache/stats", expected=owner_expected)
            self.get("settings", "/api/errors?limit=20&hours=168", expected=owner_expected, summarizer=self.errors_summary)
            self.get("settings", "/api/diagnostics", expected=owner_expected, summarizer=self.diagnostics_summary)
            self.get("settings", "/api/devsuite", expected=owner_expected, summarizer=self.devsuite_summary)
            self.get("settings", "/api/notify/status", expected=operator_expected)
            self.get("settings", "/api/audit?limit=20&days=30&scope=all", expected=operator_expected, summarizer=self.events_summary)

            # Market and risk.
            self.get("market", "/api/universe", summarizer=lambda p: self.list_summary(p, "universe"))
            self.get("market", f"/api/market/overview?tf={self.tf}")
            self.get("market", "/api/coins")
            self.get("risk", "/api/risk/state")
            self.get("risk", "/api/exec/mode")
            self.get("risk", "/api/ws/status")
            if not self.skip_upstream:
                self.get("market", f"/api/signals?tf={self.tf}", summarizer=self.signals_summary)
                self.get("market", f"/api/candles/{self.sample_symbol}?tf={self.tf}&count=120", summarizer=self.candles_summary)
                self.get("market", f"/api/orderbook/{self.sample_symbol}?limit=8", summarizer=self.orderbook_summary)
                self.get("market", f"/api/fa/{self.sample_symbol}")
                self.get("market", "/api/reference/status")
                self.get("market", f"/api/reference/cross-check?symbol={self.sample_symbol}")
                self.get("risk", f"/api/risk/plan?symbol={self.sample_symbol}&side=buy&tf={self.tf}&approval=false")
                self.get("adaptive", f"/api/adaptive/thresholds/{self.sample_symbol}?tf={self.tf}&source=nobitex&limit=120")
            else:
                self.check("upstream-heavy reads", "info", "skipped by --skip-upstream")

            # Paper and live read-only views.
            self.get("paper", "/api/paper/orders?limit=20", summarizer=lambda p: self.list_summary(p, "orders"))
            self.get("paper", "/api/paper/positions", summarizer=lambda p: self.list_summary(p, "positions"))
            self.get("paper", "/api/paper/metrics")
            self.get("live", "/api/live/status", expected=operator_expected)
            self.get("live", "/api/live/evidence", expected=operator_expected)
            self.get("live", "/api/live/orders?status=all", expected=(*operator_expected, 409), summarizer=self.live_orders_summary)

            # Journal, intelligence, drift, backtest reports.
            self.get("journal", f"/api/journal/summary?days={self.days}&limit={self.limit}")
            self.get("journal", "/api/journal/stage")
            self.get("journal", f"/api/journal/signals?days={self.days}&limit={self.limit}", summarizer=lambda p: self.list_summary(p, "signals"))
            self.get("journal", "/api/journal/msi")
            self.get("intelligence", f"/api/signal-intelligence/summary?days={self.days}")
            self.get("intelligence", f"/api/signal-intelligence/funnel?days={self.days}")
            self.get("intelligence", f"/api/signal-intelligence/cohorts?days={self.days}")
            self.get("intelligence", f"/api/signal-intelligence/signals?days={self.days}&limit={self.limit}", summarizer=lambda p: self.list_summary(p, "signals"))
            self.get("intelligence", f"/api/v1/signal-intelligence/evidence?days={self.days}&limit={self.limit}&cohort_limit=10")
            self.get("intelligence", f"/api/v1/signal-intelligence/summary?days={self.days}")
            self.get("intelligence", f"/api/v1/signal-intelligence/pipeline?days={self.days}")
            self.get("intelligence", f"/api/v1/signal-intelligence/gate-reasons?days={self.days}")
            self.get("intelligence", f"/api/v1/signal-intelligence/signals?days={self.days}&limit={self.limit}")
            self.get("intelligence", "/api/v1/signal-intelligence/drift/latest")
            self.get("intelligence", "/api/v1/signal-intelligence/drift/schedule")
            self.get("intelligence", "/api/v1/signal-intelligence/drift/config-inventory")
            self.get("intelligence", "/api/v1/signal-intelligence/drift/cohorts")
            self.get("intelligence", "/api/v1/signal-intelligence/drift/runs?limit=20")
            self.get("backtest", "/api/backtest/reports?limit=10", summarizer=lambda p: self.list_summary(p, "reports"))
            self.get("backtest", "/api/backtest/promotion-gate")

            # Computationally heavier GETs are opt-in, still no mutation.
            if self.include_compute and not self.skip_upstream:
                self.get("adaptive", f"/api/adaptive/evidence/{self.sample_symbol}?tf={self.tf}&source=nobitex&limit=120")
                self.get("backtest", f"/api/backtest/quick?symbol={self.sample_symbol}&tf={self.tf}&bars=200")
            else:
                self.check("heavy compute GETs", "info", "skipped; use --include-compute for one small adaptive/backtest sample")
            self.check("gate ledger evidence GET", "info", "skipped because it can fetch many symbols; gate status is covered by promotion/live evidence")

            # Authenticated schema inventory and static asset fingerprints.
            self.get("schema", "/openapi.json", expected=(200,), summarizer=self.openapi_summary)
            for path in ("/nbx-core.js", "/nbx-app.js", "/nobitex-mobile.html"):
                resp = self.request("GET", path)
                self.results.append({"section": "assets", "method": "GET", "path": path,
                                     "status": resp.status, "outcome": "ok" if resp.status == 200 else "error",
                                     "bytes": len(resp.body), "truncated": bool(resp.error),
                                     "summary": {"sha256": hashlib.sha256(resp.body).hexdigest(),
                                                 "version_markers": sorted(set(re.findall(r"v?\d+\.\d+\.\d+", resp.body.decode("utf-8", "replace"))))[:10]}})
        except KeyboardInterrupt:
            self.aborted = True
            self.check("probe interrupted", "warn", "KeyboardInterrupt")
        except Exception as exc:
            self.aborted = True
            self.check("probe completed", "fail", type(exc).__name__)
        finally:
            if self.logged_in:
                self.logout()
        return self.report()

    def report(self) -> dict:
        counts: dict[str, int] = {}
        for c in self.checks:
            counts[c["status"]] = counts.get(c["status"], 0) + 1
        endpoint_outcomes: dict[str, int] = {}
        sections: dict[str, int] = {}
        for r in self.results:
            endpoint_outcomes[r.get("outcome", "unknown")] = endpoint_outcomes.get(r.get("outcome", "unknown"), 0) + 1
            sections[r.get("section", "unknown")] = sections.get(r.get("section", "unknown"), 0) + 1
        known_gets = {x["path"] for x in self.openapi_inventory if x["method"] == "GET"}
        unreviewed = sorted(p for p in known_gets if p not in self.executed_get_paths and "{" not in p)
        return {
            "script_version": SCRIPT_VERSION,
            "target_app_version": TARGET_APP_VERSION,
            "generated_at": now_iso(),
            "base_origin": self.base,
            "expected_role": self.role,
            "read_only_guarantee": {
                "allowed_mutations": ["POST /api/auth/login", "POST /api/auth/logout"],
                "all_other_requests": "GET only",
                "orders": False, "settings_changes": False, "user_changes": False,
                "diagnostics_run": False, "devsuite_run": False, "backup_restore_restart": False,
            },
            "privacy": {
                "credentials_in_output": False,
                "cookie_values_in_output": False,
                "raw_logs_in_output": False,
                "usernames_ips_user_agents_in_output": False,
                "note": "Financial/operational metrics may still be sensitive; inspect before sharing.",
            },
            "aborted": self.aborted,
            "request_count": self.request_count,
            "duration_s": round(time.monotonic() - self.started, 2),
            "check_counts": counts,
            "endpoint_outcomes": endpoint_outcomes,
            "section_counts": sections,
            "checks": self.checks,
            "endpoints": self.results,
            "openapi_unreviewed_static_get_paths": unreviewed,
            "limitations": [
                "This is an HTTP/API probe, not a rendered-browser test.",
                "It samples one symbol/timeframe; it does not exercise every market or every endpoint.",
                "Heavy GETs are skipped unless --include-compute is supplied.",
                "Some GET endpoints refresh caches or initialize read-model state; no control/mutation endpoint is called.",
                "Owner role does not bypass the reviewer's no-mutation safety boundary.",
            ],
        }


def validate_base(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.username or parsed.password:
        raise SystemExit("Base URL must not contain credentials.")
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise SystemExit("Base URL must be an http(s) origin.")
    if parsed.scheme == "http" and parsed.hostname not in LOOPBACK_HOSTS:
        raise SystemExit("Refusing to send owner credentials over non-loopback HTTP.")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise SystemExit("Use an origin only, e.g. https://capoy.ir")
    return f"{parsed.scheme}://{parsed.netloc}"


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        if tmp.exists():
            tmp.unlink()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("NBX_BASE_URL", DEFAULT_BASE))
    ap.add_argument("--role", choices=("owner", "operator", "viewer"), default="owner")
    ap.add_argument("--username", default=os.environ.get("NBX_USERNAME", ""))
    ap.add_argument("--output", default="capoy-owner-probe.json")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--max-requests", type=int, default=120)
    ap.add_argument("--sample-symbol", default="BTCUSDT")
    ap.add_argument("--tf", choices=("15m", "1h", "4h", "D"), default="15m")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--include-compute", action="store_true", help="also run a few small, read-only computational GETs")
    ap.add_argument("--skip-upstream", action="store_true", help="skip endpoints likely to fetch external market/fundamental data")
    args = ap.parse_args()
    base = validate_base(args.base_url)
    username = args.username or input("Username: ").strip()
    password = os.environ.get("NBX_PASSWORD", "")
    if not password:
        password = getpass.getpass("Password (hidden): ")
    if not username or not password:
        print("Username and password are required.", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[A-Z0-9]{6,12}", args.sample_symbol.upper()):
        print("Invalid sample symbol.", file=sys.stderr)
        return 2
    probe = Probe(base=base, role=args.role, timeout=max(1.0, args.timeout),
                  delay=max(0.0, args.delay), max_requests=max(10, args.max_requests),
                  sample_symbol=args.sample_symbol.upper(), tf=args.tf,
                  days=max(1, min(args.days, 365)), limit=max(1, min(args.limit, 50)),
                  include_compute=args.include_compute, skip_upstream=args.skip_upstream,
                  username=username, password=password)
    report = probe.run()
    out = Path(args.output)
    write_report(out, report)
    print(f"Wrote {out} ({report['request_count']} requests; checks={report['check_counts']})", file=sys.stderr)
    if report["aborted"] or report["check_counts"].get("fail", 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
