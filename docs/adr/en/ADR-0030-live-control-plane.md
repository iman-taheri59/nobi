# ADR-0030 — Live control plane: observe and cancel before exposing placement

[فارسی](../fa/ADR-0030-صفحه-کنترل-اجرای-واقعی.md) · **English**

**Status:** Accepted — B3 primitives implemented in 1.2.3; routes remain disabled
**Date:** 2026-10-25
**Related:** ADR-0027, ADR-0028, ADR-0029

## Context

Before real placement is exposed through the application API, an operator must be able to read exchange orders, reconcile a recorded intent and cancel it by `clientOrderId`. Shipping placement before these controls makes exit weaker than entry.

## Requirements

1. Order listing is read-only and signs the exact query string.
2. Cancellation is allowed only for a `clientOrderId` already present in the ledger.
3. A cancel timeout becomes `cancel-unknown` and is never blindly retried.
4. A final response becomes `canceled` or `cancel-rejected` and remains durable.
5. Neither secrets nor raw transport-error text are stored.
6. Real-placement routes remain disabled until role controls, preflight and audit are completed next.

## Decision

First add `list_orders` and `cancel` primitives to the isolated client and fixture-test them. FastAPI routes and UI wiring follow separately only after these primitives prove fail-closed.

## Acceptance

- list signs the full query path and sends a bodyless GET;
- cancel rejects an unknown identifier before networking;
- a timeout records one attempt and a second call does not send;
- success stores canceled and failure stores cancel-rejected.
