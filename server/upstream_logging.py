"""Bound repeated *known per-pair refusals*, not real outages or other errors."""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict

from nobitex_client import ClientError


class RefusalLogLimiter:
    """Keep the first warning, then a 15-minute summary for each operation/pair/tf.

    Suppression affects logs only: callers still receive the exception and omit
    unavailable data. State is bounded and process-local. A timeout/5xx is always
    logged, even if the same pair was previously refused.
    """

    def __init__(self, interval_s: float = 900, max_keys: int = 512):
        if interval_s <= 0 or max_keys <= 0:
            raise ValueError("interval and capacity must be positive")
        self.interval_s = interval_s
        self.max_keys = max_keys
        self._state: OrderedDict[tuple[str, str, str], tuple[float, int]] = OrderedDict()
        self._lock = threading.Lock()

    def recovered(self, operation: str, symbol: str, tf: str) -> None:
        """A later failure after successful data retrieval starts a new episode."""
        with self._lock:
            self._state.pop((operation, symbol.upper(), tf), None)

    def warning(self, logger: logging.Logger, operation: str, symbol: str,
                tf: str, error: ClientError) -> None:
        suppressed = 0
        if error.meta.get("refused") is True:
            key = (operation, symbol.upper(), tf)
            now = time.monotonic()
            with self._lock:
                previous = self._state.get(key)
                if previous and now - previous[0] < self.interval_s:
                    self._state[key] = (previous[0], previous[1] + 1)
                    self._state.move_to_end(key)
                    return
                suppressed = previous[1] if previous else 0
                self._state[key] = (now, 0)
                self._state.move_to_end(key)
                while len(self._state) > self.max_keys:
                    self._state.popitem(last=False)
        suffix = f" ({suppressed} repeated refusal warnings suppressed)" if suppressed else ""
        logger.warning("%s %s %s: %s%s", operation, symbol, tf, error, suffix)


CANDLE_WARNINGS = RefusalLogLimiter()
