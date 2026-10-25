# ADR-0040 — Owner-only system troubleshooting

[فارسی](../fa/ADR-0040-خطایابی-فقط-مالک.md) · **English**

**Status:** Accepted — implemented in 1.4.2
**Date:** 2026-10-25

## Decision

- The System Troubleshooting tab appears in navigation only after an active owner session is confirmed.
- Reading the error log, diagnostics catalogue, devsuite status and prior runs requires an owner session.
- Running diagnostics, clearing logs, self-test and devsuite execution additionally require valid CSRF.
- Viewer/operators receive 403; anonymous and bearer API-token requests receive 401. DOM manipulation cannot bypass backend enforcement.
- `POST /api/errors` remains under general authenticated error-reporting controls so browsers can report failures, while reading or deleting reports is owner-only.

## Rationale

Error logs may contain paths, service state and operational context, while diagnostics access network and internal state. Operators do not require this access, and least privilege takes precedence over backward compatibility.
