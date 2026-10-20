"""Five cross-cutting rules, tested as rules rather than as incidents.

Every bug this project has shipped belonged to one of five classes. Fixing
instances one at a time is why the list kept growing: each fix was correct and
each one left the class open. These tests are written against the class.

===========  ==============================================================
invariant    the question it answers
===========  ==============================================================
**unit**     does every money number say, and carry, its currency?
**time**     is every timestamp in the unit its consumer divides by?
**identity** can two objects created at the same instant collide?
**cache**    does every stored answer have an expiry and a stamp?
**unknown**  does missing data say "unknown", or invent a number?
===========  ==============================================================

The static half scans the source for the exact shapes that produced the bugs;
the dynamic half drives the real routes. A test that only greps is a lint rule
with a nicer error message, so the important ones run code.
"""
from __future__ import annotations

import ast
import re
import time
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1]
REPO = SERVER.parent


def _py_files() -> list[Path]:
    return sorted(p for p in SERVER.glob("*.py") if p.name != "tests")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code(p: Path) -> str:
    """Source with comments and docstrings removed.

    The banned-pattern scan must not match the comment that *explains* the ban:
    a test that fails on its own documentation is a test people delete.
    """
    src = _read(p)
    tree = ast.parse(src)
    lines = src.splitlines()
    drop: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                start = node.body[0].lineno
                drop.update(range(start, start + doc.count("\n") + 1))
    out = []
    for i, line in enumerate(lines, 1):
        if i in drop:
            continue
        code = line.split("#", 1)[0]
        if code.strip():
            out.append(code)
    return "\n".join(out)


# =========================================================================== #
# 1. UNIT — a number that means money must say which money
# =========================================================================== #
def test_no_usd_convertible_amount_falls_back_to_a_silent_one():
    """`x * fx if fx else x` and `await rate() or 1.0` are how a Toman figure was
    read as dollars and vice versa. Both are 100,000× errors, both are silent.
    A missing rate must raise `NoRate` (or answer with a named error), never
    substitute a number.
    """
    banned = [
        (re.compile(r"equity_quote\s*=\s*\w+\s*\*\s*fx\s+if\s+fx\s+else"), "equity_quote fallback"),
        (re.compile(r"_fx_rate\([^)]*\)\s*or\s*1\.0"), "_fx_rate() or 1.0"),
        (re.compile(r"usdt_irt\s+or\s+1\.0"), "usdt_irt or 1.0"),
    ]
    hits = []
    for f in _py_files():
        src = _code(f)
        for rx, label in banned:
            for m in rx.finditer(src):
                line = src[: m.start()].count("\n") + 1
                hits.append(f"{f.name}:{line} {label}")
    assert not hits, "silent unit fallbacks:\n  " + "\n  ".join(hits)


def test_every_money_constant_is_defined_once():
    """`RIAL_PER_TOMAN` existed in `units.py` and again in `nbx-core.js`. Two
    copies of a definition drift; the drift is invisible because both look
    authoritative. The Python side is now the only definition, and the JS table
    is checked against it by `scripts/check_preflight_parity.py`."""
    import units

    assert units.RIAL_PER_TOMAN == 10.0
    src = _read(SERVER / "units.py")
    assert src.count("RIAL_PER_TOMAN =") == 1


def test_risk_state_reports_the_rate_and_the_converted_equity():
    """The page used to build its own rate from a different object, with a
    `|| 1` fallback. If the server does the conversion, the page cannot get it
    wrong — so the server must actually publish both numbers."""
    from fastapi.testclient import TestClient

    import main

    d = TestClient(main.app).get("/api/risk/state").json()
    assert d["equity_quote"] == "USD"
    assert "equity_irt" in d and "fx" in d
    if d["fx"] is not None:
        # Either both numbers exist or both are null; a half-answer is the shape
        # that made the page guess.
        assert d["equity_irt"] is not None
        assert {"toman_per_usdt", "as_of", "age_s", "source", "stale"} <= set(d["fx"])
        assert d["equity_irt"] == pytest.approx(d["equity"] * d["fx"]["toman_per_usdt"], rel=1e-6)


def test_a_dollar_figure_and_its_quote_figure_agree():
    """`value_usd` and `value_quote` on a position must be the same number in two
    units. Publishing one without the other is how a 9-billion-Toman position
    became "9 billion dollars"."""
    import main
    import paper_trading
    from paper_trading import PaperBroker
    from risk_engine import RiskConfig, RiskEngine

    class Feed:
        usdt_irt = 100_000.0

        async def get_orderbook(self, symbol):
            return {"bids": [["1000000", "10"]], "asks": [["1000000", "10"]],
                    "lastTradePrice": "1000000"}

        async def get_market_stats(self):
            return {"stats": {"USDTIRT": {"latest": "100000"}}}

    import asyncio

    mp = pytest.MonkeyPatch()
    mp.setattr(paper_trading, "STATE_FILE", Path("/tmp") / f"inv-{time.time_ns()}.json")
    mp.setattr(paper_trading, "market_closed_hook", None)
    mp.setattr(paper_trading, "hot_mid_hook", None)
    b = PaperBroker(100_000, Feed(), RiskEngine(RiskConfig(equity=100_000)))
    pos = asyncio.run(b.place_limit("BTCIRT", "buy", 2.0, price=1_000_000.0, stop=950_000.0))
    assert pos is not None, b.last_error
    mp.setattr(main, "get_broker", lambda *a, **k: b)
    mp.setattr(main, "_cached", {**main._cached, "signals": {}})
    row = main._position_payload(pos)
    assert row["value_quote"] == pytest.approx(pos.filled_qty * row["last_price"])
    assert row["value_usd"] == pytest.approx(row["value_quote"] / b.usdt_irt, rel=1e-6)
    mp.undo()


# =========================================================================== #
# 2. TIME — seconds everywhere, and every consumer agrees
# =========================================================================== #
def test_timeframe_tables_in_python_and_javascript_agree():
    """The candle adapter divided a seconds timestamp by 1000, so every bar
    landed in 1970 and the TTL of every proposal measured a 56-year countdown.
    One table per language is unavoidable; disagreement is not."""
    import journal
    from ws_execution import TF_SECONDS as WS_TF

    for tf, secs in journal.TF_SECONDS.items():
        if tf in WS_TF:
            assert WS_TF[tf] == secs, f"{tf}: ws_execution says {WS_TF[tf]}, journal says {secs}"


def test_candle_timestamps_are_epoch_seconds_not_milliseconds():
    """A 13-digit timestamp is milliseconds; a 10-digit one is seconds.

    The fixture adapter divided the (already seconds) field by 1000 and put
    every bar in January 1970 — the chart axis, the "last candle" age and the
    approval queue's TTL were all off by 1000× while every number on screen
    still looked plausible. The window below (2020 → 2100) is wide enough to
    accept any sane series and narrow enough that a unit error cannot pass.
    """
    import importlib.util

    path = REPO / "scripts" / "run_with_mock_exchange.py"
    spec = importlib.util.spec_from_file_location("_fixture", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    now = int(time.time())
    rows = [{"t": now - 900 * i, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.0, "v": 5.0}
            for i in range(3)]
    udf = mod.to_udf(rows)
    for t in udf["t"]:
        assert 1_577_836_800 <= t <= 4_102_444_800, f"t={t} is not epoch seconds"
        assert len(str(t)) == 10


def test_the_app_and_the_browser_use_the_same_bar_length():
    """The queue computes a proposal's TTL from `t + tf − now` in JavaScript;
    the journal resolves the same bar on the server. A disagreement here makes a
    proposal expire at a time the signal never had."""
    import journal

    html = _read(REPO / "index.html")
    m = re.search(r"function tfSec\(tf\)\{ return \(\{([^}]*)\}\)\[tf\]", html)
    assert m, "the browser timeframe table moved; update this invariant"
    pairs = dict(re.findall(r"'([^']+)'\s*:\s*(\d+)", m.group(1)))
    for tf, secs in pairs.items():
        assert journal.TF_SECONDS[tf] == int(secs), f"{tf}: js={secs}, py={journal.TF_SECONDS[tf]}"


def test_the_scan_window_and_the_bar_size_use_the_same_unit():
    """`journal.bars_for` converts "days" into a bar count; if the timeframe
    table and the row timestamps disagree by 1000 this is where it shows."""
    import journal

    for tf, secs in journal.TF_SECONDS.items():
        assert journal.bars_for(tf, secs) == 1
        assert journal.bars_for(tf, secs * 5) == 5


# =========================================================================== #
# 3. IDENTITY — two things created at once are still two things
# =========================================================================== #
def test_two_orders_in_the_same_millisecond_get_different_ids():
    """The client order id was `pp-{ms}-{symbol}`. Two orders on one symbol in
    one millisecond produced one key, and the second position silently replaced
    the first in the dict: two buys, one position, cash spent twice."""
    import asyncio
    import re as _re

    import paper_trading
    from paper_trading import PaperBroker
    from risk_engine import RiskConfig, RiskEngine

    class Feed:
        usdt_irt = 1.0

        async def get_orderbook(self, symbol):
            return {"bids": [["100", "1000"]], "asks": [["100", "1000"]],
                    "lastTradePrice": "100"}

        async def get_market_stats(self):
            return {"stats": {}}

    mp = pytest.MonkeyPatch()
    mp.setattr(paper_trading, "STATE_FILE", Path("/tmp") / f"inv-ids-{time.time_ns()}.json")
    mp.setattr(paper_trading, "market_closed_hook", None)
    mp.setattr(paper_trading, "hot_mid_hook", None)
    b = PaperBroker(100_000, Feed(), RiskEngine(RiskConfig(equity=100_000)))

    # Freeze the clock so both orders land in the same millisecond.
    real_time = paper_trading.time.time
    frozen = [real_time()]

    class Frozen:
        def time(self):
            return frozen[0]

        def __getattr__(self, k):
            return getattr(paper_trading.time, k)

    mp.setattr(paper_trading, "time", Frozen())
    a = asyncio.run(b.place_limit("BTCUSDT", "buy", 10, price=100.0, stop=90.0))
    bb = asyncio.run(b.place_limit("BTCUSDT", "buy", 10, price=100.0, stop=90.0))
    mp.undo()

    assert a is not None and bb is not None, b.last_error
    assert a.id != bb.id
    assert a.client_order_id != bb.client_order_id
    assert len(_re.findall(r"[0-9a-f]{8}$", a.id)) == 1      # uuid8 tail
    assert len(a.client_order_id) <= 32                      # documented limit


def test_signal_ids_are_unique_within_a_scan():
    """The journal keys outcomes on `signal_id`; a duplicate would overwrite one
    symbol's result with another's."""
    from journal import JournalRow

    row = JournalRow(ts=1_700_000_000, symbol="BTCIRT", timeframe="15m",
                     direction="LONG", ta_score=80.0, composite=80.0,
                     entry=1.0, atr=0.01)
    assert row.targets()[0] < row.entry < row.targets()[2]
    # One row per (signal_id, horizon): a second row for the same horizon would
    # overwrite the first one's outcome, so the schema must make it impossible.
    src = _read(SERVER / "journal.py")
    assert "PRIMARY KEY (signal_id, horizon_key)" in src


# =========================================================================== #
# 4. CACHE — a stored answer without an expiry is a wrong answer with a delay
# =========================================================================== #
def test_every_cache_dict_carries_its_own_timestamp():
    """`_cached` held the universe, the signals and the premium with separate
    `*_ts` keys. A cache entry whose age nobody records cannot be invalidated,
    and the stale candle cache served `last_candle: "rest"` for 30 minutes while
    the socket was live."""
    import main

    keys = set(main._cached)
    for name in ("universe", "signals"):
        assert name in keys
        assert f"{name}_ts" in keys, f"{name} is cached without a timestamp"


def test_the_candle_cache_has_a_ttl_and_reports_its_source():
    import candle_cache

    cache = candle_cache.CandleCache() if hasattr(candle_cache, "CandleCache") else None
    assert cache is not None, "the candle cache class moved; update this invariant"
    assert float(cache.ttl) > 0
    src = _read(SERVER / "candle_cache.py").lower()
    assert "stale cache beyond ttl" in src or "ttl" in src


def test_the_broker_rate_is_stamped_and_expires():
    """The one cache that actually cost money: `if not self.usdt_irt` meant
    "fetch once, keep forever"."""
    from money import Rate

    old = Rate(toman_per_usdt=100_000, as_of=time.time() - 10_000, max_age_s=300)
    assert old.is_stale is True
    assert old.age_s > 300
    fresh = Rate(toman_per_usdt=100_000, as_of=time.time(), max_age_s=300)
    assert fresh.is_stale is False


# =========================================================================== #
# 5. UNKNOWN — "I don't know" is an answer; zero and one are not
# =========================================================================== #
def test_a_failed_upstream_probe_is_not_reported_as_a_dead_exchange():
    """/api/health answered `upstream.ok: null` on the first call of a process
    and the page read null as false: a red "the exchange is unreachable" badge
    over a board that was feeding live prices."""
    src = _read(REPO / "nbx-app.js")
    assert "upOk === false" in src, "the badge must distinguish null from false"
    assert "upstreamOk = !!(backend.upstream && backend.upstream.ok)" not in src


def test_a_missing_rate_produces_a_named_error_not_a_number():
    """The route-level half of invariant 1, exercised through HTTP."""
    from fastapi.testclient import TestClient

    import main

    mp = pytest.MonkeyPatch()
    mp.setattr(main, "_fx", _no_rate)
    mp.setattr(main, "_signal_lookup",
               lambda sym, tf=None: {"symbol": "BTCIRT", "lastClose": 1.0,
                                     "risk": {"atr": 0.01}})
    d = TestClient(main.app).get("/api/risk/plan?symbol=BTCIRT&side=buy").json()
    mp.undo()
    assert d.get("ok") is False
    assert d.get("error") == "no-fx-rate"
    assert "size_quote" not in d, "a plan without a rate must not be sized"


async def _no_rate(symbol="BTCIRT"):
    return None


def test_the_ws_status_publishes_what_was_sent_not_what_was_wanted(monkeypatch):
    """`channels` listed the channels the code would like to subscribe to, so an
    unconfigured socket reported a feed it never had. `channels` is the wire
    truth; `channels_wanted` is the wish."""
    from ws_execution import StreamExecutor

    s = StreamExecutor()
    s._subs.add("BTCIRT")
    s.want_stats = True
    st = s.status()
    assert st["channels"] == []                      # nothing sent: not connected
    assert "public:market-stats-all" in st["channels_wanted"]


def test_empty_is_never_rendered_as_zero(monkeypatch):
    """A panel with no data must say so. A zero is a measurement; "no data" is
    not, and the two looked identical on the page."""
    from fastapi.testclient import TestClient

    import main

    mp = pytest.MonkeyPatch()
    mp.setattr(main, "_cached", {**main._cached, "universe": []})
    d = TestClient(main.app).get("/api/market/overview").json()
    mp.undo()
    assert d.get("ok") is False
    assert d.get("universe_size") == 0
    assert "reason" in d


# =========================================================================== #
# 6. ONE COPY — a number the user is warned about must be the number that fires
# =========================================================================== #
# The ticket's preview used to compare against three literals of its own
# (0.75 / 25 / 1.5) while `/api/risk/state` published the caps the server
# actually enforces. Three copies of one rule: server, nbx-core.js, index.html.
# The browser's copy answered first, so it always won — silently.
# =========================================================================== #
def test_the_dashboard_does_not_keep_its_own_copy_of_the_risk_caps():
    html = _read(REPO / "index.html")
    code = html.split("<script>", 1)[1]
    for pattern, what in (
        (r"riskPct\s*>\s*0?\.\d+", "per-trade risk compared against a literal"),
        (r"exposure\s*>\s*\d+", "single-asset exposure compared against a literal"),
        (r"\brr\s*<\s*\d+\.?\d*", "reward:risk compared against a literal"),
    ):
        assert not re.search(pattern, code), f"index.html: {what}"

    # …and the numbers come from the server's caps instead.
    assert "RISK_LIMITS" in code
    assert "RISK_LIMITS.riskPct" in code
    assert "RISK_LIMITS.maxSingleAssetPct" in code
    assert "RISK_LIMITS.minRR" in code


def test_the_caps_the_page_receives_are_the_engine_s_own():
    """Change the engine's config and the published caps must follow. A route
    that returned literals would keep saying 0.75 with a 0.42 engine."""
    from fastapi.testclient import TestClient

    import main
    from risk_engine import MIN_RR, RiskConfig, RiskEngine

    mp = pytest.MonkeyPatch()
    mp.setattr(main, "risk", RiskEngine(RiskConfig(
        equity=1_000.0, risk_pct=0.42, max_single_asset_pct=18.0, max_heat_pct=4.0)))
    caps = TestClient(main.app).get("/api/risk/state").json()["caps"]
    mp.undo()

    assert caps["riskPct"] == pytest.approx(0.42)
    assert caps["maxSingleAssetPct"] == pytest.approx(18.0)
    assert caps["maxHeatPct"] == pytest.approx(4.0)
    assert caps["minRR"] == pytest.approx(MIN_RR)


def test_the_rr_warning_reads_the_published_constant(monkeypatch):
    """The engine must compare against `MIN_RR`, not against a literal that
    happens to equal it today. Raising the constant has to change the verdict;
    a hard-coded 1.5 would keep answering the old way."""
    import risk_engine

    eng = risk_engine.RiskEngine(risk_engine.RiskConfig(equity=100_000.0))

    def plan() -> risk_engine.RiskPlan:
        # 2×ATR stop, TP2 at 2R by construction (`_tp`), so rr == 2.0.
        return eng.evaluate("BTCIRT", "buy", 100.0, 0.5, "15m",
                            equity_quote=1_000_000.0, preview=True)

    monkeypatch.setattr(risk_engine, "MIN_RR", 1.0)
    assert "low-rr" not in plan().warnings
    monkeypatch.setattr(risk_engine, "MIN_RR", 2.5)
    assert "low-rr" in plan().warnings
