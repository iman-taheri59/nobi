# mypy: check_untyped_defs = False
"""`max_drawdown` must not be a property of the last hour.

The number was derived by scanning `equity_curve`, and `save()` truncates that
curve to its last 500 points. With a mark every 8 s (the position tick) that is
~67 minutes of history. So a restart re-based the high-water mark on whatever
point happened to survive: an account that had fallen 20% from its peak could
report a 2% drawdown, with nothing in the response saying the figure had a
window at all.

The fix keeps two floats (`peak_equity`, `max_drawdown_pct`) that are saved and
restored, and publishes both the durable number and the window-only one so the
difference is visible instead of hidden inside a single figure.
"""
from __future__ import annotations

import json
import time

import pytest

import paper_trading
from paper_trading import PaperBroker
from risk_engine import RiskConfig, RiskEngine


class _Feed:
    async def get_orderbook(self, symbol):
        return {"bids": [["100", "1000"]], "asks": [["100", "1000"]],
                "lastTradePrice": "100"}

    async def get_market_stats(self):
        return {"stats": {"USDTIRT": {"latest": "100805"}}}


def _broker(tmp_path, monkeypatch, equity: float = 100_000.0) -> PaperBroker:
    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "PAPER_BROKER", None)
    return PaperBroker(
        equity, _Feed(), RiskEngine(RiskConfig(equity=equity)))  # type: ignore[arg-type]


def test_a_deep_drawdown_is_remembered_after_a_restart(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    # Up to 120k, then down to 96k: a 20% drawdown from the peak.
    broker._track_drawdown(120_000.0)
    broker.equity = 96_000.0
    broker._track_drawdown(96_000.0)
    assert broker.max_drawdown_pct == pytest.approx(20.0)
    assert broker.peak_equity == pytest.approx(120_000.0)
    broker.save()

    # A "restart": a fresh broker reading the same file.
    monkeypatch.setattr(paper_trading, "PAPER_BROKER", None)
    reloaded = _broker(tmp_path, monkeypatch)
    assert reloaded.peak_equity == pytest.approx(120_000.0)
    assert reloaded.max_drawdown_pct == pytest.approx(20.0)
    # And it is what the public number says, not a window artefact.
    assert reloaded.metrics()["max_drawdown"] == pytest.approx(20.0)


def test_the_truncated_curve_cannot_lower_the_reported_drawdown(tmp_path, monkeypatch):
    """The exact defect: 500 points, all near the trough, reported as ~0%."""
    broker = _broker(tmp_path, monkeypatch)
    broker._track_drawdown(100_000.0)
    broker._track_drawdown(80_000.0)          # -20% at the peak -> trough
    # Now the curve holds only trough-level marks, which is what a restart used
    # to be left with. Old code scanned exactly this list.
    now = time.time()
    broker.equity_curve = [(now - 400 + i, 80_000.0 + (i % 2)) for i in range(500)]
    broker.equity = 80_000.0

    m = broker.metrics()
    assert m["max_drawdown"] == pytest.approx(20.0)          # the truth
    assert m["max_drawdown_curve_window"] == pytest.approx(0.0, abs=0.01)  # the trap
    assert m["max_drawdown"] > m["max_drawdown_curve_window"]


def test_a_partial_state_file_is_restored_without_claiming_zero_drawdown(tmp_path, monkeypatch):
    """Old state files have no peak. A down account must not read as 0%."""
    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    state = {
        "start_equity": 100_000.0,
        "equity": 70_000.0,
        "cash": 70_000.0,
        "equity_curve": [[time.time() - 100, 100_000.0], [time.time(), 70_000.0]],
        "positions": {}, "orders": [], "halted": False, "halt_reasons": [],
    }
    (tmp_path / "paper.json").write_text(json.dumps(state), encoding="utf-8")

    monkeypatch.setattr(paper_trading, "PAPER_BROKER", None)
    broker = _broker(tmp_path, monkeypatch)
    # Reconstructed from the surviving evidence: peak 100k, trough 70k.
    assert broker.peak_equity == pytest.approx(100_000.0)
    assert broker.max_drawdown_pct == pytest.approx(30.0)
    assert broker.metrics()["max_drawdown"] == pytest.approx(30.0)


def test_the_drawdown_basis_and_window_are_published(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    m = broker.metrics()
    assert m["drawdown_basis"] == "all-observed-marks"
    assert "curve_covers_s" in m
    assert m["peak_equity"] == pytest.approx(100_000.0)


def test_a_recovery_does_not_erase_the_recorded_drawdown(tmp_path, monkeypatch):
    """A drawdown is history, not a state: coming back to the peak keeps it."""
    broker = _broker(tmp_path, monkeypatch)
    broker._track_drawdown(110_000.0)
    broker._track_drawdown(88_000.0)   # -20%
    broker._track_drawdown(115_000.0)  # new high
    assert broker.max_drawdown_pct == pytest.approx(20.0)
    assert broker.peak_equity == pytest.approx(115_000.0)


def test_a_broker_built_without_init_still_reports_metrics():
    """`metrics()` is served on a read-only route; it must not raise."""
    broker = object.__new__(PaperBroker)
    broker.equity = 1000.0
    broker.start_equity = 1000.0
    broker.cash = 1000.0
    broker.positions = {}
    broker.orders = []
    broker.equity_curve = [(1, 1000.0)]
    from types import SimpleNamespace
    broker.risk = SimpleNamespace(daily_pnl=0.0)
    m = broker.metrics()
    assert m["max_drawdown"] == pytest.approx(0.0)
    assert m["peak_equity"] == pytest.approx(1000.0)
