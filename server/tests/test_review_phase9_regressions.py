# mypy: check_untyped_defs = False
"""Isolated runner only: private SQLite, model bars, in-process HTTP; no providers."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import journal as J
import main
from ta_engine import candles_to_df


def seed(c, ts, *, direction="LONG", source="synthetic", origin="live"):
    J.insert_rows(c, [J.JournalRow(ts=ts, symbol="XUSDT", timeframe="15m",
        direction=direction, ta_score=70, composite=72, entry=100, atr=1,
        features={"components": {"trend": 3, "momentum": 1}}, source=source, origin=origin)])
    return c.execute("SELECT id FROM signal_log WHERE ts=?", (ts,)).fetchone()["id"]


def outcome(c, sid, key, r=1):
    c.execute("INSERT INTO signal_outcomes(signal_id,horizon_key,r_multiple,return_pct,exit_ts) VALUES(?,?,?,?,?)",
              (sid, key, r, r, 123))
    c.commit()


@pytest.mark.parametrize("stage", ["train", "run", "watch", "retire"])
@pytest.mark.parametrize("status", ["healthy", "degraded", "corrupted", "insufficient"])
def test_stage_get_never_evaluates_or_advances(tmp_path, monkeypatch, stage, status):
    db = tmp_path / "private.db"
    c = J.connect(db)
    saved = {"stage": stage, "msi_status": status, "streak": 2, "changed_at": 1700000000}
    J.set_meta(c, "strategy_stage", saved)
    before = c.execute("SELECT * FROM journal_meta").fetchall()
    monkeypatch.setattr(J, "DB_PATH", db)
    def forbidden(*args, **kwargs):
        raise AssertionError("GET must not evaluate or initialize")
    monkeypatch.setattr(J, "lifecycle", forbidden)
    monkeypatch.setattr(J, "msi", forbidden)
    monkeypatch.setattr(J, "connect", forbidden)
    monkeypatch.setattr(J, "init", forbidden)
    for _ in range(4):
        r = asyncio.run(main.journal_stage())
        assert r["ok"] and r["read_only"] and r["evaluated"] is False
        assert r["stage"] == stage and r["streak"] == 2
        assert r["recorded_at"] == saved["changed_at"]
    assert [tuple(x) for x in c.execute("SELECT * FROM journal_meta")] == [tuple(x) for x in before]
    assert J.get_meta(c, "strategy_stage") == saved
    c.close()


def test_stage_missing_db_never_creates_parent(tmp_path, monkeypatch):
    db = tmp_path / "does-not-exist" / "private.db"
    monkeypatch.setattr(J, "DB_PATH", db)
    r = asyncio.run(main.journal_stage())
    assert r["ok"] is False and r["stage"] is None and r["read_only"]
    assert not db.parent.exists()


@pytest.mark.parametrize("value", [None, {}, [], "run", {"stage": "bogus"}, {"stage": None}])
def test_absent_or_invalid_stage_is_unknown_not_train(tmp_path, monkeypatch, value):
    db = tmp_path / "private.db"
    c = J.connect(db)
    if value is not None:
        J.set_meta(c, "strategy_stage", value)
    before = c.total_changes
    r = J.stage_snapshot(c)
    assert r["stage"] is None and not r["evaluated"]
    assert c.total_changes == before
    c.close()
    monkeypatch.setattr(J, "DB_PATH", db)
    assert asyncio.run(main.journal_stage())["stage"] is None


#: The point of these names is the read-only URI: `as_uri()` has to percent-encode
#: a space, a `?` (which would otherwise start a URI query) and a `#` (a fragment).
#: `?` is not a legal character in a Windows filename, so that case cannot even be
#: created there — the advisory Windows job failed with "unable to open database
#: file" on this test for exactly that reason, not because the URI was wrong.
@pytest.mark.parametrize("name", [
    "plain journal.db",
    "reserved # journal.db",
    pytest.param("reserved ? journal.db", marks=pytest.mark.skipif(
        os.name == "nt", reason="'?' cannot appear in a Windows filename")),
])
def test_readonly_connection_rejects_writes_and_skips_migrations(tmp_path, name):
    db = tmp_path / name
    c = J.connect(db)
    c.close()
    ro = J.connect_readonly(db)
    assert ro.execute("PRAGMA query_only").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        ro.execute("INSERT INTO journal_meta(key,value) VALUES('bad','true')")
    assert J.get_meta(ro, "bad") is None
    ro.close()


def test_stage_legacy_schema_is_unavailable_not_migrated(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE marker (value TEXT)")
    c.commit()
    c.close()
    original = db.read_bytes()
    monkeypatch.setattr(J, "DB_PATH", db)
    assert asyncio.run(main.journal_stage())["ok"] is False
    assert db.read_bytes() == original


def test_horizons_have_independent_recording_source_counts(tmp_path):
    c = J.connect(tmp_path / "private.db")
    a = seed(c, 1700000000)
    b = seed(c, 1700000900, source="live", origin="backfill")
    d = seed(c, 1700001800, source="cache")
    void = seed(c, 1700002700)
    c.execute("UPDATE signal_log SET voided_at=1 WHERE id=?", (void,))
    c.commit()
    for sid, key, val in [(a,"H1",1),(a,"H3",-1),(b,"H1",-1),(b,"H2",1),(d,"H1",1),(void,"H1",99)]:
        outcome(c, sid, key, val)
    before = c.total_changes
    r = J.summary(c)
    h = {x["k"]: x for x in r["horizons"]}
    assert h["H1"]["sources"] == {"synthetic": 1, "live": 1, "cache": 1}
    assert h["H2"]["sources"] == {"live": 1}
    assert h["H3"]["sources"] == {"synthetic": 1}
    for x in h.values():
        assert sum(x["sources"].values()) == x["n"]
    assert h["H1"]["hit"] == pytest.approx(2/3) and h["H1"]["avgr"] == pytest.approx(1/3)
    assert r["kpi"]["sig"] == 3 and r["kpi"]["void"] == 1 and r["kpi"]["res"] == 1
    f = J.summary(c, origin="live")
    assert {x["k"]:x for x in f["horizons"]}["H1"]["sources"] == {"synthetic":1,"cache":1}
    f = J.summary(c, source="synthetic")
    assert {x["k"]:x for x in f["horizons"]}["H2"]["n"] == 0
    assert r["resolved_sources"] == {"live":1}  # historical H2 field stays H2 only
    assert c.total_changes == before
    c.close()


@pytest.mark.parametrize("direction, view", [("LONG","long"),("EXIT","exit"),("NEUTRAL","neutral"),("UNKNOWN","unknown")])
@pytest.mark.parametrize("legacy_outcome", [False, True])
def test_direction_is_not_coerced_and_non_long_is_not_pending(tmp_path, direction, view, legacy_outcome):
    c = J.connect(tmp_path / "private.db")
    sid = seed(c, 1700000000, direction=direction)
    if legacy_outcome:
        outcome(c, sid, "H2")
    r = J.summary(c)
    v = r["signals"][0]
    assert v["dir"] == view
    if direction != "LONG":
        assert v["st"] == "not_applicable"
        assert all(v[k] is None for k in ("h1","h2","h3","r"))
        assert r["kpi"]["sig"] == r["kpi"]["res"] == 0
    else:
        assert v["st"] == ("resolved" if legacy_outcome else "pending")
    c.close()


def test_h1_only_never_implies_h2_or_empty_journal(tmp_path):
    c = J.connect(tmp_path / "private.db")
    sid = seed(c, 1700000000)
    outcome(c, sid, "H1")
    r = J.summary(c)
    assert r["kpi"]["sig"] == 1 and r["kpi"]["res"] == 0
    assert r["kpi"]["hit"] is None and r["families"] == [] and r["calibration"] == []
    assert r["signals"][0]["st"] == "inprogress"
    assert r["horizons"][0]["n"] == 1
    c.close()


def test_positive_only_pf_is_json_safe_over_http(tmp_path, monkeypatch):
    db = tmp_path / "private.db"
    c = J.connect(db)
    sid = seed(c, 1700000000)
    outcome(c, sid, "H2", 1)
    c.close()
    monkeypatch.setattr(J, "DB_PATH", db)
    # No context manager/lifespan: never starts background scan/account tasks.
    client = TestClient(main.app)
    response = client.get("/api/journal/summary?days=3650")
    assert response.status_code == 200
    r = response.json()
    assert r["stage_read_only"] is True
    assert r["vpf_state"] == "no_losses" and r["kpi"]["vpf"] is None
    assert r["kpi"]["res"] == 1 and r["kpi"]["hit"] == 1
    json.dumps(r, allow_nan=False)
    stage = client.get("/api/journal/stage").json()
    assert stage["ok"] and stage["stage"] is None and stage["read_only"]
    client.close()


@pytest.mark.parametrize("stop_bar", [1, 2, 4, None])
@pytest.mark.parametrize("ts_unit", ["ns", "us", "s"])
def test_exit_label_matches_actual_walked_candle_not_requested_horizon(stop_bar, ts_unit):
    bars = [{"t":1700000000+i*900,"o":100,"h":100.5,"l":99.5,"c":100,"v":1} for i in range(12)]
    if stop_bar:
        bars[stop_bar]["l"] = 97
    df = candles_to_df(bars)
    df["t"] = pd.DatetimeIndex(df["t"]).as_unit(ts_unit)
    out = J.simulate_long(df, 0, 100, 1, 8)
    assert out is not None
    used = stop_bar or 8
    assert out["exit_ts"] == bars[used]["t"] and out["bars_used"] == used
    assert out["exit_price"] == (98 if stop_bar else 100)
    assert out["r_multiple"] == (-1 if stop_bar else 0)
    assert out["hit_sl"] == bool(stop_bar) and out["timeout"] == (stop_bar is None)


def test_partial_tp_then_stop_labels_second_bar():
    bars = [{"t":1700000000+i*900,"o":100,"h":100.5,"l":99.5,"c":100,"v":1} for i in range(12)]
    bars[1]["h"], bars[2]["l"] = 103, 95
    out = J.simulate_long(candles_to_df(bars), 0, 100, 1, 8)
    assert out["hit_tp1"] and out["hit_sl"] and out["bars_used"] == 2
    assert out["exit_ts"] == bars[2]["t"]
    assert out["exit_price"] == pytest.approx(100*(1+J.TRAIL_R))
    assert out["r_multiple"] == pytest.approx(round(0.5*J.TP1_R + 0.5*(100*J.TRAIL_R)/2,4))


def test_resolver_does_not_rewrite_old_exit_labels(tmp_path):
    c = J.connect(tmp_path / "private.db")
    bars = [{"t":1700000000+i*900,"o":100,"h":100.5,"l":99.5,"c":100,"v":1} for i in range(12)]
    sid = seed(c, bars[0]["t"]+900)
    outcome(c, sid, "H1", -1)
    before = dict(c.execute("SELECT * FROM signal_outcomes WHERE signal_id=?",(sid,)).fetchone())
    J.resolve_symbol(c,"XUSDT","15m",candles_to_df(bars))
    assert dict(c.execute("SELECT * FROM signal_outcomes WHERE signal_id=?",(sid,)).fetchone()) == before
    c.close()
