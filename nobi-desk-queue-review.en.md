# Review of approval-queue orders rejected with `execution-rr-too-low`

[فارسی](nobi-desk-queue-review.md)

Review date: 2026-11-02<br>
User example: HOT, reject event with `ts=1793562353.1871986`<br>
Event time: `2026-11-01 19:45:53 UTC` ≈ `2026-11-01 23:15:53 Asia/Tehran`

> English version of the Persian report `nobi-desk-queue-review.md`. This report describes the pre-remediation `2.1.8` behavior; the queue execution-preview contract was later changed in `2.1.9`.

## Short answer

From the trading-engine perspective, this behavior is **fail-closed**: an unexecutable order is not placed and no position is created. But from the product/UI perspective, it is a **serious defect in the approval-queue contract**:

> The approval queue currently shows a “reviewable signal candidate”, not a “guaranteed executable order”. However, the “Approve and send” button creates the expectation that if a signal is in the queue, executing it is consistent with the R:R policy.

For that reason, a signal may appear in the queue, the user may approve it, and at that same moment it may be recorded on the Orders page as a `rejected-attempt` with reason `execution-rr-too-low`.

---

## Exact code chain

### 1. The queue checks signal/time validity, not executability

`server/queue_policy.py` → `describe()`

It checks:

- valid timeframe
- candle is closed
- signal is not expired
- direction is `LONG`
- `lastClose`, ATR, and score are valid

But it does not check:

- R:R at the current executable price
- order-book depth
- average execution price for the proposed size
- slippage
- distance from actual execution price to TP2/Stop

### 2. The queue preview is built on `lastClose`

`server/main.py` → `/api/risk/plan?approval=true`

The plan is built with:

```python
s["lastClose"]
s["risk"]["atr"]
```

and `risk.evaluate(...)` builds stop/tp1/tp2 around that signal price. This is not necessarily the current executable price.

### 3. The queue UI shows that plan and prints R:TP1

`index.html` → `refreshQueue()` and `paintQueueRow()`

The queue considers these conditions sufficient for display:

- plan is not `ok=false` or `blocked`
- `size_quote > 0`
- `entry > 0`
- approval clock is valid

But it does not evaluate executability against the order book.

Another misleading point: the queue row prints:

```text
R:TP1
```

while the broker’s final gate is based on **TP2 R:R**.

### 4. The queue-row what-if is only account capacity, not execution geometry

`index.html` → `askRowWhatIf()`

This path sends only:

```js
{symbol, side, tf, entry, stop, size_quote}
```

and `/api/risk/whatif` in `server/main.py` mainly predicts heat/cash/cap/risk. It does not bring TP1/TP2 and the order book into an execution preview.

Therefore the what-if may not say blocked, while the broker rejects the same order.

### 5. Queue submission has no explicit limit price, and the broker derives execution price from the book

`index.html` → `resolveQ()`

Queue submission is approximately:

```js
{
  symbol,
  side: 'buy',
  size_quote,
  stop,
  tp1,
  tp2,
  source: 'QUEUE',
  approval: {tf, signal_key}
}
```

No explicit price is sent. In `server/main.py` → `/api/paper/order`, when `price` is absent, the broker takes the market-like path:

`server/paper_trading.py` → `place_limit()`

- `_execution_price()` walks the order book for the requested size.
- Modeled slippage is added.
- Price and quantity are rounded.
- Then `_execution_plan_error()` checks using the **actual execution price**.

### 6. Final gate: TP2 R:R must be at least 1.5

`server/paper_trading.py` → `_execution_plan_error()`

For a buy:

```text
distance = entry - stop
reward2  = tp2 - entry
RR       = reward2 / distance
```

If:

```text
RR < MIN_RR
```

it is rejected with:

```text
execution-rr-too-low
```

`MIN_RR = 1.5` in `server/risk_engine.py`.

Important: stop/tp1/tp2 are the values sent from the queue, and the broker does not move them to make the order pass. That is correct; the problem is that the queue did not perform this same check at an executable price before submission.

---

## Why can this happen for multiple signals?

With the default ladder:

```python
DEFAULT_RR_LEVELS = (1.2, 2.0)
```

the plan around the signal price usually has TP2 R:R around `2.0`. But if the execution price moves even slightly against the entry, R:R drops quickly.

For a buy, if the stop-to-entry distance is `d`:

```text
stop = E - d
tp2  = E + 2d
```

The maximum execution price that passes `MIN_RR=1.5` is approximately:

```text
fill_max = E + 0.2d
```

That means only about **20% of the stop distance** in adverse movement/slippage is enough for TP2 R:R to fall below 1.5.

For a shallow symbol or large size, such as what was seen in the HOT event:

```text
qty ≈ 24,552,278
price ≈ 114.6844
```

walking the order book can raise the average execution price enough that a plan valid on paper becomes invalid in execution. If this repeats across several signals, it is likely a combination of:

- proposed size is large relative to market depth
- actual execution price is far from the signal `lastClose`
- slippage/spread affects R:R
- the queue remains open until the next candle deadline, but market price does not wait
- queue what-if measures only account capacity, not executable geometry

---

## Local reproduction

Script:

```text
/home/user/nobi-desk-review/repro_queue_rr.py
```

Summary output:

```text
queue/risk plan:
  entry=112.0 stop=108.0 tp1=116.8 tp2=120.0 rr_ratio=2.0 blocked=False

plan geometry:
  error=None rr=2.0

execution geometry at 114.6844:
  error=execution-rr-too-low rr≈0.795 min_rr=1.5

broker:
  position=None
  reject event reason=execution-rr-too-low
  no position, cash unchanged
```

That is, the same plan that is valid in preview is rejected at a higher execution price. This is exactly the mechanism the user saw.

Related targeted tests were also run:

```text
server/tests/test_queue_policy.py
server/tests/test_execution_plan.py
server/tests/test_paper_order_route.py
server/tests/test_udf_price_units.py
```

Result:

```text
104 passed
```

This shows the execution gate is intentional and tested; the main problem is the queue display/contract before submission.

---

## About the text `rejected-attempt · unavailable`

This part is not itself an error:

- `rejected-attempt`: a failed attempt was recorded, not a position.
- `position_link=unavailable`: because no position was created, there is no position link.
- exits, entry fee, PnL, and close are `—` because no entry occurred.

The main problem is that before reaching this page, the user saw a signal in the approval queue that was not acceptable under current execution conditions.

---

## Another diagnostic defect

In `server/main.py` → `_order_rows()`, the reject event has these useful fields:

```text
stop, tp1, tp2, rr, min_rr, detail
```

But the order projection in `row["rejection"]` transfers only part of them, and `event_details` mainly shows `reason`. As a result, the Orders page may state the reason but not show the exact rejection geometry in a clear machine-readable way.

To verify that exact HOT case, these are needed:

- full symbol: `HOTIRT` or `HOTUSDT`
- signal timeframe
- entry/stop/tp1/tp2/size shown by the queue
- full `rejection` object, or at least `rejection.detail`
- if possible, an order-book snapshot near the same timestamp

Without these, the mechanism can be explained definitively, but the exact numbers of that order cannot be reconstructed.

---

## Severity and impact

- **Trade safety:** lower, because the broker does not allow invalid execution.
- **Product/trading-desk trust:** high, because the approval queue implicitly promises executability.
- **Operations:** medium; rejected attempts repeat, the Orders page becomes noisy, and the user cannot tell whether the problem is the signal, size, market depth, or price movement.

---

## Proposed fix

### Main fix: server-side execution preview for the queue

Add a server path or extension, for example:

```text
/api/risk/plan?approval=true&execution_preview=true
```

or a separate path:

```text
/api/queue/preview
```

This path should use the same broker functions:

1. Take the proposed size.
2. Walk the current order book for that size.
3. Build rounded price and quantity.
4. Run `_execution_plan_error()` on the submitted stop/tp1/tp2.
5. Also run account-capacity preflight.
6. Return explicit output:

```json
{
  "executable": false,
  "blocked_by": "execution-rr-too-low",
  "plan_entry": 112.0,
  "execution_entry": 114.68,
  "execution_rr": 0.796,
  "min_rr": 1.5,
  "stop": 108.0,
  "tp1": 116.8,
  "tp2": 120.0,
  "depth_ok": true,
  "as_of": 1793562353
}
```

The queue should enable the approve button only when `executable=true` and the preview is fresh.

### Queue UI fix

- Show execution `R:TP2` instead of `R:TP1`.
- If execution is rejected because of RR/depth, either remove the row or show it as non-submittable with the exact reason.
- Keep the approve button disabled while the preview is unknown or stale.
- Keep the broker’s final gate; removing it is not the solution.

### Orders-page fix

`rejection` should explicitly transfer:

```text
plan_entry, execution_entry, stop, tp1, tp2,
execution_rr, min_rr, depth/slippage context
```

so the user can see exactly why the plan was accepted on paper but rejected in execution.

### Optional sizing fix for shallow markets

For symbols such as HOT, proposed size should not be based only on ATR/equity; market depth and size impact on average execution should also affect sizing or queue warnings. This change needs an explicit policy, not a later surprise from the broker.

---

## Conclusion

Direct answer to the user’s question:

> If this signal is rejected, why is it shown in the approval queue?

Because the current approval queue does not evaluate execution rejection before display/submission. The signal is valid by clock, direction, score, and plan at `lastClose`; but the broker later checks at the real order-book price and, if TP2 R:R is below 1.5, rejects fail-closed.

This should be fixed as a queue/preview contract defect, not by removing the execution gate.

---

## Appendix: exact analysis of the two HOTIRT rows sent by the user

The data in this section comes from the two reject records sent by the user. No network request, order placement, or state change was performed.

Calculation script:

```text
/home/user/nobi-desk-review/hotirt_exact_analysis.py
```

Machine-readable output:

```text
/home/user/nobi-desk-review/hotirt_exact_analysis.json
```

### Result using the existing repository gate

Both rows were rechecked with the real `_execution_plan_error` function in `server/paper_trading.py`, and both returned exactly `execution-rr-too-low`.

| Quantity | Row 1 | Row 2 |
|---|---:|---:|
| Execution price | 114.6844 | 117.1312 |
| Stop | 102.67800457 | 101.11402956 |
| TP1 | 120.98661452 | 118.69570453 |
| TP2 | 127.64429087 | 125.08904089 |
| Entry-to-stop distance | 12.00639543 | 16.01717044 |
| Entry-to-TP2 reward | 12.95989087 | 7.95784089 |
| Execution TP2 R:R | **1.079416** | **0.496832** |
| Policy minimum | 1.5 | 1.5 |
| Execution TP1 R:R | 0.524905 | 0.097677 |

Buy formula:

```text
RR = (TP2 - execution_entry) / (execution_entry - stop)
```

Row 1:

```text
(127.64429087 - 114.6844) / (114.6844 - 102.67800457)
= 12.95989087 / 12.00639543
= 1.0794156 < 1.5
```

Row 2:

```text
(125.08904089 - 117.1312) / (117.1312 - 101.11402956)
= 7.95784089 / 16.01717044
= 0.4968319 < 1.5
```

So the reject reason is mathematically and code-wise definitive.

### Maximum acceptable entry price with the same Stop/TP2

Keeping the submitted stop/tp1/tp2:

```text
max_entry = (TP2 + 1.5 * stop) / 2.5
```

| Quantity | Row 1 | Row 2 |
|---|---:|---:|
| Maximum entry for RR=1.5 | 112.66451909 | 110.70403409 |
| Actual entry | 114.6844 | 117.1312 |
| Amount above limit | 2.01988091 | 6.42716591 |

That means even without additional slippage, the recorded execution price itself is above the policy limit for this geometry.

### What was the original plan price?

This section is **inference**, not direct historical observation. If the ladder is the repository default:

```text
TP1 = E + 1.2d
TP2 = E + 2.0d
stop = E - d
```

Then:

```text
d = (TP2 - TP1) / 0.8
E = TP2 - 2d
```

Result:

| Quantity | Row 1 | Row 2 |
|---|---:|---:|
| Inferred original entry | about 111.0001 | about 109.1057 |
| Original stop distance | about 8.3221 | about 7.9917 |
| Execution-price increase vs plan | about 3.32% | about 7.36% |
| Increase relative to stop distance | about 44.3% | about 100.4% |
| Maximum allowed increase from original plan for RR=1.5 | about 1.50% | about 1.46% |

The stop/TP1/TP2 numbers for both rows are consistent with the default ladder `(1.2, 2.0)` and reach the same original entry from three independent relations. However, without the plan log or `signal_key` at that moment, these original entries cannot be called definitive historical observations.

This inference shows the previously reported mechanism is not just a possibility: in these two samples, the execution price’s distance from the plan is much larger than the margin the RR policy tolerates.

### Why are the reject financial fields null?

In `place_limit()`, the geometry gate runs **before** IRT/USD rate, fee, dollar notional, and account preflight calculations. Therefore, for this kind of reject:

```text
fee_quote = null
pnl_usd = null
risk_pct = null
equity_usd = null
quote_rate = null
```

is natural. These rows are failed attempts, not open or closed positions. `counts.closed=1` is not necessarily related to these two rejects.

### Another queue behavior defect after definite failure

In `index.html` → `resolveQ()`, if the server returns a definite `ok=false`, the UI only shows a toast/audit and returns. It does not add the row to `queueDismissed`; removal happens only on success or manual user rejection.

Result: a proposal that has just failed definitively can remain in the queue until expiry or signal change, and the user can approve it again. This combines with the execution-preview defect and worsens the experience of “a rejected signal is still in the queue”.

Suggested complementary fix:

- A definite, known failure should disable/remove the row for that proposal, or at least lock the approve button.
- Unknown network responses should still be handled conservatively and direct the user to the Orders page.
- A definite `execution-rr-too-low` failure should not be treated like an unknown outcome.
