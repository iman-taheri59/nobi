"""Accounts: hashing, throttling, sessions, invites — the storage layer alone.

The API layer is tested in `test_auth_api.py`; this file answers "does the
crypto and the bookkeeping actually hold" without HTTP in the way, because that
is where a mistake is silent.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

import auth


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Every test gets its own database — and its own throttle counters."""
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    yield
    auth.reset_caches()


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def test_a_hash_verifies_and_a_wrong_password_does_not() -> None:
    stored = auth.hash_password("correct horse battery")
    assert stored.startswith("scrypt$")
    assert auth.verify_password("correct horse battery", stored)
    assert not auth.verify_password("correct horse batterY", stored)
    assert not auth.verify_password("", stored)


def test_the_salt_is_per_password() -> None:
    a = auth.hash_password("correct horse battery")
    b = auth.hash_password("correct horse battery")
    assert a != b, "identical passwords produced identical hashes: no per-password salt"
    assert auth.verify_password("correct horse battery", a)
    assert auth.verify_password("correct horse battery", b)


def test_the_hash_string_carries_its_parameters() -> None:
    """So cost can be raised later without a migration of the column."""
    scheme, n, r, p, salt, digest = auth.hash_password("correct horse battery").split("$")
    assert scheme == "scrypt"
    assert (n, r, p) == ("16384", "8", "1")
    assert salt and digest


def test_a_corrupted_hash_is_a_failed_login_not_a_crash() -> None:
    for bad in ("", "plaintext", "scrypt$1$2", "bcrypt$whatever", "scrypt$x$y$z$a$b"):
        assert auth.verify_password("whatever", bad) is False


@pytest.mark.parametrize("password", [
    "short", "elevenchars", "123456789012", "password", "qwertyuiop12",
])
def test_weak_passwords_are_refused_with_a_reason(password: str) -> None:
    with pytest.raises(auth.AuthError) as caught:
        auth.check_password_quality(password, "iman")
    assert caught.value.message


def test_the_password_may_not_contain_the_username() -> None:
    with pytest.raises(auth.AuthError):
        auth.check_password_quality("iman-is-my-password", "iman")


def test_a_long_passphrase_is_accepted_without_composition_rules() -> None:
    auth.check_password_quality("چهار کلمه ساده و طولانی", "iman")   # no exception


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
def test_a_username_is_normalised_and_unique_case_insensitively() -> None:
    user = auth.create_user("Iman", "a long enough password", role="owner")
    assert user.username == "Iman"
    with pytest.raises(auth.AuthError) as caught:
        auth.create_user("iman", "a long enough password")
    assert caught.value.status == 409


@pytest.mark.parametrize("name", ["ab", "with space", "-leading", "a" * 33, "semi;colon"])
def test_bad_usernames_are_refused(name: str) -> None:
    with pytest.raises(auth.AuthError):
        auth.create_user(name, "a long enough password")


def test_an_unknown_user_costs_the_same_as_a_wrong_password() -> None:
    """Same message *and* a real scrypt comparison, so timing does not leak."""
    auth.create_user("iman", "a long enough password")
    with pytest.raises(auth.AuthError) as unknown:
        auth.authenticate("nobody", "a long enough password")
    with pytest.raises(auth.AuthError) as wrong:
        auth.authenticate("iman", "the wrong password")
    assert unknown.value.message == wrong.value.message == "نام کاربری یا رمز عبور درست نیست"
    assert unknown.value.status == wrong.value.status == 401


def test_an_inactive_account_cannot_sign_in() -> None:
    user = auth.create_user("iman", "a long enough password", role="operator")
    auth.set_active(user.id, False, actor="test")
    with pytest.raises(auth.AuthError) as caught:
        auth.authenticate("iman", "a long enough password")
    assert caught.value.status == 403


def test_ten_wrong_guesses_lock_the_pair_for_the_window() -> None:
    auth.create_user("iman", "a long enough password")
    for _ in range(auth.LOGIN_ATTEMPTS):
        with pytest.raises(auth.AuthError) as caught:
            auth.authenticate("iman", "wrong", ip="203.0.113.9")
        assert caught.value.status == 401
    with pytest.raises(auth.AuthError) as locked:
        auth.authenticate("iman", "a long enough password", ip="203.0.113.9")
    assert locked.value.status == 429
    # A different address is not punished for someone else's guesses.
    assert auth.authenticate("iman", "a long enough password", ip="198.51.100.7").id


def test_a_good_login_clears_the_failure_counter() -> None:
    user = auth.create_user("iman", "a long enough password")
    for _ in range(auth.LOGIN_ATTEMPTS - 1):
        with pytest.raises(auth.AuthError):
            auth.authenticate("iman", "wrong", ip="10.0.0.1")
    assert auth.authenticate("iman", "a long enough password", ip="10.0.0.1").id == user.id
    assert auth.attempts_left("10.0.0.1", "iman") == auth.LOGIN_ATTEMPTS


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def test_a_session_resolves_to_its_user() -> None:
    user = auth.create_user("iman", "a long enough password", role="operator")
    token, csrf, expires = auth.create_session(user, ip="127.0.0.1", ua="pytest")
    session = auth.session_from_token(token)
    assert session is not None
    assert session.user.id == user.id
    # What comes back is the hash, not the token the browser holds.
    assert session.csrf_hash != csrf
    assert len(session.csrf_hash) == 64
    assert expires > time.time()


def test_the_database_never_stores_the_token_itself() -> None:
    """A leaked auth.db must not be a set of working cookies."""
    user = auth.create_user("iman", "a long enough password")
    token, csrf, _ = auth.create_session(user)
    with sqlite3.connect(str(auth.AUTH_DB)) as conn:
        blob = " ".join(str(v) for row in conn.execute("SELECT * FROM sessions") for v in row)
    assert token not in blob
    assert csrf not in blob          # the CSRF secret is per-session too
    assert token not in (auth.AUTH_DB.read_text("utf-8", errors="replace"))


def test_garbage_tokens_are_just_unknown() -> None:
    for token in ("", "x", "a" * 5000, "../../etc/passwd"):
        assert auth.session_from_token(token) is None


def test_an_expired_session_is_refused_and_removed() -> None:
    user = auth.create_user("iman", "a long enough password")
    token, _, _ = auth.create_session(user)
    with sqlite3.connect(str(auth.AUTH_DB)) as conn:
        conn.execute("UPDATE sessions SET expires_at = ?", (int(time.time()) - 1,))
        conn.commit()
    assert auth.session_from_token(token) is None
    with sqlite3.connect(str(auth.AUTH_DB)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_deactivating_an_account_kills_its_sessions() -> None:
    user = auth.create_user("iman", "a long enough password")
    token, _, _ = auth.create_session(user)
    auth.set_active(user.id, False, actor="test")
    assert auth.session_from_token(token) is None


def test_logout_removes_only_that_session() -> None:
    user = auth.create_user("iman", "a long enough password")
    a, _, _ = auth.create_session(user)
    b, _, _ = auth.create_session(user)
    auth.delete_session(a)
    assert auth.session_from_token(a) is None
    assert auth.session_from_token(b) is not None


def test_changing_the_password_signs_every_device_out() -> None:
    user = auth.create_user("iman", "a long enough password")
    token, _, _ = auth.create_session(user)
    auth.change_password(user.id, "a long enough password", "another long password")
    assert auth.session_from_token(token) is None
    assert auth.authenticate("iman", "another long password").id == user.id


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #
def test_the_last_owner_cannot_be_demoted_or_disabled() -> None:
    owner = auth.create_user("iman", "a long enough password", role="owner")
    with pytest.raises(auth.AuthError):
        auth.set_role(owner.id, "viewer", actor="test")
    with pytest.raises(auth.AuthError):
        auth.set_active(owner.id, False, actor="test")
    auth.create_user("second", "a long enough password", role="owner")
    assert auth.set_role(owner.id, "viewer", actor="test").role == "viewer"


def test_roles_decide_whether_an_account_can_change_anything() -> None:
    owner = auth.create_user("owner1", "a long enough password", role="owner")
    operator = auth.create_user("oper1", "a long enough password", role="operator")
    viewer = auth.create_user("viewer1", "a long enough password", role="viewer")
    assert owner.can_mutate and owner.is_owner
    assert operator.can_mutate and not operator.is_owner
    assert not viewer.can_mutate


# --------------------------------------------------------------------------- #
# Invites
# --------------------------------------------------------------------------- #
def test_an_invite_creates_an_account_with_the_invited_role() -> None:
    code = auth.create_invite("operator", created_by="iman")
    user = auth.redeem_invite(code, "newbie", "a long enough password")
    assert user.role == "operator"
    stored = auth.get_user_by_name("newbie")
    assert stored is not None and stored.id == user.id


def test_an_invite_is_single_use() -> None:
    code = auth.create_invite("viewer", created_by="iman")
    auth.redeem_invite(code, "first", "a long enough password")
    with pytest.raises(auth.AuthError) as caught:
        auth.redeem_invite(code, "second", "a long enough password")
    assert caught.value.status == 409


def test_an_unknown_or_expired_invite_is_refused() -> None:
    with pytest.raises(auth.AuthError):
        auth.redeem_invite("not-a-code", "x", "a long enough password")
    code = auth.create_invite("viewer", created_by="iman")
    with sqlite3.connect(str(auth.AUTH_DB)) as conn:
        conn.execute("UPDATE invites SET expires_at = ?", (int(time.time()) - 1,))
        conn.commit()
    with pytest.raises(auth.AuthError) as caught:
        auth.redeem_invite(code, "late", "a long enough password")
    assert caught.value.status == 409


def test_the_plaintext_code_is_not_stored() -> None:
    code = auth.create_invite("viewer", created_by="iman")
    raw = auth.AUTH_DB.read_text("utf-8", errors="replace")
    assert code not in raw
    assert code not in str(auth.list_invites())


def test_a_failed_redeem_does_not_burn_the_invite() -> None:
    """A taken username must not consume the code — that would be a griefing tool."""
    auth.create_user("taken", "a long enough password")
    code = auth.create_invite("viewer", created_by="iman")
    with pytest.raises(auth.AuthError):
        auth.redeem_invite(code, "taken", "a long enough password")
    assert auth.redeem_invite(code, "free", "a long enough password").username == "free"


def test_revoking_an_open_invite_works_and_a_used_one_is_kept() -> None:
    open_code = auth.create_invite("viewer", created_by="iman")
    used_code = auth.create_invite("viewer", created_by="iman")
    auth.redeem_invite(used_code, "someone", "a long enough password")
    invites = {i["state"]: i["id"] for i in auth.list_invites()}
    open_id, used_id = invites["open"], invites["used"]
    assert auth.revoke_invite(open_id) is True
    assert auth.revoke_invite(used_id) is False
    with pytest.raises(auth.AuthError):
        auth.redeem_invite(open_code, "anyone", "a long enough password")


def test_invite_states_are_reported() -> None:
    auth.create_invite("viewer", created_by="iman")
    states = [i["state"] for i in auth.list_invites()]
    assert states == ["open"]
