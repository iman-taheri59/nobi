"""Owner backup/restore and privileged-helper boundary tests."""
from __future__ import annotations

import contextlib
import os
import sqlite3

from test_install_api import _owner_client

import operations_api


def _configured(monkeypatch, tmp_path):
    client, csrf = _owner_client(monkeypatch, tmp_path)
    monkeypatch.setattr(operations_api, "AUDIT", tmp_path / "audit.jsonl")
    monkeypatch.setattr(operations_api, "_paths", lambda: (
        tmp_path / "backups", tmp_path / "journal.db", tmp_path / "auth-state.db",
        tmp_path / "live.db"))
    for name in ("journal.db", "auth-state.db", "live.db"):
        with contextlib.closing(sqlite3.connect(tmp_path / name)) as conn:
            conn.execute("CREATE TABLE fixture(value TEXT)")
            conn.commit()
    return client, csrf


def test_owner_backup_and_online_restore_blocked(monkeypatch, tmp_path):
    client, csrf = _configured(monkeypatch, tmp_path)
    headers = {"X-CSRF-Token": csrf}
    with client:
        made = client.post("/api/operations/backup", headers=headers)
        name = made.json()["archive"]
        bad = client.post("/api/operations/restore", json={"archive": name, "confirmation": "yes"}, headers=headers)
        restored = client.post("/api/operations/restore", json={"archive": name, "confirmation": f"RESTORE {name}"}, headers=headers)
    assert made.status_code == 200
    assert bad.status_code == 422
    assert restored.status_code == 409
    assert restored.json()["detail"]["error"] == "online-restore-disabled"
    if os.name != "nt":
        assert operations_api.AUDIT.stat().st_mode & 0o777 == 0o600


def test_restore_rejects_traversal_and_extra_arguments(monkeypatch, tmp_path):
    client, csrf = _configured(monkeypatch, tmp_path)
    with client:
        traversal = client.post("/api/operations/restore", json={
            "archive": "../state.zip", "confirmation": "RESTORE ../state.zip"},
            headers={"X-CSRF-Token": csrf})
        # apply-config is retired (domain now comes from a root-owned file).
        extra = client.post("/api/operations/apply-config", json={
            "domain": "example.com", "command": "id"}, headers={"X-CSRF-Token": csrf})
        oversized = client.post("/api/operations/restart", json={
            "confirmation": "RESTART", "command": "id"}, headers={"X-CSRF-Token": csrf})
    assert traversal.status_code == 422
    assert extra.status_code == 410
    assert extra.json()["detail"]["where"] == "/api/install/wizard/action"
    assert oversized.status_code == 422


def test_root_helper_reads_only_root_owned_templates():
    """Templates must not come from the service-writable checkout."""
    source = (operations_api.ROOT / "deploy/nobi-privileged-helper").read_text(encoding="utf-8")
    assert 'SOURCE = Path("/usr/local/share/nobi-installer")' in source
    assert 'SOURCE / "nobi.service"' in source
    assert "APP_ROOT" not in source
    assert "shell=True" not in source
    assert 'st.st_uid != 0' in source and 'st.st_mode & 0o022' in source
    assert not (operations_api.ROOT / "scripts/nobi_privileged_helper.py").exists()


def test_operations_helper_uses_protocol_one(monkeypatch, tmp_path):
    """The operations card and the wizard must call the very same argv."""
    import subprocess

    helper = tmp_path / "client"
    helper.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(operations_api, "HELPER", str(helper))
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr(operations_api.subprocess, "run", fake_run)
    result = operations_api._helper("restart-service")
    assert result["ok"] is True
    assert seen == [[str(helper), "--protocol", "1", "restart-service"]]


def test_restart_requires_exact_confirmation_and_configured_helper(monkeypatch, tmp_path):
    client, csrf = _configured(monkeypatch, tmp_path)
    monkeypatch.setattr(operations_api, "HELPER", "")
    with client:
        bad = client.post("/api/operations/restart", json={"confirmation": "restart"}, headers={"X-CSRF-Token": csrf})
        unavailable = client.post("/api/operations/restart", json={"confirmation": "RESTART"}, headers={"X-CSRF-Token": csrf})
    assert bad.status_code == 422
    assert unavailable.status_code == 503
