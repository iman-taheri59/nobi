"""2.5.2 item 9 — realised stop slippage recorded on paper close events."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

import paper_trading
from paper_trading import stop_slip

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("journal_diagnostic", ROOT / "scripts" / "journal_diagnostic.py")
assert _spec is not None and _spec.loader is not None
jd = importlib.util.module_from_spec(_spec)
sys.modules["journal_diagnostic"] = jd
_spec.loader.exec_module(jd)


def test_stop_slip_sign_and_units():
    # long: entry 100, stop 95 (1 R = 5). filled at 94 → 1 past the stop = 1.0526 % of stop, 0.2 R
    d = stop_slip("buy", 100, 95, 94)
    assert d["stop_model"] == 95
    assert d["stop_slip_pct"] == pytest.approx(1.0526, abs=1e-3) and d["stop_slip_r"] == pytest.approx(0.2)
    # better than the stop is negative
    assert stop_slip("buy", 100, 95, 95.5)["stop_slip_r"] == pytest.approx(-0.1)
    # short mirrors
    assert stop_slip("sell", 100, 105, 106)["stop_slip_r"] == pytest.approx(0.2)
    # degenerate inputs never invent a number
    assert stop_slip("buy", 100, 0, 94)["stop_slip_pct"] is None
    assert stop_slip("buy", 95, 95, 94)["stop_slip_r"] is None and stop_slip("buy", 95, 95, 94)["stop_slip_pct"] is not None


def test_close_event_carries_the_slip(tmp_path, monkeypatch):
    import asyncio
    asyncio.run(_close_event_carries_the_slip(tmp_path, monkeypatch))


async def _close_event_carries_the_slip(tmp_path, monkeypatch):
    from risk_engine import RiskConfig, RiskEngine

    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "hot_mid_hook", None)
    monkeypatch.setattr(paper_trading, "market_closed_hook", None)

    class Feed:
        px = "100"

        async def get_orderbook(self, symbol):
            p = float(self.px)
            return {"bids": [[str(p - 0.01), "1000"]], "asks": [[str(p + 0.01), "1000"]], "lastTradePrice": self.px}

    feed = Feed()
    broker = paper_trading.PaperBroker(100_000, feed, RiskEngine(RiskConfig(equity=100_000)))  # type: ignore[arg-type]

    pos = await broker.place_limit("BTCUSDT", "buy", 10, stop=95, tp1=106, tp2=110, source="SEMI")
    assert pos is not None, broker.last_error
    # the book gaps below the stop: the exit fills at ~93.99, 1 R = entry−95
    feed.px = "94"
    await broker._close(pos.id, "sl-hit")
    ev = [e for e in broker.orders if e.kind == "close"][-1]
    assert ev.extra["reason"] == "sl-hit" and ev.extra["stop_model"] == 95
    assert ev.extra["stop_slip_pct"] == pytest.approx((95 - ev.price) / 95 * 100, abs=1e-4)
    assert ev.extra["stop_slip_r"] > 0
    # the daily reader sees it in the saved file
    state = json.loads((tmp_path / "paper.json").read_text(encoding="utf-8"))
    stats = jd.stop_slip_stats(state["orders"])
    assert stats["n"] == 1 and stats["legacy"] == 0 and stats["pct"]["worse_share"] == 1.0
    assert stats["by_reason"]["sl-hit"]["n"] == 1


def test_reader_skips_legacy_and_non_stop_closes():
    now = time.time()
    orders = [
        {"kind": "close", "ts": now, "extra": {"reason": "sl-hit", "stop_slip_pct": 0.5, "stop_slip_r": 0.1}},
        {"kind": "close", "ts": now, "extra": {"reason": "trail", "stop_slip_pct": -0.2, "stop_slip_r": -0.05}},
        {"kind": "close", "ts": now, "extra": {"reason": "tp2-hit", "stop_slip_pct": 9.9}},   # not a stop exit
        {"kind": "close", "ts": now, "extra": {"reason": "sl-hit"}},                            # pre-2.5.2 event
        {"kind": "close", "ts": now - 10 ** 6, "extra": {"reason": "sl-hit", "stop_slip_pct": 3.0}},
    ]
    s = jd.stop_slip_stats(orders, since=now - 100)
    assert s["n"] == 2 and s["legacy"] == 1
    assert s["pct"]["mean"] == pytest.approx(0.15) and s["pct"]["worse_share"] == 0.5
    assert s["r"]["mean"] == pytest.approx(0.025)
    assert set(s["by_reason"]) == {"sl-hit", "trail"}


def test_section7_prints_even_with_zero_rejects(tmp_path, capsys):
    """Day-1 soak 2.5.2: with 0 rejects section 6 returned early and section 7 never printed."""
    now = time.time()
    state = {"orders": [{"kind": "close", "ts": now, "extra": {"reason": "sl-hit", "stop_slip_pct": 0.4, "stop_slip_r": 0.1}}]}
    f = tmp_path / "paper.json"
    f.write_text(json.dumps(state), encoding="utf-8")
    jd.print_paper_rejects(str(f), None)
    out = capsys.readouterr().out
    assert "6) paper rejects" in out and "none" in out
    assert "7) stop slippage" in out and "n=1" in out
