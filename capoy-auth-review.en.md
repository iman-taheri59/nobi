# Authentication, roles, and CSRF review — nobiTrader

[فارسی](capoy-auth-review.md)

Date: 2026-11-02<br>
Status: **completed in an isolated local environment**; no behind-login review of `capoy.ir` had been performed at that point.

> English version of the Persian report `capoy-auth-review.md`.

## Executive summary

The project’s authentication and authorization layer showed **no critical weakness or role bypass** in local review.

- Custom authentication review: **83 of 83 checks passed**
- Official auth/setup/install/live-policy related tests: **92 tests passed**
- Anonymous, viewer, operator, owner, invite, live-policy, session, machine-token, cookie, CORS, and login rate-limit paths were reviewed.
- No real order, real exchange key, or change to the online site was made.
- No tracked repository file was modified at the time; `git status` was clean.

Key result: **the main issue at this stage was the lack of safe test access to the online site, not discovery of an authentication breach in the code.**

---

## Review method

Everything ran in an isolated environment:

- FastAPI `TestClient` against the same repository application
- Temporary SQLite databases for auth/journal/live
- Separate paper state and cache
- `NBX_LIVE_MODE=disabled`
- No connection to the real exchange
- No scan loop or tick loop running
- Temporary `owner`, `operator`, and `viewer` accounts only in the temporary database

Review script:

```text
/home/user/nobi-auth-review/auth_review.py
```

Re-run:

```bash
/home/user/nobi/.venv/bin/python /home/user/nobi-auth-review/auth_review.py
```

Full output:

```text
/home/user/nobi-auth-review/auth-review.log
/home/user/nobi-auth-review/results.json
```

Related official tests:

```bash
.venv/bin/pytest \
  server/tests/test_auth.py \
  server/tests/test_auth_api.py \
  server/tests/test_client_identity.py \
  server/tests/test_install_api.py \
  server/tests/test_live_policy_api.py \
  server/tests/test_setup_api.py \
  -q
```

Result: `92 passed`.

---

## Successfully verified behavior

### 1. Anonymous level

- `/api/health` is public.
- `/api/auth/me` returns `auth_required=true` and `user=null` for anonymous users.
- `/api/setup/status` returns `setup_required=false` after owner creation.
- Attempting to claim setup again after an owner exists is rejected.
- Protected paths such as these return `401` without login:
  - `/api/settings`
  - `/api/live/status`
  - `/api/paper/metrics`
  - `/api/risk/state`
  - `/openapi.json`
  - `/docs`
- Mutations without login are rejected.
- Registration with an invalid invite code is rejected.

### 2. Viewer role

- Login succeeds, but `can_mutate=false`.
- Can read public account-readable sections.
- Cannot halt/resume paper trading.
- Cannot access live status or place live orders.
- Cannot access owner sections:
  - `/api/auth/users`
  - `/api/install/settings`
  - `/api/devsuite`
- Changing their own personal settings is possible only with valid CSRF.

### 3. Operator role

- `can_mutate=true`, but `is_owner=false`.
- Mutations without CSRF are rejected.
- Wrong CSRF is rejected.
- CSRF belonging to another session is rejected.
- With valid CSRF, can perform paper operations such as halt/resume.
- Cannot change user roles.
- Cannot create invites.
- Cannot read install settings or owner sections.
- Cannot enable their own live permission.
- When `NBX_LIVE_MODE=disabled`, live order placement is blocked before execution.

### 4. Owner role

- Can access users, install settings, and devsuite.
- Install mutations without CSRF are rejected.
- Unknown install settings are rejected.
- The last owner cannot be demoted.
- The last owner cannot be disabled.
- Can create invites.
- Can enable/disable operator live policy.
- Can close user sessions.
- Can disable/enable accounts.

### 5. Invites and registration

- Invite codes are single-use.
- Account role comes from the invite, not the registration body.
- Attempting privilege escalation by sending `role=owner` during registration had no effect.
- Reusing an invite code was rejected.

### 6. Sessions and passwords

- Password change with valid CSRF succeeded.
- After password change, the previous session was invalidated.
- The old password was rejected.
- The new password was accepted.
- `logout-all` closed other sessions for the same user.
- Owner could close a user’s sessions from management.
- Disabled accounts cannot log in.
- Logout cleared the session.

### 7. Cookies and transport

- Session cookie is `HttpOnly`.
- Session cookie is `SameSite=Lax`.
- CSRF cookie is readable by JS, i.e. not `HttpOnly`; this is intentional for the client-side CSRF pattern.
- On HTTPS, the session cookie received `Secure`.
- When a trusted proxy sends `X-Forwarded-Proto: https`, `Secure` is enabled.
- A forged `X-Forwarded-Proto: http` header from an untrusted peer cannot remove `Secure` on HTTPS.
- A forged `https` header only makes the cookie stricter, not weaker.

### 8. Machine token

- A valid `NBX_API_TOKEN` can perform some mutations without session/CSRF; this is intentional machine behavior.
- The machine token is not considered owner.
- With the machine token, users or install settings cannot be read.
- A wrong token was rejected.

### 9. CORS and rate limiting

- Arbitrary origins were not accepted in preflight.
- Repeated failed login attempts eventually received `429`.

---

## Hardening suggestions / improvements

These are not critical breaches, but they are better for public deployment:

### 1. Authenticated JSON responses lack explicit `Cache-Control: no-store`

In the code, explicit no-store was mostly seen for `/api/health` and `/api/risk/plan`. For a financial dashboard, all `/api/*` responses served with a user session should explicitly carry:

```http
Cache-Control: private, no-store, max-age=0
```

This reduces the risk of account data being unintentionally cached in browsers, proxies, or CDNs.

Suggested severity: **low to medium**

### 2. Cookie names could migrate to `__Host-` on HTTPS

The current session cookie is host-only and receives `Secure` on HTTPS, but its name is `nbx_session`. Names such as:

```text
__Host-nbx_session
__Host-nbx_csrf
```

in an HTTPS deployment, conditional on `Secure`, `Path=/`, and no `Domain`, can be more resistant to cookie tossing from subdomains.

Suggested severity: **low**

### 3. Logout does not explicitly check CSRF

`POST /api/auth/logout` clears the session but does not explicitly require CSRF. `SameSite=Lax` provides good protection against cross-site POST, but adding CSRF to logout is an extra defense layer.

Suggested severity: **low**

### 4. The machine token is very powerful

`NBX_API_TOKEN` bypasses session and CSRF for mutations under `require_client`. This is intentional for scripts, but if leaked it can change paper/risk state. Recommendations:

- Store in a secret manager or a file with strict permissions.
- Rotate periodically.
- Do not use it in the browser.
- If possible, use a separate read-only token for monitoring.

Suggested severity: **informational / deployment**

### 5. Rate limiting is in-process

Rate limits are kept in the memory of the same process. If the deployment has multiple workers or instances, login limiting should also be enforced at the edge/nginx or in a shared store.

Also, if `NBX_TRUSTED_PROXIES` is configured, the proxy must sanitize/rewrite internet-facing `X-Forwarded-For` and `X-Forwarded-Proto` headers; otherwise a user can send a forged IP.

Suggested severity: **medium for multi-worker/multi-instance deployments**

### 6. Public health and version-check surface

This was also noted in the earlier online review: `/api/health` and `/version-check.html` publicly show version/status details. For a financial tool, the public surface should be minimal and details should move behind login.

Suggested severity: **low to medium**

---

## Items not considered security problems

- A disabled account receives `403` after a correct password, not `401`. This only reveals that the account is disabled after the correct password is already known.
- Live order placement when live mode is off is blocked with `409` before full body validation. This is fail-closed.
- Re-claiming setup with a valid body returns `409`; an invalid body may receive `422` earlier. This is not a setup bypass.
- A forged `X-Forwarded-Proto: https` from an untrusted peer only makes the cookie `Secure`; it cannot reduce security.

---

## Online capoy.ir status

The earlier online review was **public and unauthenticated only**. At this stage, no behind-login review of `capoy.ir` had been performed.

Current tooling limitation:

- The available tools could not send a login POST to `capoy.ir`.
- Therefore, even if username/password were provided, direct login from this environment was not possible.
- The safer path is for the site owner to run a read-only script with a temporary viewer account and send the JSON output.

Suggested script:

```text
/home/user/nobi-auth-review/capoy_viewer_probe.py
```

Run on your own machine:

```bash
export NBX_USERNAME='temporary-viewer'
export NBX_PASSWORD='the-temporary-password'
python3 capoy_viewer_probe.py > capoy-viewer-probe.json
```

Then send only `capoy-viewer-probe.json`. **Do not send the password.**

This script:

- Performs only login, several GETs, header checks, and logout.
- Places no orders.
- Changes no settings.
- Does not halt/resume.
- Does not print cookies or passwords.

After review, disable or delete the temporary viewer account.

---

## Conclusion

The auth/role/CSRF/session layer in the repository was assessed as **healthy and fail-closed** in an isolated environment. Findings at this stage are mostly hardening recommendations for public deployment, not immediate breaches.

To complete the behind-login review of `capoy.ir`, output from the read-only script above with a temporary viewer account is needed.
