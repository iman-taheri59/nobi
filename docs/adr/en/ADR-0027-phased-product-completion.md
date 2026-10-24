# ADR-0027 — Phased product completion: stable 1.2, then live trading

[فارسی](../fa/ADR-0027-تکمیل-مرحله‌ای-محصول.md) · **English**

**Status:** Accepted — Phase A code-complete in 1.2.0; real soak pending; Phase B not started
**Date:** 2026-10-25
**Related:** ADR-0003, ADR-0004, ADR-0009, ADR-0014, ADR-0021 through ADR-0026

## Context

The project is already a tested analysis, risk, backtest and paper-trading dashboard, but “complete” has two materially different meanings: a stable read-only/paper product and a real-order execution system. Finishing both as one undivided effort leaves scope and security risk unbounded.

## Requirements

### Phase A — stable 1.2 release

1. Analysis, paper trading, journal, backtest and scheduled scans work without fabricated data.
2. Installation, health checking, backup/restore and restart recovery are repeatable.
3. OHLC history can be fetched with pagination, deduplication, gap detection and checkpoints.
4. The version agrees across artifacts and Linux/Windows CI is blocking.
5. Adaptive thresholds and gates remain measurement/shadow only; live trading stays disabled.
6. A living handoff records status, decisions, tests and the next action throughout the work.

### Phase B — live trading 2.0

1. API keys stay server-side and use the official signer; secrets never reach the browser, logs or unencrypted backups.
2. Real order placement, cancellation and status are idempotent and use client order IDs.
3. Reconciliation after timeouts/restarts converges local and exchange state.
4. Every order runs through the same preflight and kill switch.
5. An immutable audit trail, role controls, explicit confirmation and dry-run mode exist.
6. Live activation requires a feature flag, valid configuration, sandbox/fixture tests and a separate release decision.

## Decision

Work proceeds in two phases. Phase A first reaches its own acceptance criteria and becomes releasable. Phase B then uses an isolated execution boundary. No shortcut may enable real orders before signing, reconciliation and auditing exist.

`docs/HANDOFF.md` and `docs/HANDOFF.en.md` are living operational records and are updated after each stage. Architectural requirement changes are recorded bilingually here or in a successor ADR at the same time.

## Phase A acceptance

- pytest, ruff, mypy, ESLint, parity and build all pass;
- smoke test covers health, login, signals, paper order, restart and persistence;
- atomic SQLite backup and restore are tested;
- the history downloader is fixture-tested without network-dependent tests;
- Windows CI is blocking;
- at least a 24-hour soak on a real installation has no crash or fabricated data.

## Phase B acceptance

- live is off by default and cannot start without valid secrets;
- place/cancel/status and reconciliation have contract tests;
- secrets never appear in logs or diagnostics;
- emergency stop and exposure limits pass end-to-end tests;
- independent security review precedes use with real funds.

## Consequences

- The fastest useful release ships without trading away security.
- “Complete” becomes testable and no longer moves whenever a feature is proposed.
- Live trading arrives later, but network failures and restarts cannot silently create ambiguous orders.
