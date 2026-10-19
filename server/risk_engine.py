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

    def evaluate(self, symbol: str, side: str, entry: float, atr: float, exec_tf="15m") -> RiskPlan:
        warnings: list[str] = []
        if self.halted:
            return RiskPlan(symbol, "blocked", entry, entry, entry, entry, 0.0, 0.0, 0.0, ["kill-switch-armed"], True)

        # volatility-based size: risk / (2*ATR)
        risk_amt = self.cfg.equity * self.cfg.risk_pct / 100
        stop_dist = self.cfg.risk_k_multiplier * atr
        if stop_dist <= 0 or entry <= 0:
            return RiskPlan(symbol, "blocked", entry, entry, entry, entry, 0.0, 0.0, 0.0, ["bad-atr"], True)
        size_base = risk_amt / stop_dist
        size_quote = size_base * entry

        # precision guard (approximate — Nobitex steps usually safe to round 6)
        size_quote = float(f"{size_quote:.2f}")
        nominal_pct = size_quote / self.cfg.equity * 100

        rp = self.cfg.risk_pct
        # trading day check
        if self.daily_pnl <= -3.0:
            warnings.append("daily-loss-halt")
        if self.open_heat + rp > self.cfg.max_heat_pct:
            warnings.append("heat-cap-exceeded")
            rp = max(0, self.cfg.max_heat_pct - self.open_heat)
        if nominal_pct > self.cfg.max_single_asset_pct:
            warnings.append("single-asset-cap")
        if rp <= 0:
            return RiskPlan(symbol, "blocked", entry, entry, entry, entry, 0.0, 0.0, 0.0, warnings, True)

        stop = entry - stop_dist if side == "buy" else entry + stop_dist
        tp1, tp2 = self._tp(entry, stop, side)
        stop_pct = stop_dist / entry * 100
        rr = abs((tp2 - entry) / (entry - stop))
        if rr < 1.5:
            warnings.append("low-rr")

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
