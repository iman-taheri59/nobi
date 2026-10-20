# ADR-0008: Risk Management, Position Sizing, and Drawdown Controls

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
