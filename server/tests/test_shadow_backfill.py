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


# ---- 2.5.2: day-of-week slice (Asia/Tehran; Sat–Wed vs Thu–Fri) -------------
class _Args:
    def __init__(self, **kw):
        self.weekdays_only = kw.get("weekdays_only", False)
        self.weekend_only = kw.get("weekend_only", False)
        self.days = kw.get("days")
        self.tz = kw.get("tz", "Asia/Tehran")


def test_day_filter_splits_iranian_week_without_mutating_rows():
    # 2026-11-05 is a Thursday, 2026-11-07 a Saturday (UTC). 20:30 UTC on a
    # Wednesday is already Thursday 00:00 in Tehran (+3:30) — the slice must
    # follow the venue's clock, not UTC.
    import datetime as dt
    def ts(y, m, d, hh=12, mm=0):
        return int(dt.datetime(y, m, d, hh, mm, tzinfo=dt.UTC).timestamp())
    rows = [
        {"ts": ts(2026, 11, 5), "k": "thu"},        # Thursday noon UTC -> Thu in Tehran
        {"ts": ts(2026, 11, 6), "k": "fri"},        # Friday
        {"ts": ts(2026, 11, 7), "k": "sat"},        # Saturday
        {"ts": ts(2026, 11, 4, 20, 45), "k": "wed-late"},  # Wed 20:45Z = Thu 00:15 Tehran
    ]
    frozen = json.dumps(rows)
    wk, label = sb.day_filter(rows, _Args(weekdays_only=True))
    assert [r["k"] for r in wk] == ["sat"] and "Sat–Wed" in label and "Asia/Tehran" in label
    we, _ = sb.day_filter(rows, _Args(weekend_only=True))
    assert [r["k"] for r in we] == ["thu", "fri", "wed-late"]
    custom, label = sb.day_filter(rows, _Args(days="6,7"))      # global weekend: Sat, Sun
    assert [r["k"] for r in custom] == ["sat"] and "days 6,7" in label
    utc_cut, _ = sb.day_filter(rows, _Args(weekdays_only=True, tz="UTC"))
    assert [r["k"] for r in utc_cut] == ["sat", "wed-late"], "in UTC the late-Wednesday row is still a weekday"
    none, label = sb.day_filter(rows, _Args())
    assert none is rows and label == "all days"
    assert json.dumps(rows) == frozen
    with pytest.raises(SystemExit):
        sb.day_filter(rows, _Args(days="0,8"))
