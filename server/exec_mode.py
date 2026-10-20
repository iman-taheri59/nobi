"""Who pulls the trigger: the human, or the executor?

Three modes, one meaning each:

``semi``
    The queue proposes; a human approves. This is the default and, until now,
    the only mode that actually did anything.

``auto``
    For every symbol marked ``auto`` the server sizes the trade with the same
    `risk.evaluate` the ticket uses, sends it through the same
    `broker.place_limit` and its ten gates, and records the result in the
    journal audit. The order is a **paper** order: this repository holds no
    exchange key and has no ``POST /market/orders`` path, so nothing is sent to
    Nobitex. The UI says "کاغذی" for that reason.

``manual``
    The queue stays empty for that symbol; the ticket is the only way in.

Where the state lives
---------------------
It used to live in the browser: clicking «خودکار» set a string, printed a chip
and a toast, and nothing read the value. A hard-coded `symMode` table then
overrode the human's choice per symbol, so the "per-symbol mode" the hint
promised could not be changed at all. State now lives here, on the server,
persisted in the journal's key/value table: the executor and the page read the
same value, and a reload cannot disagree with a running engine.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

MODES = ("auto", "semi", "manual")
DEFAULT = "semi"


class InvalidMode(ValueError):
    """Raised for a mode the executor does not implement."""


def normalise(mode: str | None, *, allow_empty: bool = False) -> str | None:
    m = (mode or "").strip().lower()
    if not m and allow_empty:
        return None
    if m not in MODES:
        raise InvalidMode(f"mode must be one of {', '.join(MODES)}")
    return m


@dataclass
class ModeStore:
    """Global default plus per-symbol overrides."""

    default: str = DEFAULT
    symbols: dict[str, str] = field(default_factory=dict)
    updated: float = 0.0

    def mode_for(self, symbol: str) -> str:
        return self.symbols.get((symbol or "").upper(), self.default)

    def auto_symbols(self) -> list[str]:
        """Symbols the executor may act on without an approval."""
        out = [s for s, m in self.symbols.items() if m == "auto"]
        if self.default == "auto":
            out = out or []
        return sorted(set(out))

    def is_auto_default(self) -> bool:
        return self.default == "auto"

    def set_default(self, mode: str) -> str:
        self.default = normalise(mode) or DEFAULT
        self.updated = time.time()
        return self.default

    def set_symbol(self, symbol: str, mode: str | None) -> str | None:
        """Set or clear one symbol's override. An empty mode removes it."""
        sym = (symbol or "").strip().upper()
        if not sym:
            raise InvalidMode("symbol is required")
        m = normalise(mode, allow_empty=True)
        if m is None:
            self.symbols.pop(sym, None)
        else:
            self.symbols[sym] = m
        self.updated = time.time()
        return m

    def snapshot(self) -> dict:
        return {
            "default": self.default,
            "symbols": dict(sorted(self.symbols.items())),
            "modes": list(MODES),
            "updated": self.updated or None,
            # Said out loud so no reader can mistake `auto` for live trading.
            "executor": "paper",
            "sends_to_exchange": False,
        }

    # -- persistence (journal_meta) ---------------------------------------- #
    def to_json(self) -> dict:
        return {"default": self.default, "symbols": dict(self.symbols)}

    def load(self, payload) -> None:
        if not isinstance(payload, dict):
            return
        try:
            self.default = normalise(payload.get("default")) or DEFAULT
        except InvalidMode:
            self.default = DEFAULT
        syms: dict[str, str] = {}
        raw = payload.get("symbols")
        if isinstance(raw, dict):
            for sym, mode in raw.items():
                try:
                    m = normalise(mode)
                except InvalidMode:
                    continue
                if m:
                    syms[str(sym).upper()] = m
        self.symbols = syms


EXEC_MODE = ModeStore()

__all__ = ["DEFAULT", "EXEC_MODE", "MODES", "InvalidMode", "ModeStore", "normalise"]
