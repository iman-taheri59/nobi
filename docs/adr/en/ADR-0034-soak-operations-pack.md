# ADR-0034 — Soak operations pack: complete backup and daily snapshot

[فارسی](../fa/ADR-0034-بسته-عملیاتی-سواک.md) · **English**

**Status:** Accepted — B6 implemented in 1.2.7
**Date:** 2026-10-25
**Related:** ADR-0027, ADR-0033

## Context

An evidence gate is insufficient without durable operations. `live_execution.db` was not backed up, and daily evidence observation depended on manual curl. The five-day soak must survive restart/restore and produce an auditable, secret-free CSV.

## Decision and requirements

1. Backup format 2 contains journal, auth and live execution; `.env` and credentials remain excluded.
2. Restore accepts legacy format 1 and leaves an existing live database untouched in that case.
3. Every database passes checksum and `PRAGMA integrity_check` before replacement.
4. The snapshot tool performs GET only against health, live/status and live/evidence and reads credentials only from the environment. The live routes are operator-session routes (they refuse a bare `NBX_API_TOKEN`), so an unattended run authenticates with `NBX_SNAPSHOT_USERNAME` / `NBX_SNAPSHOT_PASSWORD` (Operator or Owner), uses that one session for the reads, and logs out; a Viewer account is refused with 403 and writes nothing. *(Amended 2026-11-04: the original token-only reading was never accepted by the live routes, which the mock-only test had not revealed.)*
5. CSV contains aggregate data only: version, mode, samples/days/ambiguity, state counts and readiness, plus (since 2.5) the `scan_loop` run count and the alert scheduler's enabled rules, last tick outcome and `sent/failed/pending` split — the ADR-0024 facts a soak needs without a browser. Servers that predate a column leave it blank.
6. Failed or malformed responses append no row.

## Acceptance

- round-trip restores all three databases;
- a tampered archive is rejected before destinations change;
- format 1 restores without changing the live destination;
- a successful snapshot writes one header and row; failure changes no file;
- token, username and password are absent from CSV, stdout and exceptions;
- the login path is exercised against the real session contract: no credentials and a Viewer both exit 1 without a row, an Operator appends exactly one row and leaves no session in `auth.db`.
