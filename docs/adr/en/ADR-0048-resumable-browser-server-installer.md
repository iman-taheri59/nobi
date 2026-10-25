# ADR-0048 — Resumable browser server installer and constrained OS helper

[فارسی](../fa/ADR-0048-نصبگر-سرور-مرورگر.md) · **English**

**Status:** Accepted and implemented for 1.9.0
**Date:** 2026-10-25
**Related:** ADR-0035, ADR-0036

## Context

Owner bootstrap, non-secret runtime settings, and source-tree gates already worked in the browser. Completing deployment still required a shell for systemd/nginx installation and restart. Giving the web process root or accepting command text would create a remote shell.

## Decision

The owner-only wizard persists eight resumable steps in SQLite: bootstrap-owner, host-preflight, storage, runtime, reverse-proxy, service-install, validation, and complete. Mutations require session CSRF and use a process-wide lock. Host preflight reports OS, non-root execution, Python, disk, writable data, ports, helper, systemd, and nginx without changing the host.

Privileged operations cross one protocol-versioned boundary. The web process passes only one allowlisted action id to `nobi-helper-client`; the root-owned helper accepts exact argv for install-service, install-proxy, restart-service, reload-proxy, or rollback. It accepts no command text, package, service name, or path. Sudoers contains exact commands and no wildcard. Managed files use fixed source/target paths, backups, atomic replacement, nginx validation, and bounded subprocess timeouts.

Output is bounded and secret-pattern redacted. Restart, reload, and rollback require explicit confirmation. Live mode remains disabled/dry-run and cannot be promoted by this wizard.

## Failure and rollback

Every action creates an immutable run row and updates its step to running, passed, or failed. Existing service/proxy files are backed up before replacement. Rollback restores only fixed known targets and revalidates systemd/nginx. A browser refresh reads persisted state and recent runs.

## Security invariants

- web process is never root;
- owner session and CSRF are mandatory;
- helper actions and argv are closed allowlists;
- no shell invocation, wildcard sudo rule, user path, or package name;
- no secrets in managed settings, state, logs, API output, or audit detail;
- concurrent mutations fail closed;
- Paper Trading remains the deployment default.
