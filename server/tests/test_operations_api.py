"""Owner backup/restore and privileged-helper boundary tests."""
from __future__ import annotations

import os

from test_install_api import _owner_client

import operations_api


def _configured(monkeypatch, tmp_path):
    client, csrf = _owner_client(monkeypatch, tmp_path)
    monkeypatch.setattr(operations_api, "AUDIT", tmp_path / "audit.jsonl")
    monkeypatch.setattr(operations_api, "_paths", lambda: (
        tmp_path / "backups", tmp_path / "journal.db", tmp_path / "auth-state.db",
        tmp_path / "live.db"))
    return client, csrf


def test_owner_backup_and_confirmed_restore(monkeypatch, tmp_path):
    client, csrf = _configured(monkeypatch, tmp_path)
    headers = {"X-CSRF-Token": csrf}
    with client:
        made = client.post("/api/operations/backup", headers=headers)
        name = made.json()["archive"]
        bad = client.post("/api/operations/restore", json={"archive": name, "confirmation": "yes"}, headers=headers)
        restored = client.post("/api/operations/restore", json={"archive": name, "confirmation": f"RESTORE {name}"}, headers=headers)
    assert made.status_code == 200
    assert bad.status_code == 422
    assert restored.status_code == 200
    assert restored.json()["restart_required"] is True
    if os.name != "nt":
        assert operations_api.AUDIT.stat().st_mode & 0o777 == 0o600


def test_restore_rejects_traversal_and_extra_arguments(monkeypatch, tmp_path):
    client, csrf = _configured(monkeypatch, tmp_path)
    with client:
        traversal = client.post("/api/operations/restore", json={
            "archive": "../state.zip", "confirmation": "RESTORE ../state.zip"},
            headers={"X-CSRF-Token": csrf})
        extra = client.post("/api/operations/apply-config", json={
            "domain": "example.com", "command": "id"}, headers={"X-CSRF-Token": csrf})
    assert traversal.status_code == extra.status_code == 422


def test_root_helper_reads_only_root_owned_templates():
    source = (operations_api.ROOT / "scripts/nobi_privileged_helper.py").read_text(encoding="utf-8")
    assert 'TEMPLATE_ROOT = Path("/usr/local/share/nobi-installer")' in source
    assert 'TEMPLATE_ROOT / "nobi.service"' in source
    assert 'APP_ROOT / "deploy' not in source
    assert "shell=True" not in source


def test_restart_requires_exact_confirmation_and_configured_helper(monkeypatch, tmp_path):
    client, csrf = _configured(monkeypatch, tmp_path)
    monkeypatch.setattr(operations_api, "HELPER", "")
    with client:
        bad = client.post("/api/operations/restart", json={"confirmation": "restart"}, headers={"X-CSRF-Token": csrf})
        unavailable = client.post("/api/operations/restart", json={"confirmation": "RESTART"}, headers={"X-CSRF-Token": csrf})
    assert bad.status_code == 422
    assert unavailable.status_code == 503
