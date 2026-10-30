"""Private fixtures for the scoped audit read."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

import auth
import journal
import main


@pytest.fixture
def operator(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    user = auth.create_user("phase13-operator", "A-strong-operator-password!", role="operator")
    token, csrf, _ = auth.create_session(user, ip="testclient", ua="pytest")
    client = TestClient(main.app)
    client.cookies.set("nbx_session", token)
    client.cookies.set("nbx_csrf", csrf)
    yield client
    auth.reset_caches()


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    path = tmp_path / "journal.db"
    monkeypatch.setattr(journal, "DB_PATH", path)
    with journal.connect() as conn:
        journal.audit_record(conn, "paper.halt", detail={"flat": False})
        for _ in range(30):
            journal.audit_record(conn, "risk.whatif")
        journal.audit_record(conn, "unknown.future.action")
    return path


def test_scoped_filter_count_and_legacy(audit_db, operator):
    c = operator
    legacy = c.get("/api/audit?limit=2").json()
    assert set(legacy) == {"events", "counts", "n"}
    r = c.get("/api/audit?scope=control&limit=1").json()
    assert r["ok"] and r["scope"] == "control" and r["total"] == 2
    assert len(r["events"]) == 1 and r["events"][0]["action"] == "unknown.future.action"
    p = c.get("/api/audit?scope=preview&limit=25").json()
    assert p["total"] == 30 and len(p["events"]) == 25
    assert c.get("/api/audit?scope=invalid").status_code == 422


def test_scoped_missing_database_is_unknown_not_created(tmp_path, monkeypatch, operator):
    path = tmp_path / "absent.db"
    monkeypatch.setattr(journal, "DB_PATH", path)
    r = operator.get("/api/audit?scope=all").json()
    assert r["ok"] is False and r["events"] is None and r["total"] is None
    assert not path.exists()


def test_scoped_read_authorizer_blocks_writes(audit_db):
    conn = journal.connect_readonly()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM audit_log")
        assert journal.audit_total(conn, scope="preview") == 30
    finally:
        conn.close()


def test_scoped_and_notify_require_operator(audit_db, monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "roles.db")
    auth.reset_caches()
    viewer = auth.create_user("phase13-viewer", "A-strong-viewer-password!", role="viewer")
    token, csrf, _ = auth.create_session(viewer, ip="testclient", ua="pytest")
    client = TestClient(main.app)
    client.cookies.set("nbx_session", token)
    client.cookies.set("nbx_csrf", csrf)
    try:
        assert client.get("/api/audit?scope=all").status_code == 403
        assert client.get("/api/notify/status").status_code == 403
    finally:
        auth.reset_caches()


def test_action_filter_takes_precedence(audit_db, operator):
    data = operator.get("/api/audit?scope=control&action=risk.whatif&limit=2").json()
    assert data["ok"] and data["total"] == 30
    assert [event["action"] for event in data["events"]] == ["risk.whatif"] * 2
