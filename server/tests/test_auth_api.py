"""The API side of accounts: the gate, CSRF, roles, and the token that stays.

Everything here goes through a real `TestClient`, because the questions are
about HTTP behaviour: does a signed-out browser get a 401 for a read, does a
mutation without the CSRF header get refused, can a viewer place an order, and
does `NBX_API_TOKEN` still work for a script with no cookies at all.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import auth
import main
import security


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """A private auth database and no shared token, per test."""
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    monkeypatch.setattr(security, "API_TOKEN", "")
    auth.reset_caches()
    security.reset_rate_limits()
    yield
    auth.reset_caches()


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def _owner(**kw) -> auth.User:
    return auth.create_user("owner1", "a long enough password", role="owner", **kw)


def _viewer() -> auth.User:
    return auth.create_user("viewer1", "a long enough password", role="viewer")


def _login(client: TestClient, username: str = "owner1",
           password: str = "a long enough password"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _csrf(client: TestClient) -> str:
    return client.cookies.get("nbx_csrf") or ""


# --------------------------------------------------------------------------- #
# Before anybody signs up: the old behaviour, unchanged
# --------------------------------------------------------------------------- #
def test_with_no_accounts_reads_stay_open_and_me_says_so(client: TestClient) -> None:
    body = client.get("/api/auth/me").json()
    assert body["auth_required"] is False
    assert body["user"] is None
    assert client.get("/api/paper/positions").status_code == 200


def test_registration_is_refused_before_an_owner_exists(client: TestClient) -> None:
    """Otherwise the first stranger to find the URL becomes the owner."""
    r = client.post("/api/auth/register",
                    json={"code": "whatever", "username": "squatter",
                          "password": "a long enough password"})
    assert r.status_code == 403
    assert "manage_users.py" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# Signing in
# --------------------------------------------------------------------------- #
def test_login_sets_a_session_and_a_csrf_cookie(client: TestClient) -> None:
    _owner()
    r = _login(client)
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "owner1"
    assert r.json()["is_owner"] is True
    assert client.cookies.get("nbx_session")
    assert client.cookies.get("nbx_csrf")


def test_the_session_cookie_is_http_only_and_lax(client: TestClient) -> None:
    _owner()
    raw = _login(client).headers["set-cookie"]
    assert "HttpOnly" in raw
    assert "SameSite=lax" in raw.lower() or "samesite=lax" in raw.lower()


def test_the_csrf_cookie_is_readable_on_purpose(client: TestClient) -> None:
    """The page has to echo it in a header; HttpOnly would make that impossible."""
    _owner()
    raw = _login(client).headers["set-cookie"]
    csrf_header = next(c for c in raw.split(",") if "nbx_csrf" in c)
    assert "HttpOnly" not in csrf_header


def test_a_wrong_password_is_a_401_with_one_message(client: TestClient) -> None:
    _owner()
    wrong = client.post("/api/auth/login", json={"username": "owner1", "password": "nope"})
    unknown = client.post("/api/auth/login", json={"username": "ghost", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_me_reports_the_signed_in_user(client: TestClient) -> None:
    _owner()
    _login(client)
    body = client.get("/api/auth/me").json()
    assert body["user"]["username"] == "owner1"
    assert body["method"] == "session"
    assert body["can_mutate"] is True


# --------------------------------------------------------------------------- #
# The gate: once accounts exist, reads need a session too
# --------------------------------------------------------------------------- #
def test_reads_are_closed_to_anonymous_visitors(client: TestClient) -> None:
    _owner()
    for path in ("/api/paper/positions", "/api/paper/positions", "/api/settings"):
        r = client.get(path)
        assert r.status_code == 401, path
        assert r.json().get("auth") == "login"


def test_health_stays_open_for_monitors(client: TestClient) -> None:
    _owner()
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["auth"]["accounts"] is True


def test_the_api_docs_are_closed_too(client: TestClient) -> None:
    """The schema is a map of everything worth poking; it follows the data."""
    _owner()
    assert client.get("/openapi.json").status_code == 401
    assert client.get("/openapi.json", headers={"X-API-Token": "x"}).status_code == 401


def test_static_files_stay_public_so_the_login_screen_can_load(client: TestClient) -> None:
    _owner()
    assert client.get("/").status_code == 200
    assert client.get("/nbx-core.js").status_code == 200


def test_logging_in_opens_the_reads(client: TestClient) -> None:
    _owner()
    _login(client)
    assert client.get("/api/paper/positions").status_code == 200


def test_logging_out_closes_them_again(client: TestClient) -> None:
    _owner()
    _login(client)
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/paper/positions").status_code == 401


# --------------------------------------------------------------------------- #
# Mutations: identity, role, CSRF
# --------------------------------------------------------------------------- #
def test_a_mutation_without_a_session_is_a_401(client: TestClient) -> None:
    _owner()
    assert client.post("/api/paper/halt").status_code == 401


def test_a_session_without_the_csrf_header_is_refused(client: TestClient) -> None:
    """The cookie alone is not enough: it is exactly what a cross-site POST has."""
    _owner()
    _login(client)
    r = client.post("/api/paper/halt")
    assert r.status_code == 403
    assert "CSRF" in r.json()["detail"]


def test_a_session_with_the_csrf_header_may_mutate(client: TestClient) -> None:
    _owner()
    _login(client)
    r = client.post("/api/paper/halt", headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 200
    assert r.json()["halted"] is True
    client.post("/api/paper/resume", headers={"X-CSRF-Token": _csrf(client)})


def test_a_wrong_csrf_header_is_refused(client: TestClient) -> None:
    _owner()
    _login(client)
    assert client.post("/api/paper/halt",
                       headers={"X-CSRF-Token": "not-the-token"}).status_code == 403


def test_a_viewer_can_read_everything_and_change_nothing(client: TestClient) -> None:
    _owner()
    _viewer()
    _login(client, "viewer1")
    assert client.get("/api/paper/positions").status_code == 200
    r = client.post("/api/paper/halt", headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 403
    assert "viewer" in r.json()["detail"]


def test_the_viewer_keeps_reading_after_being_refused(client: TestClient) -> None:
    """A refusal must not be a logout — that would look like a random bounce."""
    _owner()
    _viewer()
    _login(client, "viewer1")
    client.post("/api/paper/halt", headers={"X-CSRF-Token": _csrf(client)})
    assert client.get("/api/auth/me").json()["user"]["username"] == "viewer1"


# --------------------------------------------------------------------------- #
# The shared token: scripts do not log in
# --------------------------------------------------------------------------- #
def test_the_token_still_reads_and_writes_with_accounts_present(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "API_TOKEN", "script-token-0123456789")
    _owner()
    h = {"X-API-Token": "script-token-0123456789"}
    assert client.get("/api/paper/positions", headers=h).status_code == 200
    r = client.post("/api/paper/halt", headers=h)          # no cookies, no CSRF
    assert r.status_code == 200
    client.post("/api/paper/resume", headers=h)


def test_a_wrong_token_is_not_a_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "API_TOKEN", "script-token-0123456789")
    _owner()
    assert client.get("/api/paper/positions", headers={"X-API-Token": "x"}).status_code == 401


# --------------------------------------------------------------------------- #
# Registration by invite
# --------------------------------------------------------------------------- #
def test_an_invite_creates_a_working_viewer_account(client: TestClient) -> None:
    _owner()
    _login(client)
    code = client.post("/api/auth/invites", json={"role": "viewer"},
                       headers={"X-CSRF-Token": _csrf(client)}).json()["code"]
    client.post("/api/auth/logout")

    r = client.post("/api/auth/register",
                    json={"code": code, "username": "friend1",
                          "password": "another long password"})
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "viewer"
    # Registering signs you in.
    assert client.get("/api/auth/me").json()["user"]["username"] == "friend1"


def test_the_role_in_the_request_body_is_ignored(client: TestClient) -> None:
    """A viewer invite must not become an owner because the body says so."""
    _owner()
    _login(client)
    code = client.post("/api/auth/invites", json={"role": "viewer"},
                       headers={"X-CSRF-Token": _csrf(client)}).json()["code"]
    client.post("/api/auth/logout")
    r = client.post("/api/auth/register",
                    json={"code": code, "username": "sneaky1",
                          "password": "another long password", "role": "owner"})
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "viewer"


def test_only_an_owner_can_create_invites(client: TestClient) -> None:
    _owner()
    _viewer()
    _login(client, "viewer1")
    r = client.post("/api/auth/invites", json={"role": "viewer"},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 403


def test_an_owner_can_promote_and_demote(client: TestClient) -> None:
    _owner()
    other = _viewer()
    _login(client)
    r = client.patch(f"/api/auth/users/{other.id}",
                     json={"role": "operator"},
                     headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "operator"
    assert auth.get_user(other.id).can_mutate  # type: ignore[union-attr]


def test_the_user_list_hides_nothing_but_hashes(client: TestClient) -> None:
    _owner()
    _login(client)
    body = client.get("/api/auth/users").json()
    assert [u["username"] for u in body["users"]] == ["owner1"]
    assert "pw_hash" not in str(body)


# --------------------------------------------------------------------------- #
# Sessions and other people's reach
# --------------------------------------------------------------------------- #
def test_one_persons_cookie_does_not_work_as_anothers(client: TestClient) -> None:
    _owner()
    _viewer()
    _login(client, "owner1")
    stolen = client.cookies.get("nbx_session") or ""
    client.cookies.clear()
    _login(client, "viewer1")
    client.cookies.set("nbx_session", stolen)
    assert client.get("/api/auth/me").json()["user"]["username"] == "owner1"


def test_an_owner_can_end_someone_elses_sessions(client: TestClient) -> None:
    _owner()
    other = _viewer()
    other_client = TestClient(main.app)
    _login(other_client, "viewer1")
    assert other_client.get("/api/paper/positions").status_code == 200
    _login(client)
    r = client.post(f"/api/auth/users/{other.id}/logout",
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 200 and r.json()["closed"] == 1
    assert other_client.get("/api/paper/positions").status_code == 401


def test_changing_the_password_signs_the_other_device_out(client: TestClient) -> None:
    _owner()
    other_client = TestClient(main.app)
    _login(other_client)
    _login(client)
    r = client.post("/api/auth/password",
                    json={"current": "a long enough password",
                          "new": "a different long password"},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 200
    assert other_client.get("/api/paper/positions").status_code == 401
    assert client.get("/api/paper/positions").status_code == 401   # this one too


# --------------------------------------------------------------------------- #
# The owner's controls, as the settings panel calls them
# --------------------------------------------------------------------------- #
def test_an_owner_can_disable_and_enable_an_account(client: TestClient) -> None:
    _owner()
    other = _viewer()
    _login(client)
    h = {"X-CSRF-Token": _csrf(client)}
    r = client.patch(f"/api/auth/users/{other.id}", json={"active": False}, headers=h)
    assert r.status_code == 200 and r.json()["user"]["active"] is False
    # A disabled person is signed out at the same moment.
    assert client.post("/api/auth/login",
                       json={"username": "viewer1",
                             "password": "a long enough password"}).status_code == 403
    r = client.patch(f"/api/auth/users/{other.id}", json={"active": True}, headers=h)
    assert r.status_code == 200 and r.json()["user"]["active"] is True


def test_the_last_owner_cannot_be_disabled_over_http(client: TestClient) -> None:
    owner = _owner()
    _login(client)
    r = client.patch(f"/api/auth/users/{owner.id}", json={"active": False},
                     headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 409


def test_an_invite_can_be_revoked_before_it_is_used(client: TestClient) -> None:
    _owner()
    _login(client)
    h = {"X-CSRF-Token": _csrf(client)}
    code = client.post("/api/auth/invites", json={"role": "viewer"},
                       headers=h).json()["code"]
    invite_id = client.get("/api/auth/invites").json()["invites"][0]["id"]
    assert client.delete(f"/api/auth/invites/{invite_id}", headers=h).json()["ok"] is True
    client.post("/api/auth/logout")
    r = client.post("/api/auth/register",
                    json={"code": code, "username": "late",
                          "password": "another long password"})
    assert r.status_code == 403


def test_the_user_list_is_owner_only_and_anonymous_gets_a_login(client: TestClient) -> None:
    _owner()
    assert client.get("/api/auth/users").status_code == 401
    _login(client)
    assert client.get("/api/auth/users").status_code == 200


def test_owner_can_review_user_activity_but_operator_cannot(client: TestClient) -> None:
    _owner()
    _login(client)
    activity = client.get("/api/auth/activity")
    assert activity.status_code == 200
    assert activity.json()["events"][0]["action"] == "auth.login"
    assert activity.json()["events"][0]["username"] == "owner1"
    csrf = _csrf(client)
    client.post("/api/auth/invites", json={"role": "operator"},
                headers={"X-CSRF-Token": csrf})
    client.post("/api/auth/logout")
    auth.create_user("operator-view", "another long password", role="operator")
    client.post("/api/auth/login", json={"username": "operator-view",
                                         "password": "another long password"})
    assert client.get("/api/auth/activity").status_code == 403


def test_bearer_token_cannot_manage_users(monkeypatch, client: TestClient) -> None:
    _owner()
    monkeypatch.setattr(security, "API_TOKEN", "script-token")
    response = client.get("/api/auth/users", headers={"X-API-Token": "script-token"})
    assert response.status_code == 401
