# mypy: check_untyped_defs = False
"""Deployment checks must probe real anonymous reads and real config sources."""
from __future__ import annotations

import io
import json
import os
import urllib.error

from test_check_deploy import check_deploy


class _Response:
    def __init__(self, code: int, payload: bytes):
        self.status = code
        self._payload = payload

    def read(self, limit: int = -1) -> bytes:
        return self._payload if limit < 0 else self._payload[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_stub(monkeypatch, responses: dict[str, tuple[int, bytes]]):
    calls = []

    def open_url(request, timeout):
        del timeout
        url = request.full_url
        calls.append(url)
        path = url.split("capoy.ir", 1)[1]
        code, payload = responses[path]
        if code >= 400:
            raise urllib.error.HTTPError(url, code, "refused", {}, io.BytesIO(payload))
        return _Response(code, payload)

    monkeypatch.setattr(check_deploy.urllib.request, "urlopen", open_url)
    return calls


def _health(accounts: bool) -> bytes:
    return json.dumps({"ok": True, "auth": {"accounts": accounts}}).encode()


def test_anonymous_sensitive_gets_are_probed_and_health_stays_public(monkeypatch):
    responses = {"/api/health": (200, _health(True))}
    responses.update(dict.fromkeys(
        ("/api/paper/positions", "/api/paper/metrics", "/api/risk/state"),
        (401, b"{}"),
    ))
    calls = _http_stub(monkeypatch, responses)
    row = check_deploy.check_reads("https://capoy.ir")
    assert row["status"] == "ok"
    assert "/api/health=200 (public)" in row["detail"]
    assert len(calls) == 4


def test_large_production_health_payload_is_not_truncated(monkeypatch):
    health = json.dumps({
        "ok": True,
        "auth": {"accounts": 4},
        "symbols": {f"SYMBOL{i}": {"fresh": True} for i in range(600)},
    }).encode()
    assert len(health) > 4096
    responses = {"/api/health": (200, health)}
    responses.update(dict.fromkeys(
        ("/api/paper/positions", "/api/paper/metrics", "/api/risk/state"),
        (403, b"{}"),
    ))
    _http_stub(monkeypatch, responses)
    assert check_deploy.check_reads("https://capoy.ir")["status"] == "ok"


def test_anonymous_account_data_is_a_failure_not_a_generic_skip(monkeypatch):
    _http_stub(monkeypatch, {
        "/api/health": (200, _health(True)),
        "/api/paper/positions": (200, b'{"positions":[]}'),
    })
    row = check_deploy.check_reads("https://capoy.ir")
    assert row["status"] == "fail"
    assert "account data is exposed" in row["detail"]


def test_an_unclaimed_install_is_explicitly_not_called_hardened(monkeypatch):
    _http_stub(monkeypatch, {
        "/api/health": (200, _health(False)),
        "/api/paper/positions": (200, b'{"positions":[]}'),
    })
    row = check_deploy.check_reads("https://capoy.ir")
    assert row["status"] == "skip"
    assert "no account has been claimed" in row["detail"]


def test_edge_authentication_can_protect_the_whole_site(monkeypatch):
    calls = _http_stub(monkeypatch, {"/api/health": (401, b"login")})
    row = check_deploy.check_reads("https://capoy.ir")
    assert row["status"] == "ok"
    assert len(calls) == 1


def test_an_unexpected_sensitive_get_status_cannot_pass(monkeypatch):
    _http_stub(monkeypatch, {
        "/api/health": (200, _health(True)),
        "/api/paper/positions": (404, b"not found"),
    })
    assert check_deploy.check_reads("https://capoy.ir")["status"] == "fail"


def test_unit_environment_is_a_complete_config_without_server_dot_env(tmp_path, monkeypatch):
    server = tmp_path / "server"
    server.mkdir()
    unit = tmp_path / "nobi.env"
    unit.write_text("NBX_API_TOKEN=0123456789abcdef0123456789abcdef\n", encoding="utf-8")
    unit.chmod(0o640)
    monkeypatch.setattr(check_deploy, "SERVER", server)
    monkeypatch.setattr(check_deploy, "UNIT_ENV", unit)
    _clear_nbx_environment(monkeypatch)
    row = check_deploy.check_config()
    assert row["status"] == "ok"
    assert str(unit) in row["detail"]
    assert "1 keys" in row["detail"]


def test_missing_both_config_files_still_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(check_deploy, "SERVER", tmp_path / "server")
    monkeypatch.setattr(check_deploy, "UNIT_ENV", tmp_path / "missing.env")
    assert check_deploy.check_config()["status"] == "fail"


def test_world_readable_unit_secrets_fail(tmp_path, monkeypatch):
    server = tmp_path / "server"
    server.mkdir()
    unit = tmp_path / "nobi.env"
    unit.write_text("NBX_API_TOKEN=0123456789abcdef0123456789abcdef\n", encoding="utf-8")
    unit.chmod(0o644)
    monkeypatch.setattr(check_deploy, "SERVER", server)
    monkeypatch.setattr(check_deploy, "UNIT_ENV", unit)
    row = check_deploy.check_config()
    assert row["status"] == "fail"
    assert "other users can read" in row["detail"]


def _deny_unit_stat(unit, monkeypatch):
    """Reproduce root-only /etc/nobi/nobi.env deterministically, even as root."""
    from pathlib import Path

    real_stat = Path.stat

    def stat_with_denied_unit(path, *args, **kwargs):
        if path == unit:
            raise PermissionError(13, "Permission denied", str(unit))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_with_denied_unit)


def _clear_nbx_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith("NBX_") and key != "NBX_UNIT_ENV":
            monkeypatch.delenv(key, raising=False)


def test_verify_uses_supplied_values_when_unit_stat_is_denied(tmp_path, monkeypatch):
    """Exact production 2.3.1 traceback: check_config crashed before _env()."""
    server = tmp_path / "server"
    server.mkdir()
    unit = tmp_path / "nobi.env"
    unit.write_text("NBX_API_TOKEN=0123456789abcdef0123456789abcdef\n", encoding="utf-8")
    monkeypatch.setattr(check_deploy, "SERVER", server)
    monkeypatch.setattr(check_deploy, "UNIT_ENV", unit)
    _clear_nbx_environment(monkeypatch)
    monkeypatch.setenv("NBX_API_TOKEN", "0123456789abcdef0123456789abcdef")
    _deny_unit_stat(unit, monkeypatch)

    row = check_deploy.check_config()  # must not raise PermissionError
    assert row["status"] == "ok"
    assert "1 keys" in row["detail"]
    assert "mode unreadable by this user" in row["detail"]


def test_unreadable_unit_without_supplied_values_skips_instead_of_crashing(
        tmp_path, monkeypatch):
    server = tmp_path / "server"
    server.mkdir()
    unit = tmp_path / "nobi.env"
    unit.write_text("NBX_API_TOKEN=0123456789abcdef0123456789abcdef\n", encoding="utf-8")
    monkeypatch.setattr(check_deploy, "SERVER", server)
    monkeypatch.setattr(check_deploy, "UNIT_ENV", unit)
    _clear_nbx_environment(monkeypatch)
    _deny_unit_stat(unit, monkeypatch)

    row = check_deploy.check_config()
    assert row["status"] == "skip"
    assert "unreadable by this user" in row["detail"]
