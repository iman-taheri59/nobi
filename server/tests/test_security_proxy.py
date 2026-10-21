"""Behind a reverse proxy, "loopback only" must not mean "everyone".

The bug this file pins down, measured before it was fixed::

    auth mode: loopback-only
    POST /api/paper/halt  (+ X-Forwarded-For: 203.0.113.9)  -> 200

An nginx or Caddy on the same host connects from 127.0.0.1, so *every* request
looked local and the loopback fallback approved it. A public deployment without
`NBX_API_TOKEN` would have accepted `POST /api/paper/halt`,
`POST /api/paper/order` and `POST /api/devsuite/run` from anyone on the
internet — the exact failure mode the module claims to prevent.

Three rules, and each one is a test below:

1. No `NBX_TRUSTED_PROXIES` → `X-Forwarded-For` is ignored. A header nobody
   configured cannot change who you are.
2. A trusted proxy → the caller is the **rightmost** hop that is not itself a
   trusted proxy. The leftmost value is written by the client and may be a lie.
3. Trusted proxy + no token → the process refuses to start. The combination has
   no safe meaning, so it does not get to run and hope.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import security


def _request(peer: str, forwarded: str | None = None, path: str = "/who",
             token: str | None = None) -> Request:
    """A request as Starlette builds it: a TCP peer plus whatever headers arrived.

    Built by hand rather than through `TestClient`, because the peer address is
    the thing under test and every HTTP client in the stack insists on setting
    it to "testclient".
    """
    headers: list[tuple[bytes, bytes]] = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    if token is not None:
        headers.append((b"x-api-token", token.encode()))
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": headers, "client": (peer, 54321), "server": ("testserver", 80),
    })


def _call(peer: str, forwarded: str | None = None) -> str:
    """Who does the guard think is calling, from a given TCP peer?"""
    return security.client_host(_request(peer, forwarded))


def _post(peer: str, forwarded: str | None = None, token: str | None = None) -> None:
    """Run the real dependency; raises HTTPException exactly as FastAPI would."""
    asyncio.run(security.require_client(_request(peer, forwarded, path="/mutate", token=token)))


def _configure(monkeypatch: pytest.MonkeyPatch, *, proxies: str | None = None,
               token: str | None = None) -> None:
    """Configure the two knobs; `monkeypatch` undoes both at teardown."""
    if proxies is not None:
        monkeypatch.setenv("NBX_TRUSTED_PROXIES", proxies)
    if token is not None:
        monkeypatch.setattr(security, "API_TOKEN", token)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    """Neither the token nor the proxy list may outlive a test.

    An earlier version of this file reloaded `security` and set the token with
    `os.environ[...] = ...`. Both are traps: `monkeypatch` reverts the
    environment *after* the fixture's own teardown, so a reload there snapshots
    the dirty value into the module, and a later `main` import then demands a
    token from every test in the suite. The fix is to stop reloading: the
    token is a module global that `monkeypatch.setattr` restores for free, and
    `trusted_proxies()` already reads the environment on every call.
    """
    monkeypatch.setattr(security, "API_TOKEN", "")
    monkeypatch.delenv("NBX_TRUSTED_PROXIES", raising=False)
    yield


# --------------------------------------------------------------------------- #
# 1. an unconfigured header is not evidence
# --------------------------------------------------------------------------- #
def test_a_forwarded_header_from_an_untrusted_peer_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _call("203.0.113.9", "10.0.0.1") == "203.0.113.9"


def test_a_loopback_peer_carrying_forwarded_headers_is_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """No configuration, no browser-sent header — but a proxy is clearly there.

    This is the default deployment mistake: run behind nginx, forget
    NBX_TRUSTED_PROXIES. Failing closed turns "everyone is local" into
    "nobody is", which is a 403 with an explanation instead of an open console.
    """
    assert _call("127.0.0.1", "203.0.113.9") == "203.0.113.9"


def test_no_header_no_config_is_just_the_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _call("198.51.100.7") == "198.51.100.7"


# --------------------------------------------------------------------------- #
# 2. the proxy chain: rightmost untrusted hop wins
# --------------------------------------------------------------------------- #
def test_a_trusted_proxy_passes_the_real_client_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, proxies="127.0.0.1")
    assert _call("127.0.0.1", "203.0.113.9") == "203.0.113.9"


def test_a_client_cannot_choose_its_own_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """`X-Forwarded-For: <anything the client likes>, <real address>`.

    The proxy appends the address it saw, so the truth is on the right. Reading
    the leftmost hop — the obvious implementation — lets a caller become
    127.0.0.1 and walk straight through the loopback check.
    """
    _configure(monkeypatch, proxies="127.0.0.1")
    assert _call("127.0.0.1", "127.0.0.1, 203.0.113.9") == "203.0.113.9"
    assert _call("127.0.0.1", "1.2.3.4, 203.0.113.9") == "203.0.113.9"


def test_a_chain_of_proxies_skips_every_trusted_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, proxies="127.0.0.1,10.0.0.0/8")
    assert _call("127.0.0.1", "203.0.113.9, 10.0.0.5") == "203.0.113.9"
    assert _call("10.0.0.5", "203.0.113.9") == "203.0.113.9"


def test_an_empty_header_falls_back_to_the_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, proxies="127.0.0.1")
    assert _call("127.0.0.1", "") == "127.0.0.1"
    assert _call("127.0.0.1") == "127.0.0.1"


def test_a_garbage_cidr_is_ignored_rather_than_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, proxies="127.0.0.1, not-an-ip ,10.0.0.0/8")
    nets = [str(n) for n in security.trusted_proxies()]
    assert nets == ["127.0.0.1/32", "10.0.0.0/8"]


# --------------------------------------------------------------------------- #
# 3. the configuration that has no safe meaning refuses to run
# --------------------------------------------------------------------------- #
def test_proxy_without_a_token_is_a_startup_emergency(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, proxies="127.0.0.1")
    problem = security.startup_problem()
    assert "NBX_API_TOKEN" in problem and "NBX_TRUSTED_PROXIES" in problem


def test_proxy_with_a_token_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, proxies="127.0.0.1", token="s3cret")
    assert security.startup_problem() == ""


def test_a_token_without_a_proxy_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, token="s3cret")
    assert security.startup_problem() == ""


def test_plain_development_has_no_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    assert security.startup_problem() == ""


# --------------------------------------------------------------------------- #
# The dependency itself, end to end
# --------------------------------------------------------------------------- #
def test_a_remote_caller_through_a_local_proxy_is_refused_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The original bug, as an assertion: this used to return 200."""
    with pytest.raises(HTTPException) as caught:
        _post("127.0.0.1", "203.0.113.9")
    assert caught.value.status_code == 403
    assert "NBX_API_TOKEN" in caught.value.detail


def test_a_genuine_local_caller_still_works_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    _post("127.0.0.1")                   # no exception = allowed


def test_with_a_token_the_header_is_still_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, token="s3cret")
    with pytest.raises(HTTPException) as caught:
        _post("127.0.0.1", "203.0.113.9")
    assert caught.value.status_code == 401


def test_with_the_right_token_the_mutation_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, token="s3cret")
    _post("127.0.0.1", "203.0.113.9", token="s3cret")     # no exception = allowed
