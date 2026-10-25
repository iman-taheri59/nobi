# ADR-0041 — System access matrix after full review

[فارسی](../fa/ADR-0041-ماتریس-دسترسی-سامانه.md) · **English**

**Status:** Accepted — applied in 1.4.4
**Date:** 2026-10-25

## Context

After making users and troubleshooting owner-only, remaining routes were reviewed. Runtime settings exposed paths and secret presence to viewers/operators; audit contains operational context and IPs; private exchange reads used a mutation+CSRF dependency, which was semantically wrong.

## Decision and matrix

| Area | viewer | operator | owner | machine token |
|---|---|---|---|---|
| Market, signals, reports and paper state | read | read | read | middleware read policy |
| Paper order, halt/resume, exec mode, backtest, journal maintenance | deny | mutate+CSRF | mutate+CSRF | legacy `require_client` allowlist |
| Operational audit and notification status | deny | read | read | deny |
| Live status/evidence/private orders | deny | read | read | deny |
| Live dry-run/cancel/reconcile | deny | mutate+CSRF | mutate+CSRF | `require_client` policy |
| Runtime settings, cache internals, Telegram credential probe | deny | deny | owner; mutations+CSRF | deny |
| Users, installation, privileged operations and troubleshooting | deny | deny | owner; mutations+CSRF | deny |
| Live placement | deny | selected operator with policy+CSRF only | owner+CSRF | deny |

- New `require_operator` accepts only active operator/owner sessions and does not demand CSRF for operational GETs.
- Settings navigation and the audit card are hidden according to role; the backend enforces independently.
- Settings remain read-only, but read-only does not mean deployment context is public.
- A machine token does not inherit a human role and cannot enter private/admin domains.

## Unchanged

Viewers retain market, paper-state, result and report reads. Operators retain routine paper/backtest/journal operations. Browser error ingestion and health monitoring retain their existing policies.
