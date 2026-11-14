"""The `C_gate` audit has to be able to say no.

ADR-0050 added a regime gate; ADR-0051 recorded a `C_gate` shadow arm next to
the live verdict. The arm existed, the averages were printed, and nobody could
answer the one question that mattered: *did the gate remove the rows it says it
removes?* Section 5 of the diagnostic cannot answer it, because a blocked row
produces no `A_live` row to compare against — the gate's whole effect is what
it deleted.

The blocker: `shadow_arms` knew only about `risk-off`, so in an `fx-shock`
regime the arm recorded "nothing blocks" while the live path was blocking. On a
`neutral` day that defect is invisible, which is why the claim stayed unproven.
The audit compares the recorded regime against the engine's own
`BLOCKING_STATES` instead, so it can be measured on any day's data.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "journal_diagnostic_gate", ROOT / "scripts" / "journal_diagnostic.py")
assert _spec is not None and _spec.loader is not None
jd = importlib.util.module_from_spec(_spec)
sys.modules["journal_diagnostic_gate"] = jd
_spec.loader.exec_module(jd)


def _db(tmp_path: Path) -> Path:
    """A journal with one LONG per row: (symbol, regime, c_gate_direction)."""
    import journal

    path = tmp_path / "journal.db"
    conn = sqlite3.connect(path)
    conn.executescript(journal.SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(signal_log)")}
    for col in ("voided_at", "void_reason"):  # added by _migrate, not by SCHEMA
        if col not in have:
            conn.execute(f"ALTER TABLE signal_log ADD COLUMN {col} TEXT")
    conn.commit()
    conn.close()
    return path


def _insert(path: Path, rows: list[tuple[str, str, str]]) -> None:
    conn = sqlite3.connect(path)
    base = int(time.time()) - len(rows) * 3600
    for i, (symbol, regime, c_gate) in enumerate(rows):
        feats = {"market_regime": regime, "shadow_arms": {
            "A_live": {"direction": "LONG"},
            "C_gate": {"direction": c_gate, "composite": 70.0},
        }}
        conn.execute(
            """INSERT INTO signal_log(ts,symbol,timeframe,direction,ta_score,fa_score,
                   liquidity_score,composite,rank_in_cycle,regime,entry,stop,tp1,tp2,atr,
                   features,source,origin,config_hash,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (base + i * 3600, symbol, "15m", "LONG", 70, 55, 50, 70.0, i, regime,
             100.0, 95.0, 106.0, 110.0, 1.0, json.dumps(feats), "scan", "engine",
             "h-test", base + i * 3600),
        )
    conn.commit()
    conn.close()


def _section(capsys, path: Path) -> str:
    argv = sys.argv
    sys.argv = ["journal_diagnostic.py", str(path)]
    try:
        jd.main()
    finally:
        sys.argv = argv
    out = capsys.readouterr().out
    body = out.split("3c) C_gate audit", 1)[1].split("\n4)", 1)[0]
    return body.replace("\n", " ")


def test_a_leak_in_a_blocking_state_is_reported_as_broken(tmp_path, capsys):
    """The pre-fix engine: `fx-shock` LONGs passed the gate. Must be flagged."""
    path = _db(tmp_path)
    _insert(path, [
        ("BTCUSDT", "risk-off", "NEUTRAL"),
        ("ETHUSDT", "fx-shock", "LONG"),   # the defect
        ("SOLUSDT", "fx-shock", "LONG"),   # the defect
        ("ADAUSDT", "neutral", "LONG"),
    ])
    body = _section(capsys, path)
    assert "C_gate said LONG inside a blocking state       2" in body
    assert "verdict: BROKEN" in body or "verdict: LEAKING" in body
    assert "fx-shock" in body and "risk-off" in body


def test_a_gate_that_holds_is_not_reported_as_broken(tmp_path, capsys):
    path = _db(tmp_path)
    _insert(path, [
        ("BTCUSDT", "risk-off", "NEUTRAL"),
        ("ETHUSDT", "fx-shock", "NEUTRAL"),
        ("ADAUSDT", "neutral", "LONG"),
        ("DOTUSDT", "risk-on", "LONG"),
    ])
    body = _section(capsys, path)
    assert "C_gate said LONG inside a blocking state       0" in body
    assert "verdict: CONSISTENT" in body


def test_an_all_neutral_window_says_no_evidence_instead_of_passing(tmp_path, capsys):
    """The failure mode of the old claim: a quiet day was read as a working gate."""
    path = _db(tmp_path)
    _insert(path, [
        ("BTCUSDT", "neutral", "LONG"),
        ("ETHUSDT", "neutral", "LONG"),
        ("ADAUSDT", "risk-on", "LONG"),
    ])
    body = _section(capsys, path)
    assert "rows a blocking state should have removed       0" in body
    assert "verdict: NO EVIDENCE" in body
    assert "Not the" not in body  # one sentence, not two fragments
    assert "not the same as 'it works'" in body.lower() or "Not the same as" in body


def test_a_second_filter_in_a_passing_state_is_named(tmp_path, capsys):
    """A refusal outside a blocking state is not the regime gate; say so."""
    path = _db(tmp_path)
    _insert(path, [
        ("BTCUSDT", "risk-off", "NEUTRAL"),
        ("ADAUSDT", "neutral", "NEUTRAL"),   # refused in a passing regime
        ("DOTUSDT", "risk-on", "LONG"),
    ])
    body = _section(capsys, path)
    assert "C_gate refused a row in a passing state        1" in body
    assert "verdict: GATE HOLDS, SECOND FILTER ACTIVE" in body


def test_rows_without_an_arm_are_not_measured(tmp_path, capsys):
    """Pre-ADR-0051 rows carry no verdict; the audit must not invent one."""
    import journal

    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute(
        """INSERT INTO signal_log(ts,symbol,timeframe,direction,regime,entry,stop,
               tp1,tp2,atr,features,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (int(time.time()) - 3600, "BTCUSDT", "15m", "LONG", "risk-off",
         100.0, 95.0, 106.0, 110.0, 1.0, json.dumps({"market_regime": "risk-off"}),
         int(time.time())),
    )
    conn.commit()
    conn.close()
    assert journal.SCHEMA  # the schema imported is the one the fixture used
    body = _section(capsys, path)
    assert "verdict: NOT MEASURABLE" in body
    assert "predate ADR-0051" in body
