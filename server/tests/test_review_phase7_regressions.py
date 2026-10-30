# mypy: check_untyped_defs = False
"""Private-runner only: synthetic bars, tmp reports, in-process route readers."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

import backtest
import main
from fees import FEE_PCT
from ta_engine import candles_to_df


def rows(n=110):
    return [{"t": 1_700_000_000 + i * 3600, "o": 100, "h": 100, "l": 100,
             "c": 100, "v": 1} for i in range(n)]


@pytest.mark.parametrize("slip", [0, 6, 35])
@pytest.mark.parametrize("length", [2, 5, 40])
def test_terminal_settlement_agrees_with_cash_pnl_and_drawdown(slip, length):
    data = rows()
    signals = [None] * len(data)
    signals[60] = {"direction": "LONG", "score": 80, "risk": {"atr": 1.5}}
    bt = backtest.EventBacktester({"initial_equity": 50_000, "risk_pct": 0.5,
                                  "max_single_asset_pct": 100, "slippage_bps": slip})
    r = bt.run_symbol(candles_to_df(data), "XUSDT", "1h", signals, 60, 60 + length)
    assert r.trades == 1 and r.sample_trades[0]["reason"] == "eod"
    assert not bt.positions
    assert r.final_equity == round(bt.cash, 2) == r.equity_curve[-1][1]
    assert r.final_equity == round(50_000 + sum(t.realized_pnl for t in bt.closed), 2)
    qty = 250 / 3
    entry = 100 * (1 + slip / 10_000)
    exit_price = 100 * (1 - slip / 10_000)
    pnl = qty * (exit_price * (1 - FEE_PCT) - entry * (1 + FEE_PCT))
    assert bt.closed[0].realized_pnl == pytest.approx(pnl)
    assert r.max_drawdown == round((50_000 - bt.cash) / 50_000 * 100, 1)
    t = r.sample_trades[0]
    assert datetime.fromisoformat(t["entry_t"]).timestamp() == data[60]["t"]
    assert datetime.fromisoformat(t["exit_t"]).timestamp() == data[59 + length]["t"]
    assert datetime.fromisoformat(t["entry_t"]).tzinfo == UTC


def window(end, points):
    return {"trades": 1, "wins": 0, "expectancy_r": -1, "gross_profit": 0,
            "gross_loss": 100 - end, "initial_equity": 100, "final_equity": end,
            "equity_curve": points}


def test_chained_drawdown_includes_prior_window_peak():
    r = backtest._aggregate_windows([
        window(90, [[1, 120], [2, 90]]), window(90, [[3, 100], [4, 90]])], 100)
    assert r["final_equity"] == r["equity_curve"][-1][1] == 81
    assert r["max_drawdown"] == 32.5  # not max(25%, 10%)


def test_zero_equity_is_not_an_absent_endpoint():
    r = backtest._aggregate_windows([
        window(0, [[1, 0]]), window(150, [[2, 150]])], 100)
    assert r["final_equity"] == 0
    assert r["equity_curve"] == [[1, 0], [2, 0]]
    assert r["max_drawdown"] == 100


def test_mdd_uses_full_curve_before_display_truncation():
    points = [[i, 200 if i == 0 else 100] for i in range(500)]
    r = backtest._aggregate_windows([window(100, points)], 100)
    assert len(r["equity_curve"]) == 400
    assert r["max_drawdown"] == 50  # peak excluded from displayed tail


def test_no_trade_run_has_zero_mdd_and_unchanged_equity():
    r = backtest.EventBacktester({"initial_equity": 50_000}).run_symbol(
        candles_to_df(rows()), "XUSDT", "1h", signals=[None] * 110, start=60)
    assert r.trades == r.max_drawdown == 0
    assert r.final_equity == 50_000
    assert r.equity_curve[-1][1] == 50_000


def test_same_second_runs_are_distinct_and_history_reads_writer_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(backtest, "REPORTS", tmp_path)
    monkeypatch.setattr(backtest.time, "time", lambda: 1234567890)
    signals = [None] * 110
    signals[60] = {"direction": "LONG", "score": 80, "risk": {"atr": 1.5}}
    monkeypatch.setattr(backtest, "ta_score_series", lambda df: signals)
    cfg = {"initial_equity": 12345, "risk_pct": 0.5, "max_concurrent": 2}
    first, second = [backtest.run_and_save("XUSDT", "1h", rows(), cfg, mode="single")
                     for _ in range(2)]
    assert first["run_id"] != second["run_id"]
    assert first["report_file"] != second["report_file"]
    assert len(list(tmp_path.glob("*.json"))) == 2
    for r in [first, second]:
        saved = json.loads((tmp_path / r["report_file"]).read_text())
        assert saved == r
        assert saved["effective_config"]["initial_equity"] == 12345
        assert saved["effective_config"]["max_concurrent"] == 2
        assert saved["effective_config"]["parameter_selection"] == "fixed"
        assert "_signals" not in saved["effective_config"]
        assert "not a unique" in saved["config_hash_scope"]
        assert r["aggregate"]["final_equity"] == r["aggregate"]["equity_curve"][-1][1]
    history = asyncio.run(main.backtest_reports(limit=20))
    assert history["ok"] and len(history["reports"]) == 2
    assert all(r["windows"] == 1 and r["run_id"] and r["generated_at"] for r in history["reports"])
    assert {r["file"] for r in history["reports"]} == {first["report_file"], second["report_file"]}
    promotion = asyncio.run(main.backtest_promotion_gate())
    assert promotion["ok"] and promotion["report_file"] in {first["report_file"], second["report_file"]}


def test_reader_of_missing_directory_is_a_read_not_mkdir(monkeypatch, tmp_path):
    absent = tmp_path / "absent"
    monkeypatch.setattr(backtest, "REPORTS", absent)
    assert asyncio.run(main.backtest_reports(limit=20))["reports"] == []
    assert asyncio.run(main.backtest_promotion_gate())["gate"] is None
    assert not absent.exists()


@pytest.mark.parametrize(("grid", "fixed", "optimised"), [
    (None, False, True),
    ([{"atr_stop_k": 3.0, "tp1_r": 1.5, "tp2_r": 2.5}], False, False),
    ([{"atr_stop_k": 2.0}, {"atr_stop_k": 3.0}], True, True),
])
def test_selection_metadata_describes_actual_windows_not_just_requested_flags(monkeypatch, tmp_path, grid, fixed, optimised):
    monkeypatch.setattr(backtest, "REPORTS", tmp_path)
    monkeypatch.setattr(backtest, "ta_score_series", lambda df: [None] * len(df))
    report = backtest.run_and_save("XUSDT", "1h", rows(300), {"fixed_params": fixed}, grid=grid)
    assert any(w["params_optimised"] for w in report["details"]) is optimised
    assert report["effective_config"]["parameter_selection"].startswith("train-selected" if optimised else "fixed")
