# ADR-0033 — Evidence gate for live-execution promotion

[فارسی](../fa/ADR-0033-دروازه-ارتقای-اجرای-واقعی.md) · **English**

**Status:** Accepted — B5 implemented in 1.2.6; no automatic application
**Date:** 2026-10-25

## Decision

A signer and routes are insufficient to enable live mode. The ledger must contain at least 30 dry-run intents over at least 5 distinct days, no `unknown`/`cancel-unknown` state, and every sample must have an explainable outcome. The API reports evidence and refusal reasons only; it never changes mode automatically.

## Acceptance

- count unique ledger rows, not polling;
- derive days from durable `created_at`;
- unknown keeps ready false even with enough samples;
- an empty database invents no numbers;
- `/api/live/evidence` is private and reveals no secret/path;
- no live placement is added.
