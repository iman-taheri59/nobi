# ADR-0028 — Safe live-execution boundary: signing before orders

[فارسی](../fa/ADR-0028-مرز-امن-اجرای-واقعی.md) · **English**

**Status:** Accepted — stage B1 implemented in 1.2.1; real order submission remains disabled
**Date:** 2026-10-25
**Related:** ADR-0003, ADR-0008, ADR-0009, ADR-0014, ADR-0027

## Context

Nobitex API keys require `Nobitex-Key`, `Nobitex-Signature` and `Nobitex-Timestamp`. The signed message is exactly `timestamp + METHOD + full_path + raw_body`, signed with Ed25519 and URL-safe Base64 encoded. Any difference between the signed and transmitted body invalidates the request. Putting this directly into the public market client would blur the current read-only boundary and increase secret-leak and accidental-order risk.

## B1 requirements

1. The signer is network-free and signs only the final raw body bytes.
2. The query string is part of `full_path`; its order and encoding match the transmitted path.
3. The private key enters memory only from an environment/secret store and never reaches logs, repr, diagnostics, backups or the browser.
4. Invalid key, timestamp and Base64 fail before network access.
5. Tests verify the signature with the Ed25519 public key and reject any changed payload component.
6. Adding the signer alone enables no trading endpoint, route or feature flag.

## Decision

`server/nobitex_signing.py` is the sole source for signed payloads and authentication headers. It accepts the public key, URL-safe-Base64 private key, method, full path, raw body and timestamp. It does not serialize JSON: the caller must sign and transmit the exact same raw body.

A later stage introduces a `LiveExecutionClient` separate from the public `NobitexClient`. Until a successor ADR completes idempotency and reconciliation, the existing `_auth_headers` stub and every live path remain disabled.

## Acceptance

- the matching public key verifies generated signatures;
- changing timestamp, method, path or body breaks verification;
- malformed Base64 and non-32-byte keys fail without including the secret in the message;
- no existing route or execution mode changes;
- all previous gates remain green.
