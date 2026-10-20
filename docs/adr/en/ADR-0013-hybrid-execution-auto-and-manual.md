# ADR-0013: Hybrid Execution — Automated, Semi-Automated and Manual Trading

- **Status:** Accepted (manual mode implemented)
- **Date:** 2026-02-14
- **Deciders:** Project Owner, Technical Architect, Risk Owner
- **Related ADRs:** 0001, 0007, 0008, 0009, 0011
- **UI surface:** "Manual Trading Desk" page in `index.html`

---

## 1. Context

Explicit requirement: the system must support **both automated and manual trading**. A single automation on/off switch is insufficient — the boundary between automation and human intervention must be controllable at three granularities: portfolio, symbol, and individual trade. Critically, any human-initiated trade must pass the **same risk gates** the bot passes (ADR-0008); being manual is not a license to bypass risk.

## 2. Decision — Three Execution Modes

### 2.1 Portfolio level
| Mode | Behavior |
|---|---|
| **AUTO** | Signals scoring ≥ threshold are sized and executed automatically. Human monitors only. |
| **SEMI — default in v1** | Bot builds the signal and a full trade plan (size/entry/stop/TP) and places it in an **Approval Queue**; execution requires human confirmation. Risk preview and TTL are shown. |
| **MANUAL** | No orders are ever auto-placed. The signal engine acts as an advisor only; trades come exclusively from the manual order ticket. |

- Switching the portfolio mode requires **two-step confirmation** (hold-to-confirm) and is written to the audit log.
- Leaving AUTO is immediate; entering AUTO is only permitted when promotion gates (ADR-0009) are satisfied.
- **The Kill Switch outranks every mode** (ADR-0008): while halted, even the manual ticket is locked.

### 2.2 Symbol-level override
Every symbol in the signals list carries an independent mode chip: `AUTO / SEMI / MANUAL / BLOCKED`, which takes precedence over the portfolio mode. Example uses: "fully automated, but I decide BTC myself" or "this symbol is blocked due to a news veto".

### 2.3 Trade level
- In SEMI, any queued signal can be **edited before approval**: size, entry, stop, TP1/TP2, TTL.
- In AUTO, executed signals remain cancellable until fill (short window, default 5s).
- Rejected signals keep their reason and signal_id and are tagged as `human_rejected` in the performance report (ADR-0012) — later revealing whether human intervention adds or destroys value.

## 3. Approval Queue
```
PendingTrade {
  id, signal_id, ts, symbol, direction, composite_score, rank,
  plan: { entry_type, entry_price, stop, tp1, tp2, size_quote, risk_pct, ttl_secs },
  overrides: { ...human edits kept separately... },
  state: PENDING | APPROVED | REJECTED | EXPIRED
}
```
- **TTL:** each proposal auto-expires (default 3 candles of the execution timeframe); approving a stale signal is forbidden.
- **Risk gates apply to approvals too:** if a human edit pushes per-trade risk beyond the cap (0.75%) or single-asset exposure beyond 25%, approval is blocked with a warning. Override requires an explicit "risk accepted" flag that is logged.
- **Queue priority:** descending composite score; the concurrent-position cap (5) still applies at portfolio level.
- **Manual Ticket:** order form with symbol, side, type (limit/market), price, size, stop, TP — with a **live risk preview** (notional, risk, R ratio, cap status). Manual orders receive a `clientOrderId` and flow through the same order state machine (ADR-0009).

## 4. Lifecycle & Audit
- Every transition (mode change, approval, rejection, edit, manual order) is recorded in `audit_log` with user, timestamp, and before/after values.
- Both automated and manual trades land in `signal_log` / `signal_outcomes` (ADR-0012) tagged with `source=AUTO|SEMI|MANUAL` so performance is comparable.
- The dashboard exposes a "human intervention" metric: average R of human-approved vs automated trades.

## 5. Alternatives Considered
| Option | Verdict |
|---|---|
| Single automation toggle | Rejected: too coarse; can't express "everything automatic except this symbol" |
| Manual mode bypassing risk gates | Rejected: unbounded risk |
| Telegram-only approvals | Accepted as a secondary channel; the web desk remains primary (ADR-0011) |

## 6. Consequences
**Positive:** gradual control; low-risk path to full automation; comparable human-vs-bot performance data; complete auditability.
**Negative:** SEMI latency depends on human availability (mitigated by TTL, and by defaulting to semi only during the trust-building phase); manual edits can damage a quantitative plan (mitigated by deviation warnings and logged overrides).

---

## Implementation amendment — 2026-10-20

- **Implemented:** the full manual mode (ticket priced from a single source, risk cap
  checks, TTL-expiring approval queue) with an audit record for every action.
- **Not implemented:** automatic mode. No order leaves this repository for Nobitex;
  the client has no order-submitting method at all — `POST /market/orders/*` is
  named in its docstring and never implemented. The only outbound `POST` is to
  `api.telegram.org`.
- **The lesson that produced ADR-0014:** after a recovery the ticket kept its
  "unavailable" marker — it had a number on screen while claiming an outage.

## Second amendment — where the queue comes from (2026-10-20)

Until this date the approval queue was a hard-coded empty array in `index.html`:
the page promised "the robot plans, you approve" while no candidate ever reached
it, so the approve/reject buttons could not be pressed to any effect. Now:

- **Source:** the engine's own ranking (`live.ranked`) — LONG only, symbols whose
  mode is not `manual`, at most the top five.
- **Price and size:** `POST /api/risk/plan`, the route that also sizes the manual
  ticket. A candidate whose plan is not placeable is not shown, and the count is
  stated in words; no number is substituted.
- **TTL:** the close of the candle the signal was computed on (`t + tf − now`).
  When the series carries no usable clock the cell is an em-dash, not a zero.
- **Approve:** `POST /api/paper/order` with `source=QUEUE`. The gates run on the
  server only; the local R:R veto (which refused earlier, in the browser, naming
  no gate) was removed.
- **Reject:** removes the symbol from this session's queue and sends nothing.
