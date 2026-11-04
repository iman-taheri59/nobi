# mypy: check_untyped_defs = False
"""Risk triggers must gate orders and backtest entries, not only exist as helpers."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from backtest import EventBacktester, _cfg_hash
from paper_trading import OrderEvent, PaperBroker, Position
from risk_engine import RiskConfig, RiskEngine
from ta_engine import candles_to_df


def run(coro):
    return asyncio.run(coro)


class _NoNetwork:
    async def get_orderbook(self, _symbol):
        raise AssertionError("a halted order reached the exchange")


def _broker(tmp_path, monkeypatch):
    import paper_trading

    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    return PaperBroker(100_000, _NoNetwork(), RiskEngine(RiskConfig(equity=100_000)))


def _closed(symbol: str, ts: float) -> Position:
    return Position(
        id=f"closed-{symbol}-{ts}", symbol=symbol, side="buy", entry_price=100,
        qty=1, size_quote=100, stop=98, tp1=102, tp2=105, stop_pct=2,
        opened_at=ts - 60, state="CLOSED", closed_at=ts, close_reason="sl-hit",
        risk_usd=2, risk_pct=0.002,
    )


def test_daily_realized_loss_latches_before_any_orderbook_read(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    now = datetime(2026, 11, 4, 12, tzinfo=UTC).timestamp()
    broker.orders = [OrderEvent(now, "close", "BTCUSDT", "buy", 1, 95, {"pnl": -4000})]
    state = broker.refresh_risk_triggers(now)
    assert state["daily_pnl_pct"] == pytest.approx(-4.0)
    assert "daily-loss-halt" in state["reasons"]
    assert broker.risk.halted is True
    assert run(broker.place_limit("BTCUSDT", "buy", 1)) is None
    assert broker.last_error.startswith("halted")


def test_three_closed_stop_positions_form_a_real_consecutive_halt(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    now = datetime(2026, 11, 4, 12, tzinfo=UTC).timestamp()
    broker.positions = {f"p{i}": _closed("BTCUSDT", now - 100 + i) for i in range(3)}
    state = broker.refresh_risk_triggers(now)
    assert state["consecutive_sl"] == 3
    assert "consecutive-stop-halt" in state["reasons"]
    plan = broker.risk.evaluate("BTCUSDT", "buy", 100, 1)
    assert plan.blocked is True
    assert "consecutive-stop-halt" in plan.warnings


def _long(atr: float = 1.0) -> dict:
    return {"direction": "LONG", "score": 80, "risk": {"atr": atr}}


def test_backtest_third_stop_blocks_the_otherwise_valid_fourth_entry():
    rows = []
    for i in range(36):
        low = 95 if i in {31, 32, 33} else 100
        rows.append({"t": 1_700_000_000 + i * 3600, "o": 100, "h": 100,
                     "l": low, "c": 100, "v": 100})
    signals = [_long() if 30 <= i <= 34 else None for i in range(36)]
    bt = EventBacktester({
        "initial_equity": 100_000, "risk_pct": 1, "slippage_bps": 0,
        "daily_loss_halt_pct": 100, "weekly_loss_halt_pct": 100,
    })
    result = bt.run_symbol(candles_to_df(rows), "XUSDT", "1h", signals=signals, start=30, end=36)
    assert result.trades == 3
    assert result.risk_triggers["halted"] is True
    assert result.risk_triggers["consecutive_sl"] == 3
    assert result.risk_triggers["reasons"] == ["consecutive-stop-halt"]


def test_config_hash_covers_sizing_and_trigger_configuration():
    base = {"risk_pct": 0.75, "min_score": 68}
    assert _cfg_hash(base) != _cfg_hash({**base, "risk_pct": 1.0})
    assert _cfg_hash(base) != _cfg_hash({**base, "min_score": 70})
    assert _cfg_hash(base) != _cfg_hash({**base, "risk_triggers": False})
    assert _cfg_hash(base) != _cfg_hash({**base, "consecutive_sl_halt": 4})
