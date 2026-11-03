# nobiTrader / nobi project review

[فارسی](nobi-review.md)

- Review date: 2026-11-02
- Project version: `2.1.8`
- Commit: `21eee89`
- Scope: code review, existing tests, offline test execution, and reproduction of several defects without connecting to the exchange and without placing real orders.
- Code-change status at the time of this review: no tracked repository file was modified; `git status --short` was empty.

> English version of the Persian report `nobi-review.md`. This is a historical review of version `2.1.8`; later remediation is tracked in the changelog.

## Executive summary

The project is notable for its test structure, ADR documentation, unit separation, CSRF/auth handling, and three-state data model. However, several important defects exist at the trade-execution boundary. If the live path or automatic execution is enabled, they can lead to incorrect financial decisions. The most important are:

1. Live order preflight uses equity/cash/positions from the paper account, not real exchange balances.
2. The daily loss halt is not actually enforced; even after a 5% loss, the risk engine still opens a new order.
3. Automatic execution can trade a signal from another timeframe or a very stale signal.
4. `sell`/short accounting is inconsistent in both paper engines; in JS, the sign of closing PnL is even reversed.
5. The live-promotion gate counts rejected attempts as “samples” and confuses `accepted` status with an actual fill.

These are detailed below with priority, code evidence, and reproductions.

---

## Findings

### 1) Critical, conditional on enabling live: live preflight depends on the paper account

**Locations:**

- `server/main.py:3290-3292` — `_live_preflight` simply calls `risk_whatif`.
- `server/main.py:3183` and `server/main.py:3244-3255` — `risk_whatif` uses `br.equity`, `br.cash`, and `br.positions`; `br` is the paper broker.
- `server/live_api.py:160-196` — `/api/live/place` sends the real order after preflight and policy.

**Impact:** If `NBX_LIVE_MODE=live`, order size and permission are evaluated against the simulated account, not the real Nobitex wallet. An order may be “allowed” on paper but rejected by the exchange for insufficient balance, or worse, the real account risk cap may not be respected. For `sell`, this path also does not appear to check base-asset balance.

**Recommendation:** Before every live order, build an independent balance/risk snapshot from `/v2/wallets` and exchange open orders, and feed live preflight only from that snapshot. The paper account must not be the source of truth for the live path.

---

### 2) High: daily loss halt is only a warning, not a gate

**Locations:**

- `server/risk_engine.py:291-293` — if `daily_pnl <= -3`, only `warnings.append("daily-loss-halt")` runs.
- `server/risk_engine.py:347-357` — `close_trade` and `triggers` exist, but are not called from paper/live execution paths.
- `server/paper_trading.py:597-627` and `server/paper_trading.py:629-687` — closing a position or TP1 does not call `risk.close_trade`.
- `nbx-core.js:715` and search for `dailyPnl` — the browser engine also does not update the daily value.

**Reproduction:**

```text
daily_pnl = -5.0
halted = false
plan.action = "open"
plan.blocked = false
plan.size_quote = 25000.0
warnings = ["daily-loss-halt", "single-asset-cap-resized"]
```

**Impact:** The “halt after daily loss” policy from ADR-0008 is not enforced in practice. Even if the value is manually populated, the engine only warns and does not block the order.

**Recommendation:** Turn `daily-loss-halt` into a real gate in `preflight`; feed daily realized/unrealized PnL from close events and mark-to-market; add a regression test for “3% loss ⇒ no new order”.

---

### 3) High: automatic execution can trade a stale or wrong-timeframe signal

**Locations:**

- `server/main.py:1812-1823` — `_signal_lookup` falls back to `_cached["signals"]`, the last scanned timeframe, if the symbol is not present in the requested timeframe.
- `server/main.py:4264-4272` — `auto_tick` builds both pool and lookup with the same fallback.
- `server/main.py:4359-4382` — the auto loop always calls `auto_tick()` with default timeframe `15m`.
- In contrast, the queue path uses `server/queue_policy.py` and closed-candle/expiry metadata; auto does not pass through that protection.

**Reproduction:** With a `D` snapshot that is 50 days old, `auto_tick("15m")` produced:

```text
considered=1, placed=1
submitted=("TESTUSDT", "buy", 250.0, 100.0, 98.0, 102.4, 104.0), source="AUTO"
```

**Impact:** Automatic execution can place a paper order based on ATR/direction/price from another timeframe or very old data. This corrupts backtest/evidence validity and is dangerous if later connected to live.

**Recommendation:** Auto should, like the queue, use only the exact timeframe cache, closed candles, `signal_key`, and server-time-based expiry. Remove the fallback to the last timeframe for order execution.

---

### 4) High: sell/short accounting is inconsistent in the paper engines

**Locations:**

- `server/paper_trading.py:494` — for sell, `total_cost = notional_usd - fee_usd`, then that amount is subtracted from cash.
- `server/paper_trading.py:586` — `self.cash -= total_cost` for both sides.
- `server/paper_trading.py:611-616` — closing a short has the correct PnL direction formula, but principal/cash return is inconsistent with an asset/liability model.
- `server/paper_trading.py:657-660` — TP1 for short still uses the long formula `(notional_tp1 - cost_basis)`.
- `server/paper_trading.py:689-698` — mark-to-market adds all open positions as positive assets.
- `nbx-core.js:886` and `nbx-core.js:898-902` — in the browser, sell subtracts from cash like buy, and `closePosition` computes `(outQuote - size_quote)` for both sides.

**JS reproduction:** Sell 1 unit at 100, close at 90, fee 0.2% per leg:

```text
accepted = true
realized_pnl = -10.18
expected_net_pnl ≈ +9.62
```

**Python reproduction:** In the same scenario, equity when marked at 90 becomes `99990.2` instead of about `100009.8`; the short direction is reversed in open-position valuation.

**Impact:** Performance statistics, equity curve, drawdown, profit factor, and subsequent decisions for the sell side are unreliable. If the project is long/spot only, sells without base inventory should be explicitly rejected; if shorting is intended, it needs a separate liability/collateral/fee model.

**Recommendation:** Either reject `sell` without base asset, or add an explicit short/spot accounting model. Current tests mostly check stop/TP geometry, not the full cash/equity/PnL cycle.

---

### 5) High, conditional on live: promotion evidence and accepted status are unreliable

**Locations:**

- `server/live_execution.py:160-186` — `promotion_evidence` counts all `live_execution` rows; only `unknown` and `cancel-unknown` are treated as ambiguous.
- `server/live_execution.py:229-235` — if the exchange response is `status:"ok"`, local state becomes `accepted`, even if the order itself has status `Rejected` or `matchedAmount:0`.
- `server/live_api.py:172-177` — `/live/place` depends on this same `ready`.

**Reproduction:** 30 `rejected` records over 5 days:

```text
ready = true
samples = 30
days = 5
states = {"rejected": 30}
```

And for an exchange response with `order.status="Rejected"` and `matchedAmount="0"`:

```text
local_state = "accepted"
```

**Impact:** The promotion gate can turn green with failed attempts. Also, `accepted` should not be treated as a fill or even final order acceptance.

**Recommendation:** Promotion evidence should count only valid dry-runs, preferably across diverse market scenarios, not rejections. Order state should be derived from the order fields and reconciliation with the exchange: `accepted`, `rejected`, `partial`, `filled`, `canceled`, `unknown`.

---

### 6) Medium: liquidity ranking mixes toman and dollar volumes

**Locations:**

- `server/main.py:1081-1082` — universe is sorted by raw `vol24`.
- `server/main.py:1194-1197` — liquidity score is built from `log10(vol24 + 1)`.
- In contrast, `server/market_overview.py:273-280` explicitly normalizes volume to USD.

**Reproduction:** Two markets with the same dollar turnover `1,000,000 USD` and rate `100,000 toman/USD`:

```text
BTCUSDT: raw_vol24=1,000,000       liquidity_score=74
BTCIRT:  raw_vol24=100,000,000,000 liquidity_score=94
```

**Impact:** IRT markets are artificially ranked as more liquid/higher quality, and the top-60 universe can become biased.

**Recommendation:** Convert `vol24` to USD using a valid USDTIRT rate before sorting and liquidity scoring; if the rate is unavailable, do not fabricate a score/rank and report unavailable status.

---

### 7) Medium: FA fallback still receives a score and cache ignores the depth input

**Locations:**

- `server/fa_engine.py:189-209` — cache key is only `symbol` and premium bucket; `nobil_depth_score` is not in the key.
- `server/fa_engine.py:351-353` — depth affects the final score.
- `server/fa_engine.py:363-370` — fallback/none is labeled, but still returns `FAResult.score`.
- `server/main.py:1235-1250` — rescan puts the same score into composite even if `fresh=false` or profile is fallback/none.

**Reproduction:**

```text
depth=0   -> score=65.0
depth=100 -> score=65.0, cached=true
profile=fallback, fresh=false
```

In circuit-open mode, BTC also receives a fallback score around `75.0`.

**Impact:** The fundamental score can be built from unmeasured data or a cache inconsistent with current depth, and still carry weight in the composite decision.

**Recommendation:** If FA is not truly fresh/measured, composite should renormalize to `ta+liq` according to the same contract, not consume fallback score as valid data. The cache key should cover all inputs affecting score, or score should be separated from cache and the depth-dependent part recomputed.

---

### 8) Medium: journal metrics are inconsistent at higher timeframes and partial exits

**Locations:**

- `server/journal.py:64-68` and `server/journal.py:97-99` — H1 is intended to be one hour, but `bars_for` has a floor of one candle.
- `server/journal.py:490-503` — `r_multiple` combines partial exits, but `return_pct` is only `(exit_price / entry - 1)`.

**Horizon reproduction:**

```text
15m: H1 = 3600s
1h:  H1 = 3600s
4h:  H1 = 14400s
D:   H1 = 86400s
```

**Partial-exit reproduction:**

```text
r_multiple = 0.65
stored_return_pct = 0.2
leg_weighted_gross_return_pct ≈ 1.3
```

**Impact:** H1 at 4h/D is no longer “one hour” and overlaps H2/Daily. Also, `return_pct` after TP1 does not match the actual portfolio return of a partial exit and can mislead calibration/IC.

**Recommendation:** Either compute horizons using real time and lower-timeframe data, or explicitly label/document that H1 at higher timeframes means “nearest candle”. For returns, either store weighted exit return or rename the field to `final_leg_return_pct`.

---

### 9) Low: moderate vulnerabilities in the npm development toolchain

**Observation:** `npm audit --audit-level=high` passed with exit 0, but reported 5 moderate findings:

```text
minimatch < 11.0.1
GHSA-g3hv-75wg-m249
via eslint / @eslint/config-array / @eslint-community/eslint-utils / @eslint/js
```

**Impact:** These are devDependencies and do not necessarily reach the dashboard runtime, but they can affect CI/glob-processing tooling.

**Recommendation:** Instead of `npm audit fix --force`, which may downgrade ESLint to an incompatible version, upgrade the ESLint/minimatch chain to a compatible fixed version and rerun lint/CI tests.

---

## Positive points reviewed and not treated as defects

- Unit boundaries in `server/units.py` and tick/parity tests are taken seriously.
- The queue path uses closed candles, server time, expiry, and idempotency.
- Auth/CSRF and owner/operator paths have good test coverage.
- Backtest models fees/slippage and rejects certification when costs are missing.
- Many UI/API states distinguish live/cache/synthetic/unavailable.
- Nobitex order `srcCurrency/dstCurrency` orientation was checked; according to official docs, src is the base asset and dst is the quote currency, and should not be swapped for buy/sell. Therefore this was not reported as a bug. Reference: [nobitex/docs-api](https://github.com/nobitex/docs-api/blob/master/source/includes/_market_trade.md).

---

## Execution results

| Check | Result |
|---|---|
| `pytest server/tests` | `1663 passed, 1 warning` |
| `ruff check server scripts` | pass |
| `mypy` | pass, 155 files |
| `npm run lint` and `lint:dashboard` | pass |
| 23 `npm run test:*` scripts | all pass |
| `check_preflight_parity.py` | pass, 28 cases × 10 gates |
| `npm audit --audit-level=high` | exit 0, but 5 moderate dev findings |
| CSS and standalone mobile build | pass; `nobitex-mobile.html` had no tracked diff |
| `probe_live.py --selftest` | not run because Chromium/Playwright browser was unavailable |
| Full `pip-audit` on main requirements | not completed on target interpreter; see environment limitation below |

### Test environment limitation

The project target interpreter is Python 3.12, but the available environment was Python 3.11.2. Attempts to install 3.12 through allowed network paths failed. To run backend tests, only `numpy==2.5.4` was changed to `numpy==2.4.6`; the rest of the main pins were installed. Therefore pytest/ruff/mypy results are valid, but were not run on the exact Python 3.12 + numpy 2.5.4 matrix.

Full `pip-audit` also could not complete in this environment because the main requirements need Python 3.12. Direct PyPI metadata review of the pinned versions for 13 main packages showed no known advisories; this is not a substitute for a full transitive dependency audit.

---

## Suggested remediation order

1. **Before any live enablement:** separate live preflight from the paper account, fix promotion evidence, and distinguish accepted/filled/rejected.
2. **Before relying on auto:** remove timeframe fallback and apply the same queue freshness/expiry rules to auto.
3. **Before relying on performance statistics:** fix sell/short accounting and daily PnL/halt.
4. **For signal quality:** normalize liquidity to USD and fix FA fallback/cache behavior.
5. **For reporting:** fix H1 and `return_pct` in the journal.
6. **Maintenance:** controlled upgrade of the ESLint/minimatch chain and complete pip-audit on Python 3.12.

---

## Reproduction appendix

The reproduction script is kept outside the repository:

```text
/home/user/nobi-review/reproduce_findings.py
/home/user/nobi-review/reproductions.log
```

Run from the repository root:

```bash
PYTHONPATH=server .venv/bin/python /home/user/nobi-review/reproduce_findings.py
```

This script sends no real orders and writes state only under `/home/user/nobi-review/repro-state`.
