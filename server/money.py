"""Money that carries its own currency.

Why this module exists
----------------------
Every unit bug this project has had was the same shape: a bare ``float`` that
meant Rial in one place, Toman in another and USD in a third, with the meaning
written only in the variable name. ``equity / fx`` and ``equity * fx`` are one
keystroke apart and both compile. Four instances so far:

* ``/market/stats`` quoted Rial, the page labelled Toman (10×);
* ``risk`` was compared with ``equity`` in different currencies (68,000×);
* a candle timestamp in seconds was divided by 1000 (1000×);
* ``_quote_rate`` cached a rate with no expiry, so a long-running process
  valued new orders at last week's rate.

A comment cannot be enforced. A type can. This module gives each amount a
``unit`` and makes the only legal way to change it an explicit ``convert`` that
needs a named, dated rate. There is no ``__mul__`` between two ``Money`` values
in different units, and no implicit conversion anywhere: asking for a
conversion without a rate raises ``NoRate`` instead of quietly assuming 1.0.

Units used here
---------------
========  ===========================================================
``IRR``   Iranian Rial — what the exchange quotes Rial markets in
``IRT``   Iranian Toman — 1 Toman = 10 Rial, what the dashboard shows
``USD``   the account currency (see ``Rate``: it is USDT, treated as USD)
``USDT``  the stablecoin; 1 USDT = 1 USD in this book, by decision
========  ===========================================================

Anything else (BTC, ETH, ...) is an ``Asset``: a quantity, not a currency.
``Money`` only ever holds money; coin amounts stay plain floats because
``0.02 BTC`` is not a price and converting it needs a per-symbol quote.

Deliberate limits
-----------------
* No FX lookup inside this module. A rate must be passed in, so every
  conversion in the code base can be traced to the object that supplied it.
* No arithmetic between different units. ``usd + toman`` raises.
* ``convert`` requires a ``Rate`` whose age is checked by the caller; the
  ``Rate`` itself exposes ``is_stale`` rather than hiding a TTL.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: One Toman is ten Rial. A definition, not a market price.
RIAL_PER_TOMAN: Final[float] = 10.0

#: Units this module knows. ``USDT`` and ``USD`` are the same thing in this
#: book — the exchange has no USD market and the paper account is denominated
#: in what USDT is worth. They are separate names so a future depeg can be
#: modelled in one place instead of a search-and-replace.
CURRENCIES: Final[frozenset[str]] = frozenset({"IRR", "IRT", "USD", "USDT"})

#: The account currency. Every gate in `risk_engine` compares against this.
ACCOUNT_UNIT: Final[str] = "USD"


class MoneyError(ValueError):
    """Base class for anything money-related that should stop the caller."""


class NoRate(MoneyError):
    """A conversion was requested without a usable rate.

    This exception is the whole point of the module. Callers used to answer
    this situation with ``or 1.0`` (100,000× wrong in one direction) or with a
    silent ``if fx else eq`` (100,000× wrong in the other). Both produced
    numbers that looked fine. Neither can happen if the only way forward is an
    exception with a name.
    """


class UnitMismatch(MoneyError):
    """Two amounts of different currencies were combined."""


class UnknownUnit(MoneyError):
    """A currency code this module does not know."""


def normalise(unit: str) -> str:
    u = (unit or "").strip().upper()
    if u not in CURRENCIES:
        raise UnknownUnit(f"unknown currency {unit!r}; expected one of {sorted(CURRENCIES)}")
    return u


@dataclass(frozen=True)
class Rate:
    """A dated exchange rate: how many Tomans one USDT is worth.

    ``toman_per_usdt`` is the number the dashboard shows as ``USDTIRT``
    (≈ 100,805). The rate is a *quote of one market at one moment*, so it
    carries both: an amount without an age cannot be trusted, and an age
    without a source cannot be explained.

    ``max_age_s`` is decided by the caller — a preview wants a fresh rate, a
    backtest may reasonably use an older one — so the staleness is a property
    of the rate object, not a global constant hidden here.
    """

    toman_per_usdt: float
    as_of: float
    source: str = "usdt-irt"
    max_age_s: float = 120.0

    def __post_init__(self) -> None:
        try:
            v = float(self.toman_per_usdt)
        except (TypeError, ValueError) as e:
            raise NoRate(f"rate is not a number: {self.toman_per_usdt!r}") from e
        if not (v > 0):
            raise NoRate(f"rate must be positive, got {v!r}")
        object.__setattr__(self, "toman_per_usdt", v)

    @property
    def age_s(self) -> float:
        import time

        return max(0.0, time.time() - float(self.as_of or 0.0))

    @property
    def is_stale(self) -> bool:
        return self.age_s > float(self.max_age_s)

    def require_fresh(self) -> Rate:
        """Raise unless the rate is inside its own window.

        Callers that must not use an old rate (sizing a live order) call this;
        callers that may (a historical report) do not.
        """
        if self.is_stale:
            raise NoRate(
                f"rate from {self.source} is {self.age_s:.0f}s old "
                f"(limit {self.max_age_s:.0f}s)"
            )
        return self

    def to_dict(self) -> dict:
        return {
            "toman_per_usdt": round(self.toman_per_usdt, 4),
            "as_of": self.as_of,
            "age_s": round(self.age_s, 2),
            "source": self.source,
            "stale": self.is_stale,
        }


@dataclass(frozen=True)
class Money:
    """An amount with a unit. Construct with ``usd``/``toman``/``rial``."""

    amount: float
    unit: str

    def __post_init__(self) -> None:
        try:
            v = float(self.amount)
        except (TypeError, ValueError) as e:
            raise MoneyError(f"amount is not a number: {self.amount!r}") from e
        object.__setattr__(self, "amount", v)
        object.__setattr__(self, "unit", normalise(self.unit))

    # -- constructors ------------------------------------------------------- #
    @property
    def is_zero(self) -> bool:
        return self.amount == 0.0

    def __repr__(self) -> str:  # keeps pytest output readable and unambiguous
        return f"Money({self.amount:g} {self.unit})"

    # -- same-unit arithmetic ----------------------------------------------- #
    def _check(self, other: Money) -> Money:
        if not isinstance(other, Money):
            raise MoneyError(f"cannot combine Money with {type(other).__name__}")
        if other.unit != self.unit:
            raise UnitMismatch(f"{self.unit} + {other.unit}: convert one side first")
        return other

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount + other.amount, self.unit)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount - other.amount, self.unit)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.unit)

    def __abs__(self) -> Money:
        return Money(abs(self.amount), self.unit)

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.amount >= other.amount

    def scaled(self, k: float) -> Money:
        """Multiply by a dimensionless factor (a fee rate, a share). """
        return Money(self.amount * float(k), self.unit)

    def pct_of(self, other: Money) -> float:
        """``self`` as a percentage of ``other`` (same unit required)."""
        self._check(other)
        if other.amount == 0:
            raise MoneyError("percentage of zero is undefined")
        return self.amount / other.amount * 100.0

    # -- the only conversion ----------------------------------------------- #
    def _to_irt(self, rate: Rate | None) -> Money:
        """Pivot: everything converts through Toman, in one place."""
        if self.unit == "IRT":
            return self
        if self.unit == "IRR":
            return Money(self.amount / RIAL_PER_TOMAN, "IRT")
        if self.unit in ("USD", "USDT"):
            if rate is None:
                raise NoRate("converting USD → IRT needs a USDT/IRT rate; "
                             "none was supplied (refusing to assume 1.0)")
            return Money(self.amount * rate.toman_per_usdt, "IRT")
        raise UnknownUnit(self.unit)

    def convert(self, unit: str, rate: Rate | None = None, *,
                require_fresh: bool = True) -> Money:
        """Return the same value in another unit.

        ``IRR``↔``IRT`` is the fixed 10:1 definition. Anything crossing
        ``USD`` is a market price and requires a ``Rate``; without one this
        raises ``NoRate`` — it never falls back to 1.0.
        """
        target = normalise(unit)
        if target == self.unit:
            return self
        # USDT <-> USD is the identity by decision, not a market price: the
        # exchange has no USD market and the account is denominated in what
        # USDT is worth. Stated once, here, so a future depeg has one place
        # to go instead of a search-and-replace.
        if {self.unit, target} == {"USDT", "USD"}:
            return Money(self.amount, target)
        if rate is not None and require_fresh:
            rate.require_fresh()          # fail before returning a plain swap
        irt = self._to_irt(rate)
        if target == "IRT":
            return irt
        if target == "IRR":
            return Money(irt.amount * RIAL_PER_TOMAN, "IRR")
        if target in ("USD", "USDT"):
            # Pivoting through Toman means an IRT source also needs the rate,
            # not just a USD one — the first version asserted here and turned a
            # missing rate into an AssertionError instead of a named refusal.
            if rate is None:
                raise NoRate(
                    f"converting {self.unit} → {target} needs a USDT/IRT rate; "
                    "none was supplied (refusing to assume 1.0)"
                )
            return Money(irt.amount / rate.toman_per_usdt, target)
        raise UnknownUnit(target)

    def to_irt(self, rate: Rate | None = None, *, require_fresh: bool = True) -> Money:
        return self.convert("IRT", rate=rate, require_fresh=require_fresh)

    def to_usd(self, rate: Rate | None = None, *, require_fresh: bool = True) -> Money:
        return self.convert("USD", rate=rate, require_fresh=require_fresh)

    # -- serialisation ------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {"amount": round(self.amount, 8), "unit": self.unit}


# -- constructors (kept as functions: `usd(1)` reads better than `Money(1,"USD")`)
def usd(amount: float) -> Money:
    return Money(amount, "USD")


def usdt(amount: float) -> Money:
    return Money(amount, "USDT")


def toman(amount: float) -> Money:
    return Money(amount, "IRT")


def rial(amount: float) -> Money:
    return Money(amount, "IRR")


def from_api(amount: float, symbol: str) -> Money:
    """An amount as the exchange sends it, for the market's quote currency.

    Rial markets are quoted in **Rial** on the wire (docs §market/orders/add)
    and in Toman everywhere in this app; this is the boundary that says which.
    """
    unit = "IRR" if (symbol or "").upper().endswith("IRT") else "USDT"
    return Money(amount, unit)


__all__ = [
    "ACCOUNT_UNIT",
    "CURRENCIES",
    "RIAL_PER_TOMAN",
    "Money",
    "MoneyError",
    "NoRate",
    "Rate",
    "UnitMismatch",
    "UnknownUnit",
    "from_api",
    "rial",
    "toman",
    "usd",
    "usdt",
]
