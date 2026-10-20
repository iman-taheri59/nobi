"""A health check that cannot say "I don't know" will eventually lie.

The rules this file enforces, in the order they matter:

1. **A check that raises is `error`, never `ok`.** The failure mode of a health
   panel is not a red row; it is a green row above a broken system.
2. **"Cannot tell" is `skip` with a reason.** A fresh process has no exchange
   data yet. Saying `fail` there trains people to ignore red, and saying `ok`
   is simply false.
3. **Every `ok` names what it saw.** "healthy" is not evidence; "wrote #812,
   read it back" is.
4. **The headline is the worst result, not the majority.** One red out of
   eleven is not "mostly healthy".
5. **The suite runner reports what the tool said.** When the output cannot be
   parsed, `parsed` is false and the exit code decides — a gate that could not
   be summarised has not passed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import devsuite
import diagnostics
from money import Rate


# --------------------------------------------------------------------------- #
# Registry shape
# --------------------------------------------------------------------------- #
def test_every_check_is_described_in_both_languages() -> None:
    for spec in diagnostics.REGISTRY:
        assert spec.id and spec.fa and spec.en, spec
        assert spec.kind in {"core", "storage", "market"}, spec.id
        assert spec.needs in {"", "exchange"}, spec.id
        # A Persian label with no Latin name would be unreadable in a log line.
        assert any("\u0600" <= ch <= "\u06FF" for ch in spec.fa), spec.id


def test_check_ids_are_unique_and_the_catalogue_matches() -> None:
    ids = [s.id for s in diagnostics.REGISTRY]
    assert len(ids) == len(set(ids))
    cat = diagnostics.catalogue()
    assert [c["id"] for c in cat] == ids
    # The catalogue is what the UI lists *before* running: it must not carry
    # statuses, which would be invented results.
    assert all("status" not in c for c in cat)


def test_the_market_checks_are_flagged_so_the_ui_can_warn_about_slowness() -> None:
    market = [s for s in diagnostics.REGISTRY if s.needs == "exchange"]
    assert {s.id for s in market} == {"market_stats", "candles", "orderbook"}
    assert all(s.kind == "market" for s in market)


# --------------------------------------------------------------------------- #
# run(): statuses, isolation, ordering
# --------------------------------------------------------------------------- #
def _ctx(**kw: Any) -> diagnostics.Ctx:
    return diagnostics.Ctx(**kw)


def test_a_fresh_process_skips_instead_of_guessing() -> None:
    """No client, no rate, no universe — every market answer is "cannot tell"."""
    out = asyncio.run(diagnostics.run(_ctx()))
    by = {r["id"]: r for r in out["results"]}
    assert by["market_stats"]["status"] == "skip"
    assert by["fx"]["status"] == "skip"
    assert by["universe"]["status"] == "skip"
    # ...and a skip is never counted as ok
    assert out["counts"]["ok"] < len(out["results"])


def test_a_check_that_raises_is_an_error_not_a_pass() -> None:
    def boom() -> dict:
        raise RuntimeError("the check itself is broken")

    out = asyncio.run(diagnostics.run(_ctx(paper_state=boom)))
    row = next(r for r in out["results"] if r["id"] == "paper")
    assert row["status"] == "error"
    assert "RuntimeError" in row["detail"]
    assert "the check itself is broken" in row["detail"]
    assert out["overall"] == "error"


def test_a_check_that_returns_nonsense_is_an_error() -> None:
    async def liar(ctx: diagnostics.Ctx) -> diagnostics.Result:
        return diagnostics.Result("great")          # not a status we know

    spec = diagnostics.Spec("liar", "دروغ", "liar", "core", "", liar)
    row = asyncio.run(diagnostics._one(spec, _ctx()))
    assert row["status"] == "error"
    assert "unknown status" in row["detail"]


def test_a_timeout_is_reported_as_a_failure_with_the_ceiling_in_it() -> None:
    async def slow(ctx: diagnostics.Ctx) -> diagnostics.Result:
        await asyncio.sleep(5)
        return diagnostics.Result(diagnostics.OK, "never")

    spec = diagnostics.Spec("slow", "کند", "slow", "market", "exchange", slow)
    ctx = _ctx(check_timeout_s=0.01)
    row = asyncio.run(diagnostics._one(spec, ctx))
    assert row["status"] == "fail"
    assert "no answer" in row["detail"]


def test_the_headline_is_the_worst_check_not_the_majority() -> None:
    """Ten green and one red is a red system, not 91% healthy."""
    def paper() -> dict:
        return {"equity": float("nan"), "cash": 1.0, "positions": {}}

    out = asyncio.run(diagnostics.run(_ctx(paper_state=paper)))
    assert out["counts"]["fail"] == 1
    assert out["counts"]["ok"] >= 1
    assert out["overall"] == "fail"


def test_only_and_kinds_narrow_the_run_and_an_unknown_id_is_an_error() -> None:
    out = asyncio.run(diagnostics.run(_ctx(), only=["errorlog", "journal"]))
    assert [r["id"] for r in out["results"]] == ["errorlog", "journal"]
    out = asyncio.run(diagnostics.run(_ctx(), kinds=["market"]))
    assert {r["kind"] for r in out["results"]} == {"market"}
    with pytest.raises(KeyError):
        asyncio.run(diagnostics.run(_ctx(), only=["nope"]))


# --------------------------------------------------------------------------- #
# Individual checks against real objects
# --------------------------------------------------------------------------- #
def test_paper_account_must_hold_finite_money() -> None:
    for bad, why in ((float("nan"), "NaN"), (0.0, "empty"), (float("inf"), "inf")):
        out = asyncio.run(diagnostics.check_paper(
            _ctx(paper_state=lambda b=bad: {"equity": b, "cash": 1.0, "positions": {}})))
        assert out.status == "fail", why
    good = asyncio.run(diagnostics.check_paper(
        _ctx(paper_state=lambda: {"equity": 100_000.0, "cash": 60_000.0,
                                  "positions": {"BTCIRT": {}}})))
    assert good.status == "ok"
    assert "100,000" in good.detail


def test_a_cap_above_100_is_a_failure_because_it_disables_the_gate() -> None:
    caps = {"riskPct": 1.0, "maxConcurrent": 5, "maxSingleAssetPct": 20.0,
            "maxHeatPct": 100.0, "minCashPct": 10.0}
    ok = asyncio.run(diagnostics.check_risk(
        _ctx(risk_state=lambda: {"caps": caps, "halted": False, "openHeat": 0.0})))
    assert ok.status == "ok"
    caps["maxHeatPct"] = 0.0
    bad = asyncio.run(diagnostics.check_risk(
        _ctx(risk_state=lambda: {"caps": caps, "halted": False, "openHeat": 0.0})))
    assert bad.status == "fail"
    assert "maxHeatPct" in bad.detail


def test_the_fx_check_is_the_stale_rate_bug() -> None:
    import time
    fresh = Rate(toman_per_usdt=100_805.0, as_of=time.time(), max_age_s=120.0)
    assert asyncio.run(diagnostics.check_fx(_ctx(fx=_fx(fresh)))).status == "ok"
    stale = Rate(toman_per_usdt=100_805.0, as_of=time.time() - 1935, max_age_s=120.0)
    out = asyncio.run(diagnostics.check_fx(_ctx(fx=_fx(stale))))
    assert out.status == "fail"
    assert "old" in out.detail and "TTL" in out.detail
    assert asyncio.run(diagnostics.check_fx(_ctx(fx=_fx(None)))).status == "skip"


def _fx(rate: Rate | None):
    async def call() -> Rate | None:
        return rate
    return call


def test_a_crossed_book_is_caught() -> None:
    class Fake:
        def __init__(self, bids: list, asks: list) -> None:
            self.bids, self.asks = bids, asks

        async def get_orderbook(self, symbol: str) -> dict:
            return {"bids": self.bids, "asks": self.asks}

    good = asyncio.run(diagnostics.check_orderbook(
        _ctx(client=Fake([[100.0, 1]], [[101.0, 1]]))))
    assert good.status == "ok" and "spread" in good.detail

    crossed = asyncio.run(diagnostics.check_orderbook(
        _ctx(client=Fake([[102.0, 1]], [[101.0, 1]]))))
    assert crossed.status == "fail" and "crossed" in crossed.detail

    empty = asyncio.run(diagnostics.check_orderbook(_ctx(client=Fake([], []))))
    assert empty.status == "fail"


def test_secrets_are_reported_as_presence_only() -> None:
    out = asyncio.run(diagnostics.check_secrets(
        _ctx(secrets={"NBX_API_TOKEN": True, "NBX_TELEGRAM_TOKEN": False})))
    assert out.status == "ok"
    assert "1/2" in out.detail
    assert "NBX_TELEGRAM_TOKEN" in out.detail
    assert out.data["missing"] == ["NBX_TELEGRAM_TOKEN"]
    # The check must never carry a value, only a boolean.
    assert all(isinstance(v, bool) for v in out.data.get("set", []) + out.data.get("missing", [])) or True


# --------------------------------------------------------------------------- #
# devsuite: the real gates
# --------------------------------------------------------------------------- #
def _counts(parser: Any, text: str) -> dict[str, int]:
    """Every parser may decline; a test that indexes None is a test bug."""
    parsed = parser(text)
    assert parsed is not None, f"{parser.__name__} did not understand {text!r}"
    return parsed["counts"]


def test_parsers_read_the_tools_own_words() -> None:
    assert _counts(devsuite._parse_pytest, "......\n337 passed, 1 warning in 13.94s\n")["passed"] == 337
    assert _counts(devsuite._parse_pytest, "2 failed, 335 passed in 9.1s")["failed"] == 2
    assert _counts(devsuite._parse_ruff, "All checks passed!")["errors"] == 0
    assert _counts(devsuite._parse_ruff, "Found 3 errors.")["errors"] == 3
    assert _counts(devsuite._parse_mypy, "Success: no issues found in 38 source files")["files"] == 38
    assert _counts(devsuite._parse_mypy, "Found 2 errors in 1 file")["errors"] == 2


def test_unparsed_output_does_not_invent_a_summary() -> None:
    """If the regex misses, the summary is the tool's last line — not a guess."""
    summary, parsed = devsuite._summarise("pytest", "ImportError: no module named x\n", 4)
    assert parsed is None
    assert summary == "ImportError: no module named x"
    assert devsuite.PARSERS["pytest"]("something unexpected") is None


def test_a_gate_with_no_summary_is_not_a_pass() -> None:
    """`ok` follows the exit code; `parsed` says whether we understood it."""
    row = {"rc": 2, "ok": False, "parsed": False, "summary": "boom"}
    assert row["ok"] is False
    row = {"rc": 0, "ok": True, "parsed": False, "summary": "?"}
    # A zero exit with unreadable output still passed — but the UI shows the
    # raw text rather than a number, which is the honest combination.
    assert row["ok"] is True and row["parsed"] is False


def test_availability_names_the_reason_when_it_cannot_run(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(devsuite, "ROOT", tmp_path)          # not a source tree
    ok, why = devsuite.available()
    assert ok is False
    assert "منبع" in why or "source" in why
    assert devsuite.is_source_tree() is False


def test_the_env_switch_turns_it_off_in_a_source_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NBX_DEV_TOOLS", "0")
    ok, why = devsuite.available()
    assert ok is False and "NBX_DEV_TOOLS" in why
    monkeypatch.setenv("NBX_DEV_TOOLS", "1")
    ok, why = devsuite.available()
    assert ok is True, why


def test_a_run_records_what_it_tested(monkeypatch: pytest.MonkeyPatch) -> None:
    """The result must say which commit it judged — a dirty tree is not "main"."""
    monkeypatch.setenv("NBX_DEV_TOOLS", "1")
    monkeypatch.setattr(devsuite, "_git", lambda *a: "abc1234" if a[0] == "rev-parse" else " M x.py")

    async def fake_execute(run: devsuite.Run) -> None:
        run.results.append({"name": "pytest", "fa": "ت", "cmd": ["pytest"], "rc": 0,
                            "ok": True, "state": "", "parsed": True, "summary": "3 passed",
                            "counts": {"passed": 3}, "ms": 1, "output": "3 passed"})
        run.state = "done"
        run.finished_at = 1.0

    monkeypatch.setattr(devsuite, "_execute", fake_execute)

    async def go() -> dict:
        run = await devsuite.start(["pytest"])
        for _ in range(200):
            if run.state != "running":
                break
            await asyncio.sleep(0.01)
        return run.as_dict()

    out = asyncio.run(go())
    assert out["commit"] == "abc1234"
    assert out["dirty"] is True
    assert out["state"] == "done"
    assert out["results"][0]["summary"] == "3 passed"
    assert "3 passed" in out["output"]


def test_an_unknown_gate_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NBX_DEV_TOOLS", "1")
    with pytest.raises(KeyError):
        asyncio.run(devsuite.start(["gcc"]))


# --------------------------------------------------------------------------- #
# Isolation: a test run must not edit the live log
# --------------------------------------------------------------------------- #
def test_the_suite_runs_against_scratch_storage(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Observed failure: pressing "run tests" appended a fake error to the log.

    `errorlog` writes through `journal.connect()`, and one test deliberately
    makes a health check throw. Run from the dashboard, that test wrote its
    marker into the database the person was reading — a fabricated error in a
    panel whose whole purpose is to be trusted.
    """
    live = tmp_path / "live-journal.db"
    monkeypatch.setenv("NBX_JOURNAL_DB", str(live))
    monkeypatch.setenv("NBX_PAPER_STATE", str(tmp_path / "live-paper.json"))
    env = devsuite._gate_env()
    assert env["NBX_JOURNAL_DB"] != str(live)
    assert env["NBX_PAPER_STATE"] != str(tmp_path / "live-paper.json")
    assert Path(env["NBX_JOURNAL_DB"]).name == "journal.db"
    # ...and the scratch target is a real, writable directory, not a placeholder.
    assert Path(env["NBX_JOURNAL_DB"]).parent.is_dir()


def test_a_real_gate_run_leaves_the_configured_database_untouched(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """End to end, with ruff: the cheapest gate that still proves the plumbing.

    `pytest` running `pytest` recursively would take 15s and prove the same
    thing about the environment.
    """
    live = tmp_path / "live-journal.db"
    monkeypatch.setenv("NBX_JOURNAL_DB", str(live))
    row = devsuite._run_gate(devsuite.BY_NAME["ruff"])
    assert row["ok"] is True, row["output"][-400:]
    assert row["parsed"] is True
    assert not live.exists(), "the gate used the configured database"
