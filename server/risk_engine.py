"""
Risk engine & position sizing (ADR-0008).

Computes ATR-based sizing, applies global caps, evaluates gate violations,
produces a per-trade risk plan. Same math is used by paper + live brokers.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskPlan:
    symbol: str
    action: str
    entry: float
    stop: float
    tp1: float
    tp2: float
    rr_ratio: float
    size_quote: float
    risk_pct: float
    warnings: list[str] = field(default_factory=list)
    blocked: bool = False


@dataclass
class RiskConfig:
    equity: float
    risk_pct: float = 0.75            # % equity per trade (default 0.75%)
    max_concurrent: int = 5
    max_single_asset_pct: float = 25.0
    max_heat_pct: float = 6.0
    min_cash_pct: float = 10.0
    risk_k_multiplier: float = 2.0     # stop distance = k * ATR
    max_risk_pct: float = 2.0          # hard ceiling


@dataclass
class Gate:
    """One admission rule, with the numbers that decided it."""
    id: str
    ok: bool
    value: float | None = None
    limit: float | None = None
    detail: str = ""


@dataclass
class Preflight:
    """What would happen to the account if this order were placed.

    Pure data: no I/O, no state change. The paper broker runs it to decide
    whether to accept an order, and `/api/risk/whatif` runs it to *show* the
    same decision before it is placed. One implementation, so the preview
    cannot disagree with the executor — a preview that lies is worse than no
    preview, because it is believed.
    """
    equity: float
    notional_usd: float
    fee_usd: float
    risk_usd: float
    risk_pct: float
    heat_before: float
    heat_after: float
    max_heat: float
    open_positions: int
    max_concurrent: int
    cash_before: float
    cash_after: float
    min_cash: float
    loss_at_stop_usd: float
    gates: list[Gate] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(not g.ok for g in self.gates)

    def first_block(self) -> Gate | None:
        return next((g for g in self.gates if not g.ok), None)

    def summary(self) -> dict:
        return {
            "equity": round(self.equity, 2),
            "notional_usd": round(self.notional_usd, 2),
            "fee_usd": round(self.fee_usd, 2),
            "risk_usd": round(self.risk_usd, 2),
            "risk_pct": round(self.risk_pct, 3),
            "heat_before": round(self.heat_before, 3),
            "heat_after": round(self.heat_after, 3),
            "max_heat": self.max_heat,
            "heat_headroom_pct": round(max(0.0, self.max_heat - self.heat_after), 3),
            "open_positions": self.open_positions,
            "max_concurrent": self.max_concurrent,
            "slots_left": max(0, self.max_concurrent - self.open_positions - 1),
            "cash_before": round(self.cash_before, 2),
            "cash_after": round(self.cash_after, 2),
            "min_cash": round(self.min_cash, 2),
            "loss_at_stop_usd": round(self.loss_at_stop_usd, 2),
            "loss_at_stop_pct_of_equity": round(
                self.loss_at_stop_usd / self.equity * 100 if self.equity else 0.0, 3),
            "blocked": self.blocked,
            "blocked_by": (self.first_block().id if self.first_block() else None),
            "gates": [{"id": g.id, "ok": g.ok, "value": None if g.value is None else round(g.value, 3),
                       "limit": None if g.limit is None else round(g.limit, 3),
                       "detail": g.detail} for g in self.gates],
        }


def preflight(*, equity: float, cash: float, open_heat: float, open_positions: int,
              concurrent_limit: int, max_heat_pct: float, min_cash_pct: float,
              max_single_asset_pct: float, risk_pct_cap: float,
              halted: bool, notional_usd: float, fee_usd: float,
              risk_usd: float, min_notional_usd: float = 0.0,
              market_closed: bool = False) -> Preflight:
    """Evaluate every admission gate for one candidate order.

    Mirrors the checks in `PaperBroker.place_limit` exactly — same order, same
    thresholds, same arithmetic. If they ever diverge, the what-if panel starts
    promising trades the broker will reject.
    """
    equity = max(float(equity or 0.0), 1e-9)
    risk_pct = float(risk_usd) / equity * 100
    heat_before = float(open_heat or 0.0)
    # A rejected order must not move heat; report the projected value anyway and
    # let the gate list explain why it would not happen.
    heat_after = heat_before + risk_pct
    min_cash = float(min_cash_pct) / 100 * equity
    cash_after = float(cash) - (notional_usd + fee_usd)

    gates = [
        Gate("halted", not halted, None, None,
             "kill switch is armed" if halted else ""),
        # From the exchange documentation: a market can be temporarily closed
        # (`isClosed` on /market/stats, `MarketClosed` on order placement). A
        # paper fill in a closed market is a fill that could not have happened.
        Gate("market-closed", not market_closed, None, None,
             "the exchange reports this market as closed" if market_closed else ""),
        Gate("concurrent-cap", open_positions < concurrent_limit,
             float(open_positions), float(concurrent_limit),
             f"{open_positions} of {concurrent_limit} slots in use"),
        Gate("cash-present", float(cash) > 0, float(cash), 0.0,
             "paper account is empty" if float(cash) <= 0 else ""),
        # A size computed as exactly `cap` is not "over the cap"; floating
        # division made 25% of equity a hair larger than 25% of equity.
        Gate("single-asset-cap", notional_usd <= max_single_asset_pct / 100 * equity * (1 + 1e-9),
             notional_usd / equity * 100, float(max_single_asset_pct),
             f"notional {notional_usd / equity * 100:.2f}% of equity"),
        # `SmallOrder` on the exchange: 3,000,000 Rial or 11 USDT depending on
        # the quote. Default 0 keeps older callers working unchanged.
        Gate("min-notional",
             notional_usd >= float(min_notional_usd) - 1e-9,
             notional_usd, float(min_notional_usd),
             # The detail explains a *failure*; printing it on a passing gate
             # made the panel say "$25,000 is below the minimum ($2.95)".
             (f"${notional_usd:,.2f} is below the exchange minimum "
              f"(${min_notional_usd:,.2f})"
              if min_notional_usd and notional_usd < min_notional_usd - 1e-9 else "")),
        # The broker allows 5 % slack over the configured per-trade risk; the
        # preview has to allow the same or it will block trades that fill.
        Gate("risk-cap", risk_pct <= float(risk_pct_cap) * 1.05,
             risk_pct, float(risk_pct_cap),
             f"this order risks {risk_pct:.2f}% of equity"),
        Gate("heat-cap", heat_after <= float(max_heat_pct), heat_after, float(max_heat_pct),
             f"heat {heat_before:.2f}% + {risk_pct:.2f}% = {heat_after:.2f}%"),
        Gate("min-cash", cash_after >= min_cash, cash_after, min_cash,
             f"cash after the order ${cash_after:,.2f}, floor ${min_cash:,.2f}"),
        Gate("funds", cash_after >= 0, cash_after, 0.0,
             f"needs ${notional_usd + fee_usd:,.2f}, available ${cash:,.2f}"),
    ]
    return Preflight(
        equity=equity, notional_usd=notional_usd, fee_usd=fee_usd,
        risk_usd=risk_usd, risk_pct=risk_pct,
        heat_before=heat_before, heat_after=heat_after,
        max_heat=float(max_heat_pct), open_positions=int(open_positions),
        max_concurrent=int(concurrent_limit), cash_before=float(cash),
        cash_after=cash_after, min_cash=min_cash,
        # Losing the full stop distance plus the entry fee is the real worst
        # case; quoting only the stop distance understates it.
        loss_at_stop_usd=float(risk_usd) + float(fee_usd),
        gates=gates,
    )


class RiskEngine:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self.open_heat: float = 0.0
        self.cluster_exposure: dict = {}
        self.asset_exposure: dict = {}
        self.daily_pnl: float = 0.0
        self.halted = False

    def _tp(self, entry: float, stop: float, side: str, rr_levels=(1.2, 2.0)) -> tuple[float, float]:
        dist = abs(entry - stop)
        if side == "buy":
            return entry + dist * rr_levels[0], entry + dist * rr_levels[1]
        return entry - dist * rr_levels[0], entry - dist * rr_levels[1]

    def evaluate(self, symbol: str, side: str, entry: float, atr: float, exec_tf="15m",
                 equity_quote: float | None = None, preview: bool = False) -> RiskPlan:
        """Size one trade.

        ``equity_quote`` is the account equity expressed in the *quote currency
        of the pair* (IRT for `*IRT`, USDT for `*USDT`). Without it, sizing an
        IRT pair against a USD balance mixes units and under-sizes by ~68,000x.

        ``preview=True`` answers "what would this trade size to?" without
        reserving heat. Previously the caller had to subtract the heat after
        the fact, which corrupted any concurrent request that read state in
        between.
        """
        warnings: list[str] = []
        if self.halted:
            return RiskPlan(symbol, "blocked", entry, entry, entry, entry, 0.0, 0.0, 0.0, ["kill-switch-armed"], True)

        equity = float(equity_quote) if equity_quote and equity_quote > 0 else self.cfg.equity
        # volatility-based size: risk / (2*ATR)
        risk_amt = equity * self.cfg.risk_pct / 100
        stop_dist = self.cfg.risk_k_multiplier * atr
        if stop_dist <= 0 or entry <= 0:
            return RiskPlan(symbol, "blocked", entry, entry, entry, entry, 0.0, 0.0, 0.0, ["bad-atr"], True)
        size_base = risk_amt / stop_dist
        size_quote = size_base * entry

        # precision guard (approximate — Nobitex steps usually safe to round 6)
        size_quote = float(f"{size_quote:.2f}")
        nominal_pct = size_quote / max(equity, 1e-9) * 100

        rp = self.cfg.risk_pct
        # Hard ceiling on per-trade risk. It existed in the config but nothing
        # read it, so a mis-set risk_pct could silently exceed the mandate.
        if rp > self.cfg.max_risk_pct:
            warnings.append("risk-pct-clamped")
            rp = self.cfg.max_risk_pct
            if risk_amt > 0 and self.cfg.risk_pct:
                size_quote *= rp / self.cfg.risk_pct
        # trading day check
        if self.daily_pnl <= -3.0:
            warnings.append("daily-loss-halt")
        if self.open_heat + rp > self.cfg.max_heat_pct:
            warnings.append("heat-cap-exceeded")
            rp = max(0, self.cfg.max_heat_pct - self.open_heat)
            # Shrink the order to whatever heat budget is left (was: only a
            # warning, which let the cap be exceeded silently).
            if risk_amt > 0:
                size_quote *= (rp / self.cfg.risk_pct) if self.cfg.risk_pct else 0

        # Enforce (not merely warn about) the per-asset exposure cap: shrink the
        # order to the cap instead of letting an outlier ATR produce a giant size.
        if nominal_pct > self.cfg.max_single_asset_pct:
            scale = self.cfg.max_single_asset_pct / nominal_pct
            size_quote = float(f"{size_quote * scale:.2f}")
            nominal_pct = self.cfg.max_single_asset_pct
            warnings.append("single-asset-cap-resized")
        if rp <= 0 or size_quote <= 0:
            return RiskPlan(symbol, "blocked", entry, entry, entry, entry, 0.0, 0.0, 0.0, warnings, True)

        stop = entry - stop_dist if side == "buy" else entry + stop_dist
        tp1, tp2 = self._tp(entry, stop, side)
        stop_pct = stop_dist / entry * 100
        rr = abs((tp2 - entry) / (entry - stop))
        if rr < 1.5:
            warnings.append("low-rr")

        if not preview:
            self.open_heat += rp
        return RiskPlan(
            symbol=symbol,
            action="open",
            entry=round(entry, 2),
            stop=round(stop, 2),
            tp1=round(tp1, 2),
            tp2=round(tp2, 2),
            rr_ratio=round(rr, 2),
            size_quote=size_quote,
            risk_pct=round(rp, 2),
            warnings=warnings,
            blocked=False,
        )

    def close_trade(self, risk_pct: float, pnl_pct: float):
        self.open_heat = max(0, self.open_heat - risk_pct)
        self.daily_pnl += pnl_pct

    def triggers(self, portfolio_pnl_today: float, weekly: float, consecutive_sl: int):
        if portfolio_pnl_today <= -3:
            self.halted = True
        if weekly <= -6:
            self.halted = True
        if consecutive_sl >= 3:
            self.halted = True


__all__ = ["RiskEngine", "RiskPlan", "RiskConfig"]
