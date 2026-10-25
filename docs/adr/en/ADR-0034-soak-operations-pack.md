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
4. The snapshot tool performs GET only against health and live/evidence and reads a token only from the environment.
5. CSV contains aggregate data only: version, mode, samples/days/ambiguity, state counts and readiness.
6. Failed or malformed responses append no row.

## Acceptance

- round-trip restores all three databases;
- a tampered archive is rejected before destinations change;
- format 1 restores without changing the live destination;
- a successful snapshot writes one header and row; failure changes no file;
- token is absent from CSV, stdout and exceptions.
