# ADR-0045 — Signal Intelligence v1.1 linked evidence

[فارسی](../fa/ADR-0045-شواهد-پیوندی-هوشمندی-سیگنال-۱-۱.md) · **English**

**Status:** Accepted and implemented for 1.6.8  
**Date:** 2026-10-25

## Context

The v1 dashboard combined independently recorded scan, gate and outcome data. Timestamp proximity could not prove that a gate verdict and a signal belonged to the same decision. Legacy rows must remain useful without receiving invented provenance.

## Decision

1. Every new scan receives a time-sortable 128-bit `scan_cycle_id` and immutable engine/config identity.
2. `scan_cycles`, `scan_candidates`, and normalized `gate_verdict_reasons` preserve cycle, candidate, primary reason and all reasons. Unknown codes remain `unknown`; they are never dropped.
3. A journal row is linked at insert time. Re-scanning the same closed bar cannot overwrite the first cycle's provenance. Existing rows migrate as `legacy-unlinked`; no timestamp inference is permitted.
4. The linked API is versioned under `/api/v1/signal-intelligence`: summary, pipeline, gate reasons, stable `(ts,id)` cursor pages, and bounded CSV/JSON export.
5. Summary reports linkage coverage, pending-not-due separately from due-unresolved backlog, Wilson 95% win-rate intervals, and an approximate normal 95% interval for mean expectancy. Sample sufficiency remains explicit.
6. CSV cells whose first visible character (after spaces or control whitespace) is `=`, `+`, `-`, or `@` are prefixed with an apostrophe. Export is capped at 10,000 rows and audited.
7. Human review remains append-only and restricted to operator/owner sessions with CSRF. Read/export may use authenticated sessions or the configured machine token.

## Compatibility and failure law

Schema creation and column migration are idempotent SQLite operations. Old v1 endpoints remain available. Evidence persistence must not stop market scanning; failed evidence completion marks a cycle `partial`, and a later scan recovers crash-orphaned `running` cycles after one hour. Upstream failures are retained as `scan-error` candidates with `source-unavailable` evidence. No counterfactual, contribution, execution cost, or unresolved outcome is guessed.

## Consequences

The funnel is now a true linked funnel for post-migration cycles. Coverage makes the mixed legacy period visible. Full replay, drift detection, execution-cost attribution, and PostgreSQL remain outside v1.1.

## Verification

Migration/idempotency, exact linkage, duplicate-bar provenance, normalized multi-reason taxonomy, Wilson/expectancy statistics, maturity, cursor isolation, CSV injection defense, JSON/CSV export, API, role, Linux/Windows and UI checks are release gates.
