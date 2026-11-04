"""Browser and server must compute the same causal indicators and score series."""
from __future__ import annotations

import json
import math
import random
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from ta_engine import adx_di, atr, bollinger, candles_to_df, rsi, ta_score, ta_score_series

ROOT = Path(__file__).resolve().parents[2]


def _rows(n: int = 180) -> list[dict]:
    rng = random.Random(20261104)
    price = 100.0
    out = []
    for i in range(n):
        op = price
        close = max(1.0, op * (1 + rng.uniform(-0.025, 0.025) + (0.001 if i % 45 < 25 else -0.001)))
        hi = max(op, close) * (1 + rng.uniform(0, 0.012))
        lo = min(op, close) * (1 - rng.uniform(0, 0.012))
        out.append({"t": 1_700_000_000 + i * 900, "o": op, "h": hi, "l": lo,
                    "c": close, "v": 50 + (i % 17) * 9 + rng.uniform(0, 100)})
        price = close
    return out


def _series(values) -> list[float | None]:
    return [None if pd.isna(x) else float(x) for x in values]


def _assert_optional_arrays(actual, expected):
    assert len(actual) == len(expected)
    for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
        if e is None:
            assert a is None, f"index {i}: browser={a}, expected null"
        else:
            assert a == pytest.approx(e, rel=2e-11, abs=2e-11), f"index {i}"


def test_browser_matches_python_primitives_and_every_scored_bar(tmp_path):
    rows = _rows()
    fixture = tmp_path / "candles.json"
    fixture.write_text(json.dumps(rows), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(ROOT / "scripts/js_ta_fixture.js"), str(fixture)],
        cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    js = json.loads(proc.stdout)
    df = candles_to_df(rows)
    py_scores = ta_score_series(df)

    assert js["scores"][:60] == [None] * 60
    for i in range(60, len(rows)):
        browser, server = js["scores"][i], py_scores[i]
        assert server is not None
        assert browser["score"] == server["score"], f"score at {i}"
        assert browser["direction"] == server["direction"], f"direction at {i}"
        assert browser["regime"] == server["regime"], f"regime at {i}"
        assert browser["components"] == server["components"], f"components at {i}"
        assert browser["risk"]["atr"] == pytest.approx(server["risk"]["atr"], rel=2e-11)
        assert browser["risk"]["stopPct"] == server["risk"]["stopPct"]
        for key in ("rsi", "adx", "pctB", "hist"):
            assert browser["diagnostics"][key] == server["diagnostics"][key], f"{key} at {i}"

    scalar = ta_score(df)
    assert js["scalar"]["score"] == scalar["score"]
    assert js["scalar"]["components"] == scalar["components"]

    _assert_optional_arrays(js["rsi"], _series(rsi(df["c"], 14)))
    _assert_optional_arrays(js["atr"], _series(atr(df, 14)))
    _assert_optional_arrays(js["pctB"], _series(bollinger(df["c"], 20, 2)[3]))
    pdi, ndi, adx = adx_di(df, 14)
    _assert_optional_arrays(js["pdi"], _series(pdi))
    _assert_optional_arrays(js["ndi"], _series(ndi))
    _assert_optional_arrays(js["adx"], _series(adx))


def test_score_series_handles_an_empty_array_without_nan_json(tmp_path):
    fixture = tmp_path / "empty.json"
    fixture.write_text("[]", encoding="utf-8")
    proc = subprocess.run(
        ["node", str(ROOT / "scripts/js_ta_fixture.js"), str(fixture)],
        cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    payload = json.loads(proc.stdout)
    assert payload["scores"] == payload["rsi"] == payload["atr"] == []
    assert all(math.isfinite(x) for x in payload["pdi"])
