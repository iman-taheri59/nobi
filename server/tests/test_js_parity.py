"""The standalone broker must refuse what the server refuses.

`nbx-core.js` carries a port of `risk_engine.preflight` for the bundle that runs
without a backend. A port drifts silently unless something compares it, so the
comparison is a test: same inputs into both implementations, gate by gate.

Skipped (not passed) when node is unavailable: a skipped gate is visible, an
assertion that never ran is not.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(*args: str) -> subprocess.CompletedProcess:
    assert NODE is not None, "module is skipped without node"
    return subprocess.run([NODE, *args], cwd=ROOT, capture_output=True, text=True, timeout=120)


def test_preflight_gates_match_between_python_and_javascript():
    out = subprocess.run(
        ["python3", str(ROOT / "scripts" / "check_preflight_parity.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "PASS" in out.stdout
    assert "tables and defaults identical" in out.stdout


def test_standalone_broker_behaviour():
    out = _run(str(ROOT / "scripts" / "js_broker_test.js"))
    assert out.returncode == 0, out.stdout + out.stderr
    assert "PASS — standalone broker behaves like the server" in out.stdout


def test_both_brokers_name_the_same_gate_for_the_same_order():
    """A refusal that says `min-notional` on the phone and `risk-cap` on the
    server is the same divergence as accepting the order at all."""
    import asyncio

    import paper_trading
    from paper_trading import PaperBroker
    from risk_engine import RiskConfig, RiskEngine

    tmp = Path(__file__).parent / "_parity_state"
    old_state = paper_trading.STATE_FILE
    paper_trading.STATE_FILE = tmp
    try:
        class C:
            usdt_irt = 100_805.0

            async def get_orderbook(self, symbol):
                return {"status": "ok", "bids": [["498400000", "10"]],
                        "asks": [["498600000", "10"]], "lastTradePrice": "498500000"}

            async def get_market_stats(self):
                return {"stats": {"USDTIRT": {"latest": "100805"}}}

        broker = PaperBroker(100_000, C(), RiskEngine(RiskConfig(equity=100_000)))
        pos = asyncio.run(broker.place_limit("BTCIRT", "buy", 0.0002, price=498_500_000,
                                            stop=480_000_000))
        assert pos is None
        py_gate = (broker.last_error or "").split(":")[0]
    finally:
        paper_trading.STATE_FILE = old_state
        for f in tmp.parent.glob("_parity_state*"):
            f.unlink(missing_ok=True)

    js = _run(str(ROOT / "scripts" / "js_broker_test.js"))
    assert js.returncode == 0, js.stdout
    assert py_gate == "min-notional", f"python blamed {py_gate}"

    # And the browser broker said the same thing for the same order.
    assert "min-notional" in js.stdout


def test_js_core_still_parses():
    out = _run("--check", str(ROOT / "nbx-core.js"))
    assert out.returncode == 0, out.stderr
