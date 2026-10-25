"""`/api/auth/*` — signing in, signing up with an invite, and managing people.

Two rules shape this file:

1. **Nothing here trusts the client.** The role of a new account comes from the
   invite row, never from the request body. A viewer cannot promote themselves.
2. **Failures are specific enough to act on, and vague enough not to help an
   attacker.** "نام کاربری یا رمز عبور درست نیست" for both halves; a distinct
   message only for an invite that is used/expired (that code is already in the
   caller's hand).

`/api/auth/me` is deliberately a 200 even when nobody is signed in: the page
asks "who am I?" on boot and uses `auth_required` to decide between the login
screen and the dashboard. A 401 there would be logged as an error by every
proxy in the world.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

import auth
import identity
import security

router = APIRouter(prefix="/api/auth", tags=["auth"])


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #
class LoginBody(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=200)


class RegisterBody(BaseModel):
    code: str = Field(max_length=128)
    username: str = Field(max_length=64)
    password: str = Field(max_length=200)


class PasswordBody(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


class RoleBody(BaseModel):
    role: str = Field(max_length=16)


class ActiveBody(BaseModel):
    active: bool


class PreferencesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str = Field(pattern="^(fa|en)$")
    timezone: str = Field(max_length=64)
    theme: str = Field(pattern="^(dark|system)$")
    default_page: str = Field(pattern="^(overview|signals|intelligence|fa|orders|performance|desk|risk|backtest)$")


class LivePolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_order_usdt: float = Field(ge=1, le=1_000_000)
    max_daily_usdt: float = Field(ge=1, le=10_000_000)
    max_daily_orders: int = Field(ge=1, le=1000)
    allowed_symbols: list[str] = Field(min_length=1, max_length=50)
    allowed_execution: list[str] = Field(default_factory=lambda: ["limit"], min_length=1, max_length=2)


class InviteBody(BaseModel):
    role: str = "viewer"
    note: str = ""
    ttl_hours: int = Field(default=72, ge=1, le=24 * 30)


# --------------------------------------------------------------------------- #
# Identity helpers used by both this router and the app-wide gate
# --------------------------------------------------------------------------- #
def _client_ip(request: Request) -> str:
    """The same address the security layer decided on — not the raw socket.

    Behind a proxy the socket is always 127.0.0.1, and throttling login by that
    would turn ten wrong guesses into a lockout for everybody.
    """
    return security.client_host(request)


def _bearer_ok(request: Request) -> bool:
    """`NBX_API_TOKEN` still works — for scripts, not for people."""
    return security.token_valid(request)


def _login_budget(request: Request) -> Response | None:
    """Refuse before hashing when one address is spending the whole CPU.

    Placed in front of `auth.authenticate` on purpose: the expensive step is the
    scrypt comparison, and a throttle that runs after it protects nothing. The
    counter is per address, not per `(address, username)`, so cycling usernames
    does not reset it (see `security.LOGIN_RATE_LIMIT`).
    """
    if security.login_rate_ok(_client_ip(request)):
        return None
    return _refuse(
        429,
        f"تلاش‌های ورود از این آدرس بیش از حد مجاز است "
        f"({security.LOGIN_RATE_LIMIT} در دقیقه)؛ یک دقیقه بعد دوباره امتحان کنید",
    )


def _capabilities(user: auth.User) -> dict[str, bool]:
    operator = user.can_mutate
    owner = user.is_owner
    policy = auth.get_live_policy(user.id) if user.role == "operator" else None
    return {
        "market_read": True, "paper_read": True, "reports_read": True,
        "signal_intelligence_read": True, "signal_intelligence_review": operator,
        "paper_trade": operator, "backtest_run": operator,
        "journal_maintain": operator, "operational_audit": operator,
        "live_view": operator, "live_cancel": operator, "live_reconcile": operator,
        "live_place": owner or bool(policy and policy.get("enabled")),
        "manage_users": owner, "manage_settings": owner,
        "troubleshooting": owner, "manage_server": owner,
    }


def _me(request: Request) -> dict:
    session = identity.session_of(request)
    if session is not None:
        user = session.user
        return {
            "auth_required": True,
            "user": user.public(),
            "method": "session",
            "can_mutate": user.can_mutate,
            "is_owner": user.is_owner,
            "live_policy": auth.get_live_policy(user.id) if user.role == "operator" else None,
            "live_usage": auth.live_usage_today(user.id),
            "capabilities": _capabilities(user),
            "preferences": auth.get_preferences(user.id),
        }
    if _bearer_ok(request):
        return {"auth_required": True, "user": None, "method": "token",
                "can_mutate": True, "is_owner": False, "token": True}
    return {"auth_required": auth.any_users(), "user": None, "method": None,
            "can_mutate": False, "is_owner": False}


# --------------------------------------------------------------------------- #
# Sign in / out
# --------------------------------------------------------------------------- #
@router.post("/login")
def login(body: LoginBody, request: Request, response: Response):
    if (denied := _login_budget(request)) is not None:
        return denied
    ip = _client_ip(request)
    try:
        user = auth.authenticate(body.username, body.password, ip=ip)
    except auth.AuthError as e:
        return Response(status_code=e.status,
                        content=_json({"detail": e.message}),
                        media_type="application/json")
    token, csrf, _expires = auth.create_session(
        user, ip=ip, ua=request.headers.get("user-agent", ""))
    identity.set_session_cookies(
        response, token=token, csrf=csrf,
        max_age=auth.SESSION_DAYS * 86400, secure=identity.is_secure(request))
    auth.record_activity(user, "auth.login", ip=ip)
    return {"ok": True, "user": user.public(), "can_mutate": user.can_mutate,
            "is_owner": user.is_owner}


@router.post("/logout")
def logout(request: Request, response: Response):
    session = identity.session_of(request)
    token = request.cookies.get(identity.SESSION_COOKIE, "")
    if session is not None:
        auth.record_activity(session.user, "auth.logout", ip=_client_ip(request))
    auth.delete_session(token)
    identity.clear_session_cookies(response)
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    return _me(request)


@router.get("/me/activity")
def my_activity(request: Request, limit: int = 100):
    session = identity.session_of(request)
    if session is None:
        return _refuse(401, "برای این بخش باید وارد شوید")
    return {"events": auth.list_activity(limit=limit, user_id=session.user.id)}


@router.get("/me/sessions")
def my_sessions(request: Request):
    session = identity.session_of(request)
    if session is None:
        return _refuse(401, "برای این بخش باید وارد شوید")
    token = request.cookies.get(identity.SESSION_COOKIE, "")
    return {"sessions": auth.list_user_sessions(session.user.id, current_token=token)}


@router.delete("/me/sessions/{session_id}")
def delete_my_session(session_id: str, request: Request, response: Response):
    session = identity.session_of(request)
    if session is None:
        return _refuse(401, "برای این بخش باید وارد شوید")
    if not identity.csrf_ok(request, session):
        return _refuse(403, "CSRF نامعتبر است")
    sessions = auth.list_user_sessions(
        session.user.id, current_token=request.cookies.get(identity.SESSION_COOKIE, ""))
    current = any(row["id"] == session_id and row["current"] for row in sessions)
    removed = auth.delete_public_session(session.user.id, session_id)
    if current and removed:
        identity.clear_session_cookies(response)
    auth.record_activity(session.user, "auth.session.revoke",
                         result="ok" if removed else "not-found", ip=_client_ip(request))
    return {"ok": removed, "reload": current and removed}


@router.post("/me/logout-all")
def logout_all(request: Request, response: Response):
    session = identity.session_of(request)
    if session is None:
        return _refuse(401, "برای این بخش باید وارد شوید")
    if not identity.csrf_ok(request, session):
        return _refuse(403, "CSRF نامعتبر است")
    user = session.user
    closed = auth.delete_user_sessions(user.id)
    auth.record_activity(user, "auth.logout-all", detail=f"closed={closed}", ip=_client_ip(request))
    identity.clear_session_cookies(response)
    return {"ok": True, "closed": closed, "reload": True}


@router.get("/me/preferences")
def my_preferences(request: Request):
    session = identity.session_of(request)
    if session is None:
        return _refuse(401, "برای این بخش باید وارد شوید")
    return {"preferences": auth.get_preferences(session.user.id)}


@router.patch("/me/preferences")
def patch_my_preferences(body: PreferencesBody, request: Request):
    session = identity.session_of(request)
    if session is None:
        return _refuse(401, "برای این بخش باید وارد شوید")
    if not identity.csrf_ok(request, session):
        return _refuse(403, "CSRF نامعتبر است")
    if "/" not in body.timezone or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-/+" for ch in body.timezone):
        return _refuse(422, "timezone معتبر نیست")
    values = auth.set_preferences(session.user.id, body.model_dump())
    return {"ok": True, "preferences": values}


# --------------------------------------------------------------------------- #
# Sign up — invite only
# --------------------------------------------------------------------------- #
@router.post("/register")
def register(body: RegisterBody, request: Request, response: Response):
    """Redeem an invite. The account's role is whatever the invite says."""
    if (denied := _login_budget(request)) is not None:
        return denied
    if not auth.any_users():
        # A fresh install with no accounts would let the first visitor create
        # one — and whoever finds the URL first would be the owner. Refuse, and
        # say how to create it instead.
        return Response(status_code=403, media_type="application/json",
                        content=_json({"detail":
                                       "این نصب هنوز حساب مالک ندارد؛ با دستور "
                                       "python scripts/manage_users.py create ... روی سرور بسازید"}))
    try:
        user = auth.redeem_invite(body.code, body.username, body.password)
    except auth.AuthError as e:
        return Response(status_code=e.status, media_type="application/json",
                        content=_json({"detail": e.message}))
    token, csrf, _ = auth.create_session(
        user, ip=_client_ip(request), ua=request.headers.get("user-agent", ""))
    identity.set_session_cookies(
        response, token=token, csrf=csrf,
        max_age=auth.SESSION_DAYS * 86400, secure=identity.is_secure(request))
    auth.record_activity(user, "auth.register", ip=_client_ip(request))
    return {"ok": True, "user": user.public(), "can_mutate": user.can_mutate,
            "is_owner": user.is_owner}


# --------------------------------------------------------------------------- #
# Own account
# --------------------------------------------------------------------------- #
@router.post("/password")
def change_own_password(body: PasswordBody, request: Request, response: Response):
    if (denied := _login_budget(request)) is not None:
        return denied
    session = identity.session_of(request)
    if session is None:
        return _refuse(401, "برای این کار باید وارد شوید")
    if not identity.csrf_ok(request, session):
        return _refuse(403, "CSRF نامعتبر است")
    try:
        auth.change_password(session.user.id, body.current, body.new)
    except auth.AuthError as e:
        return _refuse(e.status, e.message)
    auth.record_activity(session.user, "auth.password.change", ip=_client_ip(request))
    # Changing a password signs every device out — including this one.
    identity.clear_session_cookies(response)
    return {"ok": True, "reload": True}


# --------------------------------------------------------------------------- #
# Owner area
# --------------------------------------------------------------------------- #
def _owner_gate(request: Request, *, csrf: bool = False) -> Response | None:
    """401 when nobody is signed in, 403 when the wrong person is.

    The difference is what the page does with it: 401 shows the login screen,
    403 says "your role is read-only". Collapsing them made a signed-out visitor
    look like a permission problem.
    """
    session = identity.session_of(request)
    if session is None:
        return _refuse(401, "برای این بخش باید با حساب مالک وارد شوید")
    if not session.user.is_owner:
        return _refuse(403, "فقط مالک می‌تواند کاربران و دعوت‌ها را مدیریت کند")
    if csrf and not identity.csrf_ok(request, session):
        return _refuse(403, "CSRF نامعتبر است")
    return None


def _refuse(status: int, message: str) -> Response:
    return Response(status_code=status, media_type="application/json",
                    content=_json({"detail": message}))


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


@router.get("/users")
def list_users(request: Request):
    if (denied := _owner_gate(request)) is not None:
        return denied
    users = []
    for user in auth.list_users():
        item = user.public()
        item["live_policy"] = auth.get_live_policy(user.id) if user.role == "operator" else None
        users.append(item)
    return {"users": users, "sessions": auth.active_sessions()}


@router.get("/activity")
def activity(request: Request, limit: int = 100, user_id: int | None = None):
    if (denied := _owner_gate(request)) is not None:
        return denied
    return {"events": auth.list_activity(limit=limit, user_id=user_id)}


@router.get("/users/{user_id}/live-policy")
def get_user_live_policy(user_id: int, request: Request):
    if (denied := _owner_gate(request)) is not None:
        return denied
    if auth.get_user(user_id) is None:
        return _refuse(404, "کاربر پیدا نشد")
    return {"policy": auth.get_live_policy(user_id)}


@router.patch("/users/{user_id}/live-policy")
def patch_user_live_policy(user_id: int, body: LivePolicyBody, request: Request):
    if (denied := _owner_gate(request, csrf=True)) is not None:
        return denied
    symbols = [str(value).strip().upper() for value in body.allowed_symbols]
    if any(not value.isalnum() or not value.endswith(("USDT", "IRT")) for value in symbols):
        return _refuse(422, "نماد مجاز معتبر نیست")
    executions = sorted(set(body.allowed_execution))
    if not set(executions) <= {"limit"}:
        return _refuse(422, "در این مرحله فقط سفارش limit مجاز است")
    if body.max_daily_usdt < body.max_order_usdt:
        return _refuse(422, "سقف روزانه باید حداقل برابر سقف هر سفارش باشد")
    session = identity.session_of(request)
    assert session is not None
    try:
        policy = auth.set_live_policy(user_id, {
            **body.model_dump(), "allowed_symbols": symbols,
            "allowed_execution": executions,
        }, actor=session.user.username)
    except auth.AuthError as exc:
        return _refuse(exc.status, exc.message)
    auth.record_activity(session.user, "user.live-policy.update",
                         detail=f"target_id={user_id};enabled={int(body.enabled)}",
                         ip=_client_ip(request))
    return {"ok": True, "policy": policy}


@router.patch("/users/{user_id}")
def patch_user(user_id: int, request: Request, body: dict):
    if (denied := _owner_gate(request, csrf=True)) is not None:
        return denied
    session = identity.session_of(request)
    actor = session.user.username if session else "token"
    try:
        if "role" in body:
            user = auth.set_role(user_id, str(body["role"]), actor=actor)
        elif "active" in body:
            user = auth.set_active(user_id, bool(body["active"]), actor=actor)
        else:
            return _refuse(400, "role یا active لازم است")
    except auth.AuthError as e:
        return _refuse(e.status, e.message)
    auth.record_activity(session.user if session else None, "user.update",
                         detail=f"target={user.username}", ip=_client_ip(request))
    return {"ok": True, "user": user.public()}


@router.post("/users/{user_id}/logout")
def logout_user(user_id: int, request: Request):
    if (denied := _owner_gate(request, csrf=True)) is not None:
        return denied
    closed = auth.delete_user_sessions(user_id)
    session = identity.session_of(request)
    auth.record_activity(session.user if session else None, "user.sessions.revoke",
                         detail=f"target_id={user_id};closed={closed}", ip=_client_ip(request))
    return {"ok": True, "closed": closed}


@router.get("/invites")
def list_invites(request: Request):
    if (denied := _owner_gate(request)) is not None:
        return denied
    return {"invites": auth.list_invites()}


@router.post("/invites")
def create_invite(body: InviteBody, request: Request):
    if (denied := _owner_gate(request, csrf=True)) is not None:
        return denied
    session = identity.session_of(request)
    who = session.user.username if session else "token"
    try:
        code = auth.create_invite(body.role, created_by=who,
                                  note=body.note, ttl_hours=body.ttl_hours)
    except auth.AuthError as e:
        return _refuse(e.status, e.message)
    auth.record_activity(session.user if session else None, "invite.create",
                         detail=f"role={body.role}", ip=_client_ip(request))
    return {"ok": True, "code": code, "role": body.role, "ttl_hours": body.ttl_hours,
            # The code is shown exactly once; the database keeps only its hash.
            "note": "این کد فقط همین یک بار نمایش داده می‌شود"}


@router.delete("/invites/{invite_id}")
def delete_invite(invite_id: int, request: Request):
    if (denied := _owner_gate(request, csrf=True)) is not None:
        return denied
    return {"ok": auth.revoke_invite(invite_id)}


__all__ = ["router"]
