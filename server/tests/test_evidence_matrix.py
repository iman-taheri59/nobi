"""The evidence matrix writes down what it measured — and what it may not claim.

`scripts/evidence_matrix.py` runs the A/B comparison for several symbols and
timeframes and saves one artefact. Two things can go wrong with an artefact like
that, and neither is a crash:

* **It forgets where the numbers came from.** A verdict computed on the reference
  feed must never be readable as a certification, so the file stamps itself
  `REFERENCE_DATA`, sets `promotable: false`, and `adaptive_evidence` caps the
  verdict at `inconclusive` (ADR-0009/ADR-0014).
* **It makes a failure look like a result.** A named refusal (`bad-factor`,
  `insufficient-data`) is evidence and belongs in the table. An unnamed exception
  is not, and it must change the exit code instead of vanishing into a row.

The tests below drive the script with injected candles — no network, no fixtures
on disk — and read the artefact back the way a person would.
"""
from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))

SCRIPT = ROOT / "scripts" / "evidence_matrix.py"


def _load():
    spec = importlib.util.spec_from_file_location("evidence_matrix", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candles(n: int = 900, *, seed: int = 5, tf_seconds: int = 900) -> list[dict]:
    rng = random.Random(seed)
    price = 100.0
    rows: list[dict] = []
    for i in range(n):
        o = price
        c = max(0.01, o * (1 + rng.uniform(-0.01, 0.01) + 0.0002))
        rows.append({"t": 1_700_000_000 + i * tf_seconds, "o": o, "h": max(o, c) * 1.004,
                     "l": min(o, c) * 0.996, "c": c, "v": 10.0 + i % 7})
        price = c
    return rows


@pytest.fixture()
def matrix(monkeypatch, tmp_path):
    mod = _load()

    async def fake_fetch(symbol: str, tf: str, source: str, bars: int, toman: float | None):
        return candles(tf_seconds=3600), {"provider": "stub"}

    monkeypatch.setattr(mod, "fetch", fake_fetch)
    out = tmp_path / "artefact.json"
    args = SimpleNamespace(source="reference", symbols=["BTCUSDT", "ETHUSDT"],
                           timeframes=["15m", "1h"], bars=900, factor=1.25,
                           toman=None, out=str(out))
    code = __import__("asyncio").run(mod.run(args))
    return mod, json.loads(out.read_text(encoding="utf-8")), code, out


def test_every_cell_is_present_and_carries_its_own_flags(matrix):
    _mod, art, code, _out = matrix
    assert code == 0
    assert art["matrix"]["cells"] == 2 * 2
    assert len(art["cells"]) == 4
    for cell in art["cells"]:
        assert cell["applied"] is False
        assert cell["promotable"] is False
        if cell["ok"]:
            assert cell["verdict"] in {"favours-adaptive", "favours-baseline", "inconclusive"}


def test_a_reference_run_can_never_read_as_a_certification(matrix):
    _mod, art, _code, _out = matrix
    prov = art["provenance"]
    assert prov["label"] == "REFERENCE_DATA"
    assert prov["promotable"] is False
    assert prov["applied"] is False
    assert "cannot certify" in prov["note"] or "forbid" in prov["note"]
    # The word this project reserves for a certified result must not appear at
    # all — a reader greps for it, and a stray "PASS" would be read as one.
    text = _out.read_text(encoding="utf-8")
    assert "PASS" not in text
    assert "CERTIFIED" not in text


def test_a_venue_run_is_stamped_differently_and_still_applies_nothing(monkeypatch, tmp_path):
    mod = _load()

    async def fake_fetch(symbol, tf, source, bars, toman):
        return candles(tf_seconds=3600), {"provider": "stub"}

    monkeypatch.setattr(mod, "fetch", fake_fetch)
    out = tmp_path / "venue.json"
    args = SimpleNamespace(source="nobitex", symbols=["BTCIRT"], timeframes=["1h"], bars=900,
                           factor=1.25, toman=None, out=str(out))
    code = __import__("asyncio").run(mod.run(args))
    art = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0
    assert art["provenance"]["label"] == "VENUE_DATA"
    assert art["provenance"]["promotable"] is True
    assert art["provenance"]["applied"] is False
    assert all(cell["applied"] is False for cell in art["cells"])


def test_a_named_refusal_is_a_row_and_an_unnamed_one_changes_the_exit_code(monkeypatch, tmp_path):
    mod = _load()

    class Refused(Exception):
        code = "no-fx"
        message = "an IRT series needs a toman rate"

    calls = {"n": 0}

    async def fake_fetch(symbol, tf, source, bars, toman):
        calls["n"] += 1
        if symbol == "BTCUSDT":
            raise Refused()
        raise RuntimeError("socket closed")          # unnamed: a crash, not a result

    monkeypatch.setattr(mod, "fetch", fake_fetch)
    out = tmp_path / "mixed.json"
    args = SimpleNamespace(source="reference", symbols=["BTCUSDT", "ETHUSDT"],
                           timeframes=["1h"], bars=900, factor=None, toman=None, out=str(out))
    code = __import__("asyncio").run(mod.run(args))
    art = json.loads(out.read_text(encoding="utf-8"))

    assert calls["n"] == 2
    assert code == 1, "an unnamed exception must not exit 0"
    named = [c for c in art["cells"] if c["named"]]
    unnamed = [c for c in art["cells"] if not c["named"]]
    assert len(named) == 1 and named[0]["error"] == "no-fx"
    assert len(unnamed) == 1 and unnamed[0]["error"] == "RuntimeError"
    assert art["summary"]["refused"] == 2
    assert art["summary"]["unnamed_failures"] == 1


def test_the_summary_counts_add_up(monkeypatch, tmp_path):
    mod = _load()

    async def fake_fetch(symbol, tf, source, bars, toman):
        return candles(tf_seconds=3600), {}

    monkeypatch.setattr(mod, "fetch", fake_fetch)
    out = tmp_path / "sum.json"
    args = SimpleNamespace(source="reference", symbols=["BTCUSDT"], timeframes=["15m", "1h", "4h"],
                           bars=900, factor=1.25, toman=None, out=str(out))
    __import__("asyncio").run(mod.run(args))
    s = json.loads(out.read_text(encoding="utf-8"))["summary"]
    assert s["cells"] == s["answered"] + s["refused"] == 3
    assert (s["favours_adaptive"] + s["favours_baseline"] + s["inconclusive"]) == s["answered"]


def test_the_artefact_records_the_version_that_produced_it(matrix):
    _mod, art, _code, _out = matrix
    assert art["version"] == (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert art["kind"] == "adaptive-evidence-matrix"
    assert art["provenance"]["generated_at"]


def test_the_committed_artefact_is_a_reference_run_and_says_so() -> None:
    """The file in `docs/evidence/` must never be a venue claim it did not earn."""
    files = sorted((ROOT / "docs" / "evidence").glob("adaptive-evidence-*.json"))
    if not files:
        pytest.skip("no committed evidence artefact in this checkout")
    for path in files:
        art: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        prov = art["provenance"]
        if not prov["promotable"]:
            assert prov["label"] == "REFERENCE_DATA"
            assert all(c["applied"] is False for c in art["cells"])
            for cell in art["cells"]:
                if cell["ok"] and art["matrix"].get("source_never_used_for_certification", True):
                    # A non-promotable artefact may not contain a decision-grade verdict.
                    assert cell["verdict"] == "inconclusive", (
                        f"{path.name} carries a '{cell['verdict']}' from a run that "
                        "cannot certify anything")
