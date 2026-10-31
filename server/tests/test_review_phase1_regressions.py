# mypy: check_untyped_defs = False
"""Local regression tests for reviewed pages; no real server/account/helper.

Run with the isolated offline runner when auditing an existing checkout.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import state_backup as sb
from test_install_api import _owner_client

import _env
import install_api
import install_wizard
import main
import operations_api
import runtime_settings as rs


@pytest.mark.parametrize("control", ["\n", "\r", "\x00", "\t", "\v", "\x85", "\u2028", "\u2029"])
def test_path_control_rejected_before_any_directory_creation(tmp_path, monkeypatch, control):
    monkeypatch.setattr(install_api, "MANAGED_ENV", tmp_path / "managed.env")
    destination = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="control character"):
        install_api.validate({
            "NBX_CANDLE_CACHE": str(destination),
            "NBX_AUTH_DB": str(tmp_path / "auth.db") + control + "NBX_SYNTHETIC_FLAG=1",
        }, create_dirs=True)
    assert not destination.exists()
    assert not install_api.MANAGED_ENV.exists()


@pytest.mark.parametrize("suffix", ["name # comment", "apostrophe'", 'double"quote', "back\\slash", "فارسی نام"])
def test_managed_roundtrip_preserves_literal_path(tmp_path, monkeypatch, suffix):
    monkeypatch.setattr(install_api, "MANAGED_ENV", tmp_path / "managed.env")
    values = {"NBX_AUTH_DB": str(tmp_path / suffix / "auth.db"), "NBX_SCAN_LOOP_S": "0"}
    install_api.write_managed(values)
    assert install_api.read_managed() == values
    assert _env._parse(install_api.MANAGED_ENV.read_text(encoding="utf-8")) == values  # legacy CLI parser contract
    if os.name != "nt":
        assert install_api.MANAGED_ENV.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("content", [
    "NBX_SCAN_LOOP_S=900\nNBX_SYNTHETIC_FLAG=1\n",
    "NBX_SCAN_LOOP_S=900\nNBX_LIVE_MODE=live\n",
    "NBX_SCAN_LOOP_S=900\nNBX_SCAN_LOOP_S=0\n",
    'NBX_AUTH_DB="/tmp/fixture\\nNBX_SYNTHETIC_FLAG=1"\n',
])
def test_managed_loader_rejects_entire_invalid_file_without_partial_env(tmp_path, monkeypatch, content):
    path = tmp_path / "managed.env"
    path.write_text(content, encoding="utf-8")
    monkeypatch.delenv("NBX_SCAN_LOOP_S", raising=False)
    monkeypatch.delenv("NBX_SYNTHETIC_FLAG", raising=False)
    status = _env.load_managed(path)
    assert status["loaded"] is False and status["error"]
    assert "NBX_SCAN_LOOP_S" not in os.environ
    assert "NBX_SYNTHETIC_FLAG" not in os.environ


def test_managed_never_overrides_nonempty_process_env(tmp_path, monkeypatch):
    path = tmp_path / "managed.env"
    path.write_text("NBX_GATE_PREFETCH=20\n", encoding="utf-8")
    monkeypatch.setenv("NBX_GATE_PREFETCH", "10")
    status = _env.load_managed(path)
    assert status["loaded"] and status["vars"] == 0
    assert os.environ["NBX_GATE_PREFETCH"] == "10"


def test_probe_checks_existing_parent_without_mkdir(tmp_path, monkeypatch):
    client, csrf = _owner_client(monkeypatch, tmp_path)
    directory = tmp_path / "not-created" / "child"
    response = client.post("/api/install/probe", json={"NBX_CANDLE_CACHE": str(directory)},
                           headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json()["created_directories"] is False
    assert not directory.exists()
    assert not install_api.MANAGED_ENV.exists()


def test_effective_settings_and_backup_ignore_draft_paths(tmp_path, monkeypatch):
    import auth
    import journal
    import live_api

    client, _csrf = _owner_client(monkeypatch, tmp_path)
    install_api.write_managed({"NBX_JOURNAL_DB": str(tmp_path / "draft/journal.db")})
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "runtime-journal.db")
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "runtime-auth.db")
    monkeypatch.setattr(main, "EXPORT_DIR", tmp_path / "runtime-exports")
    monkeypatch.setattr(live_api.CLIENT.ledger, "path", tmp_path / "runtime-live.db")
    # Restore the fake login DB for the authenticated request; other runtime paths remain patched.
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    response = client.get("/api/install/settings")
    assert response.status_code == 200
    field = response.json()["fields"]["NBX_JOURNAL_DB"]
    assert field["effective"] == str(tmp_path / "runtime-journal.db")
    assert field["managed"] == str(tmp_path / "draft/journal.db")
    assert field["pending_or_overridden"] is True
    paths = operations_api._paths()
    assert paths == (tmp_path / "runtime-exports/backups", tmp_path / "runtime-journal.db",
                     tmp_path / "auth.db", tmp_path / "runtime-live.db")


def _db(path, value):
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE sample(value TEXT)")
        conn.execute("INSERT INTO sample VALUES(?)", (value,))
        conn.commit()


def _read(path):
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT value FROM sample").fetchone()[0]


def _backup_fixture(tmp_path):
    paths = [tmp_path / name for name in ("journal.db", "auth.db", "live.db")]
    for path in paths:
        _db(path, "before-" + path.name)
    archive = sb.create_backup(tmp_path / "backup.zip", *paths)
    return paths, archive


def test_missing_source_fails_without_empty_archive(tmp_path):
    with pytest.raises(ValueError, match="missing or empty"):
        sb.create_backup(tmp_path / "backup.zip", *(tmp_path / n for n in ("j", "a", "l")))
    assert not (tmp_path / "backup.zip").exists()
    assert not list(tmp_path.iterdir())


def test_empty_source_also_fails(tmp_path):
    paths = [tmp_path / name for name in ("j", "a", "l")]
    for path in paths:
        path.touch()
    with pytest.raises(ValueError, match="missing or empty"):
        sb.create_backup(tmp_path / "backup.zip", *paths)


def test_backup_private_and_never_overwrites_existing_archive(tmp_path):
    paths, archive = _backup_fixture(tmp_path)
    before = archive.read_bytes()
    if os.name != "nt":
        assert archive.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        sb.create_backup(archive, *paths)
    assert archive.read_bytes() == before


def test_restore_requires_offline_attestation(tmp_path):
    paths, archive = _backup_fixture(tmp_path)
    with pytest.raises(ValueError, match="--offline"):
        sb.restore_backup(archive, *paths, force=True)


def test_restore_refuses_wal_before_any_replacement(tmp_path):
    paths, archive = _backup_fixture(tmp_path)
    before = {path: path.read_bytes() for path in paths}
    sidecar = Path(str(paths[1]) + "-wal")
    sidecar.write_bytes(b"synthetic sidecar only")
    with pytest.raises(ValueError, match="sidecars"):
        sb.restore_backup(archive, *paths, force=True, offline=True)
    assert all(path.read_bytes() == content for path, content in before.items())


def test_mid_restore_failure_rolls_back_all_changed_destinations(tmp_path, monkeypatch):
    paths, archive = _backup_fixture(tmp_path)
    for path in paths:
        with contextlib.closing(sqlite3.connect(path)) as conn:
            conn.execute("UPDATE sample SET value=?", ("current-" + path.name,))
            conn.commit()
    original_replace = os.replace
    failed = False

    def fail_second(src, dst):
        nonlocal failed
        if Path(src).name == "replacement.db" and Path(dst) == paths[1] and not failed:
            failed = True
            raise OSError("synthetic replacement failure")
        return original_replace(src, dst)

    monkeypatch.setattr(sb.os, "replace", fail_second)
    with pytest.raises(OSError, match="synthetic"):
        sb.restore_backup(archive, *paths, force=True, offline=True)
    assert failed
    assert [_read(path) for path in paths] == ["current-" + path.name for path in paths]
    assert not list(tmp_path.glob(".nobi-restore-*"))


def test_online_restore_is_blocked_even_with_exact_confirmation(tmp_path, monkeypatch):
    client, csrf = _owner_client(monkeypatch, tmp_path)
    monkeypatch.setattr(sb, "restore_backup", lambda *a, **kw: pytest.fail("online restore reached storage"))
    name = "nobitrader-state-20261029T120000Z.zip"
    response = client.post("/api/operations/restore", json={
        "archive": name, "confirmation": "RESTORE " + name}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "online-restore-disabled"


def test_wizard_initial_db_failure_releases_lock(monkeypatch):
    def failed():
        raise RuntimeError("synthetic connection failure")
    monkeypatch.setattr(install_wizard.journal, "connect", failed)
    monkeypatch.setattr(install_wizard.subprocess, "run", lambda *a, **kw: pytest.fail("helper must not run"))
    with pytest.raises(RuntimeError, match="synthetic"):
        install_wizard.run_action("install-service", "fixture")
    assert not install_wizard._MUTATION_LOCK.locked()


def test_wizard_step_record_failure_releases_lock(monkeypatch, tmp_path):
    import journal

    original = journal.connect
    monkeypatch.setattr(install_wizard.journal, "connect", lambda: original(tmp_path / "wizard.db"))
    monkeypatch.setattr(install_wizard, "_set", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("synthetic step failure")))
    monkeypatch.setattr(install_wizard.subprocess, "run", lambda *a, **kw: pytest.fail("helper must not run"))
    with pytest.raises(RuntimeError, match="synthetic step"):
        install_wizard.run_action("install-service", "fixture")
    assert not install_wizard._MUTATION_LOCK.locked()


def test_top60_never_authorizes_void_and_empty_authority_cannot_void(monkeypatch):
    monkeypatch.setattr(main.signal_journal, "connect", lambda: pytest.fail("untrusted VOID must not open storage"))
    assert main.journal_void() == 0
    assert main.journal_void(["BTCIRT"]) == 0
    assert main.journal_void([], authoritative=True) == 0


def test_mode_load_retries_after_failure_and_marks_only_success(monkeypatch):
    calls = []
    conn = SimpleNamespace(close=lambda: None)

    def connect():
        calls.append("connect")
        if len(calls) == 1:
            raise RuntimeError("synthetic first failure")
        return conn

    monkeypatch.setattr(main, "_EXEC_LOADED", [False])
    # The failed load logs a warning, and the error-log handler persists that
    # warning through the very `journal.connect` this test counts. Silence the
    # capture so `calls` records only `_exec_mode_load`'s own attempts — once
    # an earlier test has started the app lifespan the handler is installed and
    # the count would otherwise grow by one per logged warning.
    monkeypatch.setattr(main.errorlog, "capture", lambda **kwargs: None)
    monkeypatch.setattr(main.signal_journal, "connect", connect)
    monkeypatch.setattr(main.signal_journal, "get_meta", lambda *a: {"fixture": True})
    loaded = []
    monkeypatch.setattr(main, "EXEC_MODE", SimpleNamespace(load=loaded.append))
    monkeypatch.delenv("NBX_JOURNAL", raising=False)
    main._exec_mode_load()
    assert main._EXEC_LOADED == [False]
    main._exec_mode_load()
    main._exec_mode_load()
    assert main._EXEC_LOADED == [True] and len(calls) == 2 and loaded == [{"fixture": True}]


def test_default_scan_prefetch_match_runtime_without_starting_loop(monkeypatch):
    monkeypatch.delenv("NBX_SCAN_LOOP_S", raising=False)
    monkeypatch.delenv("NBX_GATE_PREFETCH", raising=False)
    assert rs.DEFAULTS["NBX_SCAN_LOOP_S"] == str(main.scan_loop_seconds()) == "0"
    assert rs.DEFAULTS["NBX_GATE_PREFETCH"] == str(main.gate_prefetch_n()) == "10"


def test_settings_ws_url_does_not_expose_credentials(monkeypatch):
    import settings_view

    monkeypatch.setattr(settings_view, "WS_URL", "wss://fixture:SYNTHETIC_ONLY@fixture.invalid/token?key=SYNTHETIC_ONLY")
    value = next(f["value"] for f in settings_view.flags() if f["name"] == "NBX_WS_URL")
    assert value == "configured (URL hidden)"


def test_effective_view_never_imports_missing_application_modules(monkeypatch):
    monkeypatch.delitem(sys.modules, "live_api", raising=False)
    assert rs.effective_values()["NBX_LIVE_MODE"] == "disabled"
    assert "live_api" not in sys.modules


def test_old_empty_database_archive_is_refused_before_any_replace(tmp_path):
    paths, archive = _backup_fixture(tmp_path)
    invalid = tmp_path / "old-empty.zip"
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(invalid, "w") as dst:
        manifest = json.loads(src.read("manifest.json"))
        manifest["files"]["journal.db"]["sha256"] = hashlib.sha256(b"").hexdigest()
        for name in src.namelist():
            payload = json.dumps(manifest).encode() if name == "manifest.json" else b"" if name == "journal.db" else src.read(name)
            dst.writestr(name, payload)
    before = [path.read_bytes() for path in paths]
    with pytest.raises(ValueError, match="empty archived database"):
        sb.restore_backup(invalid, *paths, force=True, offline=True)
    assert [path.read_bytes() for path in paths] == before


def test_restore_cli_refuses_implicit_destination_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["state_backup.py", "restore", str(tmp_path / "unused.zip"), "--force", "--offline"])
    with pytest.raises(SystemExit) as exc:
        sb.main()
    assert exc.value.code == 2
    assert not list(tmp_path.iterdir())


def test_explicit_authoritative_void_path_uses_complete_list_and_closes(monkeypatch):
    seen = []
    conn = SimpleNamespace(close=lambda: seen.append("closed"))
    monkeypatch.setattr(main.signal_journal, "connect", lambda: conn)
    monkeypatch.setattr(main.signal_journal, "mark_void", lambda c, symbols: seen.append(list(symbols)) or 1)
    assert main.journal_void(["BTCIRT", "ETHIRT"], authoritative=True) == 1
    assert seen == [["BTCIRT", "ETHIRT"], "closed"]


def test_failed_undo_retains_private_mapped_recovery_copies(tmp_path, monkeypatch):
    paths, archive = _backup_fixture(tmp_path)
    for path in paths:
        with contextlib.closing(sqlite3.connect(path)) as conn:
            conn.execute("UPDATE sample SET value=?", ("current-" + path.name,))
            conn.commit()
    original = os.replace

    def fail_replace_and_undo(src, dst):
        if Path(src).name == "replacement.db" and Path(dst) == paths[1]:
            raise OSError("synthetic second replace failure")
        if Path(src).name == "previous.db":
            raise OSError("synthetic undo failure")
        return original(src, dst)

    monkeypatch.setattr(sb.os, "replace", fail_replace_and_undo)
    with pytest.raises(RuntimeError, match="recovery copies retained"):
        sb.restore_backup(archive, *paths, force=True, offline=True)
    directories = list(tmp_path.glob(".nobi-restore-*"))
    assert len(directories) == 3
    mapped = {json.loads((d / "recovery.json").read_text(encoding="utf-8"))["destination"]: d for d in directories}
    previous = mapped[str(paths[0])] / "previous.db"
    assert _read(previous) == "current-journal.db"
    metadata = json.loads((previous.parent / "recovery.json").read_text(encoding="utf-8"))
    assert metadata["previous_sha256"] == hashlib.sha256(previous.read_bytes()).hexdigest()
    if os.name != "nt":
        assert previous.stat().st_mode & 0o777 == 0o600
        assert previous.parent.stat().st_mode & 0o777 == 0o700


def test_restore_destination_cannot_overwrite_source_archive(tmp_path):
    paths, archive = _backup_fixture(tmp_path)
    before = archive.read_bytes()
    with pytest.raises(ValueError, match="archive cannot"):
        sb.restore_backup(archive, archive, paths[1], paths[2], force=True, offline=True)
    assert archive.read_bytes() == before


def test_partial_save_preserves_untouched_managed_settings(tmp_path, monkeypatch):
    client, csrf = _owner_client(monkeypatch, tmp_path)
    original = {"NBX_CANDLE_CACHE": str(tmp_path / "persisted-cache"), "NBX_SCAN_LOOP_S": "900"}
    install_api.write_managed(original)
    response = client.post("/api/install/settings", json={"NBX_GATE_PREFETCH": "15"},
                           headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert install_api.read_managed() == {**original, "NBX_GATE_PREFETCH": "15"}
    assert not (tmp_path / "persisted-cache").exists()  # untouched draft was not provisioned


def test_online_restore_still_requires_owner_session_and_csrf(tmp_path, monkeypatch):
    client, csrf = _owner_client(monkeypatch, tmp_path)
    name = "nobitrader-state-20261029T120000Z.zip"
    payload = {"archive": name, "confirmation": "RESTORE " + name}
    assert client.post("/api/operations/restore", json=payload).status_code == 403
    client.cookies.clear()
    response = client.post("/api/operations/restore", json=payload, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 401
