# mypy: check_untyped_defs = False
"""Synthetic paper metrics / private SQLite only; use run_page_regressions.py."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_signal_intelligence import _seed

import journal
import main
import paper_trading
import signal_intelligence as si


def _broker(pnls):
    # Do not construct a broker: __init__ loads persisted account state.
    broker = object.__new__(paper_trading.PaperBroker)
    broker.equity = 1100
    broker.start_equity = 1000
    broker.cash = 900
    broker.positions = {}
    broker.risk = SimpleNamespace(daily_pnl=-2)
    broker.equity_curve = [(1, 1000), (2, 1200), (3, 1100)]
    broker.orders = [
        paper_trading.OrderEvent(1, "fill", "BTCUSDT", "buy", 1, 100,
                                {"pnl": -999, "fee": 8}),
        *[paper_trading.OrderEvent(1, "tp1" if i == 0 else "close", "BTCUSDT", "sell", 1, 100,
                                  {"pnl": pnl, "risk": 10}) for i, pnl in enumerate(pnls)],
    ]
    return broker


def test_exit_event_contract_is_additive_not_a_net_closed_trade_30_day_metric():
    broker = _broker([20, -10])
    m = broker.metrics()
    assert {key: m[key] for key in (
        "trades", "start_equity", "openPositions", "cash", "equity", "hit_rate",
        "profit_factor", "expectancy", "expectancy_r", "max_drawdown", "daily_pnl",
    )} == {
        "trades": 2, "start_equity": 1000, "openPositions": 0, "cash": 900,
        "equity": 1100, "hit_rate": 50.0, "profit_factor": 2.0,
        "expectancy": 5.0, "expectancy_r": 0.5, "max_drawdown": 8.3, "daily_pnl": -2,
    }
    assert m["exit_events"] == 2  # TP1 and close of the same symbol are separate.
    assert m["measurement"] == {
        "basis": "retained-exit-events", "time_filter_days": None,
        "partial_exits_separate": True, "entry_fees_included": False,
        "persisted_event_limit": 400, "complete_account_history": False,
        "profit_factor_display_cap": 99.99,
    }
    # Events at epoch second 1 remain included: not a 30-day account window.
    assert broker.orders[1].ts == 1
    assert m["expectancy"] != (20 - 10 - 8) / 2  # not entry-fee-net PnL


@pytest.mark.parametrize("pnls,pf", [([], 0.0), ([10], 99.99), ([10, -0.01], 99.99), ([-10], 0)])
def test_metadata_does_not_rewrite_existing_profit_factor_arithmetic(pnls, pf):
    m = _broker(pnls).metrics()
    assert m["profit_factor"] == pf
    assert m["trades"] == m["exit_events"] == len(pnls)


@pytest.mark.parametrize("horizon", ["H1", "H2", "H3"])
def test_maturity_separates_non_long_invalid_void_resolved_and_eligible(monkeypatch, tmp_path, horizon):
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "eligibility.db")
    now = 1_800_000_000
    monkeypatch.setattr(si.time, "time", lambda: now)
    duration = journal.HORIZONS[horizon]
    with journal.connect() as conn:
        # Every row is in the day window; only two rows are eligible unresolved LONG.
        rows = [
            ("due", "LONG", now-duration-1, 100, 2, None),
            ("pending", "LONG", now-duration+1, 100, 2, None),
            ("neutral", "NEUTRAL", now-duration-20, 100, 2, None),
            ("exit", "EXIT", now-duration-30, 100, 2, None),
            ("invalid-entry", "LONG", now-duration-40, 0, 2, None),
            ("invalid-atr", "LONG", now-duration-50, 100, 0, None),
            ("voided", "LONG", now-duration-60, 100, 2, now),
            ("resolved", "LONG", now-duration-70, 100, 2, None),
        ]
        for symbol, direction, ts, entry, atr, voided in rows:
            cur = conn.execute(
                """INSERT INTO signal_log(ts,symbol,timeframe,direction,entry,atr,voided_at,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""", (ts, symbol, "15m", direction, entry, atr, voided, now),
            )
            if symbol == "resolved":
                conn.execute(
                    "INSERT INTO signal_outcomes(signal_id,horizon_key,resolved_at,r_multiple) VALUES(?,?,?,?)",
                    (cur.lastrowid, horizon, now, 1.5),
                )
        conn.commit()
    s = si.linked_summary(days=30, horizon=horizon)
    assert s["signals"] == 8 and s["resolved"] == 1 and s["voided"] == 1
    assert s["outcome_maturity"] == {"pending_not_due": 1, "due_unresolved": 1}
    assert s["unresolved_eligibility"] == {"non_long_not_applicable": 2, "invalid_model_inputs": 2}
    assert s["unresolved"] == sum(s["outcome_maturity"].values()) + sum(s["unresolved_eligibility"].values())
    assert s["resolver_backlog"] == 1  # not the two old non-LONG observations
    assert s["measurement"]["horizon_seconds"] == duration
    assert s["measurement"]["net_account_pnl"] is False
    assert s["measurement"]["independent_samples_established"] is False
    assert s["expectancy_r"] == 1.5


@pytest.mark.parametrize("count", [0, 29, 30, 31])
def test_sample_threshold_is_only_a_count_not_independence_or_live_permission(monkeypatch, tmp_path, count):
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "count.db")
    now = 1_800_000_000
    monkeypatch.setattr(si.time, "time", lambda: now)
    with journal.connect() as conn:
        for i in range(count):
            cur = conn.execute(
                "INSERT INTO signal_log(ts,symbol,timeframe,direction,entry,atr,created_at) VALUES(?,?,'15m','LONG',100,2,?)",
                (now-i-4000, "BTCUSDT", now),
            )
            conn.execute(
                "INSERT INTO signal_outcomes(signal_id,horizon_key,resolved_at,r_multiple) VALUES(?,'H1',?,1)",
                (cur.lastrowid, now),
            )
        conn.commit()
    s = si.linked_summary(days=1)
    assert s["sample_sufficient"] is (count >= 30)
    assert s["measurement"]["sample_threshold_is_count_only"] is True
    assert s["measurement"]["independent_samples_established"] is False
    assert "live_allowed" not in s


def test_real_detail_api_contract_and_unknown_id(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    signal_id = si.signals(days=1)[0]["id"]
    with TestClient(main.app) as client:
        response = client.get(f"/api/signal-intelligence/signals/{signal_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["signal"]["id"] == signal_id
        assert body["outcomes"] and body["reviews"] == []
        assert body["signal"]["component_scores"]["ta"] == 70
        assert client.get("/api/signal-intelligence/signals/999999").status_code == 404


@pytest.mark.parametrize("pnls,pf,defined,capped", [
    ([0], 0.0, False, False), ([0, 0], 0.0, False, False),
    ([10, 0], 99.99, True, True), ([0, -10], 0.0, True, False),
    ([10, -5], 2.0, True, False),
])
def test_zero_loss_denominator_and_capped_pf_are_explicit(pnls, pf, defined, capped):
    m = _broker(pnls).metrics()
    assert m["profit_factor"] == pf
    assert m["profit_factor_defined"] is defined
    assert m["profit_factor_is_capped"] is capped
