# ADR-0009: Execution, Backtesting, Security, and Operations

- **Status:** Proposed
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
