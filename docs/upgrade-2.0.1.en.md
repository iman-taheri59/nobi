# Safe upgrade plan: 2.0.0 to 2.0.1

[فارسی](upgrade-2.0.1.md) · **English**

> Pre-release operational draft, not an installation command or permission to execute. The published version is still 2.0.0. No real 2.0.1 systemd upgrade/rollback has been run.

## Why the previous installer is insufficient

This patch corrects IRT REST UDF, WS candle and tick units. It adds a journal `price_contract` column and `signal_evidence` view. Unknown-scale IRT history is preserved unchanged but excluded from qualified evidence.

The separately supplied 2.0.0 installer restores only source on failure and may restart old code against newer state. **Changing only its tag and hashes does not make it a safe 2.0.1 installer.** Reverting this upgrade requires an explicit code-and-data compatibility decision.

## Pre-installation gates

1. Confirm the author, final commit and push authorization; all required CI gates for that exact commit must pass. Verify the published annotated `v2.0.1` tag points to it. Local results are not remote CI.
2. Pin the real verified commit and standalone SHA256 in the final installer. Never execute one with placeholder or guessed hashes.
3. Inspect effective service/version/state paths/configuration locally. Never print/upload `.env`, tokens, auth data or private backups.
4. Agree on a maintenance window and close old browser tabs. Do not accept concurrent orders/settings changes during maintenance. Do not silently change Live/Auto or risk caps.
5. After stopping the service and associated writers, recheck local state. This upgrade defaults to **no active positions or orders**. An earlier empty-position report is insufficient. If any exist, stop the upgrade and resume the unchanged old version; never force an exit, move stops or multiply prices by ten. Legacy IRT positions have no automatic unit migration.
6. Dirty tracked source, unknown state paths, missing expected files, invalid backups or inability to stop writers must abort the upgrade. Do not conceal errors by reinstalling or deleting state.

## Consistent pre-change snapshot

Use a private directory with restricted permissions and appropriate ownership:

- Previous commit/tag and source, with its version recorded.
- A recoverable runtime and dependency inventory. Product requirements did not change in this patch, but rollback must not rely on unverified runtime compatibility.
- Private configuration and service unit copies, plus hashes to detect changes; never print secret contents.
- Effective journal/auth/live SQLite paths, Paper and other configured persistent state; do not assume everything lives under `/srv/nobi-data`.
- SQLite online-backup snapshots with `integrity_check`; copying a live database file alone is insufficient.
- Paper JSON **and its matching `.bak`**, execution/scanning state and an explicit record of absent files.

`scripts/state_backup.py` covers only three SQLite databases. Paper, its `.bak`, private config and source are separate. Its `contains_secrets:false` manifest is NOT permission to publish an archive: account/user information can still be sensitive.

## Apply and initial acceptance

Install only verified source by controlled fast-forward/checkout, without destroying local changes. Verify the standalone hash and unchanged settings. Journal migration is additive: do not delete, rescale or retroactively qualify old records.

After startup, verify `/api/health`, backend/served-page versions and hashes, unchanged scanning configuration and `heat_basis`. An active systemd service alone is insufficient. Refresh stale tabs. Inspect candle prices and stream rejection status without injecting diagnostic orders into a real account.

Qualified evidence may decrease because legacy IRT is excluded. Do not confuse that with deletion or repair it by rewriting history.

## Failure and rollback matrix

| Failure phase | Safe action |
|---|---|
| Before source/state changes | Do not install. If stopped only for backup and state is unchanged, resume the old version and verify health. |
| Source changed, provably before any new-code execution/migration | Restore compatible old source/runtime. The absence of new writers must be demonstrable. |
| New code started, or its execution is uncertain | Never automatically revert source and restart. Keep stopped until failed-state capture and an explicitly approved data rollback decision. |

### Reverting after new-code execution

1. Stop the service and all writers; verify none remain.
2. **First preserve a separate post-upgrade snapshot** of all state, Paper/`.bak`, existing WAL/SHM sidecars, necessary logs and source identity. Never overwrite the pre-upgrade backup.
3. If new orders/positions or Live activity exist, do not restore old state without reconciliation. A snapshot does not reverse market history or real trades. The operator must explicitly accept rollback consequences.
4. Restore a coherent pre-upgrade source/runtime/config/state set, not only source or journal. Paper and its `.bak` must belong to the same set.
5. Before replacing databases, handle WAL/SHM: after preserving failed-state sidecars and confirming all writers are stopped, move those sidecars out of active paths so they cannot replay onto restored databases. Never restore over open SQLite databases.
6. Verify schema, integrity, JSON, ownership and permissions before restarting the old version; then verify health/configuration. Keep post-upgrade records in the failed-state snapshot. Do not automatically merge them into legacy history or delete them.

## Existing local evidence

`server/tests/test_upgrade_rollback_contract.py` rehearses two offline scenarios:

- Pre-contract journal schema, backup, migration/new insertion, failed-state snapshot and exact old schema/data restoration, with newer records preserved separately.
- Matching Paper/`.bak` backup, synthetic partial exit, failed-state preservation and coherent restoration with correct heat.

These operate only on temporary files and synthetic data. They do not execute the old application/interpreter, run systemd or guarantee deployment on a real server.

## Post-deployment acceptance

After an approved installation, inspect a natural Paper order/position/exit and qualified journal cycle. Do not force orders/exits, alter stops, raise caps or enable Live for diagnosis. Establish the formal five-day observation start separately. Legacy history is not automatically a new baseline; five days or 30 samples is neither a profit guarantee nor Live authorization.
