# ADR-0032 — Dry-run placement only after server preflight

[فارسی](../fa/ADR-0032-ثبت-آزمایشی-با-پیش-پرواز.md) · **English**

**Status:** Accepted — B4 implemented in 1.2.5; live place remains absent
**Date:** 2026-10-25

## Decision

`POST /api/live/dry-run` works only in dry-run mode and never calls the network, even when credentials exist. Size, entry and stop come from the same server path as `/api/risk/whatif`; callers cannot assert their own preflight result. A blocked gate creates no intent. `clientOrderId` is persisted only after successful preflight and actor/request are audited without secrets.

## Requirements and acceptance

- disabled and live modes reject the route;
- missing/blocked preflight fails before the ledger;
- amount and price derive from server output;
- responses reveal no raw body, intent JSON or credential;
- repeated idempotency keys create no second record;
- `require_client` protects and auditing records the route;
- no real-placement route is added.
