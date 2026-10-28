# mypy: check_untyped_defs = False
"""Activation's side effects are limited, backed up and rollback-tested."""

from __future__ import annotations

import json
import os
import sqlite3

import pytest
from scripts import background_scan as tool

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux systemd activation tool")


def test_only_scan_keys_change_in_environment_file():
    text = 'NBX_API_TOKEN="test-only-not-a-real-token"\nNBX_SCAN_LOOP_S=0\nNBX_SCAN_LOOP_S=900\nNBX_LIVE_MODE=disabled\n'
    new = tool.updated_env(text)
    assert new.count("NBX_SCAN_LOOP_S=") == 1
    before, after = tool.parse_env(text), tool.parse_env(new)
    assert {k: v for k, v in before.items() if k not in tool.TARGET} == {
        k: v for k, v in after.items() if k not in tool.TARGET
    }
    assert after["NBX_SCAN_LOOP_S"] == "300"
    assert after["NBX_SCAN_LOOP_TF"] == "15m"


@pytest.fixture()
def installation(tmp_path, monkeypatch):
    env = tmp_path / "nobi.env"
    env.write_text("NBX_SCAN_LOOP_S=0\nNBX_LIVE_MODE=disabled\n")
    env.chmod(0o600)
    monkeypatch.setattr(tool, "ENV", env)
    monkeypatch.setattr(tool, "PRIVATE_DIR", tmp_path)
    monkeypatch.setattr(tool, "run", lambda *args: str(os.getpid()))
    events = []
    monkeypatch.setattr(tool.subprocess, "run", lambda args, **kw: events.append(args))
    before_mode = {"default": "semi", "symbols": {}}
    monkeypatch.setattr(tool, "safe_mode", lambda c: before_mode)
    monkeypatch.setattr(
        tool,
        "snapshot",
        lambda c: {"journal": {"available": True}, "scan_loop": {"enabled": False}},
    )
    monkeypatch.setattr(
        tool,
        "effective_config",
        lambda: {"NBX_SCAN_LOOP_S": "300", "NBX_SCAN_LOOP_TF": "15m", "NBX_LIVE_MODE": "disabled"},
    )
    monkeypatch.setattr(
        tool,
        "wait_health",
        lambda c: {"ok": True, "scan_loop": {"enabled": True, "interval_s": 300}},
    )
    return env, events


def test_apply_backs_up_only_changes_two_keys_and_never_starts_formal_test(installation):
    env, events = installation
    original = env.read_bytes()
    data = tool.apply({"NBX_LIVE_MODE": "disabled"})
    assert data["formal_five_day_test_started"] is False
    assert tool.Path(data["environment_backup"]).read_bytes() == original
    assert tool.Path(data["environment_backup"]).stat().st_mode & 0o777 == 0o600
    assert events == [["systemctl", "restart", "nobi"]]
    assert tool.parse_env(env.read_text(encoding="utf-8"))["NBX_LIVE_MODE"] == "disabled"


def test_failed_health_rolls_back_exact_environment(installation, monkeypatch):
    env, events = installation
    original = env.read_bytes()

    def fail(c):
        raise RuntimeError("health failure")

    monkeypatch.setattr(tool, "wait_health", fail)
    with pytest.raises(RuntimeError):
        tool.apply({"NBX_LIVE_MODE": "disabled"})
    assert env.read_bytes() == original
    assert len(events) == 2


def test_auto_and_live_modes_fail_closed_before_changes(monkeypatch):
    with pytest.raises(RuntimeError):
        tool.safe_mode({"NBX_LIVE_MODE": "live"})
    monkeypatch.setattr(
        tool, "get_json", lambda *args: {"default": "semi", "symbols": {"BTCIRT": "auto"}}
    )
    with pytest.raises(RuntimeError):
        tool.safe_mode({})


def test_journal_observation_is_read_only_and_aggregated(tmp_path):
    path = tmp_path / "journal.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE scan_cycles (timeframe TEXT,status TEXT,completed_at INTEGER); CREATE TABLE signal_log(timeframe TEXT,origin TEXT,ts INTEGER);"
        )
        db.execute("INSERT INTO scan_cycles VALUES ('15m','completed',123)")
        db.execute("INSERT INTO signal_log VALUES ('15m','live',100)")
    before = path.read_bytes()
    data = tool.journal_counts({"NBX_JOURNAL_DB": str(path)})
    assert data["completed_cycles"] == 1 and data["live_signal_rows"] == 1
    assert path.read_bytes() == before
    assert "symbol" not in json.dumps(data)


def test_non_linux_invocation_refuses_before_posix_calls(monkeypatch, capsys):
    with monkeypatch.context() as patch:
        patch.setattr(tool.sys, "platform", "win32")
        patch.setattr(tool.sys, "argv", ["background_scan.py", "--check"])
        patch.delattr(tool.os, "geteuid", raising=False)
        with pytest.raises(SystemExit) as caught:
            tool.main()
    assert caught.value.code == 2
    assert "Linux/systemd" in capsys.readouterr().err
