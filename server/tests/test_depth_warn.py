"""2.5.2 item 10 — advisory thin-depth label on the gates block.

A label beside the shadow gate, never a verdict: `min_depth_usd` stays None,
`applied` stays False, and no row is removed or reordered.
"""
from __future__ import annotations

import time

import main


def test_gates_block_labels_depth_below_p10_without_touching_the_gate(monkeypatch):
    now = time.time()
    monkeypatch.setitem(main._cached, "signals_bars", {"15m": {"THIN": now - 900, "DEEP": now - 900, "NOBOOK": now - 900}})
    monkeypatch.setitem(main._cached, "closed_markets", [])
    monkeypatch.setitem(main._cached, "books", {
        "THIN": {"at": now, "spread_bps": 12.0, "depth_usd": 410.0},
        "DEEP": {"at": now, "spread_bps": 8.0, "depth_usd": 2645.0},
    })
    ranked = [{"symbol": "THIN"}, {"symbol": "DEEP"}, {"symbol": "NOBOOK"}]
    out = main.gates_block(ranked, "15m")
    assert out["applied"] is False and out["mode"] == "shadow"
    assert out["depth_warn"]["usd"] == main.DEPTH_WARN_USD == 562.0
    assert out["depth_warn"]["applied"] is False
    v = out["verdicts"]
    assert v["THIN"]["depth_thin"] is True and v["THIN"]["code"] == "ok", "label, not a verdict"
    assert v["DEEP"]["depth_thin"] is False
    assert v["NOBOOK"]["depth_thin"] is None and v["NOBOOK"]["code"] == "no-book"
    assert [r["symbol"] for r in ranked] == ["THIN", "DEEP", "NOBOOK"], "ranking untouched"


def test_depth_warn_threshold_is_configurable(monkeypatch):
    monkeypatch.setattr(main, "DEPTH_WARN_USD", 3000.0)
    now = time.time()
    monkeypatch.setitem(main._cached, "signals_bars", {"15m": {"DEEP": now - 900}})
    monkeypatch.setitem(main._cached, "closed_markets", [])
    monkeypatch.setitem(main._cached, "books", {"DEEP": {"at": now, "spread_bps": 8.0, "depth_usd": 2645.0}})
    out = main.gates_block([{"symbol": "DEEP"}], "15m")
    assert out["verdicts"]["DEEP"]["depth_thin"] is True and out["depth_warn"]["usd"] == 3000.0
