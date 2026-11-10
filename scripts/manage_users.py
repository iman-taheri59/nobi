#!/usr/bin/env python3
"""Create and manage the accounts of this installation.

The first owner cannot come from the web: on a fresh install anybody who found
the URL first would become the owner. It comes from here — a shell on the box,
which is already the highest privilege available.

    python scripts/manage_users.py create iman --role owner
    python scripts/manage_users.py invite viewer --note "for Sara"
    python scripts/manage_users.py list
    python scripts/manage_users.py role sara operator
    python scripts/manage_users.py disable sara
    python scripts/manage_users.py passwd iman
    python scripts/manage_users.py sessions

Passwords are read from the terminal (or `NBX_NEW_PASSWORD` for automation) and
never appear in the shell history. Invite codes are printed once, to stdout;
the database keeps only their hash, so a code that is lost is a code that is
replaced.

Run it against the same database the service uses:

    sudo -u nobi NBX_AUTH_DB=/srv/nobi-data/auth.db \\
        /opt/nobi/.venv/bin/python /opt/nobi/scripts/manage_users.py list

`NBX_AUTH_DB` is read from the environment exactly like the server does, and
`server/.env` is loaded first (same as the app), so the default path matches.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

#: Persian and em dashes on a console that is not UTF-8 (Windows cp1252, POSIX C):
#: printing them raised `UnicodeEncodeError` and the script "failed" for a reason
#: that had nothing to do with what it was checking. Reconfigure, do not escape.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import _env  # noqa: F401 — loads server/.env before auth reads the path
import auth


def _password(flag: str | None, username: str) -> str:
    """--password, else $NBX_NEW_PASSWORD, else a hidden prompt (twice)."""
    if flag:
        return flag
    env = os.environ.get("NBX_NEW_PASSWORD", "")
    if env:
        return env
    first = getpass.getpass(f"رمز برای {username}: ")
    again = getpass.getpass("تکرار رمز: ")
    if first != again:
        raise SystemExit("رمزها یکسان نبودند")
    return first


def cmd_create(args) -> int:
    password = _password(args.password, args.username)
    try:
        user = auth.create_user(args.username, password, role=args.role,
                                created_by="cli")
    except auth.AuthError as e:
        print(f"خطا: {e.message}", file=sys.stderr)
        return 1
    print(f"ساخته شد: {user.username} ({user.role}) — id={user.id}")
    if auth.count_owners() == 1 and user.role == "owner":
        print("این نخستین حساب است؛ از این به بعد API بدون ورود بسته است.")
    return 0


def cmd_invite(args) -> int:
    code = auth.create_invite(args.role, created_by="cli", note=args.note,
                              ttl_hours=args.hours)
    print(f"کد دعوت ({args.role}، {args.hours} ساعت):\n\n    {code}\n")
    print("این کد فقط همین حالا دیده می‌شود. کاربر آن را در صفحهٔ ثبت‌نام وارد می‌کند.")
    return 0


def cmd_list(_args) -> int:
    users = auth.list_users()
    if not users:
        print("هیچ حسابی وجود ندارد — با create --role owner یکی بسازید.")
        return 0
    print(f"{'id':>3}  {'username':<20} {'role':<9} {'active':<7} last login")
    for u in users:
        last = "-" if not u.last_login else __import__("time").strftime(
            "%Y-%m-%d %H:%M", __import__("time").localtime(u.last_login))
        print(f"{u.id:>3}  {u.username:<20} {u.role:<9} {'yes' if u.active else 'NO':<7} {last}")
    print(f"\nنشست‌های فعال: {auth.active_sessions()}")
    return 0


def _target(username: str) -> auth.User:
    user = auth.get_user_by_name(username)
    if user is None:
        raise SystemExit(f"کاربر پیدا نشد: {username}")
    return user


def cmd_role(args) -> int:
    user = auth.set_role(_target(args.username).id, args.role, actor="cli")
    print(f"{user.username} اکنون {user.role} است")
    return 0


def cmd_active(args, active: bool) -> int:
    user = auth.set_active(_target(args.username).id, active, actor="cli")
    verb = "فعال" if user.active else "غیرفعال"
    print(f"{user.username} {verb} شد")
    return 0


def cmd_passwd(args) -> int:
    user = _target(args.username)
    password = _password(args.password, user.username)
    auth.check_password_quality(password, user.username)
    auth.change_password_as_admin(user.id, password)
    print(f"رمز {user.username} عوض شد؛ همهٔ نشست‌هایش بسته شد")
    return 0


def cmd_sessions(args) -> int:
    user = _target(args.username)
    closed = auth.delete_user_sessions(user.id)
    print(f"{closed} نشست برای {user.username} بسته شد")
    return 0


def cmd_invites(_args) -> int:
    rows = auth.list_invites()
    if not rows:
        print("دعوتی ثبت نشده است.")
        return 0
    for r in rows:
        print(f"#{r['id']:<3} {r['role']:<9} {r['state']:<8} "
              f"{r['note'] or '-'}  by {r['created_by']}  used_by {r['used_by'] or '-'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create", help="create an account")
    p.add_argument("username")
    p.add_argument("--role", choices=auth.ROLES, default="viewer")
    p.add_argument("--password", help="avoid: prefer the prompt or $NBX_NEW_PASSWORD")
    p.set_defaults(fn=cmd_create)

    p = sub.add_parser("invite", help="create a single-use invite code")
    p.add_argument("role", nargs="?", choices=auth.ROLES, default="viewer")
    p.add_argument("--note", default="")
    p.add_argument("--hours", type=int, default=72)
    p.set_defaults(fn=cmd_invite)

    sub.add_parser("list", help="list accounts").set_defaults(fn=cmd_list)
    sub.add_parser("invites", help="list invite codes (metadata only)").set_defaults(fn=cmd_invites)

    p = sub.add_parser("role", help="change someone's role")
    p.add_argument("username")
    p.add_argument("role", choices=auth.ROLES)
    p.set_defaults(fn=cmd_role)

    for name, active in (("disable", False), ("enable", True)):
        p = sub.add_parser(name, help=f"{name} an account")
        p.add_argument("username")
        p.set_defaults(fn=lambda a, active=active: cmd_active(a, active))

    p = sub.add_parser("passwd", help="set someone's password (signs them out)")
    p.add_argument("username")
    p.add_argument("--password", help="avoid: prefer the prompt")
    p.set_defaults(fn=cmd_passwd)

    p = sub.add_parser("sessions", help="close every session of an account")
    p.add_argument("username")
    p.set_defaults(fn=cmd_sessions)

    args = ap.parse_args()
    if not 3 <= len(getattr(args, "username", "") or "") <= 32 and args.cmd in {
            "create", "role", "disable", "enable", "passwd", "sessions"}:
        print("خطا: نام کاربری باید بین ۳ و ۳۲ کاراکتر باشد", file=sys.stderr)
        return 1
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
