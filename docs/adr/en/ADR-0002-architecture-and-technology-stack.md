# ADR-0002: High-Level Architecture and Technology Stack

[فارسی](../fa/ADR-0002-معماری-و-پشته-فناوری.md) · **English**

- **Status:** Accepted (amended)
- **Date:** 2026-02-14
- **Deciders:** Technical Architect
- **Related ADRs:** 0001, 0003, 0004

---

## 1. Context

The system must ingest market data, compute TA and FA signals, fuse them into trade decisions, manage risk, and execute orders — reliably and without supervision. The dominant work is numerical (indicator computation, walk-forward backtests) plus I/O (REST polling). Python has the strongest ecosystem for this class of workload.

## 2. Decision

### 2.1 Language & Runtime
- **Python 3.12+**, fully type-checked (mypy), `uv`/`poetry` for dependency management.

### 2.2 Core Libraries
| Concern | Choice | Rationale |
|---|---|---|
| DataFrames | `pandas`, `numpy` | De-facto standard for indicator pipelines |
| Indicators | `pandas-ta` (primary), `TA-Lib` wrapper (optional) | Coverage of 100+ indicators |
| Backtesting | `vectorbt` (vectorized) + custom event-driven engine | Fast research + realistic fills |
| Async HTTP | `httpx` + `tenacity` | Timeouts, retries, rate budgeting |
| Scheduling | `APScheduler` (embedded) or Celery Beat if scaled to workers | Per-timeframe jobs |
| Cache / queues | `Redis` | Hot state, rate buckets, pub/sub events |
| Storage | **PostgreSQL + TimescaleDB** (hypertables for candles), `parquet` for research datasets | Time-series native + backtest parity |
| Config / validation | `pydantic-settings` | Typed config, env-driven |
| Control API / UI | `FastAPI` + minimal dashboard | Start/stop, mode, position view |
| Notifications | Telegram Bot API | Widely used in Iran for ops alerts |
| Metrics | `prometheus-client` + Grafana | Latency, PnL, errors |
| Packaging | Docker / docker-compose | Single-command deployment |

### 2.3 Module Layout (modular monorepo)
```
src/
  exchange/        # Nobitex adapter (auth, REST, normalization) — ADR-0003
  data/            # ingestion, candle store, quality checks — ADR-0004
  ta/              # indicator registry, TA signal engine — ADR-0005
  fa/              # fundamental data providers, FA scorers — ADR-0006
  decision/        # composite scoring, ranking, selection — ADR-0007
  risk/            # sizing, stops, kill switches — ADR-0008
  execution/       # order manager, paper/live brokers — ADR-0009
  backtest/        # engines, walk-forward, reports
  observability/   # logging, metrics, alerting
  config/          # Settings, feature flags
```

### 2.4 Data Flow (per cycle)
```
Nobitex REST → Ingestion → TimescaleDB → TA signals ─┐
FA providers → FA scores ────────────────────────────┼→ Decision Engine → Risk Engine → Order Manager → Nobitex orders/add
                                    Redis (hot state)┘                     ↘ Kill switch (pre-trade gate)
```

## 3. Alternatives Considered

| Option | Verdict | Reason |
|---|---|---|
| Node.js/TS full-stack | Rejected (primary) | Weaker TA/backtest/ML ecosystem |
| Microservices | Rejected (v1) | Operational burden exceeds benefit for one bot |
| Vendor SDKs (unofficial nobitex libs) | Rejected as sole dependency | Trust/freshness risk; we own the thin adapter (may reference them) |
| `ccxt` as main connector | Not used as sole path | Nobitex support is partial/stale; we implement against official docs directly |

## 4. Consequences

**Positive:** best-in-class analysis stack; train/serve parity; testable modules; local-first, no external SaaS dependencies for secrets.

**Negative:** custom Nobitex client = maintenance when API changes (mitigated by thin adapter + contract tests).

---

## 9. Implementation amendment — 2026-10-20

This ADR defines a target stack; what runs in the repository today is a subset:

| ADR component | In the code today | Note |
|---|---|---|
| `Redis` for cache/queue | **not used** — in-process cache with TTL | `redis` is commented out in `requirements.txt` |
| `PostgreSQL` + `SQLAlchemy` | **not used** — the journal runs on `sqlite3` | deliberate: no database server to install |
| `vectorbt` for backtests | **not used** — `EventBacktester` on pandas | manual grid search in `backtest.py` |

If the symbol count or backtest scope grows, returning to this stack is justified; at
the current scale all three dependencies added install cost and failure surface only.
