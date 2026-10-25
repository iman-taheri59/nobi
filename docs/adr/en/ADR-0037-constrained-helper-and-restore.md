# ADR-0037 — Constrained OS helper and owner restore

[فارسی](../fa/ADR-0037-helper-محدود-و-بازیابی.md) · **English**

**Status:** Accepted — W3b in 1.3.3
**Date:** 2026-10-25

## Decision

- The web app remains non-root. A separate root-owned helper with constrained sudoers policy exposes only fixed `apply-config` and `restart` actions.
- The helper accepts an exact JSON schema, validates DNS domains, and executes absolute allowlisted subprocesses without a shell.
- systemd/nginx files derive from root-owned `/usr/local/share/nobi-installer` templates (not the service-writable checkout) and have fixed destinations. `systemd-analyze verify` and `nginx -t` run before reload; prior files are rolled back on error.
- Backup uses the existing SQLite online backup, checksums and manifest; `.env` and secrets are excluded.
- Restore accepts only allowlisted archive names inside the managed backup directory and exact `RESTORE <file>` confirmation. Restart requires exact `RESTART`.
- Endpoints require owner+CSRF, enforce timeout/bounded output and append a mode-0600 audit log.

## Consequences

The web app cannot install arbitrary packages or run arbitrary commands. Initial helper/sudoers installation deliberately remains an OS administrative act; a missing helper fails closed with 503. Restore changes session state and requires a restart.
