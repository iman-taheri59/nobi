# mypy: check_untyped_defs = False
"""Offline backup/upgrade/rollback rehearsal; no service, config or network.

Checks data compatibility, NOT an actual systemd deployment or old interpreter.
Post-upgrade evidence is archived separately before restoring the old snapshot.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

import journal
import paper_trading
from paper_trading import PaperBroker
from risk_engine import RiskConfig, RiskEngine

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import state_backup as sb


def _marker(path: Path, value: str) -> None:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE marker (value TEXT)')
        conn.execute('INSERT INTO marker VALUES (?)', (value,))
        conn.commit()


def _schema_and_data(path: Path) -> tuple:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        schema = conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        records = [(name, conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall())
                   for (name,) in tables]
        return schema, records


def test_schema_rollback_restores_old_contract_and_preserves_failed_upgrade_evidence(tmp_path):
    db, auth, live = [tmp_path / name for name in ('journal.db', 'auth.db', 'live.db')]
    # Reconstruct the pre-2.0.1 schema: additive column/view were not present.
    with contextlib.closing(journal.connect(db)) as conn:
        conn.execute('DROP VIEW signal_evidence')
        conn.execute('ALTER TABLE signal_log DROP COLUMN price_contract')
        conn.execute("""INSERT INTO signal_log(ts,symbol,timeframe,direction,entry,atr)
                        VALUES(900,'TESTIRT','15m','LONG',100,1)""")
        sid = conn.execute('SELECT id FROM signal_log').fetchone()[0]
        conn.execute("""INSERT INTO signal_outcomes(signal_id,horizon_key,resolved_at,return_pct,r_multiple)
                        VALUES(?,'H2',1800,900,225.6)""", (sid,))
        conn.commit()
    _marker(auth, 'auth-before')
    _marker(live, 'live-before')
    original = {p.name: _schema_and_data(p) for p in (db, auth, live)}
    before = sb.create_backup(tmp_path / 'before.zip', db, auth, live)

    with contextlib.closing(journal.connect(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM signal_evidence').fetchone()[0] == 0
        journal.insert_rows(conn, [journal.JournalRow(
            ts=1800, symbol='TESTIRT', timeframe='15m', direction='LONG',
            entry=1000, atr=10, ta_score=80, composite=80)])
        assert conn.execute('SELECT COUNT(*) FROM signal_evidence').fetchone()[0] == 1
    # Never discard the newer state when deciding to revert to the old contract.
    after = sb.create_backup(tmp_path / 'after-failure.zip', db, auth, live)
    # The offline contract requires cleanly checkpointed/closed SQLite state.
    # Read-only WAL snapshots may leave empty sidecars; never unlink them by hand.
    for path in (db, auth, live):
        with contextlib.closing(sqlite3.connect(path)) as conn:
            assert conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    sb.restore_backup(before, db, auth, live, force=True, offline=True)
    assert {p.name: _schema_and_data(p) for p in (db, auth, live)} == original
    # Old-schema reads work without triggering the new application's migration.
    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert 'price_contract' not in [r[1] for r in conn.execute('PRAGMA table_info(signal_log)')]
        assert conn.execute('SELECT entry FROM signal_log').fetchone()[0] == 100
        assert conn.execute('SELECT return_pct FROM signal_outcomes').fetchone()[0] == 900

    saved = tmp_path / 'preserved-post-upgrade'
    saved.mkdir()
    saved_db, saved_auth, saved_live = [saved / p.name for p in (db, auth, live)]
    sb.restore_backup(after, saved_db, saved_auth, saved_live, force=True, offline=True)
    with contextlib.closing(sqlite3.connect(saved_db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM signal_log').fetchone()[0] == 2
        assert conn.execute('SELECT COUNT(*) FROM signal_evidence').fetchone()[0] == 1


class OfflineFeed:
    price = 100.0

    async def get_orderbook(self, symbol):
        return {'bids': [[self.price, 10000]], 'asks': [[self.price, 10000]]}


def test_paper_rollback_restores_matching_json_and_keeps_post_upgrade_partial(tmp_path, monkeypatch):
    state = tmp_path / 'paper_state.json'
    monkeypatch.setattr(paper_trading, 'STATE_FILE', state)
    monkeypatch.setattr(paper_trading, 'hot_mid_hook', None)
    monkeypatch.setattr(paper_trading, 'market_closed_hook', None)
    feed = OfflineFeed()
    broker = PaperBroker(100000, feed, RiskEngine(RiskConfig(equity=100000)))
    position = asyncio.run(broker.place_limit('TESTUSDT', 'buy', 100, price=100,
                                             stop=94, tp1=107.2, tp2=112))
    assert position is not None
    broker.save()  # A valid main + .bak pair must be restored together.
    sidecar = paper_trading._backup_path(state)
    before_main, before_bak = state.read_bytes(), sidecar.read_bytes()
    feed.price = 108
    asyncio.run(broker.evaluate_positions())
    assert position.filled_qty == 50
    assert broker.risk.open_heat == pytest.approx(0.3)
    preserved = tmp_path / 'preserved-after-failure'
    preserved.mkdir()
    shutil.copy2(state, preserved / state.name)
    shutil.copy2(sidecar, preserved / sidecar.name)

    # Emulate an explicitly approved offline restore of BOTH matching files.
    state.write_bytes(before_main)
    sidecar.write_bytes(before_bak)
    restored = PaperBroker(100000, feed, RiskEngine(RiskConfig(equity=100000)))
    assert state.read_bytes() == before_main and sidecar.read_bytes() == before_bak
    assert restored.positions[position.id].filled_qty == 100
    assert restored.risk.open_heat == pytest.approx(0.6)
    failed_state = json.loads((preserved / state.name).read_text(encoding='utf-8'))
    assert failed_state['positions'][position.id]['filled_qty'] == 50
    # Restore does not pretend the newer partial exit never happened: its
    # evidence remains in the archived failed state for operator reconciliation.
