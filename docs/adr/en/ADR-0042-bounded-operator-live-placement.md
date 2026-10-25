# ADR-0042 — Bounded live placement for selected operators

[فارسی](../fa/ADR-0042-مجوز-محدود-ثبت-واقعی-اپراتور.md) · **English**

**Status:** Accepted — implemented in 1.5.0; operational activation still awaits soak
**Date:** 2026-10-25

## Decision

- The general operator role does not grant live placement. An owner may enable an independent `live_place` capability for a selected operator only.
- Per-user policy includes `max_order_usdt`, `max_daily_usdt`, `max_daily_orders`, `allowed_symbols` and `allowed_execution`. Only `limit` is permitted at this stage.
- Policy and daily usage live in the auth SQLite database. Budget checking and usage reservation are atomic under `BEGIN IMMEDIATE`; `clientOrderId` is unique and an identical retry is idempotent.
- Owners bypass operator policy, but promotion evidence, live interlock, CSRF, explicit confirmation, preflight, ledger and no-blind-retry controls still apply.
- Owners grant/revoke policy in the Users tab. A selected operator sees the real-order form on the Trading Desk; ordinary operators do not and receive API 403.
- Ambiguous or rejected outcomes do not automatically release reserved budget. This conservative direction prevents extra placement and requires owner review.

## Endpoint control order

1. active operator/owner session and CSRF;
2. live mode and credential interlock;
3. exact `PLACE-REAL-ORDER` confirmation;
4. promotion evidence;
5. shared preflight and USD notional calculation;
6. capability, symbol/execution and atomic limits;
7. ledger/idempotency and signed submission.

## Prohibited

Operators cannot change live mode, credentials, promotion evidence, their own policy or anyone else's. Market orders are rejected by policy. Machine tokens receive no human capability.
