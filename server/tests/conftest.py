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
