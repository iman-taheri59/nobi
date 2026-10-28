"""No legacy IRT scale enters new outcomes or qualified evidence after upgrade."""
from __future__ import annotations

import time

import pandas as pd

import journal
import signal_intelligence
from units import IRT_PRICE_CONTRACT


def test_real_schema_upgrade_preserves_legacy_but_excludes_it(tmp_path, monkeypatch):
    path = tmp_path / "journal.db"
    monkeypatch.setattr(journal, "DB_PATH", path)
    base = (int(time.time()) // 900 - 10) * 900
    conn = journal.connect()
    # Reconstruct the old schema, not just an old-shaped Python object.
    conn.execute("DROP VIEW signal_evidence")
    conn.execute("ALTER TABLE signal_log DROP COLUMN price_contract")
    conn.execute("""INSERT INTO signal_log
        (ts,symbol,timeframe,direction,entry,atr,composite,source,origin)
        VALUES(?, 'TESTIRT','15m','LONG',100,1,80,'cache','live')""", (base + 900,))
    sid = conn.execute("SELECT id FROM signal_log").fetchone()[0]
    conn.execute("""INSERT INTO signal_outcomes
        (signal_id,horizon_key,resolved_at,return_pct,r_multiple)
        VALUES(?,'H2',?,900,225.6)""", (sid, base + 3600))
    conn.commit()
    conn.close()

    conn = journal.connect()  # actual additive migration
    legacy = conn.execute("SELECT * FROM signal_log WHERE id=?", (sid,)).fetchone()
    assert legacy["entry"] == 100 and legacy["atr"] == 1
    assert legacy["price_contract"] is None  # never guessed or multiplied
    assert conn.execute("SELECT COUNT(*) FROM signal_outcomes").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM signal_evidence").fetchone()[0] == 0
    df = pd.DataFrame({"t": pd.to_datetime([base+i*900 for i in range(8)], unit="s", utc=True),
                       "o": [1000.0]*8, "h": [1000.0]*8, "l": [1000.0]*8,
                       "c": [1000.0]*8, "v": [1.0]*8})
    assert journal.resolve_symbol(conn, "TESTIRT", "15m", df) == 0
    assert conn.execute("SELECT COUNT(*) FROM signal_outcomes").fetchone()[0] == 1
    out = journal.summary(conn)
    assert out["kpi"]["issued"] == 0
    assert out["price_contracts"]["excluded_legacy_irt"] == 1
    assert signal_intelligence.summary()["resolved"] == 0
    raw = journal.export_rows(conn)
    assert len(raw) == 1 and raw[0]["entry"] == 100
    assert raw[0]["evidence_eligible"] is False
    assert "price_contract" in journal.export_csv(raw).splitlines()[0]

    journal.insert_rows(conn, [journal.JournalRow(
        ts=base+1800, symbol="TESTIRT", timeframe="15m", direction="LONG",
        ta_score=80, composite=80, entry=1000, atr=10)])
    current = conn.execute("SELECT * FROM signal_evidence").fetchone()
    assert current["price_contract"] == IRT_PRICE_CONTRACT
    assert journal.resolve_symbol(conn, "TESTIRT", "15m", df) == 1
    assert conn.execute("SELECT COUNT(*) FROM signal_outcomes WHERE signal_id=? AND horizon_key='H1'",
                        (sid,)).fetchone()[0] == 0
    conn.close()


def test_legacy_usdt_contract_is_not_invalidated(tmp_path):
    conn = journal.connect(tmp_path / "usdt.db")
    conn.execute("""INSERT INTO signal_log(ts,symbol,timeframe,direction,entry,atr)
                    VALUES(900,'TESTUSDT','15m','LONG',100,1)""")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM signal_evidence").fetchone()[0] == 1
    conn.close()
