"""Read budgets, bounded counters and proxy-aware cookie security."""
from __future__ import annotations

import pytest
from starlette.requests import Request

import identity
import security


def _request(*, host: str = "203.0.113.9", proto: str | None = None,
             scheme: str = "https") -> Request:
    headers = []
    if proto:
        headers.append((b"x-forwarded-proto", proto.encode()))
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET", "path": "/api/auth/login",
        "raw_path": b"/api/auth/login", "query_string": b"", "root_path": "",
        "scheme": scheme, "server": ("dashboard.test", 443),
        "client": (host, 51000), "headers": headers,
    })


def test_read_budget_applies_to_remote_hosts_but_not_loopback(monkeypatch) -> None:
    security.reset_rate_limits()
    monkeypatch.setattr(security, "READ_RATE_LIMIT", 2)
    assert security.read_rate_ok("203.0.113.9") is True
    assert security.read_rate_ok("203.0.113.9") is True
    assert security.read_rate_ok("203.0.113.9") is False
    # A local dashboard and TestClient must never throttle themselves.
    for host in ("127.0.0.1", "::1", "testclient"):
        for _ in range(10):
            assert security.read_rate_ok(host) is True
    security.reset_rate_limits()
    assert security.read_rate_ok("203.0.113.9") is True


def test_rate_counter_map_is_bounded(monkeypatch) -> None:
    security.reset_rate_limits()
    monkeypatch.setattr(security, "READ_RATE_LIMIT", 1000)
    for i in range(5000):
        security.read_rate_ok(f"198.51.100.{i % 250}.{i}")
    assert len(security._hits) <= security._MAX_KEYS
    security.reset_rate_limits()


def test_trusted_peer_requires_a_configured_proxy(monkeypatch) -> None:
    monkeypatch.delenv("NBX_TRUSTED_PROXIES", raising=False)
    assert security.trusted_peer(_request(host="127.0.0.1")) is False
    monkeypatch.setenv("NBX_TRUSTED_PROXIES", "127.0.0.1")
    assert security.trusted_peer(_request(host="127.0.0.1")) is True
    assert security.trusted_peer(_request(host="203.0.113.9")) is False


@pytest.mark.parametrize("proto,trusted,expected", [
    ("https", True, True),
    ("http", True, False),     # a trusted proxy really says the client used http
    ("http", False, True),     # an untrusted caller must NOT downgrade https
    (None, False, True),
])
def test_is_secure_cannot_be_downgraded_by_a_header(monkeypatch, proto, trusted, expected) -> None:
    if trusted:
        monkeypatch.setenv("NBX_TRUSTED_PROXIES", "127.0.0.1")
        host = "127.0.0.1"
    else:
        monkeypatch.delenv("NBX_TRUSTED_PROXIES", raising=False)
        host = "203.0.113.9"
    assert identity.is_secure(_request(host=host, proto=proto)) is expected


def test_unhandled_exception_does_not_echo_internal_text() -> None:
    """The 500 body carries a log id, never the exception message."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert '"log_id": row.get("id")' in text
    assert '"message": row.get("message")' not in text
