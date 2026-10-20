"""Make the backend modules importable regardless of test order.

`test_core.py` inserted the server directory into `sys.path` as a side effect of
being imported, so `test_journal.py` only worked when that file happened to be
collected first: running it alone failed with `No module named 'journal'`. Path
setup belongs in one place that pytest always loads.
"""

from __future__ import annotations

import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))


import pytest


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
        try:
            main.paper_trading.PAPER_BROKER.halted = False
        except Exception:
            pass
        pytest.fail("test left the risk kill switch armed; resume it in the test")
