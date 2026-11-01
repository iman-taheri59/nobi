"""`check_deploy.py` decides whether to say "the door is closed".

That decision is a single table — address -> meaning — and it is the only thing
standing between a user and a false "ok". It used to say "ok" for any address
that was not 0.0.0.0, which would have blessed an app listening on a *public*
IP. These tests pin the table down.

The classification is also the VPC case: nginx on a second ابرک reaches the app
on a 10.x address, and that is legitimate — but only if a proxy is configured
and the subnet is not reachable from the internet.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_deploy", ROOT / "scripts" / "check_deploy.py")
assert _spec and _spec.loader
check_deploy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_deploy)


@pytest.mark.parametrize(("addr", "kind"), [
    # the only correct answer
    ("127.0.0.1", "loopback"),
    ("127.0.0.53", "loopback"),
    ("::1", "loopback"),
    # every interface — includes the public one
    ("0.0.0.0", "wildcard"),
    ("*", "wildcard"),
    ("::", "wildcard"),
    ("", "wildcard"),
    # the VPC case: private, deliberate, not the internet
    ("10.0.0.5", "private"),
    ("10.255.1.9", "private"),
    ("172.16.4.2", "private"),
    ("172.31.255.1", "private"),
    ("192.168.1.10", "private"),
    # a public address of this very ابرک
    ("185.143.234.10", "public"),
    ("2a01:5ec0::1", "public"),
    # neither: link-local and garbage must not be called "private"
    ("169.254.0.21", "other"),
    ("not-an-ip", "other"),
])
def test_the_bind_table(addr: str, kind: str) -> None:
    assert check_deploy.classify_bind(addr) == kind


def test_bracketed_ipv6_is_unwrapped() -> None:
    """`ss` prints [::1]:8000; the classifier must not fall over on it."""
    assert check_deploy.classify_bind("[::1]") == "loopback"
    assert check_deploy.classify_bind("[2a01:5ec0::1]") == "public"


def test_link_local_is_not_private() -> None:
    """169.254 is technically `is_private` in the stdlib; it is not a VPC subnet.

    Calling it private would let a cloud metadata-style address pass as
    "deliberate"; it is a misconfiguration either way.
    """
    assert check_deploy.classify_bind("169.254.0.21") == "other"


# --------------------------------------------------------------------------- #
# Wiring: the `ss` table in, the verdict out. A live wildcard bind is not
# possible in this sandbox (the platform already owns :8000), so the socket
# table is fed in directly — the parsing is what can go wrong anyway.
# --------------------------------------------------------------------------- #
class _Sock:
    def __init__(self, text: str) -> None:
        self.stdout = text


_SS_HEADER = "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"


def _ss_run(monkeypatch: pytest.MonkeyPatch, *listeners: str) -> None:
    lines = "".join(
        f'LISTEN 0 2048 {addr} 0.0.0.0:* users:(("python",pid=42,fd=16))\n'
        for addr in listeners)
    monkeypatch.setattr(check_deploy.subprocess, "run",
                        lambda *a, **k: _Sock(_SS_HEADER + lines))


def test_wildcard_bind_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _ss_run(monkeypatch, "0.0.0.0:8000")
    row = check_deploy.check_bind()
    assert row["status"] == "fail"
    assert "reachable from the internet" in row["detail"]


def test_loopback_bind_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _ss_run(monkeypatch, "127.0.0.1:8000")
    assert check_deploy.check_bind()["status"] == "ok"


def test_private_bind_with_proxy_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """nginx on a second ابرک: the app on 10.x with a trusted proxy is correct."""
    _ss_run(monkeypatch, "10.0.0.5:8000")
    monkeypatch.setenv("NBX_TRUSTED_PROXIES", "10.0.0.4")
    assert check_deploy.check_bind()["status"] == "ok"


def test_private_bind_without_proxy_is_not_green(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reachable inside the VPC with no proxy configured is a question, not an ok."""
    _ss_run(monkeypatch, "10.0.0.5:8000")
    monkeypatch.delenv("NBX_TRUSTED_PROXIES", raising=False)
    row = check_deploy.check_bind()
    assert row["status"] == "skip"
    assert "VPC" in row["hint"]


def test_a_public_address_never_reads_as_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    _ss_run(monkeypatch, "185.143.234.10:8000")
    assert check_deploy.check_bind()["status"] == "fail"


def test_another_process_on_8000_is_not_called_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """Someone else's listener must not be reported as ours either way."""
    text = (_SS_HEADER +
            'LISTEN 0 2048 0.0.0.0:8000 0.0.0.0:* users:(("nginx",pid=7,fd=6))\n')
    monkeypatch.setattr(check_deploy.subprocess, "run", lambda *a, **k: _Sock(text))
    row = check_deploy.check_bind()
    assert row["status"] == "skip"
    assert "hides its pid" in row["detail"] or "not uvicorn" in row["detail"]


def test_the_python_floor_is_the_one_the_pins_can_install() -> None:
    """A preflight that blesses an interpreter `pip install` refuses is a lie.

    `numpy==2.5.4` publishes `Requires-Python >=3.12`, but this check used to
    accept 3.11 (the version `datetime.UTC` needs) and pass a machine where
    `pip install -r server/requirements.txt` fails before the app starts.
    """
    assert check_deploy.MIN_PYTHON == (3, 12)
    refused = check_deploy.check_python((3, 11, 9))
    assert refused["status"] == "fail"
    assert "3.12" in refused["hint"]
    assert check_deploy.check_python((3, 12, 3))["status"] == "ok"


# --------------------------------------------------------------------------- #
# Where the configuration is read from
# --------------------------------------------------------------------------- #
def test_the_unit_environment_file_is_read_not_just_server_dot_env(tmp_path, monkeypatch):
    """A correctly configured install must not be reported as misconfigured.

    Measured on a live host: `server/.env` held one key, the journal and the API
    token were set in `/etc/nobi/nobi.env` — the file `deploy/nobi.service`
    loads — and this checker read only the first. It answered

        [FAIL] storage   journal is inside the checkout (/opt/nobi/server/journal.db)
        [FAIL] token     NBX_API_TOKEN is empty

    while the journal was a 40 MB file on the attached volume and the app was
    healthy. `Path.resolve()` does not need the file to exist, so the "inside the
    checkout" verdict was about a *default path*, not about any file.
    """
    # `_cfg` puts an exported variable above the files, exactly as the app does
    # (`server/_env.py`: "a value exported in the shell outranks the file"), and
    # the suite's conftest points NBX_JOURNAL_DB at a scratch database. Clear it
    # so this test is about the files.
    for key in ("NBX_JOURNAL_DB", "NBX_AUTH_DB", "NBX_PAPER_STATE", "NBX_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(check_deploy, "SERVER", tmp_path / "server")
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / ".env").write_text(
        "NBX_TRUSTED_PROXIES=127.0.0.1\n", encoding="utf-8")
    unit_env = tmp_path / "nobi.env"
    unit_env.write_text(
        "NBX_JOURNAL_DB=/srv/nobi-data/journal.db\n"
        "NBX_AUTH_DB=/srv/nobi-data/auth.db\n"
        "NBX_PAPER_STATE=/srv/nobi-data/paper_state.json\n"
        "NBX_API_TOKEN=0123456789abcdef0123456789abcdef\n",
        encoding="utf-8")
    monkeypatch.setattr(check_deploy, "UNIT_ENV", unit_env)

    cfg = check_deploy._env()
    assert cfg["NBX_JOURNAL_DB"] == "/srv/nobi-data/journal.db"
    assert cfg["NBX_API_TOKEN"].startswith("0123")
    assert check_deploy._cfg("NBX_JOURNAL_DB") == "/srv/nobi-data/journal.db"


def test_the_unit_file_wins_over_server_dot_env(tmp_path, monkeypatch):
    """`deploy/nobi.service` says systemd's environment outranks `server/.env`.

    The same key in both files must resolve the way the running service resolves
    it, or the checker would bless a configuration the app does not have.
    """
    monkeypatch.delenv("NBX_JOURNAL_DB", raising=False)
    monkeypatch.setattr(check_deploy, "SERVER", tmp_path / "server")
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / ".env").write_text(
        "NBX_JOURNAL_DB=/opt/nobi/server/journal.db\n", encoding="utf-8")
    unit_env = tmp_path / "nobi.env"
    unit_env.write_text("NBX_JOURNAL_DB=/srv/nobi-data/journal.db\n", encoding="utf-8")
    monkeypatch.setattr(check_deploy, "UNIT_ENV", unit_env)
    assert check_deploy._cfg("NBX_JOURNAL_DB") == "/srv/nobi-data/journal.db"


def test_a_host_without_a_unit_file_still_checks_server_dot_env(tmp_path, monkeypatch):
    """The fallback is the file the app itself reads; a missing unit file is not
    a reason to report empty configuration."""
    monkeypatch.delenv("NBX_API_TOKEN", raising=False)
    monkeypatch.setattr(check_deploy, "SERVER", tmp_path / "server")
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / ".env").write_text(
        "NBX_API_TOKEN=abcdefghijklmnopqrstuvwxyz012345\n", encoding="utf-8")
    monkeypatch.setattr(check_deploy, "UNIT_ENV", tmp_path / "does-not-exist.env")
    assert check_deploy._cfg("NBX_API_TOKEN").startswith("abcdef")


@pytest.mark.skipif(
    os.name != "posix",
    reason="chmod(0o000) does not make a file unreadable on Windows, so the case "
           "cannot be set up there — the guard itself is platform-independent",
)
def test_an_unreadable_unit_file_is_reported_not_fatal(tmp_path, monkeypatch):
    """A checker that dies on a file it cannot read reports nothing at all.

    The unit's EnvironmentFile is root-owned. `deploy_arvan.sh` sets it 0640
    root:nobi, but an install that predates that — or one hardened to 0600 — leaves
    the service user unable to read it, and `--verify` runs the checker as that
    user. The first version of this fix crashed there with a PermissionError
    traceback instead of a verdict, which is the worse of the two answers.
    """
    monkeypatch.setattr(check_deploy, "SERVER", tmp_path / "server")
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / ".env").write_text(
        "NBX_API_TOKEN=abcdefghijklmnopqrstuvwxyz012345\n", encoding="utf-8")
    unit_env = tmp_path / "nobi.env"
    unit_env.write_text("NBX_JOURNAL_DB=/srv/nobi-data/journal.db\n", encoding="utf-8")
    unit_env.chmod(0o000)
    monkeypatch.setattr(check_deploy, "UNIT_ENV", unit_env)
    monkeypatch.delenv("NBX_API_TOKEN", raising=False)
    monkeypatch.delenv("NBX_JOURNAL_DB", raising=False)

    cfg = check_deploy._env()          # must not raise
    assert cfg.get("NBX_API_TOKEN", "").startswith("abcdef"), cfg
    assert "NBX_JOURNAL_DB" not in cfg, cfg
    assert check_deploy._unreadable, "the unreadable file must be named, not hidden"
    unit_env.chmod(0o644)


def test_the_unreadable_note_is_suppressed_when_the_values_were_passed(monkeypatch):
    """`--verify` hands the NBX_* values over in the environment.

    When it does, the unit file being unreadable *to that user* is not a gap —
    nothing was missing, and saying so is noise on an otherwise green preflight.
    `NBX_UNIT_ENV` is the path override for that same file, so it must not count
    as "the values were supplied": it starts with NBX_ and would silence the note
    on every host.
    """
    # the suite's conftest points six NBX_* variables at a scratch database, and
    # any one of them counts as "supplied" — so clear them all, not a chosen few.
    for key in [k for k in os.environ if k.startswith("NBX_") and k != "NBX_UNIT_ENV"]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(check_deploy, "_unreadable",
                        ["/etc/nobi/nobi.env: PermissionError"])

    # values handed over by --verify: no note
    monkeypatch.setenv("NBX_JOURNAL_DB", "/srv/nobi-data/journal.db")
    assert check_deploy._unreadable_note() == []

    # and without them the note is the honest answer
    monkeypatch.delenv("NBX_JOURNAL_DB", raising=False)
    note = check_deploy._unreadable_note()
    assert note and "unreadable by this user" in note[0]
    assert any("/etc/nobi/nobi.env" in line for line in note)
