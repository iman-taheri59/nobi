"""The soak snapshot records aggregates and never writes on failed reads."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import live_evidence_snapshot as snapshot  # noqa: E402


def test_collect_and_append_are_secret_free(monkeypatch, tmp_path):
    token = "super-secret-token"

    def fake(url, presented="", timeout=20.0):
        assert presented == token
        if url.endswith("/health"):
            return {"version": "1.2.7", "scan_loop": {"enabled": True, "runs": 12},
                    "price_alerts": {"enabled_rules": 2, "runs": 40, "last_ok": False,
                                     "deliveries": {"sent": 3, "failed": 1, "pending": 1},
                                     "oldest_pending_at": 1700000000}}
        if url.endswith("/status"):
            return {"mode": "dry-run", "configured": False}
        return {
            "ok": True, "samples": 31, "days": 5, "ambiguous": 0,
            "ready": True, "applied": False,
            "states": {"dry-run": 31}, "reasons": [],
        }

    monkeypatch.setattr(snapshot, "get_json", fake)
    row = snapshot.collect("http://example", token)
    path = tmp_path / "evidence.csv"
    snapshot.append_row(path, row)
    content = path.read_text(encoding="utf-8")
    assert token not in content
    rows = list(csv.DictReader(content.splitlines()))
    assert len(rows) == 1
    assert rows[0]["samples"] == "31"
    assert rows[0]["ready"] == "True"
    # Soak columns: scheduler liveness and the durable delivery split, so a
    # stuck alert delivery is in the daily CSV without opening the bell.
    assert rows[0]["scan_runs"] == "12"
    assert rows[0]["alert_rules"] == "2" and rows[0]["alert_last_ok"] == "False"
    assert rows[0]["alert_sent"] == "3" and rows[0]["alert_failed"] == "1"
    assert rows[0]["alert_pending"] == "1"


def test_collect_tolerates_older_health_without_alert_block(monkeypatch, tmp_path):
    def fake(url, presented="", timeout=20.0):
        if url.endswith("/health"):
            return {"version": "1.10.4"}
        if url.endswith("/status"):
            return {"mode": "dry-run", "configured": False}
        return {"ok": True, "samples": 1, "days": 1, "ambiguous": 0,
                "ready": False, "applied": False, "states": {}, "reasons": []}

    monkeypatch.setattr(snapshot, "get_json", fake)
    row = snapshot.collect("http://example")
    assert row["scan_runs"] == "" and row["alert_rules"] == "" and row["alert_pending"] == ""


def test_failed_collect_does_not_create_csv(monkeypatch, tmp_path):
    path = tmp_path / "evidence.csv"

    def failed(*_args, **_kwargs):
        raise RuntimeError("snapshot request failed: TimeoutError")

    monkeypatch.setattr(snapshot, "get_json", failed)
    assert snapshot.main(["--url", "http://bad", "--csv", str(path)]) == 1
    assert not path.exists()


def test_snapshot_uses_a_real_operator_session_and_logs_out(monkeypatch, tmp_path):
    """The live routes refuse a bare API token; the tool must log in for real."""
    import io
    import sqlite3
    import urllib.error
    from email.message import Message

    from fastapi.testclient import TestClient

    import auth
    import main

    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    auth.create_user("soak-operator", "A-strong-snapshot-password!", role="operator")
    auth.create_user("soak-viewer", "A-strong-snapshot-password!", role="viewer")
    client = TestClient(main.app)

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    def bridged_open(request, timeout=0):
        url = request.full_url
        path = url[url.index("/api"):]
        if request.data:
            reply = client.post(path, content=request.data,
                                headers={"Content-Type": "application/json"})
        else:
            reply = client.get(path, headers=dict(request.header_items()))
        if reply.status_code >= 400:
            raise urllib.error.HTTPError(url, reply.status_code, "refused", Message(), None)
        return Reply(reply.content)

    monkeypatch.setattr(snapshot._OPENER, "open", bridged_open)
    csv_path = tmp_path / "evidence.csv"

    # No credentials: the live routes are operator-only, and the message says so.
    monkeypatch.delenv("NBX_SNAPSHOT_USERNAME", raising=False)
    monkeypatch.delenv("NBX_SNAPSHOT_PASSWORD", raising=False)
    monkeypatch.setenv("NBX_API_TOKEN", "a-token-that-is-not-a-session")
    assert snapshot.main(["--url", "http://testserver", "--csv", str(csv_path)]) == 1
    assert not csv_path.exists()

    # Viewer: the role boundary is kept, nothing is written.
    monkeypatch.setenv("NBX_SNAPSHOT_USERNAME", "soak-viewer")
    monkeypatch.setenv("NBX_SNAPSHOT_PASSWORD", "A-strong-snapshot-password!")
    assert snapshot.main(["--url", "http://testserver", "--csv", str(csv_path)]) == 1
    assert not csv_path.exists()

    # Operator: one row, no credential in it, and no session left behind.
    monkeypatch.setenv("NBX_SNAPSHOT_USERNAME", "soak-operator")
    assert snapshot.main(["--url", "http://testserver", "--csv", str(csv_path)]) == 0
    content = csv_path.read_text(encoding="utf-8")
    assert "A-strong-snapshot-password!" not in content and "a-token" not in content
    row = next(iter(csv.DictReader(content.splitlines())))
    assert row["version"] == main.app.version and row["mode"] != ""
    assert row["alert_pending"] in {"0", ""}
    assert client.get("/api/live/status").status_code == 401, "logout must end the session"
    with sqlite3.connect(tmp_path / "auth.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_secure_session_cookie_over_plain_http_is_explained_not_401():
    """Seen on the production host: login over http://127.0.0.1 succeeded, every
    read then returned 401, because the cookie carried `Secure`. The tool must
    name the cause and point at the HTTPS entry point."""
    import http.cookiejar

    jar = http.cookiejar.CookieJar()
    jar.set_cookie(http.cookiejar.Cookie(
        0, "nbx_session", "x", None, False, "127.0.0.1", False, False, "/", True,
        True, None, False, None, None, {},
    ))
    assert snapshot.secure_cookie_mismatch("http://127.0.0.1:8000", jar) is not None
    assert "https://" in snapshot.secure_cookie_mismatch("http://127.0.0.1:8000", jar)
    assert snapshot.secure_cookie_mismatch("https://capoy.example", jar) is None
    plain = http.cookiejar.CookieJar()
    plain.set_cookie(http.cookiejar.Cookie(
        0, "nbx_session", "x", None, False, "127.0.0.1", False, False, "/", True,
        False, None, False, None, None, {},
    ))
    assert snapshot.secure_cookie_mismatch("http://127.0.0.1:8000", plain) is None
