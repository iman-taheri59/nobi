# Post-queue owner probe analysis — capoy.ir

- Probe file: `capoy-owner-probe-post-queue-check.json`
- Generated: `2026-11-02T14:42:45Z`
- Base: `https://capoy.ir`
- Probe script version: `2.0.0`
- Requests: `75`, endpoints: `74`, duration: `157.7 s`, aborted: `false`
- Actual app version from `/api/health`: **2.1.10**
- Probe metadata `target_app_version` is still `2.1.8`; that is a script constant, not the deployed version.

## Executive summary

1. **The original approval-queue defect was not reproduced for the post-upgrade order.**
   The `1K_CATIRT` order came from `source=QUEUE`, passed the executable TP2 RR gate, and opened a paper position. Its execution RR at the filled price was about **2.05**, above `min_rr=1.5`.

2. **The deployment hardened several previously failing/warning areas.**
   Secure session cookie, authenticated API `no-store`, CSP/frame protection, nosniff/referrer headers, logout session clearing, and the drift-cohorts endpoint now pass.

3. **One edge-header warning remains.**
   The probe still warns about `strict-transport-security`. The root document `/` has no HSTS in this probe, while API responses do. This matches the pending nginx location-inheritance merge.

4. **A new, material paper-broker accounting defect is visible in the same order.**
   The exit did not sell the full open quantity, but the model marked the position `CLOSED`, reported `remaining_open=0`, and booked PnL as if the unsold residual had zero value.

5. **Live trading remains disabled.**
   `/api/live/status`: `mode=disabled`, `configured=false`, `placement_enabled=false`. `/api/live/orders` returned the expected disabled-state response with zero orders.

## Probe check results

| Status | Count |
|---|---:|
| pass | 16 |
| warn | 1 |
| fail | 0 |
| info | 2 |

Non-pass checks:

| Check | Status | Meaning |
|---|---|---|
| edge header strict-transport-security | warn | Root edge HSTS is still missing/inconsistent. |
| heavy compute GETs | info | Skipped by default; not a failure. |
| gate ledger evidence GET | info | Skipped by default; not a failure. |

Endpoint outcomes: **74 ok**. Expected non-200 responses were also classified correctly:

- `/robots.txt` → 404, expected.
- `/docs`, `/openapi.json` → 401, expected for anonymous.
- `/api/live/orders?status=all` → 409, expected while live is disabled.

## Security/deployment items confirmed improved

| Item | Result |
|---|---|
| Login HTTP status | pass, 200 |
| Login role | pass, owner |
| Session cookie HttpOnly | pass |
| Session cookie SameSite | pass, lax |
| Session cookie Secure on HTTPS | pass |
| Session cookie host-only | pass |
| CSRF cookie readable by JS | pass |
| Authenticated role | pass |
| Authenticated API no-store | pass |
| X-Content-Type-Options | pass |
| Referrer-Policy | pass |
| Content-Security-Policy | pass |
| Frame protection | pass |
| Logout HTTP status | pass, 200 |
| Session cleared after logout | pass |
| `/api/v1/signal-intelligence/drift/cohorts` | 200 |

The probe does **not** prove duplicate headers are gone. It records header values, but it is not a duplicate-counting edge checker. After the nginx merge, still run:

```bash
python3 scripts/check_edge_headers.py --base https://capoy.ir --json
```

## Queue/order evidence

### Successful post-upgrade queue order

| Field | Value |
|---|---|
| Symbol | `1K_CATIRT` |
| Side | buy |
| Source | `QUEUE` |
| Execution | market |
| Opened | `2026-11-02T10:51:53Z` |
| Closed | `2026-11-02T13:00:29Z` |
| Close reason | `sl-hit` |
| Requested qty | `6,078,028.378361904` |
| Entry filled qty | `2,448,651` |
| Entry filled percent | `40.3%` |
| Mid at submit | `511.9995` |
| Entry fill price | `524.408` |
| Adverse entry slip | `242.35 bp` |
| Stop | `491.24074233` |
| TP1 | `565.51110921` |
| TP2 | `592.51851535` |
| Execution RR at fill | **2.0535** |
| Minimum RR | `1.5` |
| Entry fee | `2,568,184.347216` quote units |
| Recorded PnL | `-1233.2148 USD` |

The important queue-gate calculation:

```text
RR = (TP2 - entry) / (entry - stop)
   = (592.51851535 - 524.408) / (524.408 - 491.24074233)
   ≈ 2.0535
```

That is above `1.5`, so this order was not an `execution-rr-too-low` candidate at submit.

### Historical rejected rows still present

These are retained pre-upgrade/early events, not evidence that the new queue gate failed:

| Symbol | Opened UTC | RR | Min RR | State |
|---|---|---:|---:|---|
| PUMPIRT | 2026-11-02 03:36:15 | 0.4140 | 1.5 | REJECTED |
| ZECIRT | 2026-11-02 03:34:11 | 0.7325 | 1.5 | REJECTED |
| HOTIRT | 2026-11-01 19:45:53 | 1.0794 | 1.5 | REJECTED |
| HOTIRT | 2026-11-01 19:18:07 | 0.4968 | 1.5 | REJECTED |

The successful `1K_CATIRT` queue order opened at `10:51:53Z`, after the app-tier upgrade. The old rejected rows remain because the paper broker keeps a bounded event history.

## New finding: partial exit was accounted as a full close

The same `1K_CATIRT` position shows:

```text
entry_filled       = 2,448,651
retained_exit_qty  = 2,096,055
remaining_open     = 0
state              = CLOSED
close_reason       = sl-hit
```

But:

```text
unsold residual = 2,448,651 - 2,096,055 = 352,596
residual percent ≈ 14.4% of the entry fill
```

The close event sold only **85.6%** of the open quantity:

```text
close qty   = 2,096,055
close price = 452.37890694900665
exit fee    = 1,896,422.1396100004 quote units
```

### Why this happens in code

In `server/paper_trading.py`, `_close()` walks the order book for `pos.filled_qty`:

```python
spx, sold_qty = await self._execution_price(..., pos.filled_qty)
```

If the book has insufficient depth, `sold_qty` can be smaller than `pos.filled_qty`. The current code only rejects when `sold_qty <= 0`. It does **not** handle:

```text
0 < sold_qty < pos.filled_qty
```

It then computes:

```python
notional_out = spx * sold_qty
pnl_net = (notional_out - pos.size_quote) / rate - fee_out_usd
pos.state = "CLOSED"
```

For a buy position, `pos.size_quote` is still the full entry cost. If only part of the quantity was sold, this subtracts the full cost while adding back only the partial sale proceeds. The unsold residual is effectively valued at zero.

### Approximate impact

Using the probe values and `quote_usd=273800`:

| Calculation | Approx USD |
|---|---:|
| Recorded PnL | `-1233.21` |
| PnL on sold quantity only, same close price | `-558.34` |
| Residual value at close price | `+582.57` |
| Full-quantity PnL if all had sold at same close price | `-652.26` |

The exact correct treatment depends on the intended paper-model policy, but the current output is internally inconsistent:

- the position is `CLOSED`;
- `remaining_open` is `0`;
- retained exits are less than the entry fill;
- recorded PnL includes a loss on quantity that was not sold.

This is separate from the original queue RR defect.

## Additional execution-quality observations

These are not necessarily bugs, but they matter for paper/live readiness:

1. **Entry fill was partial:** only `40.3%` of requested quantity filled.
2. **Adverse entry slip was large:** `242.35 bp`, about `2.42%` worse than submit mid.
3. **Stop exit slipped far beyond the stop:** close average `452.3789` vs stop `491.2407`, about `7.91%` below the stop.
4. The stop trigger uses mid, then exits by walking the book. In thin markets, the realized exit can be much worse than the stop level.

## Live safety state

| Item | Value |
|---|---|
| Live mode | disabled |
| Live configured | false |
| Placement exposed | true |
| Placement enabled | false |
| Live orders count | 0 |
| Live evidence ready | false |
| Paper open positions | 0 |
| Paper equity | `98679.36 USD` |

No real exchange order was placed by this probe. Its only POST requests are login/logout.

## Recommended next steps

1. **Do not enable Live/Auto.**
2. **Merge the live nginx site config carefully**, then run `nginx -t`, reload, and verify with `scripts/check_edge_headers.py`.
3. **Decide whether to fix the paper partial-exit accounting defect before relying on paper metrics.**
   A safe fix should either:
   - keep the residual open and reduce `filled_qty`/cost basis proportionally; or
   - retry/queue the residual exit; or
   - explicitly mark the close as partial/unknown instead of silently reporting `CLOSED` with zero residual.
4. Update the probe script metadata `TARGET_APP_VERSION` separately when convenient; it does not affect this analysis.
