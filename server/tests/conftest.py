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
