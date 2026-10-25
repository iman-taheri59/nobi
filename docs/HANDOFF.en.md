# Handoff — completing nobitrader

[فارسی](HANDOFF.md) · **English**

**Last updated:** 2026-10-25 — stage 11 (W1 owner wizard complete)
**Working version:** 1.3.0
**Target:** ADR-0027; stable product first, then staged fail-closed live execution.

## Status

- [x] Phase A: stable analysis/paper, backup/restore, paginated history and blocking CI.
- [x] B1: isolated Ed25519 signer; no secret in logs or ledger.
- [x] B2: disabled/dry-run/live, durable ledger, idempotency, unknown state and reconciliation.
- [x] B3 primitives: order listing and `clientOrderId` cancellation with no-retry cancel-unknown.
- [x] B3 routes: private status/list/cancel/reconcile; place is deliberately absent.
- [x] B4: dry-run place with mandatory preflight and actor audit; no network submission.
- [x] B5: 30-sample/5-day/zero-ambiguity evidence gate; `applied:false`.
- [x] B6: all-three-DB backup, legacy compatibility and secret-free daily snapshots.
- [ ] Run the real five-day soak in a Nobitex-reachable environment.
- [x] W1: first-run owner wizard with a one-time console token.
- [ ] W2: installation paths/reverse-proxy settings in UI.
- [ ] W3: owner-only allowlisted tests, build, backup/restore and deployment tasks.
- [ ] Real soak/private contract test; requires an Iran-reachable host and IP-limited READ/TRADE key.
- [ ] Independent security review before real funds.

## Locked decisions

1. Live is off by default and requires an explicit confirmation phrase.
2. No ambiguous placement/cancel timeout is blindly retried; reconcile first.
3. One idempotency key with a changed payload fails closed.
4. Real placement routes wait for complete preflight, role controls and audit.
5. Secrets never enter the repository, browser, diagnostics, error text or backup.

## Files delivered across these stages

- bilingual ADR-0027 through ADR-0030
- `server/nobitex_signing.py`
- `server/live_execution.py`
- `server/tests/test_nobitex_signing.py`
- `server/tests/test_live_execution.py`
- `scripts/state_backup.py` and `scripts/fetch_history.py`

## Latest verification

```text
ruff: passed
mypy: success, 102 source files
pytest: 926 passed, 1 dependency deprecation warning
ESLint/dashboard lint: passed
broker/preflight parity: passed
mobile artifact: rebuilt for 1.2.3
```

## External limits

- The earlier local browser probe could not launch Chromium in this sandbox; CI uses `--with-deps`.
- A soak/private API test cannot honestly run without suitable credentials and network location.

## Next action

1. Release 1.2.6 and run CI;
2. add operator-only dry-run placement;
3. run the same `risk_engine.preflight` before creating every intent;
4. audit actor/request without sensitive payloads;
5. dry-run soak, then limited contract test and security review.
