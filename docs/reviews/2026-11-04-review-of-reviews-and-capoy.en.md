# Second-pass review of the two nobi analyses and the public capoy.ir deployment

> **Archive note (2026-11-05):** snapshot review of commit `2b5ea13` (2.2.2); never rewritten. The current status of every finding lives in [`README.en.md`](README.en.md) in this folder.


> **Remediation update — 2026-11-04:** The original sections describe the historical baseline. Section 9 records the remediated working tree, final verification, and remaining limitations. No deployment or real order was performed.

**Date:** 2026-11-04  
**Code baseline:** commit `2b5ea130f53959d357c5c8a664bfb1a52bd781e0` (`2.2.2`)  
**Documents reviewed:** `nobi-audit-report.en.md` (890 lines) and `nobi-analysis-en.md` (488 lines)  
**Version reported by the site:** `2.2.1` in the public health snapshot  
**Nature of work:** read-only source review, independent local probes, official documentation checks, and public HTTP GETs. No exchange credentials, no login credentials, no order, and no settings mutation were used.

> **Important limitation:** The referenced evidence bundles and raw probe outputs were not attached; only the two Markdown documents were available. Their exact numerical results were therefore not accepted as independently established facts. The audited server files and `nbx-core.js` remain content-identical to HEAD. Six mode-only changes in deployment/script files were left untouched.

---

## 1. Executive conclusion

Both documents are useful and broadly consistent with the source, but they are complementary rather than interchangeable:

- **`nobi-audit-report.en.md` is the better primary document for go-live decisions.** Its additional findings about the 500-candle UDF cap, missing pagination in the main client, actual Nobitex fee tiers, Paper-derived live preflight context, and dry-run promotion evidence were independently confirmed.
- **`nobi-analysis-en.md` is a valuable research-parity supplement.** The JS/Python backtest divergence, fixed JS Profit Factor, collapsed daily H1/H2 horizons, and outer-grid OOS selection are useful additions.
- **One report-two finding must be corrected:** ignoring the entry candle's high/low is not a bug when entry occurs at that candle's close. The range preceded the position. The behavior reproduced, but the “entry-candle risk” interpretation is rejected.
- **Neither document nor the public site review proves live profitability or losses.** Health responses, scan counts, and backtests are not independent evidence of real trading performance.
- **Two live-boundary issues should block real-order enablement:** live preflight must use actual exchange account context, and promotion evidence must distinguish real orders from dry-runs. This does not establish that the public deployment is currently live-enabled; that private state remains unknown.

| Question | Answer |
|---|---|
| Are the shared core findings valid? | Yes for the main items: cross-quote liquidity, TA parity, FA fallback/cache behavior, TA-only backtests, evidence limitations, and incomplete config hashing. |
| Which document should drive remediation? | The first, supplemented by the valid additions from the second. |
| Are all severity ratings accepted? | No. Entry-candle risk is a false positive, and several numbers/wordings need qualification. |
| Is the online deployment the audited code? | Not established. Health reports 2.2.1 while the checkout is 2.2.2, and no deployment commit is exposed. |
| Is the private-account review complete? | No. Private endpoints require login, and direct connectivity from this environment timed out. A Viewer GET-only export is required. |

## 2. Independently confirmed additions

### 2.1 The 500-candle cap and missing pagination

Official Nobitex documentation states that each UDF response contains at most 500 candles, `countback` is capped at 500, older batches require `page`, and 1/5/15/30-minute candles are available only for the last seven days. The main client sends no `page`; the resolver asks for 1,200 candles; 15-minute H3 requires 672 future bars. The separate `scripts/fetch_history.py` paginator is not used by that main path.

Consequently, 15-minute H3 cannot resolve from the latest 500 bars alone: once 672 future bars exist, the entry bar is outside that window. Pagination must be paired with timely resolution and explicit expiry for rows that fall outside the seven-day retention window.

Official source: [Nobitex UDF OHLC documentation](https://apidocs.nobitex.ir/market_data/%D8%AF%D8%B1%DB%8C%D8%A7%D9%81%D8%AA-%D8%AF%D8%A7%D8%AF%D9%87-%D9%87%D8%A7%DB%8C-ohlc)

### 2.2 Adaptive recommendation and evidence use different transforms

At factor 1.2, the anchored recommendation formula produces LONG=73.6 and NEUTRAL=58.0, while the evidence path produces 81.6 and 66.0. The evidence therefore does not test the actual recommendation. Both paths are measurement-only (`applied=False`), so this invalidates the comparison rather than proving a live threshold mutation.

### 2.3 Live preflight uses Paper account context

The live place and dry-run routes both call `_checked_intent`, which invokes the injected preflight. `main._live_preflight` directly delegates to `risk_whatif`; that function reads Paper broker equity, cash, positions, and shared risk heat. It does not read actual exchange balances or positions. The authorization policy is not a substitute for account reconciliation.

Existing interlocks—disabled-by-default mode, confirmations, operator role, CSRF, and policy—reduce accidental exposure but do not correct the account-context mismatch before real enablement.

### 2.4 Dry-runs satisfy the current promotion gate

An isolated local ledger containing 30 dry-run records distributed over five synthetic dates returned:

```json
{"ready":true,"samples":30,"days":5,"ambiguous":0,"states":{"dry-run":30},"reasons":[]}
```

No network or real order was involved. The current SQL counts ledger records and days without requiring real accepted/reconciled executions.

### 2.5 Stop gaps receive optimistic fills

For entry=100, ATR=1, stop=98, followed by a candle opening at 80 with high=81 and low=79:

| Engine | Recorded exit | Recorded R |
|---|---:|---:|
| Journal | 98.0 | -1.0 |
| Python backtest | 97.9412 | -1.029 |

The actual fill is not guaranteed to be exactly 80, but a fill at 98 after a gap to 80 is optimistic. A documented gap-aware execution policy is required.

### 2.6 JS and Python backtests are materially different

JS closes the entire position at TP1 and does not use TP2 in its exit loop. Python closes half, moves the stop to break-even, and manages the remainder. With one winning trade and no losses, the independent fixture produced PF=2.4 in JS versus the 99.99 cap in Python. Zero-loss PF needs an explicit reporting convention, but an arbitrary 2.4 and cross-engine disagreement can distort ranking and gates.

### 2.7 Daily H1 and H2 collapse

`max(1, round(seconds / step))` produces H1=H2=1 bar on the daily timeframe. They are not independent horizons there. The full independent table was H1/H2/H3 = 96/288/672 on 15m, 24/72/168 on 1h, 6/18/42 on 4h, and 1/1/7 on D.

### 2.8 Candle discontinuities are not rejected

Timestamp intersection aligns symbols but does not make adjacent closes one-bar observations. Portfolio returns, annualisation, correlations, and journal outcomes can therefore cross unreported gaps. Gaps should be reported and quality-gated rather than filled with invented candles.

### 2.9 The 0.2% fee assumption does not match every base-tier market

At review time, official base-tier pricing showed IRT maker/taker=0.25%/0.25% and USDT maker/taker=0.10%/0.13%. Thus 0.2% understates base-tier IRT costs and overstates base-tier USDT costs. The actual account tier and maker/taker mix matter, and a limit order is not guaranteed to be a maker fill.

Official source: [Nobitex pricing](https://nobitex.ir/pricing/)

## 3. Independent TA parity result

Using a fixed seed, 100 valid random 80-bar OHLCV fixtures, and identical inputs in both engines:

- mean absolute score difference: **2.63**;
- median absolute score difference: **1.25**;
- maximum score difference: **25.8**;
- direction disagreement: **19/100**;
- largest component differences: trend 35, momentum 25, volume 40, structure 20.

This independently establishes decision-relevant divergence. Exact extrema quoted in report two remain unverified without its original fixtures.

## 4. Corrections and qualifications

1. Entry-candle high/low correctly precedes a close-price entry; it is not ignored post-entry risk.
2. Inner walk-forward parameters are train-selected; the outer grid selects on aggregate OOS, which then needs a fresh final holdout.
3. Structure breakout compares the current close with the previous 19 **closes**, not high prices.
4. The volume baseline `iloc[-30:-1]` contains 29 prior bars.
5. `upstream.age_s` is the age of the cached health-probe snapshot, not the age of all market data or proof of a global outage.
6. The public version does not establish the deployment commit or configuration.
7. These software-contract fixtures say nothing about market profitability.

## 5. Public capoy.ir observations

At `2026-11-04T03:56:46.930862Z`, public health reported backend 2.2.1, process `ok=true`, an enabled 300-second scan loop, 249 runs, `last_ok=true`, and 38 symbols within freshness tolerance. It also reported 26,656 upstream calls, 26,644 successes, and 12 failures; the displayed 100.0% is rounded, while the raw success share is about 99.95498%.

The cached upstream health-probe snapshot was 19,857.9 seconds old and still said `ok=true`. In the audited source, an old snapshot triggers an asynchronous replacement while the current response returns the old value. Only the cached response could be observed. This supports a health-semantics/freshness improvement, not a claim that Nobitex was down for 5.5 hours.

Signals, saved backtest reports, and risk state required login. One anonymous universe request returned HTTP 500 without a diagnosable body. No private account state, real execution mode, promotion evidence, or profitability evidence was available.

## 6. Recommended order of work

**Before real orders:** introduce a fresh, fail-closed LiveAccountSnapshot; remove Paper fallback from live preflight; count only real/reconciled orders in promotion evidence; bind evidence to strategy/config/data; and verify deployment execution state read-only.

**Before trusting research:** implement paginated/deduplicated/gap-aware UDF history; share one adaptive threshold transform; model gaps, candle continuity, and same-candle post-TP1 ambiguity; converge on one canonical backtest engine; fix zero-loss PF semantics; handle daily horizon collapse; add a final untouched holdout for grid selection; and model quote/tier/maker-taker fees.

**Observability and API semantics:** separate process liveness, probe age/result, market-data freshness, and scan health; expose a safe build identifier; review authenticated universe data; treat the earlier anonymous 500 as an incident only if reproduced; distinguish pending, expired, and excluded journal outcomes; and make Paper GET endpoints side-effect-free while retaining the background evaluation loop.

## 7. Remaining access requirement

A Viewer account was selected, but direct IPv4 access from the sandbox timed out and the public fetch tool cannot send login cookies or authorization headers. **Do not send the password.**

Use `/home/user/capoy-readonly-export.js` in the browser console of the authenticated capoy.ir origin. It performs only same-origin GET requests and downloads a JSON file. It exports no cookie, password, or localStorage value; contains no POST/order/settings/logout call; and deliberately excludes Paper positions/metrics because those GET handlers can evaluate and update Paper state. Review and redact private information before sharing the JSON. Sanitized text output is sufficient for operator-only live status/evidence if Viewer receives 403; neither Owner access nor Nobitex API keys are needed.

---

## 8. Additional findings from the continued review

### 8.1 Paper GET endpoints can mutate state — confirmed, Paper-only scope

Both `GET /api/paper/positions` and `GET /api/paper/metrics` invoke `broker.evaluate_positions()`. This is not merely mark-to-market: it can book a TP1 partial exit, move a trailing stop, execute a time-stop/SL through `_close()`, append an order event, and persist the result with `save()`.

These GET routes do not have the `require_client` mutation guard, and the general middleware requires only a valid session. A Viewer with `can_mutate=false` can therefore invoke a state-changing path. The background loop already evaluates positions too, but that does not remove the GET side effect or the read-only role inconsistency.

An isolated local probe, with no connection to the deployment, produced:

```json
{
  "before": {"state":"OPEN","orders":0,"state_file_exists":false},
  "after": {"state":"CLOSED","close_reason":"sl-hit","orders":1,"state_file_exists":true}
}
```

**Scope:** this is a Paper issue and does not itself place a real exchange order. It is a P2 API/authorization-semantics issue, not a live-trading P0. Prefer pure GET responses over persisted state, leaving evaluation to the background loop or an explicitly guarded POST. The supplied exporter already excluded both routes.

### 8.2 Post-TP1 intrabar ambiguity is resolved optimistically — confirmed research issue

After TP1, Python and the journal reduce the position and establish a new stop, but do not evaluate that new stop on the same candle. If the candle reaches TP1 and also trades below the new stop, the intrabar ordering is unknown. The current implementation effectively assumes that the low occurred before TP1 and carries the remainder forward.

Independent fixture:

- entry=100 and original stop=98;
- next candle high=104, low=99.5, close=103.5;
- TP1 is reached on that candle;
- the new journal stop is 100.2 and the approximate new Python stop is 100.06, both above the candle low.

| Output | Current result |
|---|---|
| Python backtest | `reason=eod`, `R=1.36`, `ambiguous=false` |
| Journal | `hit_sl=false`, `timeout=true`, `R=1.475` |

These are software-contract fixtures, not market-performance results. They show that a favorable path can inflate the result without recording ambiguity. A candle touching TP1 and the post-TP1 stop should be explicitly classified as ambiguous and resolved by a documented conservative policy or lower-timeframe evidence.

### 8.3 Keep the JS/Python divergence scope precise

The browser `runWalkForward` labels itself `browser-experimental`, reports `source=synthetic` and `params_optimised=false`, and does not perform the Python train-slice optimisation. The divergence is real, but does not by itself prove that canonical server reports are corrupted. The correct conclusion is that browser fallback output must not be treated as equivalent to server evidence.

Two additional differences were observed: JS `runWindow` does not apply Python's 25% single-asset cap, and the no-window fallback can report hard-coded `params_used` TP1=1.5 and TP2=2.5 even when different configuration values were supplied. The latter is a low-severity metadata error in the experimental path.

### 8.4 Second deployment snapshot and universe correction

A fresh health snapshot at `2026-11-04T04:52:36.955469Z` showed:

- scan runs increasing from **249 to 260**: 11 runs in roughly 56 minutes, consistent with the five-minute interval;
- `last_ok=true` and 38/38 symbols still current, with the latest scan completed at `04:49:05Z`;
- totals of 27,845 requests, 27,833 successes, and 12 failures; the failure count did not increase;
- `upstream.age_s=3350`, approximately the elapsed time since the previous snapshot, with `latency_ms=5.0`. This matches the source interpretation: the earlier health request asynchronously refreshed the probe and the later response observed that result.

The second `/api/universe` request returned the standard **401/login** response rather than HTTP 500. The earlier 500 was therefore not reproducible and should not be reported as a persistent deployment defect. Authenticated universe data remains unreviewed.

---

## 9. Remediated working-tree status — 2026-11-04

This section describes `/home/user/nobi` **after remediation**. Earlier probes and tables describe the pre-remediation baseline and must not be read as current behaviour. The changes are not committed or deployed.

### 9.1 Applied corrections

| Area | Current state |
|---|---|
| OHLC and horizons | History above 500 candles is paginated, deduplicated, sorted, and trimmed. Daily H1 no longer collapses into H2. Duplicate same-candle entries and unrealistic gap fills have regressions. |
| Intrabar exits and TP2 | A gap through stop fills at the open; TP1+stop in one candle is conservative and `ambiguous`. Paper (both sides) and Journal close the remainder at TP2, including TP1/TP2 in one tick/candle. Sell-side TP1 PnL was also corrected. |
| Journal | `r_multiple` and `return_pct` include proceeds from both legs. `hit_tp2`, `exit_reason`, `ambiguous`, and dataset hash/range are in the schema and Signal Evidence; migration is idempotent for old databases. |
| TA | Python/JS now share RMA/RSI/TR/Bollinger/DI/ADX and warm-up contracts. Numerical parity covers valid bars, components, direction, regime, risk, and diagnostics; injected fixtures are the only parity signal source. |
| FA | Cache keys include depth/premium. Evidence includes provider, age, staleness, cache, and profile. Missing data is not fabricated neutral evidence; scores reweight available components. TVL requires an explicit suitable asset-class mapping. |
| Liquidity | Universe ranking and scan scoring use USD turnover. IRT without valid FX and unknown quote currencies receive no invented USD value. |
| Risk | Daily/weekly realized-PnL and consecutive-stop triggers are wired into Paper, preflight, `/api/risk/state`, Python backtest, and JS. A halt blocks new entries only; open-position management continues. A regression proves the third stop blocks a fourth entry. |
| Live safety | The real-order route accepts only `_LIVE_PREFLIGHT` backed by a real, timestamped, fresh account snapshot; no provider means fail-closed 503. Dry runs and ambiguous executions no longer complete promotion evidence. |
| Adaptive/health/Paper GET | Recommendation and evidence use the same anchor/factor conversion. Health reports the last probe with age/stale/refresh semantics. Viewer GETs no longer evaluate or mutate Paper state. |
| Fees and grid search | Backtest `fee_pct` is configurable and included in the config hash/report. Zero fee cannot pass the cost gate even with positive slippage. The best grid row is selected from IS evidence, not flattering OOS. Default targets are 1.2R/2R across Live/Paper/Journal/backtest. |
| Portfolio | Asset volatility uses its own series; each pair uses only that pair's shared timestamps and the latest n observations. One gapped asset no longer erases every other pair. Benchmark intentionally retains its global-intersection semantics. |
| Replay metadata | Backtest reports dataset hash/range, engine/source hashes, and execution assumptions. Journal engine/config identity covers decision sources and effective environment, with dataset identity kept separate. |
| Standalone | `nobitex-mobile.html` was regenerated after the final JS changes; its build/byte contract passed in the full Python suite. |

### 9.2 Final verification

- **Python full suite: `1776 passed`** with `PYTHONPATH=/home/user/nobi:/home/user/nobi/server`.
- **mypy: no issues in 165 files**; **Ruff: all checks passed**.
- **ESLint and dashboard lint: clean**; JS broker, intrabar parity, and lockfile checks passed.
- All **20 DOM/Node test targets**, including display/icons/adaptive/escaping/detail/queue and review phases 1–13, passed with **Node `v22.23.4`**.
- `git diff --check` is clean, and executable bits match the Git index.

### 9.3 What remains unproven or unfinished

1. **No profitability or alpha claim is established.** Green tests, trade fixtures, and software gates are not market-return evidence. Frozen holdout and effective-sample/bootstrap evidence remain necessary for promotion.
2. **There is still no full point-in-time composite backtest.** The canonical engine remains TA-only with `matches_live=false`; historical FA/liquidity/order-book inputs must be archived.
3. **Execution remains an OHLC model.** Entry is at signal close, with zero latency, no depth/partial fills, and scenario slippage. Next-open/latency/depth stress remains research work and is now explicitly disclosed.
4. **The account's real fee tier is unknown.** 0.2% is only `default-assumption-unverified-account-tier`; actual maker/taker status and 30-day volume tier must come from the account. A limit order is not necessarily a maker fill.
5. **Pagination cannot recover expired data.** Exchange retention may leave H3 unresolved on short timeframes; the resolver must not fabricate candles or outcomes.
6. **A hash is not complete replay.** Dataset/source identity does not archive the original payload; `raw_payload_archived=false` and `complete_replay=false` are explicit.
7. **Independent-sample statistical inference remains limited.** Overlap, censoring, calibration, and effective-sample confidence intervals require separate research validation.
8. **The public deployment was not changed or retested after these edits.** There was no push, deploy, private login, real order, or write operation on capoy.ir. A future deployment requires a fresh preflight.

---

## Sources and reproducibility

- Audited checkout: `/home/user/nobi`, commit `2b5ea130f53959d357c5c8a664bfb1a52bd781e0`.
- Prior full review: `/home/user/nobi-comprehensive-audit-fa-en.md`.
- Attached documents: `/home/user/uploads/nobi-audit-report.en.md` and `/home/user/uploads/nobi-analysis-en.md`.
- The independent JS probe is retained at `/home/user/review-inputs/independent-probes.js`; the exact independent numerical results are recorded above because the temporary JSON result file did not persist.
- Viewer exporter: `/home/user/capoy-readonly-export.js` (syntax checked with Node).
- Public deployment snapshot: [capoy.ir/api/health](https://capoy.ir/api/health).
- Official market-data contract: [Nobitex UDF OHLC](https://apidocs.nobitex.ir/market_data/%D8%AF%D8%B1%DB%8C%D8%A7%D9%81%D8%AA-%D8%AF%D8%A7%D8%AF%D9%87-%D9%87%D8%A7%DB%8C-ohlc).
- Official fee schedule: [Nobitex pricing](https://nobitex.ir/pricing/).
