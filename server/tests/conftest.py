# mypy: check_untyped_defs = False
# Test doubles stand in for objects on purpose; their bodies answer to pytest.
"""Make the backend modules importable regardless of test order.

`test_core.py` inserted the server directory into `sys.path` as a side effect of
being imported, so `test_journal.py` only worked when that file happened to be
collected first: running it alone failed with `No module named 'journal'`. Path
setup belongs in one place that pytest always loads.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

# The suite must not read (or write) the paper account of whoever ran the
# dashboard last. A single demo order used to leave `paper_state.json` with an
# equity of 5.3 billion and the portfolio test then failed three files away.
_SCRATCH = Path(tempfile.mkdtemp(prefix="nbx-test-"))
os.environ.setdefault("NBX_PAPER_STATE", str(_SCRATCH / "paper.json"))

# Same argument for the journal: `errorlog` writes through `journal.connect()`,
# so a test that exercises a failing health check used to append a row to the
# database the dashboard is reading. `setdefault` keeps an explicitly configured
# path — pytest run against a fixture database still uses that one.
os.environ.setdefault("NBX_JOURNAL_DB", str(_SCRATCH / "journal.db"))

# And the accounts: a developer who ran `manage_users.py` on this checkout has a
# real auth.db with real people in it. The suite must not read it (or add users
# to it), because "does the API require a login?" is the question half of these
# tests answer.
os.environ.setdefault("NBX_AUTH_DB", str(_SCRATCH / "auth.db"))
os.environ.setdefault("NBX_LIVE_DB", str(_SCRATCH / "live-execution.db"))
os.environ.setdefault("NBX_COIN_CACHE", str(_SCRATCH / "coin-cache"))
os.environ.setdefault("NBX_COIN_METADATA_ENABLED", "0")

SERVER = Path(__file__).resolve().parents[1]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))


@pytest.fixture(autouse=True)
def _no_leaked_kill_switch():
    """Fail the test that arms the kill switch and walks away.

    The paper account and the risk engine are process globals. A test that
    halts them without resuming makes every later test read "halted" and
    blame the wrong code — the failure surfaces far from its cause.
    """
    yield
    try:
        import main
    except Exception:      # module not imported yet: nothing to check
        return
    if getattr(main.risk, "halted", False):
        main.risk.halted = False
        with contextlib.suppress(Exception):
            main.paper_trading.PAPER_BROKER.halted = False
        pytest.fail("test left the risk kill switch armed; resume it in the test")


@pytest.fixture(autouse=True)
def _documented_exchange_tables():
    """A network call must not rewrite another test's premises.

    `apply_options()` writes process globals from `/v2/options`. Two tests here
    start the app's lifespan (`with TestClient(main.app)`), which fetches those
    options — so on a machine where the exchange is reachable, the *documented*
    minimums (3,000,000 Rial / 11 USDT) were replaced by the live ones (100,000 / 1)
    and three later tests that expect a below-minimum order to be refused instead
    saw it filled. CI has no network, which is exactly why it never showed up there.

    The reset runs *before and after* every test, not only after: a polluting call
    that happened before the first test (module import, an earlier session, a
    fixture that started the app) would otherwise still reach it. Every test begins
    from the documented tables; a test that wants the exchange's values applies them
    itself.
    """
    import units

    units.reset_documented_defaults()
    yield
    units.reset_documented_defaults()
