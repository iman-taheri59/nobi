# nobi — Backend (`server/`)

Python 3.12 · FastAPI · one process (`uvicorn --workers 1`, a deliberate choice: the
scan cache, the risk-engine latch and the SQLite journal are per-process state).
The layout is **flat**: every module sits directly in this directory; there is no
`src/` package. Design decisions live in `docs/adr/` (52 bilingual ADRs); this file is
only a map.

## Map of the modules (by pipeline stage)

| Stage | Modules |
|---|---|
| Exchange I/O | `nobitex_client.py` (token-bucket REST client), `nobitex_signing.py` (Ed25519, server-side only), `nobitex_metadata.py`, `candle_cache.py`, `reference_feed.py`, `market_overview.py`, `upstream_logging.py` |
| Data validity | `freshness.py` (age in bars, `stale`/`behind`/`ahead-of-clock`), `queue_policy.py` (closed candles only, bounded approval window) |
| Scoring | `ta_engine.py` (EMA/ADX/RSI/MACD/Bollinger/ATR → 4 components → score), `fa_engine.py` (CoinGecko/DefiLlama/F&G + vetoes), `regime.py` (ADR-0050, shadow), `adaptive.py` (per-timeframe thresholds, reported not applied), `shadow_arms.py` (ADR-0051 A/B arms) |
| Fusion & gates | `main.py` (`_composite`, `apply_vetoes`, scan loop, 95 routes), `gates.py` + `gate_ledger.py` (ADR-0022 shadow gates), `signal_intelligence.py`, `signal_evidence_view.py`, `signal_drift.py`, `signal_cycles.py` |
| Risk & execution | `risk_engine.py` (k·ATR stops, caps, kill switch), `fees.py`, `exec_mode.py`, `paper_trading.py`, `live_execution.py`, `live_api.py`, `ws_execution.py`, `portfolio.py`, `money.py`, `units.py` |
| Measurement | `journal.py` (signal journal, H1/H2/H3 outcomes, holdout — ADR-0052), `backtest.py` (walk-forward, TA only: `matches_live: false`), `benchmark.py`, `diagnostics.py`, `diag_backtest.py` |
| Accounts & ops | `auth.py`, `auth_api.py`, `identity.py`, `security.py`, `setup_api.py`, `install_api.py`, `install_wizard.py`, `operations_api.py`, `settings_view.py`, `runtime_settings.py`, `price_alerts.py`, `notify.py`, `errorlog.py`, `logsafe.py`, `build_info.py`, `devsuite.py` |

## Running

```bash
python3 -m venv .venv && .venv/bin/pip install -r server/requirements-dev.txt
.venv/bin/uvicorn main:app --app-dir server --host 0.0.0.0 --port 8000
```

Secrets come from the environment / `runtime_settings.py`; nothing is read from the
browser. Production is installed by `scripts/deploy_arvan.sh` and updated by
`scripts/update_arvan.sh --yes --backup --verify` (`deploy/nobi.service`).

## Gates (run from the repository root)

```bash
ruff check server scripts
mypy
python -m pytest -q server/tests
```

What is *not* here: no ML, no short side, no TimescaleDB — SQLite files next to the
process. Older drafts of this README described a planned `src/…` package; that plan
was dropped in favour of the flat layout you see.
