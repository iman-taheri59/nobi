# nobitrader — Backend (Phase 0 → Phase 1)

Python 3.12+ implementation of the trading system design (ADR-0001..0013).
This directory hosts the production-grade components the browser dashboard
talks to via a small FastAPI proxy, and that will later execute live trading.

## What is implemented (Phase 0 → 1)

- `nobitex_client.py` — Async, token-bucketed, retry-aware Nobitex REST client
  (public endpoints first; TRADE layer wired behind env-scoped secrets).
  Matches ADR-0003.
- `data_store.py` — Universe, candles (Timescale-compatible), premium series.
  Matches ADR-0004.
- `ta_engine.py` — vectorized TA signal engine (EMA/RSI/MACD/ATR/Bollinger/ADX)
  + composite scoring and regime detection. Matches ADR-0005.
- `decision.py` — composite score, universe ranking, selection rules base.
  Matches ADR-0007.
- `main.py` — FastAPI app: health, universe, signals, scan jobs, and a
  CORS-safe `/proxy/market` pass-through used from the dashboard demo.

## Quick start (Phase 0 prerequisites)

```bash
cd server
python -m venv .venv && . .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -r requirements.txt

# CRITICAL: follow ADR-0003/0009 — no real TRADE keys here.
# For Phase 1 we only need READ access considerations:
export NBX_BASE_URL=https://apiv2.nobitex.ir      # optional override
export NBX_UA=TraderBot/               # optional: name this process in the exchange's logs
uvicorn main:app --reload --port 8000
```

## How the dashboard uses it

1. The browser no longer talks directly to `apiv2.nobitex.ir` (CORS-limited
   REST) for the public market data hub.
2. The dashboard fetches `http://localhost:8000/api/universe` and
   `http://localhost:8000/api/signals?tf=15h` which are FastAPI proxies that
   consolidate Nobitex public endpoints, normalize output, and compute the
   TA scores in Python (parity with ADR-0005).
3. Real trading endpoints (`/api/orders/*`) are intentionally stubbed and
   will be enabled only after:
   - Ed25519 key is provisioned in a safe way (never in browser),
   - Promotion gates in ADR-0009 are met,
   - Paper phase has ≥30 days of data.

## Repository layout

- `src/exchange/` — Nobitex adapter + auth (Ed25519 signing happens only server-side)
- `src/data/` — ingestion, TimescaleDB/Parquet readers
- `src/ta/` — indicator registry + signal engine
- `src/fa/` — fundamental scorers (CoinGecko, DefiLlama, news, premium)
- `src/decision/` — composite score, candidate pipeline, selection rules
- `src/risk/` — sizing, stops, kill switches
- `src/execution/` — order manager, paper/live brokers
- `src/backtest/` — vectorized + event-driven engines
- `src/observability/` — logging, Prometheus metrics, Telegram alerts
- `src/config/` — pydantic-settings, feature flags

Configuration is 100% environment-driven via `.env` (not committed).
