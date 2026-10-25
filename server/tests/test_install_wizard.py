"""Browser installation state machine and privileged-helper boundary."""
from __future__ import annotations

from fastapi.testclient import TestClient

import auth
import install_wizard
import journal
import main


def _client(monkeypatch, tmp_path, role="owner"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "journal.db")
    auth.reset_caches()
    user = auth.create_user(f"wizard-{role}", "A-strong-install-password!", role=role)
    token, csrf, _ = auth.create_session(user, ip="testclient", ua="pytest")
    client = TestClient(main.app)
    client.cookies.set("nbx_session", token)
    client.cookies.set("nbx_csrf", csrf)
    return client, csrf


def test_state_is_resumable_and_owner_only(monkeypatch, tmp_path):
    client, csrf = _client(monkeypatch, tmp_path)
    with client:
        first = client.get("/api/install/wizard/status")
        preflight = client.post("/api/install/wizard/preflight", headers={"X-CSRF-Token": csrf})
        second = client.get("/api/install/wizard/status")
    assert first.status_code == preflight.status_code == second.status_code == 200
    assert [row["step"] for row in first.json()["steps"]] == list(install_wizard.STEPS)
    state = {row["step"]: row["state"] for row in second.json()["steps"]}
    assert state["host-preflight"] in {"passed", "blocked"}
    viewer, _ = _client(monkeypatch, tmp_path / "viewer", role="viewer")
    with viewer:
        assert viewer.get("/api/install/wizard/status").status_code == 403


def test_mutations_require_csrf_confirmation_and_allowlist(monkeypatch, tmp_path):
    client, csrf = _client(monkeypatch, tmp_path)
    with client:
        assert client.post("/api/install/wizard/action", json={"action": "install-service"}).status_code == 403
        assert client.post("/api/install/wizard/action", json={"action": "rm-all", "confirm": True},
                           headers={"X-CSRF-Token": csrf}).status_code == 422
        assert client.post("/api/install/wizard/action", json={"action": "restart-service"},
                           headers={"X-CSRF-Token": csrf}).status_code == 409


def test_helper_argv_is_fixed_output_redacted_and_state_persists(monkeypatch, tmp_path):
    client, csrf = _client(monkeypatch, tmp_path)
    helper = tmp_path / "helper"
    helper.write_text("#!/bin/sh\necho 'token=never-return-this'\necho ok\n", encoding="utf-8")
    helper.chmod(0o755)
    monkeypatch.setattr(install_wizard, "HELPER", helper)
    with client:
        response = client.post("/api/install/wizard/action", json={"action": "install-service"},
                               headers={"X-CSRF-Token": csrf})
        status = client.get("/api/install/wizard/status")
    assert response.status_code == 200 and response.json()["state"] == "passed"
    assert "never-return-this" not in response.json()["output"]
    assert "[redacted]" in response.json()["output"]
    step = {row["step"]: row for row in status.json()["steps"]}["service-install"]
    assert step["state"] == "passed"


def test_sudo_policy_has_no_wildcards_or_shell():
    policy = (install_wizard.Path(__file__).resolve().parents[2] / "deploy" / "nobi-helper.sudoers").read_text(encoding="utf-8")
    assert "*" not in policy and "/bin/sh" not in policy
    for action in install_wizard.ACTIONS:
        assert action in policy
