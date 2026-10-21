"""The one place a trading fee is written down.

The paper broker, the backtester, the allocation route and the browser bundle
all charge the same 0.2 % per leg. Before this module that number was typed
into four files, each with its own comment, and nothing noticed when they
drifted — a backtest that assumes 0.2 % while the broker charges something else
produces a profit factor nobody can reproduce in paper trading.

Deliberately **one** rate for maker and taker. The exchange's current schedule is
not verified anywhere in this repository, and inventing a maker/taker split would
be exactly the kind of fabricated number ADR-0014 forbids. A single conservative
rate, applied to both legs and both sides, is wrong in a direction that cannot
flatter a strategy: it never understates the cost of the trades it models.

Change the number here and every Python consumer follows. The browser bundle
cannot import this file, so `server/tests/test_single_source_constants.py`
parses `nbx-core.js` and fails if `PAPER_FEE_PCT` disagrees with `FEE_PCT`.
"""
from __future__ import annotations

#: Per-leg fee, as a fraction of notional. Both legs (entry and exit) are
#: charged, and both maker and taker pay it — see the module docstring.
FEE_PCT: float = 0.002

#: The paper account's starting equity in USD when `NBX_EQUITY_USD` is unset.
#: Every install example ships the same default; the deployer decides the real
#: capital with that variable.
DEFAULT_EQUITY_USD: float = 100_000.0
