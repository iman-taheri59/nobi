"""
Paper Trading Engine (ADR-0009, ADR-0012, ADR-0013) — real broker-simulation.

Features:
  - Same order state machine as live: PLANNED → RISK_CHECKED → SUBMITTED →
    OPENED → (partially) FILLED → TP/SL lifecycle → CLOSED
  - Slippage + fee modelling based on real orderbook depth (Nobitex v3)
  - Stop-loss / TP1-TP2 triggers driven by tick/candle updates
  - Persistent state to JSON (resumable, audit-friendly)
  - Metrics: equity curve, hit-rate, PF, R-multiples, MDD
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nobitex_client import NobitexClient
from risk_engine import Preflight, RiskEngine, RiskPlan, preflight

log = logging.getLogger("paper-trading")

STATE_FILE = Path(os.getenv("NBX_PAPER_STATE", "paper_state.json"))

# Paper maker/taker assumptions (Nobitex mid-tier retail default):
FEE_PCT = 0.002  # 0.2% round-trip equivalent per leg
# Conservative fill model: marketable limit → fill at next level + half spread
SLIP_BPS = 6

@dataclass
class Position:
    id: str
    symbol: str
    side: str           # buy | sell
    entry_price: float
    qty: float          # base asset amount
    size_quote: float   # quote value
    stop: float
    tp1: float
    tp2: float
    stop_pct: float
    opened_at: float
    state: str          # OPEN | PARTIAL | CLOSED
    realized_pnl: float = 0.0
    closed_at: float | None = None
    close_reason: str | None = None
    max_favorable: float = 0.0
    max_adverse: float = 0.0
    filled_qty: float = 0.0
    tp1_filled: bool = False
    sl_filled: bool = False
    last_price: float = 0.0
    risk_amount: float = 0.0          # in the pair's quote currency
    size_usd: float = 0.0             # cost basis in account currency (USD)
    risk_usd: float = 0.0             # amount at risk in account currency
    risk_pct: float = 0.0             # risk_usd / equity at entry (percent)
    quote: str = "USDT"

@dataclass
class OrderEvent:
    ts: float
    kind: str           # submit|fill|partial|tp1|sl|close|reject
    symbol: str
    side: str
    qty: float
    price: float
    extra: dict = field(default_factory=dict)


class PaperBroker:
    """Loads existing state if present, otherwise starts fresh."""

    def __init__(self, equity: float, client: NobitexClient, risk: RiskEngine):
        self.start_equity = equity
        self.equity = equity
        self.cash = equity
        self.client = client
        self.risk = risk
        self.positions: dict[str, Position] = {}
        self.orders: list[OrderEvent] = []
        self.equity_curve: list[tuple[float, float]] = [(time.time(), equity)]
        self.concurrent_limit = 5
        self.usdt_irt = 0.0          # cached FX rate for IRT-quoted pairs
        self.halted = False
        #: The gate breakdown from the last order attempt, for the what-if panel.
        self.last_preflight: Preflight | None = None
        self.last_error: str | None = None
        self.load()

    # --------- persistence ----------
    def load(self):
        try:
            if STATE_FILE.exists():
                raw = json.loads(STATE_FILE.read_text())
                self.start_equity = float(raw.get("start_equity", self.start_equity))
                self.equity = raw.get("equity", self.equity)
                self.cash = raw.get("cash", self.cash)
                self.equity_curve = [tuple(x) for x in raw.get("equity_curve", [])]
                self.positions = {k: Position(**v) for k, v in raw.get("positions", {}).items()}
                self.orders = [OrderEvent(**e) for e in raw.get("orders", [])]
                log.info("loaded paper state: %d positions, equity=%s", len(self.positions), self.equity)
        except Exception as e:
            log.warning("failed to load paper state, starting fresh: %s", e)

    def save(self):
        STATE_FILE.write_text(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "start_equity": self.start_equity,
            "equity": self.equity,
            "cash": self.cash,
            "equity_curve": [list(x) for x in self.equity_curve[-500:]],
            "positions": {k: v.__dict__ for k, v in self.positions.items()},
            "orders": [e.__dict__ for e in self.orders[-400:]],
        }, ensure_ascii=False, indent=1))

    # --------- market helpers ----------
    async def _mid_price(self, symbol: str) -> float:
        ob = await self.client.get_orderbook(symbol)
        if ob and ob.get("asks") and ob.get("bids"):
            a = float(ob["asks"][0][0]); b = float(ob["bids"][0][0])
            return (a + b) / 2
        if s := ob and ob.get("lastTradePrice"):
            return float(s)
        raise ValueError(f"no price for {symbol}")

    async def _quote_rate(self, symbol: str) -> float:
        """USD value of one unit of the pair's quote currency."""
        if not symbol.upper().endswith("IRT"):
            return 1.0
        if not self.usdt_irt:
            try:
                stats = await self.client.get_market_stats()
                row = (stats.get("stats") or {}).get("usdtirt") or {}
                px = float(row.get("latest") or row.get("last") or 0)
                if px > 0:
                    self.usdt_irt = px
            except Exception as e:  # noqa: BLE001 - handled by the caller
                log.warning("fx rate unavailable: %s", e)
        return self.usdt_irt

    async def _execution_price(self, symbol: str, side: str, qty: float) -> tuple[float, float]:
        """Return (avg_price, fill_quantity) walking the orderbook."""
        ob = await self.client.get_orderbook(symbol)
        levels = ob["asks"] if side == "buy" else ob["bids"]
        filled_qty = 0.0
        cost = 0.0
        for price, amount in levels:
            p, a = float(price), float(amount)
            take = min(a, qty - filled_qty)
            cost += p * take
            filled_qty += take
            if filled_qty >= qty:
                break
        if filled_qty == 0:
            return 0.0, 0.0
        return cost / filled_qty, filled_qty

    # --------- order lifecycle ----------
    async def place_limit(self, symbol: str, side: str, qty: float, price: float | None = None,
                          stop: float | None = None, tp1: float | None = None,
                          tp2: float | None = None, source="SEMI") -> Position | None:
        self.last_error = None
        if self.halted or getattr(self.risk, "halted", False):
            self.last_error = "halted: kill switch is armed"
            self.orders.append(OrderEvent(time.time(), "reject", symbol, side, qty, price or 0, {"reason": "halted"}))
            return None
        if len([p for p in self.positions.values() if p.state != "CLOSED"]) >= self.concurrent_limit:
            self.last_error = f"concurrent-cap: already {self.concurrent_limit} open positions"
            self.orders.append(OrderEvent(time.time(), "reject", symbol, side, qty, price or 0, {"reason": "concurrent-cap"}))
            return None
        if not self.cash:
            self.last_error = "no-cash: paper account is empty"
            self.orders.append(OrderEvent(time.time(), "reject", symbol, side, qty, price or 0, {"reason": "no-cash"}))
            return None

        # walk the book to estimate fill price (model slippage via SLIP_BPS)
        if price is None:
            mid = await self._mid_price(symbol)
            px, filled_qty = await self._execution_price(symbol, side, qty)
            if not px:
                self.last_error = "no-depth: orderbook has no liquidity"
                self.orders.append(OrderEvent(time.time(), "reject", symbol, side, qty, mid, {"reason": "no-depth"}))
                return None
            slip = px * SLIP_BPS / 10000
            fill_price = px + slip if side == "buy" else px - slip
        else:
            fill_price = price
            filled_qty = qty

        notional = fill_price * filled_qty
        fee = notional * FEE_PCT

        # Everything below is in the *account* currency (USD). IRT pairs are
        # converted with the live USDTIRT rate so a 21,000 IRT order no longer
        # consumes 21,000 USD of the paper balance.
        rate = await self._quote_rate(symbol)
        if rate <= 0:
            self.last_error = "no-fx-rate: USDTIRT price unavailable for IRT-quoted pair"
            self.orders.append(OrderEvent(time.time(), "reject", symbol, side, qty, fill_price, {"reason": "no-fx-rate"}))
            return None
        notional_usd = notional / rate
        fee_usd = fee / rate
        total_cost = notional_usd + fee_usd if side == "buy" else notional_usd - fee_usd

        # Hard cap: no single position may exceed 25% of equity (ADR-0008).
        cap = 0.25 * max(self.equity, 1e-9)
        if notional_usd > cap:
            self.last_error = (
                f"single-asset-cap: notional ${notional_usd:,.2f} exceeds 25% of equity (${cap:,.2f})"
            )
            self.orders.append(OrderEvent(time.time(), "reject", symbol, side, qty, fill_price, {"reason": "single-asset-cap"}))
            return None

        # Per-trade risk + portfolio heat + cash buffer gates (ADR-0008).
        # Evaluated by the shared `preflight`, which /api/risk/whatif also calls:
        # a preview that used its own copy of these rules would eventually
        # promise trades this function rejects.
        stop_px = stop or fill_price * (0.96 if side == "buy" else 1.04)
        risk_amount = abs(fill_price - stop_px) * filled_qty
        risk_usd = risk_amount / rate
        risk_pct = risk_usd / max(self.equity, 1e-9) * 100
        self.last_preflight = preflight(
            equity=self.equity, cash=self.cash,
            open_heat=getattr(self.risk, "open_heat", 0.0),
            open_positions=len([p for p in self.positions.values() if p.state != "CLOSED"]),
            concurrent_limit=self.concurrent_limit,
            max_heat_pct=getattr(self.risk.cfg, "max_heat_pct", 6.0),
            min_cash_pct=getattr(self.risk.cfg, "min_cash_pct", 10.0),
            max_single_asset_pct=getattr(self.risk.cfg, "max_single_asset_pct", 25.0),
            risk_pct_cap=getattr(self.risk.cfg, "risk_pct", 0.75),
            halted=bool(self.halted or getattr(self.risk, "halted", False)),
            notional_usd=notional_usd, fee_usd=fee_usd, risk_usd=risk_usd,
        )
        # single-asset-cap is enforced above, before the fee is known; the rest
        # are decided here.
        skip = {"halted", "concurrent-cap", "cash-present", "single-asset-cap"}
        block = next((g for g in self.last_preflight.gates if not g.ok and g.id not in skip), None)
        if block is not None:
            self.last_error = f"{block.id}: {block.detail}"
            self.orders.append(OrderEvent(time.time(), "reject", symbol, side, qty, fill_price,
                                          {"reason": block.id}))
            return None

        # take position
        pid = f"pp-{int(time.time() * 1000)}-{symbol}"
        pos = Position(
            id=pid, symbol=symbol, side=side, entry_price=fill_price,
            qty=filled_qty, size_quote=notional,
            stop=stop_px,
            tp1=tp1 or fill_price * (1.04 if side == "buy" else 0.96),
            tp2=tp2 or fill_price * (1.06 if side == "buy" else 0.94),
            stop_pct=abs(fill_price - stop_px) / fill_price * 100,
            opened_at=time.time(), state="OPEN",
            filled_qty=filled_qty,
            max_favorable=0.0, max_adverse=0.0,
            last_price=fill_price,
            risk_amount=risk_amount,
            size_usd=notional_usd,
            risk_usd=risk_usd,
            risk_pct=risk_pct,
            quote="IRT" if symbol.upper().endswith("IRT") else "USDT",
        )
        self.positions[pid] = pos
        self.risk.open_heat = round(getattr(self.risk, "open_heat", 0.0) + risk_pct, 4)
        self.cash -= total_cost
        self.orders.append(OrderEvent(time.time(), "submit", symbol, side, filled_qty, fill_price, {"source": source}))
        self.orders.append(OrderEvent(time.time(), "fill", symbol, side, filled_qty, fill_price, {"source": source, "fee": fee}))
        self._mark_equity_minute()
        self.save()
        return pos

    async def _close(self, pos_id: str, reason: str):
        pos = self.positions.get(pos_id)
        if not pos or pos.state == "CLOSED":
            return
        spx, sold_qty = await self._execution_price(pos.symbol, "sell" if pos.side == "buy" else "buy", pos.filled_qty)
        if not spx or sold_qty <= 0:
            self.orders.append(OrderEvent(time.time(), "reject", pos.symbol, pos.side, pos.filled_qty, 0, {"reason": "revert-failed"}))
            return
        notional_out = spx * sold_qty
        rate = await self._quote_rate(pos.symbol) or 1.0
        fee_out = notional_out * FEE_PCT
        fee_out_usd = fee_out / rate
        if pos.side == "buy":
            pnl_net = (notional_out - pos.size_quote) / rate - fee_out_usd
            self.cash += pos.size_usd + pnl_net
        else:
            pnl_net = (pos.entry_price - spx) * sold_qty / rate - fee_out_usd
            self.cash += pos.size_usd + pnl_net
        self.risk.open_heat = max(0.0, round(getattr(self.risk, "open_heat", 0.0) - pos.risk_pct, 4))
        pos.last_price = spx
        pos.realized_pnl = pnl_net
        pos.closed_at = time.time()
        pos.state = "CLOSED"
        pos.close_reason = reason
        self._mark_equity_minute()
        self.orders.append(OrderEvent(time.time(), "close", pos.symbol, pos.side, sold_qty, spx, {"reason": reason, "pnl": pnl_net, "fee": fee_out, "risk": pos.risk_usd}))
        self.save()

    async def evaluate_positions(self):
        """Called on every scan tick: triggers TP1/SL/trailing based on fresh prices."""
        to_close = []
        for pid, pos in self.positions.items():
            if pos.state == "CLOSED":
                continue
            # derive live price
            try:
                mid = await self._mid_price(pos.symbol)
            except Exception:
                continue
            pos.last_price = mid
            base_entry = pos.entry_price
            diff = (mid - base_entry) / base_entry * 100
            if pos.side == "sell":
                diff = -diff
            pos.max_favorable = max(pos.max_favorable, diff)
            pos.max_adverse = min(pos.max_adverse, diff)
            # TP1 (50% exit at first target)
            hit_tp1 = (pos.side == "buy" and mid >= pos.tp1) or (pos.side == "sell" and mid <= pos.tp1)
            hit_sl = (pos.side == "buy" and mid <= pos.stop) or (pos.side == "sell" and mid >= pos.stop)
            if hit_tp1 and not pos.tp1_filled:
                pos.tp1_filled = True
                exit_qty = pos.filled_qty * 0.5
                notional_tp1 = exit_qty * mid
                rate = await self._quote_rate(pos.symbol) or 1.0
                fee_tp1 = notional_tp1 * FEE_PCT
                cost_basis_tp1 = pos.size_usd * 0.5
                pnl_tp1 = (notional_tp1 - pos.size_quote * 0.5) / rate - fee_tp1 / rate
                self.cash += cost_basis_tp1 + pnl_tp1
                pos.filled_qty -= exit_qty
                pos.size_quote -= pos.size_quote * 0.5
                pos.size_usd -= cost_basis_tp1
                pos.realized_pnl += pnl_tp1
                pos.state = "PARTIAL"
                self.orders.append(OrderEvent(time.time(), "tp1", pos.symbol, pos.side, exit_qty, mid, {"pnl_pct": diff, "pnl": pnl_tp1, "fee": fee_tp1, "risk": pos.risk_usd}))
            # trailing stop post-tp1
            if pos.tp1_filled:
                if pos.side == "buy":
                    pos.stop = max(pos.stop, pos.entry_price * 1.002)
                else:
                    pos.stop = min(pos.stop, pos.entry_price * 0.998)
            if hit_sl or (pos.tp1_filled and pos.filled_qty > 0 and ((pos.side == "buy" and mid <= pos.stop) or (pos.side == "sell" and mid >= pos.stop))):
                to_close.append((pid, "sl-hit" if not pos.tp1_filled else "trail"))
            # time stop flag (reasonable default: 48h with < 1% move)
            if time.time() - pos.opened_at > 48 * 3600 and abs(diff) < 1:
                to_close.append((pid, "time-stop"))
        for pid, reason in to_close:
            await self._close(pid, reason)
        self._mark_equity_minute()

    def _mark_equity_minute(self):
        # Mark open legs to the last observed price (not to their entry cost).
        open_val = sum(
            p.filled_qty * (p.last_price or p.entry_price)
            / (self.usdt_irt if p.quote == "IRT" and self.usdt_irt else 1.0)
            for p in self.positions.values()
            if p.state != "CLOSED"
        )
        current_eq = self.cash + open_val
        self.equity = current_eq
        self.equity_curve.append((time.time(), current_eq))

    # --------- metrics ----------
    def metrics(self) -> dict:
        realized = [e for e in self.orders if e.kind in ("tp1", "close")]
        pnl_list = [e.extra.get("pnl", 0) for e in realized]
        # R-multiple = realised PnL / the amount that was at risk on entry.
        r_mult = [p / e.extra.get("risk", 0) for p, e in zip(pnl_list, realized) if e.extra.get("risk")]
        wins = [p for p in pnl_list if p > 0]
        losses = [p for p in pnl_list if p <= 0]
        hit_rate = (len(wins) / len(pnl_list) * 100) if pnl_list else 0
        pf = (sum(wins) / abs(sum(losses))) if losses else (99.99 if wins else 0.0)
        pf = min(float(pf), 99.99)
        expectancy = (sum(pnl_list) / len(pnl_list)) if pnl_list else 0
        eq_vals = [v for _, v in self.equity_curve]
        mdd = 0.0
        peak = eq_vals[0] if eq_vals else self.equity
        for v in eq_vals:
            peak = max(peak, v)
            mdd = max(mdd, (peak - v) / max(peak, 1) * 100)
        return {
            "trades": len(pnl_list),
            "start_equity": round(self.start_equity, 2),
            "openPositions": sum(1 for p in self.positions.values() if p.state != "CLOSED"),
            "cash": round(self.cash, 2),
            "equity": round(self.equity, 2),
            "hit_rate": round(hit_rate, 1),
            "profit_factor": round(pf, 2),
            "expectancy": round(expectancy, 2),
            "expectancy_r": round(sum(r_mult) / len(r_mult), 3) if r_mult else 0.0,
            "max_drawdown": round(mdd, 1),
            "daily_pnl": round(self.risk.daily_pnl, 2),
        }


PAPER_BROKER: PaperBroker | None = None
def get_broker(equity: float, client: NobitexClient, risk: RiskEngine) -> PaperBroker:
    global PAPER_BROKER
    fez = None
    if PAPER_BROKER is None:
        PAPER_BROKER = PaperBroker(equity, client, risk)
    return PAPER_BROKER
