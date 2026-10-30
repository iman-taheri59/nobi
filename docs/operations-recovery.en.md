# Backup and offline recovery

[فارسی](operations-recovery.md) · **English**

> This guide does not authorize automatic execution. Stop the service and every data writer under a separately approved maintenance plan. Web restore is deliberately disabled: an exact confirmation phrase cannot establish quiescence.

## Sources and confidentiality

- Settings backups use the **running process** paths, not pending managed drafts. Saving a new path does not migrate a database.
- The standalone CLI does not import the application or automatically load `.env`. Restore requires explicit `--journal`, `--auth` and `--live` destinations. Match actual runtime paths; repository defaults are not evidence of those paths.
- Any missing/zero-byte required source refuses backup. Do not create empty SQLite files merely to bypass this check; first establish the correct source and state-domain requirements.
- New archives are private (`0600` on POSIX); newly created backup directories are private too. Existing directory and ancestor permissions still need independent verification.
- The three-DB archive contains sensitive account data and hashes. Excluding `.env` and API keys does not make it public. It is not a complete backup of the OS, Paper JSON, credentials or all application state.
- Snapshot consistency is **per database**, not one cross-database transaction. A coordinated multi-domain checkpoint requires quiescing/coordinating writers before backup as well.
- Existing output archives are not overwritten; choose a new output name/time.

## Restore prerequisites

1. Verify the correct archive/destinations outside trading, and separately preserve newer state that must not be lost. Restore replaces destination contents.
2. Stop the service, workers, schedulers, maintenance scripts and every SQLite connection. This procedure needs no Live activity.
3. Never manually delete remaining `-wal`, `-shm` or `-journal` files. After establishing that all writers stopped, use a clean SQLite checkpoint/close. For valid WAL, `PRAGMA wal_checkpoint(TRUNCATE)` on a maintenance connection followed by close is the usual pattern; busy/integrity failures mean stop. Read-only snapshots may also leave empty sidecars. This tool conservatively refuses any sidecar.
4. Invoke the CLI with `--force --offline` and all three explicit destination paths. `--offline` is an **operator attestation**, not automatic service shutdown or proof that another writer cannot restart.
5. Manifest, checksums and integrity for all archived files are verified before the first replacement. Stage all replacements and undo copies first. Ordinary intermediate failures undo changed destinations; if undo itself fails, retain private recovery copies for investigation.
6. This rollback is **not power-loss/process-crash atomicity across files/disks**. Verify domain consistency, schema, account and ledger before accepting traffic. A valid manifest/integrity check does not prove the right version or coherent trading state.

Legacy format 1 replaces journal/auth only and leaves Live untouched; do not call it a complete multi-domain recovery.

Command shape only; paths are placeholders and no real recovery was run for these changes:

```bash
python scripts/state_backup.py restore /private/path/snapshot.zip \
  --journal /actual/runtime/journal.db \
  --auth /actual/runtime/auth.db \
  --live /actual/runtime/live_execution.db \
  --force --offline
```

Automated tests replace fabricated databases in private temporary directories only. Passing them does not authorize deployment, shutdown or real-account recovery.
