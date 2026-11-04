"""The one place a trading fee is written down.

The paper broker, the backtester, the allocation route and the browser bundle
all charge the same 0.2 % per leg. Before this module that number was typed
into four files, each with its own comment, and nothing noticed when they
drifted — a backtest that assumes 0.2 % while the broker charges something else
produces a profit factor nobody can reproduce in paper trading.

Deliberately **one** default rate for maker and taker. The account's actual tier
and the maker/taker status of a limit fill are not verified anywhere in this
repository, so 0.2 % is a modelling assumption, not a claim about the exchange
bill. Backtests accept an explicit `fee_pct` scenario and publish its source;
the default is labelled `default-assumption-unverified-account-tier`. A zero-fee
run cannot pass the promotion gate.

Change the number here and every Python consumer follows. The browser bundle
cannot import this file, so `server/tests/test_single_source_constants.py`
parses `nbx-core.js` and fails if `PAPER_FEE_PCT` disagrees with `FEE_PCT`.
"""
from __future__ import annotations

#: Per-leg fee, as a fraction of notional. Both legs (entry and exit) are
#: charged, and both maker and taker pay it — see the module docstring.
FEE_PCT: float = 0.002
FEE_SOURCE: str = "default-assumption-unverified-account-tier"
FEE_ACCOUNT_TIER_VERIFIED: bool = False

#: The paper account's starting equity in USD when `NBX_EQUITY_USD` is unset.
#: Every install example ships the same default; the deployer decides the real
#: capital with that variable.
DEFAULT_EQUITY_USD: float = 100_000.0
