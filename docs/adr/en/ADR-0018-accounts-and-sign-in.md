# ADR-0018: Accounts, sign-in, and invite-only registration

- **Status:** Accepted and implemented
- **Date:** 2026-10-21
- **Decision owner:** technical architect
- **Related:** 0009 (security and ops), 0014 (data-honesty contract), 0017 (running the gates from the dashboard)
- **Enforced by:** `server/auth.py`, `server/identity.py`, `server/auth_api.py`,
  `scripts/manage_users.py`, `server/tests/test_auth*.py`, `scripts/check_deploy.py`

---

## 1. Context

The only credential was `NBX_API_TOKEN`: one shared string, sent as
`X-API-Token`. Two consequences showed up during deployment:

1. **The browser had nowhere to put it.** The deploy guide had to say "open the
   console and run `localStorage.setItem('nbx_api_token', '…')`". It works, and
   it makes setting up a dashboard look like cheating.
2. **Reads were open.** The token guarded mutations only; `GET
   /api/paper/positions` answered anyone with the URL. `check_deploy.py` reports
   that row as `skip`, not `ok`, because a green tick there would be a lie.

The request was "add accounts and sign-in". The scope was chosen explicitly:
**several people, one shared trading account**, **invite-only registration**,
and **the token stays for scripts**.

## 2. Decision

**One trading account, several people.** Positions, journal and the paper
balance stay shared. What is added is a person: every request carries an
identity, every change an actor.

Per-user data was deliberately *not* built. Measured reason: 61 endpoints and
singleton state (`paper_trading.STATE_FILE`, `journal.DB_PATH`). Isolating them
means threading `user_id` through all of them, re-keying positions and journal,
and keeping the (expensive) market scan shared — a full sprint with regression
risk across every trading path. The user has said that is the next phase.

*The seam for that phase exists today:* `audit_log` already has an `actor`
column, so scoping later is a filter plus a cache key, not a rewrite.

**Invite-only.** The first account is created from the CLI — on a fresh install
anybody who found the URL first would otherwise become the owner. Everyone else
redeems a single-use code, and **the role is fixed when the invite is created**,
so a viewer invite cannot become an owner by editing the request body.

**Three roles.** `owner` (manage people) > `operator` (mutate) > `viewer`
(read). The last active owner cannot be demoted or disabled.

**Sessions with stdlib crypto.** `hashlib.scrypt`, per-password salt,
self-describing hash string. The session cookie holds 32 random bytes; the table
holds `sha256` of them. `HttpOnly`, `SameSite=Lax`, `Secure` on HTTPS, sliding
14-day lifetime. CSRF is a second cookie (readable) echoed in `X-CSRF-Token`,
also stored hashed. Login is throttled per (address, username).

**The token remains.** Browser: session + CSRF. Script: `X-API-Token`, no
cookies, no CSRF. Anonymous: allowed on loopback **only while no account
exists** — the moment the first account is created, "came from 127.0.0.1" stops
being evidence.

## 3. Where the guards live

1. `require_login_for_api` middleware — "is anyone signed in?" for all `/api/*`
   except `/api/auth/*` and `/api/health`. This is what closes **reads**.
2. `security.require_client` — "may this caller mutate?" token, or a session
   with operator/owner plus a correct CSRF header.
3. `auth_api` — "is this person the owner?" for users and invites.

Removing layer 1 serves data behind a login screen; removing layer 2 lets a
viewer place orders. Both have tests.

## 4. What the work itself turned up

1. **`list_invites` iterated a `sqlite3.Row`'s values** (`for k in row`) and
   crashed with `IndexError`. An automated ruff "fix" (SIM118) caused it; it is
   now pinned with `# noqa: SIM118` and a comment.
2. **The CSRF secret was stored in plain text.** Now hashed, compared hashed.
3. **Middleware wrapped exceptions in an `ExceptionGroup`**, so the 4000-char
   error log cut off the actual cause: a 500 that said only
   `GET /api/__boom__ failed`. Fixed with `_innermost()`, and the exception type
   and message now go into `message` as well.
4. **Three backtest POSTs built their own headers** and would have 403'd under
   a session. All mutations now go through `_authHeaders()`; a test enforces it.
5. **An `auth.db` inside the checkout breaks the service:** with
   `ProtectSystem=strict` the app cannot create the file, so the first login is
   a 500. It now lives on the data volume, and `check_deploy.py` fails if it
   does not.

## 5. Accepted risks

* Login throttling is per-process, like the existing rate limiter (fine with
  `--workers 1`, and the unit pins one worker).
* No 2FA, no self-service password recovery. `manage_users.py passwd` on the
  server is the recovery path — shell access is account access, which is honest.
* Any signed-in account can read everything, including a viewer.
* No open registration. If this becomes a multi-customer product, this ADR is
  not enough.

## 6. Acceptance, each row a test

| Requirement | Evidence |
|---|---|
| Reads closed once accounts exist | `test_reads_are_closed_to_anonymous_visitors` |
| HTML still served anonymously (the login form must arrive) | `test_static_files_stay_public_so_the_login_screen_can_load` |
| `/api/health` open for monitors | `test_health_stays_open_for_monitors` |
| Mutation without CSRF refused | `test_a_session_without_the_csrf_header_is_refused` |
| Viewer reads, cannot change | `test_a_viewer_can_read_everything_and_change_nothing` |
| Token still works for scripts | `test_the_token_still_reads_and_writes_with_accounts_present` |
| Invite single-use, role-bound | `test_the_role_in_the_request_body_is_ignored` |
| Last owner protected | `test_the_last_owner_cannot_be_demoted_or_disabled` |
| No plaintext token/CSRF in the DB | `test_the_database_never_stores_the_token_itself` |
| Gate present in the served build | `test_the_gate_is_in_the_page_the_server_serves` |
