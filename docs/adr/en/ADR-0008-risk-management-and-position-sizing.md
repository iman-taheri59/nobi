# ADR-0008: Risk Management, Position Sizing, and Drawdown Controls

[فارسی](../fa/ADR-0008-مدیریت-ریسک-و-حجم-دهی-پوزیشن.md) · **English**

- **Status:** Accepted (implemented)
- **Date:** 2026-02-14
- **Deciders:** Technical Architect, Risk Owner
- **Related ADRs:** 0001, 0007, 0009

---

## 1. Context

Profitable selection is worthless without survival. This ADR makes risk constraints **code-enforced, pre-trade gates** — the risk engine can veto or shrink any TradePlan, never the reverse.

## 2. Decision

### 2.1 Capital Model & Per-Trade Sizing
- Source of truth: live balance snapshot via `/v2/wallets`, maintained between fills.
- **Risk-per-trade:** `risk_amount = equity × risk_pct`, default `risk_pct ∈ [0.5%, 1%]` (config 0.25%–2%).
- **Volatility-based size:** `size = risk_amount / (stop_distance)` where `stop_distance = ATR(14, exec TF) × k` (default k≈1.5–2.5 pack-defined). Effect: equal risk contributed per position, automatically smaller size on more volatile alts.
- **Volatility targeting (optional):** scale nominal exposure toward target daily vol (e.g., 2–3% equity vol).
- **Kelly stream:** research-only; if enabled live, capped at quarter-Kelly (0.25K) and floored by the ATR-size above.
- **Precision compliance:** round `amount`/`price` to market steps from market-options endpoint; skip trade if under min-amount/min-notional.
- **Fee & slippage realism:** expected round-trip cost (Nobitex fee tier + modeled slippage from depth) must be < 25% of expected R1 reward; otherwise candidate rejected.

### 2.2 Portfolio-Level Caps
| Cap | Default |
|---|---|
| Max concurrent positions | 5 |
| Max total portfolio heat (sum of open risk) | ≤ 6% equity |
| Max exposure per asset | 25% equity |
| Max exposure per correlation cluster | 40% equity |
| Min cash buffer (IRT/USDT) | 10% equity |

### 2.3 Stops & Exits (server-side simulation + exchange-backed when supported)
- Initial SL = ATR-based; never widened, only trailed up.
- Trailing after R1; breakeven move after R1 fill optional (config).
- Since classical OCO/stop behavior varies by market, the bot runs its **own stop engine**: market order exit when trigger breached, with max-slippage guard; where exchange stop/OCO order types are supported natively, prefer them.
- **Time stop:** exit if trade shows no progress after N=20 bars or TTL expiry of the originating signal.

### 2.4 Kill Switches & Halts (code-enforced, priority above all)
- **Daily loss halt:** realized+unrealized ≤ −3% equity → freeze new entries (24h), optional flatten.
- **Weekly loss halt:** ≤ −6% → halt until manual reset + review note.
- **Consecutive-loss cooldown:** 3 consecutive stop-outs → 48h cooldown + auto drop one risk tier.
- **API health breaker:** repeated auth/order failures → suspend trading, alert immediately.
- **News/veto halt:** FA veto (hack, delist) → immediate exit workflow (confirm if market halts).
- **Panic button:** CLI/API/Telegram command: cancel-all + flatten + disable (two-step confirm for flatten).

### 2.5 Exposure Timing
No averaging into losers (no martingale). Pyramiding allowed only after R1 in profit, max 1 add-up, and only while total position risk ≤ initial risk.

## 3. Risk Budget & Reporting
Dashboards expose: equity curve, open risk heatmap, per-trade R multiples, hit rate, PF, expectancy, current premium exposure. Nightly digest + threshold alerts via Telegram.

## 4. Alternatives Considered
| Option | Verdict |
|---|---|
| Fixed % position (no volatility link) | Rejected: unequal risk contribution |
| Full Kelly | Rejected: drawdown depth unacceptable |
| No kill switches (alerts only) | Rejected: doesn't satisfy professional requirement |

## 5. Consequences
**Positive:** bounded worst days; anti-revenge design; survivable tail risks.
**Negative:** lower maximum theoretical upside; occasional missed trades due to caps — accepted by design.

---

## Implementation amendment — 2026-10-21: the live ladder versus the backtest ladder

### What was seen

The live engine fixed its take-profit levels at `(1.2, 2.0)` (hard-coded inside
`_tp` until 1.0.5), while the backtest simulator defaults to `tp1_r=1.5, tp2_r=2.5`
and the search grid sweeps `(1.0,2.0) / (1.5,2.5) / (2.0,3.5)`. The "best parameter
the backtest finds" and "what the live bot applies" were therefore two different
numbers, and nothing anywhere said so.

### Decision

1. **The difference stays, deliberately.** The backtest ladder is a search space; the
   live ladder is a policy. Automatically feeding the winner of the first into the
   second means an optimisation over past data silently changes future trading
   behaviour — that is overfitting one step removed.
2. **But it is no longer hidden.** The live ladder now:
   - comes from `RiskConfig.rr_levels` (not from a literal inside `_tp`),
   - is published in `GET /api/risk/state` under `caps.rrLevels` — the same rule we
     set for `minRR`: the number a page warns about must come from the engine itself,
   - can be set with `NBX_RR_LEVELS=1.5,2.5`. Any unusable value (fewer than two
     levels, non-increasing, zero, negative or `nan`) is refused and the service keeps
     the documented default and says so in the log; a wrong ladder is a wrong trade.
3. **The browser broker** (the no-backend path) now derives its default targets from
   the same ladder and the stop distance (`1.2R` and `2.0R`); it used to use three
   percentages (`1.045 / 1.075`) that happened to land near those ratios.

### Operating it

```bash
# 1) read the winning ladder from the backtest report (best_params in /api/backtest)
# 2) write it deliberately into the service env and restart
NBX_RR_LEVELS=1.5,2.5
# 3) confirm it actually applied
curl -s http://127.0.0.1:8000/api/risk/state | python -m json.tool | grep -A3 rrLevels
```

### What this amendment does not say

It does **not** recommend that the backtest ladder is better. If a backtest over a
short window says `(2.0, 3.5)` is best, that number says nothing about the future.
The bridge exists for a deliberate choice, not as a substitute for judgement.
