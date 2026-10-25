# ADR-0043 — My Account dashboard for every role

[فارسی](../fa/ADR-0043-داشبورد-حساب-کاربری.md) · **English**

**Status:** Accepted — implemented in 1.5.1
**Date:** 2026-10-25

## Decision

- A My Account tab appears for human sessions across viewer/operator/owner; machine tokens have no personal dashboard.
- `GET /api/auth/me` returns profile plus backend-computed effective capabilities, today's live policy/usage and preferences. UI never infers authorization from role names.
- Users see only their own activity and sessions. Activity accepts no user_id input; session identifiers are hashed twice and exposed only as public IDs.
- Session revocation, logout-all, preference changes and password changes require CSRF. Revoking the current session or changing password forces reload/login.
- Preferences are allowlisted: language, timezone, theme and default page. Username and role remain read-only.
- Operator live policy is read-only in My Account and shows daily usage/capacity; policy mutation remains exclusively in the owner's Users tab.

## Security and privacy

No token, session hash, password hash or invite code reaches UI. User-Agent and IP are shown only to the same user for their own sessions. Owner-wide management remains in Users; My Account is not a privilege-change path.
