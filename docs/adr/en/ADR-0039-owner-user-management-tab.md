# ADR-0039 — Owner-only user management and activity tab

[فارسی](../fa/ADR-0039-تب-مدیریت-کاربران.md) · **English**

**Status:** Accepted — implemented in 1.4.1
**Date:** 2026-10-25

## Decision

- A standalone Users tab appears in navigation only after an owner session is confirmed. UI hiding is not a security control; the API independently enforces the role.
- Users, roles, active state, last login, sessions and invites come from real APIs. Owners can change roles/state, revoke sessions, and create/revoke invites.
- Bearer API tokens can no longer access user administration; an owner session is mandatory. Every mutation requires valid CSRF.
- An append-only `user_activity` table records login/logout/register, user changes, session revocation and invite creation without passwords, tokens or invite codes.
- Owner-only `GET /api/auth/activity` caps limit at 500 and supports optional user_id filtering. The tab renders the newest 200 events.

## Security consequence

Viewers/operators receive 403 even after DOM manipulation or direct API calls; anonymous and bearer-token calls receive 401. Session secrets, password hashes and invite codes never enter activity or UI responses.
