#!/usr/bin/env python3
"""Atomic backup and guarded restore for nobitrader SQLite state.

The archive deliberately excludes API keys and .env. SQLite's online backup API
produces a per-database snapshot, not a cross-database transaction. Archives
contain sensitive account data. Restore is OFFLINE ONLY; stop every writer first.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOURNAL = Path(os.getenv("NBX_JOURNAL_DB") or ROOT / "server" / "journal.db")
DEFAULT_AUTH = Path(os.getenv("NBX_AUTH_DB") or ROOT / "server" / "auth.db")
DEFAULT_LIVE = Path(os.getenv("NBX_LIVE_DB") or ROOT / "server" / "live_execution.db")
FORMAT_VERSION = 2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError("required source database is missing")
    if source.stat().st_size == 0:
        raise ValueError("required source database is empty")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # sqlite3.Connection's context manager commits/rolls back but does not
    # close. POSIX lets a still-open temporary file be unlinked; Windows does
    # not, so explicit closing is part of the portability contract.
    with (
        contextlib.closing(sqlite3.connect(
            source.resolve().as_uri() + "?mode=ro", uri=True,
        )) as src,
        contextlib.closing(sqlite3.connect(destination)) as dst,
    ):
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"snapshot integrity check failed for {source}")


def create_backup(
    output: Path, journal: Path = DEFAULT_JOURNAL, auth: Path = DEFAULT_AUTH,
    live: Path = DEFAULT_LIVE,
) -> Path:
    sources = (journal.resolve(), auth.resolve(), live.resolve())
    if len(set(sources)) != 3:
        raise ValueError("state domains must use distinct database paths")
    if any(not source.is_file() or source.stat().st_size == 0 for source in sources):
        raise ValueError("required source database is missing or empty; backup refused")
    output = output.resolve()
    if output.suffix.lower() != ".zip":
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output = output / f"nobitrader-state-{stamp}.zip"
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.exists():
        raise FileExistsError("backup already exists; choose another output name")
    with tempfile.TemporaryDirectory(prefix="nobi-backup-") as raw_tmp:
        tmp = Path(raw_tmp)
        files = {"journal.db": journal, "auth.db": auth, "live_execution.db": live}
        manifest_files: dict[str, dict[str, object]] = {}
        for name, source in files.items():
            snap = tmp / name
            _snapshot(source, snap)
            manifest_files[name] = {"sha256": _sha256(snap), "bytes": snap.stat().st_size}
        manifest = {
            "format": FORMAT_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "product": "nobitrader",
            "contains_secrets": False,  # excludes environment/API credentials, NOT public data
            "sensitive_account_data": True,
            "consistency": "per-database; not a cross-database transaction",
            "files": manifest_files,
        }
        (tmp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        fd, raw_partial = tempfile.mkstemp(prefix=".nobi-backup-", dir=output.parent)
        partial = Path(raw_partial)
        try:
            with os.fdopen(fd, "w+b") as file:
                with zipfile.ZipFile(file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for name in ("manifest.json", *files):
                        archive.write(tmp / name, name)
                file.flush()
                os.fsync(file.fileno())
            os.replace(partial, output)
        finally:
            partial.unlink(missing_ok=True)
    return output


def _safe_member(archive: zipfile.ZipFile, name: str, destination: Path) -> Path:
    if name not in archive.namelist():
        raise ValueError(f"backup is missing {name}")
    target = (destination / name).resolve()
    if target.parent != destination.resolve():
        raise ValueError("unsafe archive member")
    with archive.open(name) as source, target.open("wb") as sink:
        shutil.copyfileobj(source, sink)
    return target


def restore_backup(
    archive_path: Path,
    journal: Path = DEFAULT_JOURNAL,
    auth: Path = DEFAULT_AUTH,
    live: Path = DEFAULT_LIVE,
    *,
    force: bool = False,
    offline: bool = False,
) -> None:
    if not force:
        raise ValueError("restore is destructive; pass --force")
    if not offline:
        raise ValueError("restore requires stopped service and all writers; pass --offline")
    with tempfile.TemporaryDirectory(prefix="nobi-restore-") as raw_tmp:
        tmp = Path(raw_tmp)
        with zipfile.ZipFile(archive_path, "r") as archive:
            manifest_path = _safe_member(archive, "manifest.json", tmp)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            archive_format = manifest.get("format")
            if archive_format not in (1, FORMAT_VERSION) or manifest.get("product") != "nobitrader":
                raise ValueError("unsupported backup format")
            names = ["journal.db", "auth.db"]
            if archive_format == FORMAT_VERSION:
                names.append("live_execution.db")
            extracted: dict[str, Path] = {}
            for name in names:
                path = _safe_member(archive, name, tmp)
                expected = manifest.get("files", {}).get(name, {}).get("sha256")
                if not expected or not secrets_compare(_sha256(path), str(expected)):
                    raise ValueError(f"checksum mismatch for {name}")
                if path.stat().st_size == 0:
                    raise ValueError(f"empty archived database: {name}")
                with contextlib.closing(sqlite3.connect(
                    path.resolve().as_uri() + "?mode=ro", uri=True,
                )) as conn:
                    result = conn.execute("PRAGMA integrity_check").fetchone()
                    if not result or result[0] != "ok":
                        raise ValueError(f"database integrity check failed for {name}")
                extracted[name] = path
        destinations = {"journal.db": journal, "auth.db": auth, "live_execution.db": live}
        targets = [destinations[name].resolve() for name in extracted]
        if len(set(targets)) != len(targets):
            raise ValueError("restore destinations must be distinct")
        if archive_path.resolve() in targets:
            raise ValueError("archive cannot also be a restore destination")
        for target in targets:
            if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
                raise ValueError("SQLite sidecars exist; stop/checkpoint all writers before offline restore")
        # Stage ALL replacements and undo copies before replacing the first DB.
        # Roll back ordinary failures. This is not crash-atomic across files;
        # --offline is an operator attestation, never a proof that writers stopped.
        stages: list[tuple[Path, Path, Path | None]] = []
        directories: list[Path] = []
        changed: list[tuple[Path, Path | None]] = []
        cleanup = True
        try:
            for name, destination in zip(extracted, targets, strict=True):
                destination.parent.mkdir(parents=True, exist_ok=True)
                directory = Path(tempfile.mkdtemp(prefix=".nobi-restore-", dir=destination.parent))
                directories.append(directory)
                staged = directory / "replacement.db"
                shutil.copy2(extracted[name], staged)
                staged.chmod(0o600)
                previous = directory / "previous.db" if destination.exists() else None
                if previous is not None:
                    shutil.copy2(destination, previous)
                    previous.chmod(0o600)
                recovery = directory / "recovery.json"
                recovery.write_text(json.dumps({
                    "destination": str(destination), "previous_exists": previous is not None,
                    "previous_sha256": _sha256(previous) if previous is not None else None,
                }), encoding="utf-8")
                recovery.chmod(0o600)
                stages.append((destination, staged, previous))
            for destination, staged, previous in stages:
                os.replace(staged, destination)
                changed.append((destination, previous))
        except BaseException:
            try:
                for destination, previous in reversed(changed):
                    if previous is None:
                        destination.unlink(missing_ok=True)
                    else:
                        os.replace(previous, destination)
            except OSError as exc:
                cleanup = False
                raise RuntimeError("restore rollback incomplete; private recovery copies retained") from exc
            raise
        finally:
            if cleanup:
                for directory in directories:
                    shutil.rmtree(directory)


def secrets_compare(left: str, right: str) -> bool:
    """Constant-time comparison without making the archive checksum a secret."""
    import secrets

    return secrets.compare_digest(left, right)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--output", type=Path, default=Path("backups"))
    backup.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    backup.add_argument("--auth", type=Path, default=DEFAULT_AUTH)
    backup.add_argument("--live", type=Path, default=DEFAULT_LIVE)
    restore = sub.add_parser("restore")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--journal", type=Path, required=True)
    restore.add_argument("--auth", type=Path, required=True)
    restore.add_argument("--live", type=Path, required=True)
    restore.add_argument("--force", action="store_true")
    restore.add_argument("--offline", action="store_true",
                         help="attest that the service and ALL database writers are stopped")
    args = parser.parse_args()
    if args.command == "backup":
        print(create_backup(args.output, args.journal, args.auth, args.live))
    else:
        restore_backup(args.archive, args.journal, args.auth, args.live, force=args.force, offline=args.offline)
        print("restore complete; restart nobitrader before accepting traffic")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
