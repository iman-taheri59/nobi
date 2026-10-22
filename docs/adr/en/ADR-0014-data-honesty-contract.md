# ADR-0014: Data Honesty Contract — the three states of every panel

[فارسی](../fa/ADR-0014-قرارداد-صداقت-داده.md) · **English**

- **Status:** Accepted (implemented)
- **Date:** 2026-10-20
- **Deciders:** technical architect, quant lead
- **Related ADRs:** 0001 (evidence rule), 0004 (data pipeline), 0011 (UI), 0012 (monitoring)
- **Enforcement:** `scripts/probe_live.py`, `scripts/mock_upstream.py`, `server/tests/test_core.py`

---

## 1. Context

For a while the dashboard showed numbers nobody could have computed: "technical (78)"
tiles, `ADX=29`, `%b=0.74`, unlock trees, and equity curves built from a random walk.
When Nobitex was unreachable the same numbers stayed on screen, so a reader had no way
to tell a real quote from an invented one.

After those numbers were deleted the problem changed shape: empty panels showed a bare
"—", or the headline "Nobitex unreachable" — even when the reason had nothing to do with
the exchange ("no order has been placed yet"). A wiring bug and a network outage became
indistinguishable.

## 2. Decision

### 2.1 Every panel is in exactly one of three states

| State | Condition | Correct rendering |
|---|---|---|
| **DATA** | a number arrived from a source we can name | the real number |
| **EXPLAINED** | the exchange is unreachable | a sentence about that outage |
| **EMPTY** | the source is fine, there is simply nothing yet | "nothing to show" plus a reason |

A bare "—" is acceptable in none of them: it cannot be told apart from a broken panel.

### 2.2 A number during an outage is a bug, not graceful degradation

If the exchange is unreachable and a panel prints a number, that number is either
fabricated or a stale cache presented as current. Both are the same defect class and
both are taken as seriously as a wrong sign.

### 2.3 A stale banner is a bug in *both* directions

If the exchange is back and the panel still claims it is unreachable, that is equally a
bug: it tells the reader the data is missing while it sits in front of them. (This
happened twice — the desk ticket and the equity chart.)

### 2.4 Synthetic data lives only in a test fixture

No synthetic row may ship inside the bundle. To exercise the `DATA` state there is a
contract-shaped upstream (`mock_upstream.py`) whose every payload is marked
`source: "mock"` and which is never packaged into `nobitex-mobile.html`.

## 3. Making the contract executable

A contract without a test is a slogan. Three layers:

1. **`probe_live.py`** judges every panel in both upstream states and exits
   `0` verified, `1` problems, `2` exchange unreachable. Panels declare a kind
   (`market` / `server` / `config` / `optional`) because correctness means different
   things for each.
2. **`--selftest`** feeds the classifier 8 known inputs so the tool cannot rot silently.
3. **`server/tests/test_core.py`** tests the `judge()` decision table for every
   (kind × state × upstream) combination.

## 4. Consequences

- **Cost:** every new panel must be registered in `PANELS` and pass in both states.
  Adding a card without registering it will not turn the test red, but it does shrink
  the coverage.
- **Proof limit:** the contract cannot be run against the real Nobitex from every
  machine (external access is intermittently blocked). The fixture verifies the
  contract, not TLS, rate limits or real outages.
- **Known gap:** the backend exposes no order-book endpoint, so the depth panel stays
  in `EXPLAINED`. That is honest, but it is not a feature.

## Amendment — 2026-10-22 (version 1.0.10): the reference feed, and the fences around it

**The problem.** `apiv2.nobitex.ir` is unreachable from outside Iran (our own measurement:
timeout after 8 s). Two ordinary jobs become impossible: developing against real candles,
and checking whether the number on screen is even plausible.

**The fix.** A **reference** feed (`server/reference_feed.py`) from another public
exchange. KuCoin is the default because it answered from this sandbox; Binance returns
**451** — the same problem under a different name:

| Target | Answer from this network |
|---|---|
| `apiv2.nobitex.ir` | timeout |
| `api.binance.com` | **451** |
| `api.kucoin.com` | 200 |
| `www.okx.com` · `api.exchange.coinbase.com` | 200 |

**The fences this amendment sets**

1. **A reference is not a substitute.** Every report from this path carries
   `source="reference"` and the gate returns `REFERENCE_DATA`, which **can never be
   `PASS`** — even when the arithmetic verdict passes (it is reported under `would_be`).
   A strategy validated on another exchange's prices has not been validated on ours.
2. **A rial market is derived, not observed.** `BTCIRT` has no counterpart on a USD
   exchange. A series exists only with an operator-supplied toman rate
   (`toman_per_usdt` or `NBX_TOMAN_PER_USDT`), is marked `derived: true` and
   `quote_unit: "rial"`, and **assumes the rial premium is zero** — the very thing the
   rial market exists to price. That assumption has to travel with the report.
3. **Units are explicit.** The toman rate is multiplied by `RIAL_PER_TOMAN` so the prices
   match the rest of the repository (rial); a factor-of-ten slip here looks perfectly
   reasonable on screen.
4. **Unavailability is a named error.** `geo-blocked`, `no-fx`, `disabled`,
   `bad-timeframe`, `provider-error` — none of them turn into a generated series.

**The two permitted uses**

- **Development and longer backtests:** the reference venue's multi-year history sits
  beside Nobitex's 2000-candle ceiling rather than replacing it, and the report states
  which data it was built on.
- **Cross-check (`/api/reference/cross-check`):** venue price against reference price. A
  wide gap means one of the two feeds is stale or mis-scaled, not that there is an
  arbitrage — a failure no other panel showed, because each feed looks healthy alone.
