# ADR-0048 — Resumable browser server installer and constrained OS helper

[فارسی](../fa/ADR-0048-نصبگر-سرور-مرورگر.md) · **English**

**Status:** Accepted and implemented for 1.9.0
**Date:** 2026-10-25
**Related:** ADR-0035, ADR-0036

## Context

Owner bootstrap, non-secret runtime settings, and source-tree gates already worked in the browser. Completing deployment still required a shell for systemd/nginx installation and restart. Giving the web process root or accepting command text would create a remote shell.

## Decision

The owner-only wizard persists eight resumable steps in SQLite: bootstrap-owner, host-preflight, storage, runtime, reverse-proxy, service-install, validation, and complete. Mutations require session CSRF and use a process-wide lock. Host preflight reports OS, non-root execution, Python, disk, writable data, ports, helper, systemd, and nginx without changing the host.

Privileged operations cross one protocol-versioned boundary over a Unix socket. The web process passes only one allowlisted action id to `nobi-helper-client`, which connects to `/run/nobi-installer.sock` (group `nobi`, mode 0660). A root-owned, socket-activated helper service validates the action and invokes the root CLI helper. No sudo, no setuid binary and no sudoers rule is involved: `NoNewPrivileges=yes` and `ProtectSystem=strict` on the web unit make sudo unusable, and the helper service has its own mount namespace. Managed templates live root-owned in `/usr/local/share/nobi-installer` (never in the service-writable checkout) and are validated for owner, mode and content before installation. The domain comes from root-owned `/etc/nobi/domain`, not from the request. Files use fixed targets, backups, atomic replacement, nginx validation and bounded timeouts.

Output is bounded and secret-pattern redacted. Restart, reload, and rollback require explicit confirmation. Live mode remains disabled/dry-run and cannot be promoted by this wizard.

## Failure and rollback

Every action creates an immutable run row and updates its step to running, passed, or failed. Existing service/proxy files are backed up before replacement. Rollback restores only fixed known targets and revalidates systemd/nginx. A browser refresh reads persisted state and recent runs.

## Security invariants

- web process is never root;
- owner session and CSRF are mandatory;
- helper actions and argv are closed allowlists;
- no shell invocation, sudoers rule, user path, writable template, or package name;
- no secrets in managed settings, state, logs, API output, or audit detail;
- concurrent mutations fail closed;
- Paper Trading remains the deployment default.
