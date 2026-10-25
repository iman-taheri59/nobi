# nobitrader documentation

[فارسی](README.md) · **English**

Current version: **1.6.5**. See [versioning and GitHub releases](releases.en.md) and the
[changelog](../CHANGELOG.en.md).

## Documentation map

This file is only a way to find things; the content lives in the folders beside it.

## If you want to…

| You want to… | Go to |
|---|---|
| Get the project running | [`../README.en.md`](../README.en.md) |
| Understand why the architecture is this way | [`adr/`](adr) — 18 ADRs, each in Persian and English |
| See which decision is actually implemented | [`adr/README.md`](adr/README.md) — status table with code evidence (Persian) |
| Know what was last verified, and what was not | [`reviews/`](reviews) (Persian) |
| Know what the analysis engines actually compute today | [`analysis/`](analysis) — catalogue plus an "implemented / catalogue only" table (Persian) |
| Run the tests against a live exchange | [`testing.en.md`](testing.en.md) |
| Pick a cheap server for testing | [`ops/`](ops) — [English](ops/en/free-server-options-for-testing-and-development.md) · [Persian](ops/fa/free-server-options-for-testing-and-development.md) |
| Install the project on ArvanCloud (Ubuntu cloud server) | [`deploy-arvan.md`](deploy-arvan.md) — step by step + `scripts/deploy_arvan.sh` + `scripts/check_deploy.py` (Persian) |
| Decide whether a VPC is needed | [`deploy-arvan-vpc.md`](deploy-arvan-vpc.md) — isolation, cost, two-server topology (Persian) |
| Decide the live take-profit ladder | [`rr-ladder-tuning.en.md`](rr-ladder-tuning.en.md) |
| Understand coin metadata and logos | [`coin-metadata.en.md`](coin-metadata.en.md) |
| See where the capabilities are heading | [`roadmap/fa/capability-roadmap.md`](roadmap/fa/capability-roadmap.md) (Persian) |

## ADRs (architecture decisions)

Every ADR records one decision at the time it was made. The decision text is **never
rewritten**; if reality changes, an "implementation amendment" section with a date is
appended to the same file. That way "when was this decided" and "where is the code
today" can both be asked without one forging the other's date.

The Persian and English text of each ADR are kept side by side: if you change one,
change the other. `server/tests/test_docs_bilingual.py` fails if a pair is missing or
if an ADR number has two files in one language.

| # | Topic |
|---|---|
| 0001 | Vision, goals and scope |
| 0002 | Architecture and technology stack |
| 0003 | Nobitex API integration |
| 0004 | Market data pipeline and storage |
| 0005 | Technical analysis engine |
| 0006 | Fundamental analysis engine |
| 0007 | Decision and signal-fusion engine |
| 0008 | Risk management and position sizing |
| 0009 | Execution, backtesting, security and operations |
| 0010 | Hardware and server infrastructure |
| 0011 | User interface and dashboard strategy |
| 0012 | Signal performance monitoring and attribution |
| 0013 | Hybrid (auto and manual) execution |
| 0014 | Data honesty contract — the three states of every panel |
| 0015 | Currency units, market symbol and exchange gates at the API boundary |
| 0016 | Static gates and CI |
| 0017 | Testing and review from inside the dashboard |
| 0018 | Accounts, sign-in and invite-only membership |

## Reviews — which one to read when

Reviews are snapshots and are not rewritten. If something is fixed later, a new review
is written that refers back to the old one, so the date of every claim stays traceable.
All of them are in Persian:

| File | What it is |
|---|---|
| [`reviews/2026-10-19-project-review-fa.md`](reviews/2026-10-19-project-review-fa.md) | Full project review: state, bugs, path forward |
| [`reviews/2026-10-20-live-path-verification-fa.md`](reviews/2026-10-20-live-path-verification-fa.md) | Verifying the "live data" half: what was measured, 20 bugs, what is still unproven |
| [`reviews/2026-10-20-nobitex-api-conformance-fa.md`](reviews/2026-10-20-nobitex-api-conformance-fa.md) | Conformance with the official Nobitex docs: 16 discrepancies, fixes, and what remains |
| [`reviews/2026-10-20-js-broker-parity-fa.md`](reviews/2026-10-20-js-broker-parity-fa.md) | The browser broker: 3 gates → 10 gates, with a Python/JS parity test |
| [`reviews/probe-live-2026-10-20.md`](reviews/probe-live-2026-10-20.md) | Raw probe output against a live feed (44 panels) |
| [`reviews/probe-offline-2026-10-20.md`](reviews/probe-offline-2026-10-20.md) | Raw probe output with the exchange down (44 panels) |
| [`reviews/real-ic-2026-10-19.json`](reviews/real-ic-2026-10-19.json) | IC measurement on real data |

## Bilingual coverage

What is written twice, what is written once, and what is pending. The gate in
`server/tests/test_docs_bilingual.py` keeps the first group honest.

| Document | Persian | English |
|---|---|---|
| Root README | [`README.md`](../README.md) | [`README.en.md`](../README.en.md) |
| Documentation map | [`README.md`](README.md) | [`README.en.md`](README.en.md) |
| Versioning and releases | [`releases.md`](releases.md) | [`releases.en.md`](releases.en.md) |
| Changelog | [`CHANGELOG.md`](../CHANGELOG.md) | [`CHANGELOG.en.md`](../CHANGELOG.en.md) |
| Live handoff | [`HANDOFF.md`](HANDOFF.md) | [`HANDOFF.en.md`](HANDOFF.en.md) |
| ADR-0001 … ADR-0027 | [`adr/fa/`](adr/fa) | [`adr/en/`](adr/en) |
| Free testing servers | [`ops/fa/`](ops/fa) | [`ops/en/`](ops/en) |
| ArvanCloud deployment | [`deploy-arvan.md`](deploy-arvan.md) | **pending** |
| ArvanCloud VPC | [`deploy-arvan-vpc.md`](deploy-arvan-vpc.md) | **pending** |
| Running on Windows | [`run-windows.md`](run-windows.md) | **pending** |
| Testing guide | [`testing.md`](testing.md) | [`testing.en.md`](testing.en.md) |
| Coin metadata and logos | [`coin-metadata.md`](coin-metadata.md) | [`coin-metadata.en.md`](coin-metadata.en.md) |
| Financial display | [`financial-display.md`](financial-display.md) | [`financial-display.en.md`](financial-display.en.md) |
| Out-of-sample evidence | [`evidence.md`](evidence.md) | [`evidence.en.md`](evidence.en.md) |
| Tuning the take-profit ladder (`NBX_RR_LEVELS`) | [`rr-ladder-tuning.md`](rr-ladder-tuning.md) | [`rr-ladder-tuning.en.md`](rr-ladder-tuning.en.md) |
| Roadmap | [`roadmap/fa/`](roadmap/fa) | **pending** |
| Analysis catalogues | [`analysis/`](analysis) | **pending** |
| Reviews (date-stamped) | [`reviews/`](reviews) | snapshot only — not translated by design |

"Pending" is an honest state, not a promise: those documents are Persian-only today,
and a translation that drifts from its source would be worse than none.

## Symbol specifications and logos

[`coin-metadata.md`](coin-metadata.md) (Persian) — Nobitex specifications take priority,
CoinGecko logos and alternative names, the persistent cache and the explicit coin
identity mapping, Demo key configuration and data path, coverage status and the offline
fallback.
