"""The installer's privileged boundary: one protocol, no shell, no sudo."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("nobi_installer_helper", DEPLOY / "nobi-installer-helper.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_handle_rejects_anything_outside_the_allowlist(monkeypatch) -> None:
    daemon = _load_daemon()
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    calls: list[list[str]] = []
    monkeypatch.setattr(daemon.subprocess, "run", lambda argv, **kw: calls.append(argv))

    assert daemon.handle(b"not json")["error"] == "invalid JSON"
    assert daemon.handle(b'{"protocol":2,"action":"install-service"}')["error"] == "protocol mismatch"
    assert daemon.handle(b'{"protocol":1,"action":"rm-all"}')["error"].startswith("unsupported")
    assert daemon.handle(b'{"protocol":1,"action":"install-service","domain":"evil"}')["error"].startswith("unsupported")
    assert calls == [], "a rejected request must never reach the CLI helper"


def test_handle_forwards_only_fixed_argv(monkeypatch) -> None:
    daemon = _load_daemon()
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr(daemon.subprocess, "run", fake_run)
    reply = daemon.handle(json.dumps({"protocol": 1, "action": "restart-service"}).encode())
    assert reply["ok"] is True
    # A Path() here would render as "\usr\..." on Windows and change argv.
    assert isinstance(daemon.CLI, str), "CLI must be a plain string, not a Path"
    assert seen == [[sys.executable, "/usr/local/libexec/nobi-privileged-helper",
                     "--protocol", "1", "restart-service"]]


@pytest.mark.skipif(os.name != "posix" or not hasattr(socket, "AF_UNIX"),
                    reason="Unix domain sockets are POSIX-only")
def test_client_and_daemon_round_trip_over_a_socket(tmp_path: Path) -> None:
    path = tmp_path / "helper.sock"
    daemon = subprocess.Popen(
        [sys.executable, str(DEPLOY / "nobi-installer-helper.py"), "--socket", str(path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(40):
            if path.exists():
                break
            time.sleep(0.05)
        assert path.exists(), "daemon did not create its socket"
        env = {**os.environ, "NBX_HELPER_SOCKET": str(path)}
        ok = subprocess.run(
            [sys.executable, str(DEPLOY / "nobi-helper-client"), "--protocol", "1", "check"],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=20, check=False,
        )
        # We are not root in CI, so the daemon reports the refusal; the point is
        # that a well-formed JSON reply travels over the socket without sudo.
        payload = json.loads(ok.stdout.strip())
        assert payload["ok"] is False
        assert "root" in payload["error"]
        bad = subprocess.run(
            [sys.executable, str(DEPLOY / "nobi-helper-client"), "--protocol", "1", "rm-all"],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=20, check=False,
        )
        assert bad.returncode == 2
    finally:
        daemon.terminate()
        daemon.wait(timeout=10)


def test_root_templates_are_outside_the_checkout() -> None:
    helper = (DEPLOY / "nobi-privileged-helper").read_text(encoding="utf-8")
    assert 'SOURCE = Path("/usr/local/share/nobi-installer")' in helper
    assert "def _validate_source" in helper
    assert "st.st_uid != 0" in helper
    assert "st.st_mode & 0o022" in helper
    assert 'DOMAIN_FILE = Path("/etc/nobi/domain")' in helper
    assert "shell=True" not in helper


def test_client_rejects_wildcard_arguments() -> None:
    bad = subprocess.run(
        [sys.executable, str(DEPLOY / "nobi-helper-client"), "--protocol", "1", "check; id"],
        capture_output=True, text=True, encoding="utf-8", timeout=10, check=False,
    )
    assert bad.returncode == 2


def _load_cli_helper():
    spec = importlib.util.spec_from_file_location(
        "nobi_privileged_helper", DEPLOY / "nobi-privileged-helper",
        loader=importlib.machinery.SourceFileLoader("nobi_privileged_helper",
                                                    str(DEPLOY / "nobi-privileged-helper")),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lab(monkeypatch, tmp_path: Path):
    """Two managed targets under tmp_path, with systemctl/nginx stubbed out."""
    cli = _load_cli_helper()
    src = tmp_path / "templates"
    src.mkdir()
    targets = {}
    for name in ("nobi.service", "nobi.conf"):
        template = src / name
        template.write_text(f"# fresh {name}\n", encoding="utf-8")
        targets[name] = tmp_path / "etc" / name
    targets["nobi.service"].parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli, "BACKUPS", tmp_path / "backups")
    monkeypatch.setattr(cli, "TARGETS", {
        "install-service": (src / "nobi.service", targets["nobi.service"]),
        "install-proxy": (src / "nobi.conf", targets["nobi.conf"]),
    })
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    monkeypatch.setattr(cli.os, "geteuid", lambda: 0, raising=False)
    return cli, src, targets


def test_first_install_creates_absent_markers_and_rollback_undoes_everything(monkeypatch, tmp_path: Path):
    """Regression: rollback used to fail with 'no rollback backup exists'."""
    cli, src, targets = _lab(monkeypatch, tmp_path)
    for name, target in targets.items():
        assert not target.exists()
        cli.atomic_install(src / name if name == "nobi.service" else src / "nobi.conf", target)
        assert target.exists()
    assert (cli.BACKUPS / "nobi.service.absent").is_file()
    assert (cli.BACKUPS / "nobi.conf.absent").is_file()
    cli.rollback()
    assert not targets["nobi.service"].exists()
    assert not targets["nobi.conf"].exists()
    assert not (cli.BACKUPS / "nobi.service.absent").exists()


def test_second_install_restores_previous_content(monkeypatch, tmp_path: Path):
    cli, src, targets = _lab(monkeypatch, tmp_path)
    unit = targets["nobi.service"]
    cli.atomic_install(src / "nobi.service", unit)
    unit.write_text("# operator-edited\n", encoding="utf-8")
    cli.atomic_install(src / "nobi.service", unit)   # now a .previous exists
    assert (cli.BACKUPS / "nobi.service.previous").is_file()
    assert not (cli.BACKUPS / "nobi.service.absent").exists()
    cli.rollback()
    assert unit.read_text(encoding="utf-8") == "# operator-edited\n"


def test_rollback_refuses_when_nothing_was_installed(monkeypatch, tmp_path: Path):
    cli, _src, _targets = _lab(monkeypatch, tmp_path)
    import pytest

    with pytest.raises(RuntimeError, match="no rollback state"):
        cli.rollback()


def test_daemon_does_not_label_success_stderr_as_error(monkeypatch) -> None:
    daemon = _load_daemon()
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(daemon.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, stdout='{"ok":true}', stderr="Created symlink ... -> ..."))
    reply = daemon.handle(json.dumps({"protocol": 1, "action": "install-service"}).encode())
    assert reply["ok"] is True and reply["error"] is None
    assert "Created symlink" in reply["stderr"]


def test_daemon_surfaces_check_failures(monkeypatch) -> None:
    daemon = _load_daemon()
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    check_reply = json.dumps({
        "ok": False, "protocol": 1, "actions": ["check"],
        "install-service": "ready",
        "install-proxy": "blocked: domain file does not contain a valid DNS name",
        "domain": "domain file does not contain a valid DNS name",
    })
    monkeypatch.setattr(daemon.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 1, stdout=check_reply, stderr=""))
    reply = daemon.handle(json.dumps({"protocol": 1, "action": "check"}).encode())
    assert reply["ok"] is False
    assert "domain file" in reply["error"]
