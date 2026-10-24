# Handoff — completing nobitrader

[فارسی](HANDOFF.md) · **English**

**Last updated:** 2026-10-25 — stage 4 (Phase B1 signer implemented)
**Baseline:** `main` at `be3d4fe` plus 1.2.0 working changes
**Target:** ADR-0027; stable 1.2 first, then live-trading 2.0.

## Current status

- [x] Scope and acceptance criteria in bilingual ADR-0027.
- [x] Consistent SQLite backup/restore with checksums and three tests.
- [x] Paginated, resumable, deduplicated, gap-aware UDF history with four tests.
- [x] Version 1.2.0 in backend, package, dashboard and mobile artifact.
- [x] Blocking Windows CI.
- [x] All locally runnable gates: 897 pytest, ruff, mypy, ESLint, dashboard lint, Python/JS parity, intrabar, display, adaptive and icons.
- [x] Mock-exchange API smoke: health, signals, orders, backup and restore.
- [ ] Local browser probe: Chromium closed in this sandbox; CI installs it with `playwright install --with-deps`.
- [ ] Real 24-hour soak — requires an environment that can reach Nobitex.
- [x] Phase B1: isolated Ed25519 signer with real verification and secret-safe failures.
- [ ] Phase B2+: dry-run client, idempotency, live execution, reconciliation, audit and independent review.

## Locked decisions

1. Version 1.2 is the stable analysis/paper product; live stays off.
2. Adaptive gates remain `shadow` and `applied:false` until evidence is sufficient.
3. Tests do not depend on the Nobitex network.
4. Secrets never enter the repository, browser, logs or unencrypted backups.
5. Missing credentials or a real soak are never replaced by a mock or a fabricated claim.

## Main delivery files

- `docs/adr/fa/ADR-0027-تکمیل-مرحله‌ای-محصول.md` and its English peer
- `scripts/state_backup.py`
- `scripts/fetch_history.py`
- `server/tests/test_state_backup.py`
- `server/tests/test_fetch_history.py`
- `server/nobitex_signing.py` and `server/tests/test_nobitex_signing.py`
- `.github/workflows/ci.yml`

## Verification results

```text
ruff: passed
mypy: success, 95 source files
pytest: 902 passed, 1 dependency deprecation warning
ESLint/dashboard lint: 0 errors
broker/intrabar/preflight parity: passed
display: 306 checks passed
adaptive card/icons: passed
API smoke: health + signals + orders + backup + restore passed
```

## Risks/blockers

- A real soak and private-API contract test require the user's environment and credentials.
- The browser probe could not launch Chromium in this sandbox; CI installs system dependencies.
- Live activation before signing, reconciliation, auditing and independent review is prohibited.

## Exact next action

1. Run the 1.2.1 gates and release B1;
2. write the B2 ADR for the dry-run/idempotent client;
3. deploy 1.2 in the user's environment for a 24-hour soak;
4. run private contract tests only with a READ/TRADE key and IP allowlist.
