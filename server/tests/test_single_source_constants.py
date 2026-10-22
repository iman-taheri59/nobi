"""One number, one home — for the constants three subsystems share.

Every value below was already duplicated in two to four files before this test
existed, and each copy carried a comment claiming to agree with the others:

- the 0.2 % per-leg fee: `paper_trading.py`, `backtest.py`, `portfolio.py`'s
  dataclass default and the browser bundle's `PAPER_FEE_PCT`;
- the paper account's starting equity: the code default, `server/.env.example`,
  `deploy/nobi.env.example`, the ArvanCloud install template and the Windows
  guide. Two of them said 10,000 while the rest said 100,000, so a fresh deploy
  sized every position ten times smaller than the same install configured by
  hand;
- the take-profit levels, which lived as a literal default inside `_tp` and made
  the "low-rr" warning unreachable.

These are not style preferences. A backtest that charges a different fee than the
broker produces a profit factor paper trading cannot reproduce, and a deploy that
starts with different capital sizes every trade differently — both silently.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _python_fee() -> float:
    import fees

    return fees.FEE_PCT


def _js_constant(name: str) -> float:
    text = (ROOT / "nbx-core.js").read_text(encoding="utf-8")
    match = re.search(rf"const\s+{name}\s*=\s*([0-9.]+)", text)
    assert match, f"{name} not found in nbx-core.js"
    return float(match.group(1))


def test_the_broker_backtester_and_allocator_share_one_fee():
    import backtest
    import paper_trading
    import portfolio
    from fees import FEE_PCT

    assert paper_trading.FEE_PCT == FEE_PCT
    assert backtest.FEE_PCT == FEE_PCT
    assert portfolio.Limits(equity=1.0, cash=1.0).fee_pct == FEE_PCT


def test_the_browser_charges_what_the_server_charges():
    """`nbx-core.js` cannot import Python, so this is the only place the two
    can be compared. They agree today; nothing stopped them diverging."""
    assert _js_constant("PAPER_FEE_PCT") == pytest.approx(_python_fee())


def test_no_second_copy_of_the_fee_literal_exists():
    """A new `0.002`-shaped fee assignment is how the drift started."""
    offenders = []
    for path in sorted((ROOT / "server").glob("*.py")):
        if path.name == "fees.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"\s*FEE_PCT\s*=", line) or re.match(r"\s*fee_pct\s*:\s*float\s*=\s*0\.", line):
                offenders.append(f"{path.name}:{number}")
    for path in sorted((ROOT / "scripts").glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"fee_usd\s*=\s*[a-z_]*\s*\*\s*0\.002", line):
                offenders.append(f"scripts/{path.name}:{number}")
    assert not offenders, (
        "the fee is written down again instead of imported from server/fees.py: "
        + ", ".join(offenders)
    )


def test_every_env_sample_ships_the_same_starting_equity():
    """The install artifacts must agree with the code default they override."""
    from fees import DEFAULT_EQUITY_USD

    wanted = int(DEFAULT_EQUITY_USD)
    samples = [
        "server/.env.example",
        "deploy/nobi.env.example",
        "scripts/deploy_arvan.sh",
    ]
    seen: dict[str, int] = {}
    for name in samples:
        text = (ROOT / name).read_text(encoding="utf-8")
        values = {int(v) for v in re.findall(r"NBX_EQUITY_USD=(\d+)", text)}
        assert values, f"{name} no longer sets NBX_EQUITY_USD"
        seen[name] = values.pop()
    assert set(seen.values()) == {wanted}, (
        f"install artifacts disagree with the code default {wanted}: {seen}"
    )


def test_the_risk_engine_can_actually_warn_about_a_low_rr():
    """The `low-rr` branch used to be unreachable: `_tp` always returned a 2R
    target. A gate nobody can trip is a comment. This pins both halves — the
    default must not warn, a configured low target must."""
    from risk_engine import MIN_RR, RiskConfig, RiskEngine

    def plan(levels):
        engine = RiskEngine(RiskConfig(equity=100_000.0, rr_levels=levels))
        return engine.evaluate("BTCIRT", "buy", 100.0, 0.5, "15m",
                               equity_quote=1_000_000.0, preview=True)

    assert MIN_RR < 2.0, "the default RR is only 'not low' while it exceeds MIN_RR"
    assert "low-rr" not in plan((1.2, 2.0)).warnings
    low = plan((0.5, 1.0))
    assert low.rr_ratio == pytest.approx(1.0)
    assert "low-rr" in low.warnings


# ---------------------------------------------------------------------------
# The take-profit ladder: one default, one published number, one way to change it
# ---------------------------------------------------------------------------

def test_the_ladder_is_parsed_or_refused():
    """`NBX_RR_LEVELS` is a trading policy, so a half-understood value is worse
    than the documented default: the parser refuses instead of guessing."""
    from risk_engine import DEFAULT_RR_LEVELS, parse_rr_levels

    assert parse_rr_levels(None) == DEFAULT_RR_LEVELS
    assert parse_rr_levels("") == DEFAULT_RR_LEVELS
    assert parse_rr_levels("1.5,2.5") == (1.5, 2.5)
    assert parse_rr_levels(" 1.5 ; 2.5 ") == (1.5, 2.5)

    for bad in ("1.5", "2,1", "abc", "1.5,0", "1.5,-2", "nan,2"):
        with pytest.raises(ValueError):
            parse_rr_levels(bad)


def test_the_server_reads_the_ladder_from_the_environment(monkeypatch):
    """Setting the variable has to reach the live plan, and a broken value has to
    leave it alone (the server logs and keeps the default)."""
    import main
    from risk_engine import DEFAULT_RR_LEVELS

    monkeypatch.setenv("NBX_RR_LEVELS", "1.5,2.5")
    assert main.risk_config_from_env().rr_levels == (1.5, 2.5)

    monkeypatch.setenv("NBX_RR_LEVELS", "2,1")
    assert main.risk_config_from_env().rr_levels == DEFAULT_RR_LEVELS


def test_the_page_can_read_the_ladder_it_is_judged_by():
    """The live ladder is published with the caps, like `minRR`: a client that
    wants to compare it with the backtest's best must not keep a copy."""
    from fastapi.testclient import TestClient

    import main
    from risk_engine import RiskConfig, RiskEngine

    mp = pytest.MonkeyPatch()
    mp.setattr(main, "risk", RiskEngine(RiskConfig(equity=1_000.0, rr_levels=(1.5, 2.5))))
    caps = TestClient(main.app).get("/api/risk/state").json()["caps"]
    mp.undo()

    assert caps["rrLevels"] == [1.5, 2.5]
    assert caps["minRR"] == pytest.approx(1.5)


def test_the_browser_labels_scores_with_the_same_bars_as_the_server():
    """`ta_engine.LONG_SCORE_MIN` / `NEUTRAL_SCORE_MIN` versus the two `const`s in
    `nbx-core.js`. They agreed only by coincidence before this test, and 1.0.16
    made both sides *parameters* — which is exactly when a default starts to
    drift unnoticed."""
    import ta_engine

    assert _js_constant("LONG_SCORE_MIN") == pytest.approx(ta_engine.LONG_SCORE_MIN)
    assert _js_constant("NEUTRAL_SCORE_MIN") == pytest.approx(ta_engine.NEUTRAL_SCORE_MIN)


def test_the_javascript_score_function_accepts_the_same_override():
    """A parameter on one side and a hardcoded literal on the other is how the
    A/B harness would end up measuring a bar only the server obeys."""
    text = (ROOT / "nbx-core.js").read_text(encoding="utf-8")
    assert "TA.signalScore = function (candles, opts)" in text
    assert "opts.longMin" in text and "opts.neutralMin" in text
    # No bare literal may survive in the label line.
    assert "score >= 68" not in text and "score >= 55" not in text
