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
    live = tmp_path / "live.db"
    _database(journal, "journal-before")
    _database(auth, "auth-before")
    _database(live, "live-before")

    archive = sb.create_backup(tmp_path / "backup.zip", journal, auth, live)
    with zipfile.ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {
            "manifest.json", "journal.db", "auth.db", "live_execution.db"
        }
        manifest = json.loads(zipped.read("manifest.json"))
        assert manifest["contains_secrets"] is False
        assert manifest["format"] == 2

    journal.unlink()
    auth.unlink()
    live.unlink()
    sb.restore_backup(archive, journal, auth, live, force=True)
    assert _value(journal) == "journal-before"
    assert _value(auth) == "auth-before"
    assert _value(live) == "live-before"


def test_restore_is_explicitly_destructive(tmp_path):
    journal = tmp_path / "journal.db"
    auth = tmp_path / "auth.db"
    live = tmp_path / "live.db"
    _database(journal, "j")
    _database(auth, "a")
    _database(live, "l")
    archive = sb.create_backup(tmp_path / "backup.zip", journal, auth, live)
    try:
        sb.restore_backup(archive, journal, auth, live)
    except ValueError as exc:
        assert "--force" in str(exc)
    else:
        raise AssertionError("restore without --force must be refused")


def test_tampered_database_is_refused_before_destination_changes(tmp_path):
    journal = tmp_path / "journal.db"
    auth = tmp_path / "auth.db"
    live = tmp_path / "live.db"
    _database(journal, "original-j")
    _database(auth, "original-a")
    _database(live, "original-l")
    archive = sb.create_backup(tmp_path / "backup.zip", journal, auth, live)

    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(broken, "w") as destination:
        for name in source.namelist():
            payload = source.read(name)
            destination.writestr(name, b"tampered" if name == "journal.db" else payload)

    try:
        sb.restore_backup(broken, journal, auth, live, force=True)
    except ValueError as exc:
        assert "checksum mismatch" in str(exc)
    else:
        raise AssertionError("tampered backup must be refused")
    assert _value(journal) == "original-j"
    assert _value(auth) == "original-a"
    assert _value(live) == "original-l"


def test_legacy_format_one_leaves_live_database_untouched(tmp_path):
    journal = tmp_path / "journal.db"
    auth = tmp_path / "auth.db"
    live = tmp_path / "live.db"
    _database(journal, "old-j")
    _database(auth, "old-a")
    _database(live, "keep-live")
    modern = sb.create_backup(tmp_path / "modern.zip", journal, auth, live)
    legacy = tmp_path / "legacy.zip"
    with zipfile.ZipFile(modern) as source, zipfile.ZipFile(legacy, "w") as target:
        manifest = json.loads(source.read("manifest.json"))
        manifest["format"] = 1
        manifest["files"].pop("live_execution.db")
        target.writestr("manifest.json", json.dumps(manifest))
        target.writestr("journal.db", source.read("journal.db"))
        target.writestr("auth.db", source.read("auth.db"))
    with sqlite3.connect(journal) as conn:
        conn.execute("UPDATE sample SET value='changed-j'")
    sb.restore_backup(legacy, journal, auth, live, force=True)
    assert _value(journal) == "old-j"
    assert _value(live) == "keep-live"
