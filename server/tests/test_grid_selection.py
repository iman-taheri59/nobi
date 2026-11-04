"""Grid selection must not consume the OOS numbers it later advertises."""
from __future__ import annotations

import backtest


def _window(is_exp: float, oos_exp: float):
    def arm(exp: float):
        win = exp > 0
        return {
            "trades": 10, "wins": 10 if win else 0, "losses": 0 if win else 10,
            "hit_rate": 100 if win else 0, "gross_profit": 1000 if win else 0,
            "gross_loss": 0 if win else 1000, "profit_factor": 2 if win else 0,
            "expectancy_r": exp, "max_drawdown": 0 if win else 50,
            "final_equity": 55_000 if win else 45_000,
            "ambiguous_exits": 0,
        }

    in_sample, out = arm(is_exp), arm(oos_exp)
    return {
        "is": in_sample, "oos": out,
        **out,
        "initial_equity": 50_000, "equity_curve": [], "sample_trades": [],
        "window_start": "start", "window_end": "end", "params_used": {},
    }


def test_best_grid_row_is_selected_by_is_not_by_its_more_flattering_oos(monkeypatch):
    class FakeRunner:
        def __init__(self, train_bars, eval_bars):
            pass

        def run(self, df, symbol, tf, cfg, grid):
            # 2 has terrible train evidence and wonderful OOS; 3 is the reverse.
            if cfg["atr_stop_k"] == 2:
                return [_window(0.0, 5.0)]
            return [_window(1.0, -5.0)]

    monkeypatch.setattr(backtest, "WalkForwardRunner", FakeRunner)
    rows = [{"t": 1_700_000_000 + i * 3600, "o": 100, "h": 101,
             "l": 99, "c": 100, "v": 10} for i in range(320)]
    result = backtest.grid_search(
        "XUSDT", "1h", rows,
        grid={"atr_stop_k": [2, 3], "risk_pct": [0.75]},
    )
    assert result["best"]["params"]["atr_stop_k"] == 3
    assert result["selection"]["basis"] == "in-sample-only"
    assert result["selection"]["oos_used_for_selection"] is False
