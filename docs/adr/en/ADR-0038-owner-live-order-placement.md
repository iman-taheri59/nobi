# ADR-0038 — Owner-only live order placement after promotion gate

[فارسی](../fa/ADR-0038-ثبت-سفارش-واقعی-مالک.md) · **English**

**Status:** Accepted — implemented in 1.4.0; operational activation awaits a real soak
**Date:** 2026-10-25

## Decision

- `POST /api/live/place` requires an owner session plus CSRF; an API token or operator is insufficient.
- The route is discoverable while disabled, but fails closed until `NBX_LIVE_MODE=live`, valid credentials and the environment interlock exist.
- Every request requires exact `PLACE-REAL-ORDER` confirmation and a fresh idempotency key.
- A server-side promotion gate requires at least 30 dry-run samples on 5 distinct days and zero ambiguous outcomes; UI cannot override it.
- Live and dry-run use the same server preflight. Veto, heat, cash, concurrency, minimum-order and market-status checks run before intent creation.
- Deterministic clientOrderId, ledger reservation before network, Ed25519 signing and no blind retries remain. A post-send timeout becomes `unknown` and requires reconciliation.
- Responses/audit contain public projections only; raw body, signature and secrets are not returned.
- The owner card shows evidence and enables placement only when both live mode and evidence are ready.

## Consequence

Endpoint presence does not mean live is enabled. Promotion has not happened in this release because the five-day soak needs a real Nobitex-connected deployment. Default remains disabled and W2 stores only disabled/dry-run; live promotion occurs outside UI after evidence review.
