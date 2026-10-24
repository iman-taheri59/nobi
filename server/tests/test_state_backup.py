"""Backup/restore is part of the release contract, not an operator guess."""
from __future__ import annotations

import json
import sqlite3
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import state_backup as sb  # noqa: E402


def _database(path: Path, value: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        conn.execute("INSERT INTO sample VALUES (?)", (value,))


def _value(path: Path) -> str:
    with sqlite3.connect(path) as conn:
        return str(conn.execute("SELECT value FROM sample").fetchone()[0])


def test_backup_round_trip_is_complete_and_excludes_environment(tmp_path):
    journal = tmp_path / "journal.db"
    auth = tmp_path / "auth.db"
    _database(journal, "journal-before")
    _database(auth, "auth-before")

    archive = sb.create_backup(tmp_path / "backup.zip", journal, auth)
    with zipfile.ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {"manifest.json", "journal.db", "auth.db"}
        manifest = json.loads(zipped.read("manifest.json"))
        assert manifest["contains_secrets"] is False
        assert manifest["format"] == 1

    journal.unlink()
    auth.unlink()
    sb.restore_backup(archive, journal, auth, force=True)
    assert _value(journal) == "journal-before"
    assert _value(auth) == "auth-before"


def test_restore_is_explicitly_destructive(tmp_path):
    journal = tmp_path / "journal.db"
    auth = tmp_path / "auth.db"
    _database(journal, "j")
    _database(auth, "a")
    archive = sb.create_backup(tmp_path / "backup.zip", journal, auth)
    try:
        sb.restore_backup(archive, journal, auth)
    except ValueError as exc:
        assert "--force" in str(exc)
    else:
        raise AssertionError("restore without --force must be refused")


def test_tampered_database_is_refused_before_destination_changes(tmp_path):
    journal = tmp_path / "journal.db"
    auth = tmp_path / "auth.db"
    _database(journal, "original-j")
    _database(auth, "original-a")
    archive = sb.create_backup(tmp_path / "backup.zip", journal, auth)

    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(broken, "w") as destination:
        for name in source.namelist():
            payload = source.read(name)
            destination.writestr(name, b"tampered" if name == "journal.db" else payload)

    try:
        sb.restore_backup(broken, journal, auth, force=True)
    except ValueError as exc:
        assert "checksum mismatch" in str(exc)
    else:
        raise AssertionError("tampered backup must be refused")
    assert _value(journal) == "original-j"
    assert _value(auth) == "original-a"
