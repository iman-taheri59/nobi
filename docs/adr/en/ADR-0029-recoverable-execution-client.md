# ADR-0029 — Recoverable execution client: dry-run, idempotency and unknown state

[فارسی](../fa/ADR-0029-کلاینت-اجرای-قابل-بازیابی.md) · **English**

**Status:** Accepted — stage B2 implemented in 1.2.2; public routes and live remain disabled
**Date:** 2026-10-25
**Related:** ADR-0008, ADR-0009, ADR-0014, ADR-0027, ADR-0028

## Context

Correct signing is necessary but insufficient for live execution. The primary risk is a timeout after the exchange receives a request: the process cannot know whether an order exists, and a blind retry can create a second order. Nobitex makes `clientOrderId` unique among open orders and accepts it for status/cancel operations, so it must be created and durably recorded before networking.

## Requirements

1. Default mode is `disabled`; `dry-run` never calls the network; `live` requires both configuration and an explicit confirmation phrase.
2. Every intent requires an idempotency key which produces a valid, at-most-32-character `clientOrderId`.
3. Final raw JSON and identifier are stored in SQLite before the first request.
4. Reusing the same key and payload creates no second request and returns the same record.
5. Reusing a key with another payload conflicts and fails closed.
6. A timeout/ambiguous failure becomes `unknown` and never automatically retries placement.
7. `unknown` can become final only through reconciliation by `clientOrderId`.
8. Secrets never enter the ledger, errors, repr or dry-run response.
9. This stage adds no user route and does not connect the signal engine to live execution.

## Decision

`server/live_execution.py` creates an isolated boundary with `ExecutionMode`, `OrderIntent`, `ExecutionLedger` and `LiveExecutionClient`. Callers inject a sender, keeping tests network-free. The ADR-0028 signer is called only in live mode immediately before transmission.

Durable states are `prepared`, `dry-run`, `accepted`, `rejected`, `unknown` and `reconciled`. An `unknown` placement is not retryable; only reconciliation is allowed.

## Acceptance

- disabled and unconfirmed live fail before networking;
- dry-run never calls the sender;
- two calls for one intent produce one attempt;
- a changed payload under one key is rejected;
- timeout becomes unknown and another call does not send;
- reconciliation by `clientOrderId` makes the record final;
- all previous tests remain green.
