# Handoff — completing nobitrader

[فارسی](HANDOFF.md) · **English**

**Last updated:** 2026-10-25 — stage 32 (drift scheduler hardening)
**Working version:** 1.8.0 (unreleased; release only after green CI)

### Stage 32 state

- Owner-controlled scheduling is disabled by default with 6/12/24-hour intervals and no missed-run replay storm.
- Persistent next/last/error state, supervised lifecycle execution and configuration inventory are implemented.
- Schedule/inventory APIs, CSRF, audit, recovery and due/not-due tests are included.

### Stage 31 state

- Equal non-overlapping baseline/current windows compare outcome, PSI and linked pipeline rates.
- BH-adjusted findings and explicit sample suppression are persisted in immutable runs.
- Operator/owner execution and append-only review use CSRF and audit; UI is bilingual.
- ADR-0046 and dedicated statistics/integrity tests are included.

### Stage 30 state

- Deterministic journal-insert linkage uses `scan_cycle_id`; a duplicate closed bar cannot overwrite first-cycle provenance.
- Cycle/candidate/reason schema and `legacy-unlinked` migration are idempotent; skipped upstream candidates and crash-orphan recovery are explicit.
- Versioned APIs, stable cursor, safe CSV/JSON and export audit are implemented.
- Coverage, Wilson 95%, approximate expectancy CI and split maturity are exposed by API/UI.
- Bilingual ADR-0045 and v1.1 tests were added.
- Local gate: Ruff and mypy passed; pytest 973 passed; JS lint/parity/display/icon/adaptive checks passed; standalone rebuilt. jsdom authenticated UI smoke passed. Chromium could not launch in this sandbox, so browser probe remains a branch-CI gate.
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
- [x] W2: owner UI for data paths, scan/TTL and disabled/dry-run; secret-free managed.env.
- [ ] W2b: reverse-proxy/systemd validation and generation through a non-root helper.
- [x] W3: 13 owner-only allowlisted test/lint/parity/build/deploy-check gates.
- [x] W3b: backup/restore/restart and systemd/nginx through a constrained OS helper.
- [ ] On the real server: install the helper/sudoers root-owned and set `NBX_INSTALL_HELPER`.
- [x] Real `/api/live/place`: owner+CSRF, explicit confirmation, preflight and promotion gate.
- [ ] Run the real 5-day/30-sample soak; do not enable live mode before it passes.
- [x] Dedicated owner-only users tab for roles/state/sessions/invites and activity.
- [x] Owner-only troubleshooting tab, error logs, diagnostics and devsuite; mutations require CSRF.
- [x] Secret-free summary audit for self-test, diagnostics and devsuite; explicit owner-only chip.
- [x] Full matrix: owner-only settings/cache; operator/owner audit/notify/private-live reads; no human role for machine tokens.
- [x] Independent selected-operator `live_place`: limit-only, amount/count/symbol caps and atomic budget reservation.
- [x] My Account for every role: capabilities/policy/usage, sessions, activity, password and preferences.
- [x] Signal Intelligence v1: metrics, funnel, cohorts, MFE/MAE, detail and operator/owner review.
- [x] CI portability follow-up: Windows-safe SQLite connection closure/mode checks and first-run-aware browser probe.
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
mypy: success, 115 source files
pytest: 968 passed, 1 dependency deprecation warning
ESLint/dashboard lint: passed
broker/preflight parity: passed
mobile artifact: rebuilt for 1.6.0
```

## External limits

- The earlier local browser probe could not launch Chromium in this sandbox; CI uses `--with-deps`.
- A soak/private API test cannot honestly run without suitable credentials and network location.

## Next action

1. Push the hardened 1.6.0 branch and require green remote CI before moving the release tag;
2. deploy with every mutable path on the persistent `/var/lib/nobi` volume;
3. run the real five-day/30-sample paper soak on an Iran-reachable host;
4. run the limited private API contract test with an IP-limited key;
5. complete an independent security review before real funds.

## Stage 33 — Drift PSI evidence detail
- Added persisted per-bin PSI counts, proportions, bounds, and contributions.
- Added exact optional `cohort_key` filtering to drift run detail.
- Config inventory now distinguishes overlapping low-sample configurations as `not-comparable`.
- Release remains blocked until the remaining multidimensional cohort/UI scope is complete and the final gate is green.

## Stage 34 — bounded cohort inventory
- Added `/api/v1/signal-intelligence/drift/cohorts` with allowlisted single and limited combined dimensions.
- Cardinality is capped at 100 with explicit `truncated`; low samples are `not-comparable` and suppressed.
- Statistical cohort findings and their UI remain the next implementation stage; release is still blocked.

## Stage 35 — Drift operator UI
- Added bilingual PSI-bin chart, cohort population table, run CSV export, and scheduler controls.
- Scheduler mutation uses the authenticated CSRF headers and remains server-enforced owner-only.
- Standalone was regenerated; full Python and static/UI gates passed locally (`983 passed`).

## Stage 36 — cohort statistical evidence
- Drift runs accept an allowlisted cohort mode and persist expectancy, win-rate, and PSI per comparable cohort.
- Global and cohort hypothesis tests use one BH correction family; PSI bins remain auditable.
- Neutral successful runs now aggregate to `stable`; full local gate passed with `984 passed`.
