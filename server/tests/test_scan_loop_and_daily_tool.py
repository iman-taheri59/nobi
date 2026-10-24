"""ADR-0024 — the evidence window must not depend on an open browser tab.

Before 1.1.1 the only thing that scanned after startup was `GET /api/signals`,
which only an open dashboard calls. The shadow ledger's `days` count (the binding
constraint of the S2 decision in ADR-0023) was therefore held hostage by a habit.

These tests pin the four claims of ADR-0024:

1. the loop is **off** unless asked, and an invalid interval is refused loudly
   rather than rounded into something plausible;
2. a scheduled tick scans and records itself, and one failing scan does not kill
   the loop;
3. `oldest_usable_s` never exceeds the gate TTL, while `oldest_book_s` may — the
   one number that used to contradict its own name;
4. the daily tool reads only, writes one CSV row per run, and refuses to invent a
   row when the server is not answering.
"""
from __future__ import annotations

import asyncio
import csv
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from tools import daily_snapshot


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# 1. The interval: off by default, refused when too small
# --------------------------------------------------------------------------- #
def test_the_loop_is_off_unless_asked(monkeypatch):
    monkeypatch.delenv("NBX_SCAN_LOOP_S", raising=False)
    assert main.scan_loop_seconds() == 0
    assert main.scan_loop_seconds("") == 0
    assert main.scan_loop_seconds("0") == 0


def test_a_valid_interval_is_returned_unchanged(monkeypatch):
    monkeypatch.delenv("NBX_SCAN_LOOP_S", raising=False)
    assert main.scan_loop_seconds("60") == 60
    assert main.scan_loop_seconds("900") == 900


@pytest.mark.parametrize("bad", ["30", "-1", "abc", "15m"])
def test_an_unusable_interval_is_refused_not_trimmed(bad):
    """Silently rounding 30 up to 60 would look identical to a correct setting."""
    with pytest.raises(ValueError):
        main.scan_loop_seconds(bad)


def test_a_refused_interval_leaves_the_loop_off_and_the_process_healthy(monkeypatch):
    monkeypatch.setenv("NBX_SCAN_LOOP_S", "30")
    main._cached.pop("scan_loop", None)
    run(main._scan_loop())                      # returns immediately, does not raise
    state = main._cached["scan_loop"]
    assert state["enabled"] is False
    assert "floor" in state["error"]


def test_the_disabled_loop_returns_at_once(monkeypatch):
    monkeypatch.setenv("NBX_SCAN_LOOP_S", "0")
    main._cached.pop("scan_loop", None)
    run(main._scan_loop())
    state = main._cached["scan_loop"]
    assert state == {"enabled": False, "interval_s": None, "last_at": None, "runs": 0}


def test_health_reports_whether_the_process_is_filling_the_window(monkeypatch):
    """`scan_loop: off` next to a rising `days` means a tab is doing the work."""
    monkeypatch.setenv("NBX_SCAN_LOOP_S", "0")
    run(main._scan_loop())
    payload = run(main.health())
    assert payload["scan_loop"]["enabled"] is False


# --------------------------------------------------------------------------- #
# 2. One tick: it scans, it records, and a failure does not kill it
# --------------------------------------------------------------------------- #
def test_a_tick_scans_and_records_itself(monkeypatch):
    seen: list[str] = []

    async def fake_rescan(tf: str, top: int = 40):
        seen.append(tf)
        return {}

    monkeypatch.setattr(main, "rescan", fake_rescan)
    main._cached["scan_loop"] = {"enabled": True, "interval_s": 900, "runs": 0}
    ok = run(main._scan_loop_tick("15m"))
    assert ok is True and seen == ["15m"]
    state = main._cached["scan_loop"]
    assert state["runs"] == 1 and state["last_ok"] is True
    assert state["last_at"] is not None and state["enabled"] is True


def test_a_failing_tick_is_recorded_and_the_loop_survives(monkeypatch):
    async def boom(tf: str, top: int = 40):
        raise RuntimeError("exchange said no")

    monkeypatch.setattr(main, "rescan", boom)
    main._cached["scan_loop"] = {"enabled": True, "interval_s": 900, "runs": 0}
    ok = run(main._scan_loop_tick("15m"))       # must not raise
    assert ok is False
    assert main._cached["scan_loop"]["last_ok"] is False
    assert main._cached["scan_loop"]["enabled"] is True, "one bad scan disabled the loop"


# --------------------------------------------------------------------------- #
# 3. Two ages: the usable one can be read at face value
# --------------------------------------------------------------------------- #
class Feed:
    async def get_orderbook(self, symbol: str) -> dict:
        return {"bids": [{"p": 99.0, "q": 5.0}], "asks": [{"p": 100.0, "q": 5.0}],
                "lastUpdate": 1}


def test_oldest_usable_never_exceeds_the_gate_ttl(monkeypatch):
    """The 1.1.0 number that lied: 499.6 s of age under a 300 s TTL."""
    monkeypatch.setattr(main, "client", Feed())

    async def go():
        await main.gate_prefetch([{"symbol": "S0IRT"}], n=1)

    run(go())
    main._cached["books"]["S0IRT"]["at"] = time.time() - main.BOOK_GATE_TTL_S - 200
    block = main.gates_block([{"symbol": "S0IRT"}], "15m")
    inputs = block["inputs"]
    assert inputs["stale_books"] == 1
    assert inputs["books"] == 0
    assert inputs["oldest_book_s"] > main.BOOK_GATE_TTL_S      # every book in the cache
    assert inputs["oldest_usable_s"] is None, "a dropped book was called usable"


def test_a_fresh_book_is_both_seen_and_usable(monkeypatch):
    monkeypatch.setattr(main, "client", Feed())

    async def go():
        await main.gate_prefetch([{"symbol": "S0IRT"}], n=1)

    run(go())
    block = main.gates_block([{"symbol": "S0IRT"}], "15m")
    inputs = block["inputs"]
    assert inputs["books"] == 1 and inputs["stale_books"] == 0
    assert inputs["oldest_usable_s"] is not None
    assert 0 <= inputs["oldest_usable_s"] <= main.BOOK_GATE_TTL_S


def test_two_books_one_stale_reports_the_usable_age(monkeypatch):
    monkeypatch.setattr(main, "client", Feed())

    async def go():
        await main.gate_prefetch([{"symbol": "S0IRT"}, {"symbol": "S1IRT"}], n=2)

    run(go())
    main._cached["books"]["S0IRT"]["at"] = time.time() - main.BOOK_GATE_TTL_S - 60
    block = main.gates_block([{"symbol": "S0IRT"}, {"symbol": "S1IRT"}], "15m")
    inputs = block["inputs"]
    assert (inputs["books"], inputs["stale_books"]) == (1, 1)
    assert inputs["oldest_book_s"] > main.BOOK_GATE_TTL_S
    assert inputs["oldest_usable_s"] <= main.BOOK_GATE_TTL_S


# --------------------------------------------------------------------------- #
# 4. The daily tool: read only, one row per run, no invented row
# --------------------------------------------------------------------------- #
HEALTH = {"ok": True, "version": "1.1.1",
          "scan_loop": {"enabled": True, "interval_s": 900}}
SIGNALS = {"gates": {
    "counts": {"rows": 16, "ok": 6, "would_block": 0, "unknown": 10},
    "inputs": {"books": 10, "stale_books": 2, "oldest_usable_s": 41.5,
               "book_gate_ttl_s": 300.0,
               "prefetch": {"asked": 10, "ok": 10, "failed": 0, "fresh": 3}},
    "ledger": {"gates": {"ok": {"verdicts": 42, "days": 3, "ready": False},
                         "no-book": {"verdicts": 18, "days": 2, "ready": False}},
               "limits": {"min_verdicts": 30, "min_days": 5}},
}}


def _fake_get_json(monkeypatch, signals=SIGNALS, health=HEALTH, status=200):
    def fake(url, cookie="", timeout=30.0):
        if url.endswith("/api/health"):
            return health, 200
        return signals, status
    monkeypatch.setattr(daily_snapshot, "get_json", fake)


def test_the_row_is_the_numbers_and_not_a_summary(monkeypatch):
    _fake_get_json(monkeypatch)
    row = daily_snapshot.collect("http://127.0.0.1:8000")
    assert row["books"] == 10 and row["stale_books"] == 2
    assert row["oldest_usable_s"] == 41
    assert (row["pref_ok"], row["pref_asked"]) == (10, 10)
    assert row["days"] == 3, "days must be the max over gates, not the first one"
    assert row["ok"] == 42 and row["no_book"] == 18
    assert row["scan_loop"] == "on"
    assert row["version"] == "1.1.1"


def test_an_unreachable_server_returns_an_error_not_a_row(monkeypatch, tmp_path):
    _fake_get_json(monkeypatch, signals=None, status=0)
    target = tmp_path / "nbx-daily.csv"
    assert daily_snapshot.main(["--csv", str(target)]) == 1
    assert not target.exists(), "a failed read wrote a row anyway"


def test_two_runs_make_two_rows_with_one_header(monkeypatch, tmp_path):
    _fake_get_json(monkeypatch)
    target = tmp_path / "sub" / "nbx-daily.csv"
    assert daily_snapshot.main(["--csv", str(target)]) == 0
    assert daily_snapshot.main(["--csv", str(target)]) == 0
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("ts,version,rows,books")
    with target.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2 and rows[0]["days"] == "3"
    assert set(rows[0]) == set(daily_snapshot.COLUMNS)


def test_the_tool_only_ever_issues_get(monkeypatch):
    """A measurement tool that could change the thing it measures is not one."""
    calls: list[tuple] = []
    real = daily_snapshot.urllib.request.Request

    class Spy(real):                             # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw):
            calls.append((a, kw))
            super().__init__(*a, **kw)

    monkeypatch.setattr(daily_snapshot.urllib.request, "Request", Spy)
    daily_snapshot.get_json("http://127.0.0.1:9/nothing")   # fails; still recorded
    assert calls, "no request was built"
    for _args, kw in calls:
        assert kw.get("method", "GET") == "GET"
        assert "data" not in kw or kw["data"] is None
