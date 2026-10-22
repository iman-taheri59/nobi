# ADR-0009: Execution, Backtesting, Security, and Operations

[فارسی](../fa/ADR-0009-اجرا-بک‌تست-امنیت-و-عملیات.md) · **English**

- **Status:** Accepted — backtest and security implemented; live execution deliberately off
- **Date:** 2026-02-14
- **Deciders:** Technical Architect, Ops Owner
- **Related ADRs:** 0001, 0003, 0008

---

## 1. Context

This ADR makes the path Backtest → Paper → Live auditable and safe, defines how orders execute against Nobitex spot markets, and sets the operational/security posture for unattended 24/7 trading.

## 2. Execution Design
1. **Order Manager state machine:** `PLANNED → RISK_CHECKED → SUBMITTED → OPENED → (PARTIAL) FILLED → TP/SL lifecycle → CLOSED`, persisted in DB; every transition logged.
2. ** Idempotency:** deterministic `clientOrderId` (see ADR-0003). On timeout, query status before any resubmission.
3. **Order type policy:** default **limit** entries near bid/ask with TTL + chase-limit; **market** orders only for stops/exits or explicit fast mode. Partial fills handled via status polling with grace window; unfilled remainder cancels after TTL.
4. **Startup reconciliation:** fetch open orders/positions/balances from exchange; rebuild local state; reconcile mismatches → alert, block new entries until consistent.
5. **Paper trading engine:** consumes the same live orderbook stream; fills simulated from book walk (conservative: slippage + fees applied) — paper results are structurally pessimistic, not flattering.
6. **Promotion gates to live** (all must pass):
   - Backtest: PF > 1.3, MDD < 15%, ≥100 trades, walk-forward consistency across regimes.
   - Paper: ≥30 consecutive days, live-data PF > 1.2, no critical incidents.
   - Manual approval flag + micro-size stage (≤1% capital exposure/day) for ≥2 weeks before ramping within caps (ADR-0008).

## 3. Backtesting & Validation
- Two engines: vectorized research (vectorbt) for speed; event-driven simulator for final evaluation (same code path as live order manager where possible).
- Costs modeled: Nobitex maker/taker fee tiers (configurable), depth-based slippage, funding n/a for spot.
- **Anti-overfit suite:** walk-forward analysis, purged/embargoed CV for ML features, parameter-sensitivity heatmaps, Monte Carlo trade shuffling, out-of-sample holdout (most recent 20% locked).
- Reports: equity, drawdown, PF, expectancy, win rate, exposure, per-symbol/regime breakdowns; stored per run with config hash.

## 4. Security
- **Auth:** Ed25519 API key, **scope: READ + TRADE only, no withdrawal** (ADR-0003). Optional token mode supported but discouraged.
- **Secrets:** env vars / OS keychain / Docker secrets; `.gitignore`d `.env`; never logged; rotation runbook.
- **Host hardening:** run as non-root container, outbound IP allowlist where feasible, 2FA on Nobitex account, device/IP binding of API key via Nobitex panel options.
- **Access to bot controls:** FastAPI behind auth token; Telegram commands authenticated by user ID allowlist.
- **Data:** DB encryption at rest (LUKS/volume), encrypted backups.
- **Supply chain:** pinned deps + hashes, minimal base images, `pip-audit` in CI.

## 5. Observability & Ops
- **Metrics:** cycle latency, endpoint error rates, order lifecycle timings, fills, slippage vs mid, PnL, kill-switch state (Prometheus → Grafana).
- **Logs:** structured JSON, per-module levels, 30–90 day retention; redaction filter for secrets.
- **Alerts (Telegram):** fills, halts, circuit-breaker, daily digest, heartbeat-loss (watchdog pings every 60s).
- **Deployment:** docker-compose — `bot`, `scheduler`, `postgres+timescale`, `redis`, `api`, `prometheus/grafana`. One-command up; `config/` mounted; healthchecks; auto-restart.
- **CI/CD:** lint + mypy + unit (≥85% core coverage) + adapter contract tests + backtest regression suite; tagging drives image builds.
- **Runbooks:** incident playbook, key rotation, exchange maintenance handling, manual override procedures.

## 6. Consequences
**Positive:** no live capital before evidence; honest economics (fees/slippage); safe automation posture; reversible at every step (cancel-all, paper fallback).
**Negative:** operational weight (monitoring, runbooks) is non-trivial — necessary for 24/7 capital at risk.

## Implementation amendment — 2026-10-22 (version 1.0.7)

Section 3 of this ADR says "costs modelled: fees + **slippage**". The fee was
always modelled; the slippage was not. `SLIP_BPS = 6` was defined in
`server/backtest.py` and read by nothing, while `slippage_bps` defaulted to
**zero** in three places: the `EventBacktester` constructor, `run_and_save`, and
the body of `POST /api/backtest/run` (`b.get("slippage_bps", 0.0)`). Every
default backtest — and every gate verdict built on one — therefore described a
market where orders fill at the exact price. That contradicted section 3; it
was not a setting of it.

**What changed**

| Item | Before 1.0.7 | From 1.0.7 |
|---|---|---|
| Simulator default `slippage_bps` | 0 | `SLIP_BPS` = 6 |
| Default in `run_and_save` / `grid_search` | 0 | `SLIP_BPS` |
| HTTP route default | 0 (always) | only when sent; otherwise the engine's default |
| Browser simulator | no slippage | the same 6 bps, and `fee` from `PAPER_FEE_PCT` |
| Gate checks | 7 | 8 — `costs_modeled` |
| Report | numbers only | `costs: {slippage_bps, fee_pct, modeled}` |

**Why `costs_modeled`:** a run without slippage is optimistic, not
conservative. If the gate can certify it, the first real order invalidates the
certification. A `0` run is still allowed (to separate the effect of cost from
the effect of the strategy) but it cannot reach `PASS`; the state becomes
`FAIL` and `costs_modeled` is named in `failed`.

**What this amendment does not say:** it does not claim 6 bps is Nobitex's true
spread. 6 bps is a conservative default, overridable via `slippage_bps` in the
request body or through the dashboard input. The only claim is that **zero**
cannot be the default and that a report must state the cost it was built with.

**Operating it**

```bash
# default (6 bps):
curl -s -X POST http://127.0.0.1:8000/api/backtest/run \
  -H 'Content-Type: application/json' -d '{"symbol":"BTCIRT","tf":"4h","bars":2000}' \
  | python -c "import json,sys; r=json.load(sys.stdin)['report']; print(r['costs'], r['gate']['checks']['costs_modeled'])"
# a different cost:
... -d '{"symbol":"BTCIRT","tf":"4h","bars":2000,"slippage_bps":25}'
# frictionless — for comparison, not for promotion:
... -d '{"symbol":"BTCIRT","tf":"4h","bars":2000,"slippage_bps":0}'   # -> gate.failed = ["costs_modeled"]
```

## Second amendment — 2026-10-22 (version 1.0.8): the backtest must say what it measured

The live engine (ADR-0007) builds its decision from three components — technical
0.55, fundamentals 0.25, liquidity 0.20 — and vetoes can then pull the score
below the promotion threshold (`apply_vetoes`: capped at `VETO_CAP` /
`VETO_CAP_SEVERE`). The backtest simulator (`ta_score_series`) replays the
technical component only, because fundamentals and liquidity have **no history**:
both are fetched point-in-time and their past series is not stored in this
repository.

That limitation is defensible; silence about it is not. Until 1.0.7 a report
said "PF 1.42, gate: PASS" while having measured a quarter of the live rule.

**What changed**

| Item | Up to 1.0.7 | From 1.0.8 |
|---|---|---|
| Report | no `decision_basis` | `components`, `weights`, `vetoes`, `live_weights`, `matches_live` |
| Config hash | indifferent to the rule set | `basis` is part of the hash |
| Gate message | verdict reason only | reason + `BASIS_CAVEAT` sentence |
| Dashboard | OOS chip | amber "TA-only" chip with live weights in its tooltip |
| Browser (offline) | said nothing | the same declaration and the same caveat |

**Why the gate does not refuse to certify:** unlike `costs_modeled` (first
amendment), which was a numerical lie, this is a **scope of validity**, not an
error. A TA-only run produces correct numbers; it just does not speak about the
full rule. So we do not reject the verdict, we make the sentence accurate: a
`PASS` here means the technical rules, not the live composite.

**What closing the gap needs** (future, its own ADR): storing historical
liquidity (24 h turnover / depth) and fundamentals per symbol alongside candles,
so the composite can be replayed. Until then the report must stay honest rather
than look complete.

**Operating it**

```bash
curl -s -X POST http://127.0.0.1:8000/api/backtest/run \
  -H 'Content-Type: application/json' -d '{"symbol":"BTCIRT","tf":"4h","bars":2000}' \
  | python -c "import json,sys; r=json.load(sys.stdin)['report']; \
print(r['decision_basis']); print(r['gate']['message'][-160:])"
```

## Third amendment — 2026-10-22 (version 1.0.9): one candle, two engines, one answer

Section 3 of this ADR says intrabar ambiguity is resolved pessimistically: the path is
unknowable, so assume the stop was hit first. The Python engine does exactly that and marks
the trade `sl-ambiguous`. The JavaScript engine — the one that produces the phone's offline
numbers — did **the opposite**:

```js
if (tr.tp1 && c.h >= tr.tp1)      exit = tp1;   // the target won
else if (c.l <= tr.stop)          exit = stop;
```

So whenever a candle touched both the stop and the target, the browser took the target.
That is the one bias a backtest must never have, and it lived in the engine whose numbers
the operator actually sees.

**What changed**

| Item | Up to 1.0.8 | From 1.0.9 |
|---|---|---|
| Exit rule in the browser | target first | stop first, labelled `sl-ambiguous` |
| Counting | none | `ambiguous_exits` per window and in the aggregate |
| Share | server-side count only | `ambiguous_share` in both engines |
| Gate | 8 checks | **9** — `ambiguous_share_le_10` |
| Dashboard | nothing | "ambiguous 50.9 %" in the gate bar + a Persian label for the check |

**Why it gates:** the pessimistic assumption is the honest choice, but it is still an
*assumption*. If it decides more than 10 % of the exits, the expectancy describes the
assumption as much as the market. On our own fixture (ordinary bars plus wide news candles)
that share is **50.9 %** — half the result. On real 4-hour data it is usually far smaller;
when it is not, the number is not citable.

**Why this check rejects the verdict, unlike `decision_basis`:** there, the problem was the
scope of the claim and a sentence was enough. Here the number itself can be fogged — a PASS
built on 60 % ambiguous exits certifies an assumption, not a strategy.

**The two-engine test:** `scripts/js_engine_parity.js` feeds one candle to both engines and
`server/tests/test_intrabar_parity.py` compares the answers. To make the comparison possible
the browser engine also gained a `_signals` hook — the equivalent of Python's `signals=`.
