"""The kill switch is a latch: a restart must not release it.

Before this test, `halted` lived only in memory (broker + risk engine). The
automatic triggers re-arm from persisted exit events, but a halt armed by hand
— the one an operator reaches for in a crisis — vanished with the process.
"""
from __future__ import annotations

from typing import Any, cast

import paper_trading
from risk_engine import RiskConfig, RiskEngine


def _client() -> Any:
    return cast(Any, object())


def _engine() -> RiskEngine:
    return RiskEngine(RiskConfig(equity=100_000.0))


def test_manual_halt_is_persisted_and_restored(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    risk = _engine()
    broker = paper_trading.PaperBroker(100_000.0, _client(), risk)
    assert not broker.halted and not risk.halted

    broker.halted = True
    risk.halted = True
    risk.trigger_reasons = ["manual-halt"]
    broker.save()

    # "restart": new objects, same file
    risk2 = _engine()
    broker2 = paper_trading.PaperBroker(100_000.0, _client(), risk2)
    assert broker2.halted is True
    assert risk2.halted is True
    assert "manual-halt" in risk2.trigger_reasons


def test_resume_is_persisted_too(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    risk = _engine()
    broker = paper_trading.PaperBroker(100_000.0, _client(), risk)
    broker.halted = risk.halted = True
    broker.save()
    broker.halted = risk.halted = False
    risk.trigger_reasons = []
    broker.save()
    risk2 = _engine()
    broker2 = paper_trading.PaperBroker(100_000.0, _client(), risk2)
    assert broker2.halted is False and risk2.halted is False
