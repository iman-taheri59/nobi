# ADR-0031 — Private control routes without a real-placement route

[فارسی](../fa/ADR-0031-روت-های-کنترل-خصوصی.md) · **English**

**Status:** Accepted — B3 routes implemented in 1.2.4; place remains absent
**Date:** 2026-10-25
**Related:** ADR-0018, ADR-0028, ADR-0029, ADR-0030

## Context

The list/cancel/reconcile primitives exist, but operators should not need to run Python to use them. At the same time, exposing placement before wired preflight and auditing is unsafe.

## Decision and requirements

1. An isolated `/api/live` router exposes only status, list, cancel and reconcile; it has **no place route**.
2. Every private route uses `require_client`; viewers and requests without CSRF/token are rejected.
3. Default mode is disabled; live construction requires mode, confirmation phrase and both credentials.
4. The sender transmits the exact signed raw body with `httpx` and does not reserialize headers/body.
5. Status reveals no secret, public key, database path or confirmation phrase.
6. Invalid configuration fails closed without taking down the public server.

## Acceptance

- OpenAPI contains no `/api/live/place`;
- control endpoints reject unauthorized requests;
- disabled mode fails before networking;
- status reports only mode, configured and placement_exposed=false;
- fixture tests prove list/cancel/reconcile from router through client.
