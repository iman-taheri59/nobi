"""`scripts/shadow_backfill.py` — the retrospective must not invent returns."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("shadow_backfill", ROOT / "scripts" / "shadow_backfill.py")
assert _spec and _spec.loader
sb = importlib.util.module_from_spec(_spec)
sys.modules["shadow_backfill"] = sb
_spec.loader.exec_module(sb)


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "journal.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
    CREATE TABLE signal_log(id INTEGER PRIMARY KEY, ts INT, symbol TEXT, timeframe TEXT, direction TEXT,
      ta_score REAL, fa_score REAL, liquidity_score REAL, composite REAL, entry REAL, stop REAL, atr REAL,
      features TEXT, origin TEXT, voided_at INT, scan_cycle_id INT, price_contract TEXT);
    CREATE TABLE signal_outcomes(signal_id INT, horizon_key TEXT, mae_pct REAL, mfe_pct REAL, hit_sl INT,
      hit_tp1 INT, r_multiple REAL, return_pct REAL);
    """)
    feat = json.dumps({"veto_severe": None})
    rows: list[tuple] = []
    t0 = 1_800_000_000
    # XIRT: live NEUTRAL (TA 60) but FA/liq 100 -> D_comp composite 0.55*60+0.25*100+0.2*100 = 78 -> LONG
    rows.append((1, t0, "XIRT", "15m", "NEUTRAL", 60, 100, 100, 78.0, 100.0, 99.0, 0.5, feat, "live", None, 1, "irt-toman-v2"))
    rows.append((2, t0 + 3600, "XIRT", "15m", "NEUTRAL", 60, 100, 100, 78.0, 102.0, 99.0, 0.5, feat, "live", None, 2, "irt-toman-v2"))
    # YIRT: a rial->toman seam between the two rows must not become a -90 % return
    rows.append((3, t0, "YIRT", "15m", "LONG", 90, 50, 50, 74.5, 1000.0, 990.0, 5, feat, "live", None, 1, None))
    rows.append((4, t0 + 3600, "YIRT", "15m", "LONG", 90, 50, 50, 74.5, 100.0, 99.0, 0.5, feat, "live", None, 2, "irt-toman-v2"))
    conn.executemany("INSERT INTO signal_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.execute("INSERT INTO signal_outcomes VALUES (3,'H2',-0.5,1.0,1,0,-1.0,-1.0)")
    conn.commit()
    conn.close()
    return db


def test_arms_rescored_and_seam_ignored(tmp_path):
    rows = sb.load(_db(tmp_path), "15m")
    sb.attach_forward_returns(rows)
    by_id = {r["id"]: r for r in rows}
    assert by_id[1]["fwd"]["1h"] == pytest.approx(2.0)  # XIRT 100 -> 102
    assert by_id[3]["fwd"]["1h"] is None            # seam: different price contract, no return
    rep = sb.evaluate(rows)
    assert rep["arms"]["live"]["long"] == 2         # YIRT twice
    assert rep["arms"]["D_comp"]["long"] == 4       # + both XIRT rows
    assert rep["arms"]["D_comp"]["fwd"]["1h"]["n"] == 1 and rep["arms"]["D_comp"]["fwd"]["1h"]["mean_pct"] == 2.0
    # k=3 bound: YIRT stop 1 % at k=2; MAE 0.5 % < 1.5 % -> would survive
    assert rep["k3_bound"]["stopped_at_k2_H2"] == 1 and rep["k3_bound"]["would_survive_k3"] == 1
    md = sb.to_markdown(rep, "journal.db")
    assert "| D_comp | 4 |" in md
