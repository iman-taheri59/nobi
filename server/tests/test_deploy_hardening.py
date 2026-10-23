# mypy: check_untyped_defs = False
# Test doubles stand in for objects on purpose; their bodies answer to pytest.
"""Four ways a *correct* deployment used to fail, pinned so it cannot come back.

These are not unit tests of arithmetic. Each one reproduces a failure that only
appears on a real server — where the checkout is read-only, the process can be
killed mid-write, the address of the caller is shared, and a request must not
wait for `npm`:

1. **`ProtectSystem=strict` and a cache directory.** `deploy/nobi.service`
   allows writes only under `/srv/nobi-data`, and `candle_cache` used to
   `mkdir()` next to its own source at import time. `nobitex_client` imports it,
   so the application died at startup with
   `PermissionError: .../server/candle_cache` — three times, then systemd gave
   up. A cache is an optimisation; it must never decide whether the process
   starts.
2. **A torn state file.** `write_text` is not atomic, and the old `load()`
   answered every exception with "starting fresh". A power cut during a write
   silently produced an account with the starting equity, no positions and an
   empty curve — the numbers looked normal and the record was gone.
3. **An unguarded sign-in route.** The throttle in `auth.authenticate` is keyed
   by `(address, username)`, so cycling usernames pays for a scrypt comparison
   every time, and ten wrong guesses against one name lock that person out.
4. **A page build inside a request.** `GET /` could run `npm run css` (60 s) and
   `build_standalone.py` on the same event loop that serves everything else,
   including `/api/health`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_DIR.parent

#: `ProtectSystem=strict` is a systemd idea: it makes a checkout unwritable and has
#: no Windows equivalent — `chmod` there only toggles a read-only bit on *files*,
#: directories stay writable. Root ignores directory permissions too. Both make the
#: reproduction impossible, so the test says that rather than passing vacuously.
#: `os.geteuid` does not exist on Windows at all, so it is only reached on POSIX:
#: calling it at module level is what used to abort collection of the whole suite.
POSIX_MODES = os.name == "posix"


def _cannot_test_file_modes() -> bool:
    """True when the reproduction is impossible here.

    `getattr` rather than `os.geteuid` on purpose: the skip condition is evaluated
    while this module is imported, and on Windows the attribute simply is not there
    — the attribute error used to abort collection for the whole suite.
    """
    if not POSIX_MODES:
        return True
    geteuid = getattr(os, "geteuid", None)
    return geteuid is None or geteuid() == 0


# --------------------------------------------------------------------------- #
# 1. Output directories
# --------------------------------------------------------------------------- #
#: The two modules that used to create a directory beside themselves at import
#: time, each of which is imported (directly or through `nobitex_client`) by
#: `main`. Both are listed so a third one cannot appear unnoticed.
OUTPUT_DIRS = ("candle_cache", "backtest")


def _import_in_subprocess(modules: tuple[str, ...], env: dict, cwd: Path) -> str:
    """Import modules in a fresh process and print where they decided to write."""
    names = ", ".join(f'"{m}"' for m in modules)
    code = (
        f"import {', '.join(modules)}\n"
        f"for m in ({names},):\n"
        "    mod = __import__(m)\n"
        "    print(m, getattr(mod, 'CACHE_ROOT', getattr(mod, 'REPORTS', None)))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(cwd), env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout.strip()


def test_output_directories_are_configurable(tmp_path):
    """Both paths come from the environment, and nested paths are created."""
    cache, reports = tmp_path / "data" / "candle-cache", tmp_path / "data" / "reports"
    env = {**os.environ, "PYTHONPATH": str(SERVER_DIR),
           "NBX_CANDLE_CACHE": str(cache), "NBX_BT_DIR": str(reports)}
    printed = _import_in_subprocess(OUTPUT_DIRS, env, REPO_ROOT)

    assert str(cache) in printed and str(reports) in printed, printed
    assert cache.is_dir() and reports.is_dir(), "a nested path must be created, not assumed"


@pytest.mark.skipif(_cannot_test_file_modes(),
                    reason="POSIX directory modes: no Windows equivalent, and root ignores them")
def test_a_read_only_checkout_still_imports(tmp_path):
    """The P0: read-only sources must degrade, not crash at import.

    This is the deployment, reproduced: the modules sit in a directory the
    service user cannot write to (what `ProtectSystem=strict` does to
    `/opt/nobi`), no environment variable is set, and the import must still
    succeed — falling back to a writable path with a warning. Two modules are
    covered because the first fix missed the second one.
    """
    checkout = tmp_path / "readonly"
    checkout.mkdir()
    for name in ("storage.py", "candle_cache.py", "backtest.py"):
        (checkout / name).write_text(
            (SERVER_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
    # `backtest` imports the analysis engine; the real one is fine to reuse.
    checkout.chmod(0o555)
    try:
        env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(checkout), str(SERVER_DIR)))}
        env.pop("NBX_CANDLE_CACHE", None)
        env.pop("NBX_BT_DIR", None)
        printed = _import_in_subprocess(OUTPUT_DIRS, env, checkout)
    finally:
        checkout.chmod(0o755)

    for line in printed.splitlines():
        module, path = line.split(" ", 1)
        assert not path.startswith(str(checkout)), (
            f"{module} fell back to a directory inside the read-only checkout: {path}")


# --------------------------------------------------------------------------- #
# 2. The paper account on disk
# --------------------------------------------------------------------------- #
@pytest.fixture()
def state(tmp_path, monkeypatch):
    """Point the broker at a scratch state file and hand back the paths."""
    import paper_trading

    target = tmp_path / "paper.json"
    monkeypatch.setattr(paper_trading, "STATE_FILE", target)
    monkeypatch.setattr(paper_trading, "PAPER_BROKER", None)
    return target


class _Wire:
    """No exchange: these tests never place an order."""

    async def get_orderbook(self, symbol):            # pragma: no cover - unused
        raise AssertionError("no order was expected")

    async def get_market_stats(self):                 # pragma: no cover - unused
        raise AssertionError("no rate was expected")


def _broker(equity: float = 100_000.0):
    from paper_trading import PaperBroker
    from risk_engine import RiskConfig, RiskEngine

    return PaperBroker(equity, _Wire(), RiskEngine(RiskConfig(equity=equity)))  # type: ignore[arg-type]


def test_a_save_leaves_a_complete_file_and_a_backup(state):
    """Two saves: the second one rotates the first into `.bak`, atomically."""
    first = _broker()
    first.cash = 12_345.0
    first.save()
    second = _broker()
    second.cash = 999.0
    second.save()

    on_disk = json.loads(state.read_text())
    assert on_disk["cash"] == 999.0
    backup = state.with_suffix(state.suffix + ".bak")
    assert json.loads(backup.read_text())["cash"] == 12_345.0
    assert not state.with_suffix(state.suffix + ".tmp").exists(), (
        "the temporary file must be renamed away, not left behind")


def test_a_torn_state_file_falls_back_to_the_backup(state):
    """A half-written document is not an empty account.

    The old code caught `JSONDecodeError` and started from scratch: equity back
    to the configured value, positions gone, equity curve empty.
    """
    saved = _broker()
    saved.cash = 54_321.0
    saved.save()
    _broker().save()                       # produces the .bak from the good file
    state.write_text('{"equity": 100000.0, "cash": 543')   # power cut mid-write

    restored = _broker()

    assert restored.cash == 54_321.0, "the backup was not used"
    assert restored.state_recovery is not None
    assert restored.state_recovery["recovered_from"].endswith(".bak")
    assert "unreadable" in restored.state_recovery["reason"]


def test_an_unreadable_state_file_is_quarantined_and_reported(state):
    """Last resort: start fresh — but keep the damaged file and say so."""
    damaged = '{"equity": 100000.0, "positions": {"pp-1": {"symbol": "BTCIRT"'
    state.write_text(damaged)

    broker = _broker()

    assert broker.positions == {}
    recovery = broker.state_recovery
    assert recovery is not None, "a silent restart is the bug this test exists for"
    kept = Path(recovery["quarantined"])
    assert kept.exists(), "the damaged document must be kept for recovery"
    assert kept.read_text() == damaged
    assert not state.exists(), "the damaged file must be moved aside, not overwritten"


def test_a_missing_state_file_is_not_an_error(state):
    """First run: nothing to recover, and nothing to report."""
    broker = _broker()
    assert broker.state_recovery is None
    assert broker.cash == 100_000.0


# --------------------------------------------------------------------------- #
# 3. Sign-in
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(monkeypatch):
    import security

    security.reset_rate_limits()
    from fastapi.testclient import TestClient

    import main

    monkeypatch.setattr(main, "get_broker", lambda *a, **k: _broker())
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c
    security.reset_rate_limits()


def test_sign_in_is_rate_limited_per_address_across_usernames(client, monkeypatch):
    """Cycling usernames must not buy unlimited scrypt comparisons.

    The per-`(address, username)` throttle in `auth.authenticate` never fires
    here: every request uses a different name.
    """
    import security

    monkeypatch.setattr(security, "LOGIN_RATE_LIMIT", 3)
    body = {"username": "someone", "password": "not-the-password"}
    codes = []
    for i in range(4):
        res = client.post("/api/auth/login", json={**body, "username": f"user{i}"})
        codes.append(res.status_code)

    assert codes[:3] != [], codes
    assert 429 not in codes[:3], f"the first attempts must be answered normally: {codes}"
    assert codes[3] == 429, f"the fourth attempt in one minute must be refused: {codes}"
    assert "دقیقه" in client.post("/api/auth/login", json=body).json()["detail"]


def test_the_sign_in_budget_is_per_address(monkeypatch):
    """One noisy address must not spend the budget of another."""
    import security

    monkeypatch.setattr(security, "LOGIN_RATE_LIMIT", 2)
    security.reset_rate_limits()
    try:
        assert security.login_rate_ok("203.0.113.9")
        assert security.login_rate_ok("203.0.113.9")
        assert not security.login_rate_ok("203.0.113.9")
        assert security.login_rate_ok("198.51.100.4"), "an unrelated address kept its budget"
    finally:
        security.reset_rate_limits()


# --------------------------------------------------------------------------- #
# 4. The request path
# --------------------------------------------------------------------------- #
def test_the_root_page_never_runs_a_build(monkeypatch):
    """`GET /` must not wait for `npm` — the health check shares this loop."""
    import subprocess as sp

    import main

    def explode(*a, **k):                      # pragma: no cover - must not run
        raise AssertionError("a build ran while answering a request")

    monkeypatch.setattr(sp, "run", explode)
    page = main._self_contained_page()
    assert page.name in ("nobitex-mobile.html", "index.html")


def test_autobuild_is_off_unless_asked_for(monkeypatch):
    """`NBX_AUTOBUILD_PAGE` is opt-in; a deployment should never build."""
    import main

    monkeypatch.delenv("NBX_AUTOBUILD_PAGE", raising=False)
    monkeypatch.setattr(main, "_build_attempted", False)
    main._build_page_once()
    assert main._build_attempted is False


def test_a_stale_page_is_served_complete(state, monkeypatch):
    """Stale-but-complete beats fresh-but-empty, without a subprocess."""
    import main

    standalone = REPO_ROOT / "nobitex-mobile.html"
    if not standalone.exists():                           # pragma: no cover
        pytest.skip("the inlined build is not in this checkout")

    src = REPO_ROOT / "nbx-app.js"
    now = src.stat().st_mtime
    try:
        os.utime(src, (now + 5, now + 5))
        page = main._self_contained_page()
        assert page == standalone
        assert 'href="assets/css/' not in page.read_text(encoding="utf-8")[:400_000]
    finally:
        os.utime(src, (now, now))


# --------------------------------------------------------------------------- #
# 5. What the operator sees
# --------------------------------------------------------------------------- #
def test_the_recovery_is_published_to_the_dashboard(state, monkeypatch):
    """A recovered account is a fact on the wire, not a line in a logfile."""
    from fastapi.testclient import TestClient

    import main

    state.write_text(textwrap.dedent('{"equity": 100000.0, "cash": 777.0}'))
    broker = _broker()

    monkeypatch.setattr(main, "get_broker", lambda *a, **k: broker)
    with TestClient(main.app, raise_server_exceptions=False) as c:
        # No accounts exist in the test database, so reads are open.
        payload = c.get("/api/paper/metrics").json()

    assert "state_recovery" in payload
    assert payload["state_recovery"] is None            # this file was valid
    assert broker.cash == 777.0
