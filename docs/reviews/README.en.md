# Reviews — index and finding status

**English** · [فارسی](README.md)

**Last updated:** 2026-11-05 — code: `main` at `bc11fda`; production: `capoy.ir` = 2.5.0 (`dcac952`), in a stability soak.

Rule of this folder: every review is a **snapshot** and is never rewritten.
The only living document is this file: it says where each finding stands today
and which commit/tag closed it. When this file and an old review disagree, this
file is current and the review is dated.

## Chronological index

| Date | File | What it is | Code base |
|---|---|---|---|
| 2026-10-19 | `2026-10-19-project-review-fa.md` | Full phase-1 project review (fa) | 1.x |
| 2026-10-20 | `2026-10-20-*.md` (10 files) + `probe-*.md` + `*.png` | Single-topic reviews: live path, API conformance, JS broker, queue, what-if, WS, exec mode, A3/A6/A7 (fa) | 1.x |
| 2026-10-21 | `2026-10-21-shared-constants-fa.md` | Shared fee/equity constants (fa) | 1.x |
| 2026-11-02 | [`2026-11-02-external-audit-report.en.md`](2026-11-02-external-audit-report.en.md) | **External input 1** — technical/quant audit (890 lines) | 2.2.1/2.2.2 |
| 2026-11-02 | [`2026-11-02-external-analysis.en.md`](2026-11-02-external-analysis.en.md) | **External input 2** — research-parity analysis (488 lines) | 2.2.2 |
| 2026-11-03 | [`2026-11-03-comprehensive-audit.en.md`](2026-11-03-comprehensive-audit.en.md) · [fa](2026-11-03-comprehensive-audit.fa.md) | Independent comprehensive audit: actual TA/FA formulas, risk, IC, priorities + remediation addendum | `2b5ea13` |
| 2026-11-04 | [`2026-11-04-review-of-reviews-and-capoy.en.md`](2026-11-04-review-of-reviews-and-capoy.en.md) · [fa](2026-11-04-review-of-reviews-and-capoy.fa.md) | Second pass over the two external inputs + production observation + remediation status (§9) | `2b5ea13` → `66e7575` |
| 2026-11-04 | [`probes/independent-probes-2026-11-04.js`](probes/independent-probes-2026-11-04.js) | Independent JS probe that produced the numbers above | — |
| 2026-11-05 | [`../ops/en/soak-2026-11.md`](../ops/en/soak-2026-11.md) | Production stability-soak log (preconditions, baseline, daily check) | 2.5.0 |

## Findings → status matrix

Numbers refer to sections of `2026-11-04-review-of-reviews-and-capoy`, which
itself consolidates the two external inputs and the comprehensive audit.

### P0 — before any real order

| Finding | Status | Evidence |
|---|---|---|
| 2.3 live preflight read the Paper account | ✅ closed | separate `live_api._LIVE_PREFLIGHT`; 503 fail-closed without a real snapshot; v2.3.x |
| 2.4 dry-runs could complete promotion evidence | ✅ closed | `live_execution.promotion_evidence` counts only `accepted/reconciled/canceled`; production shows `samples=0` |
| Evidence tied to config/dataset | ✅ closed | `dataset_hash`, engine identity in journal/backtest |
| Read-only deployment check before enabling | ✅ closed | 2026-11-05: `mode=disabled, placement_enabled=false` |

### P1 — before trusting research output

| Finding | Status | Evidence |
|---|---|---|
| 2.1 500-candle cap, no pagination | ✅ closed | `nobitex_client` paginates with `page`; dedup/sort/trim |
| 2.1 7-day retention → endless pending | ✅ closed (explicit) | journal: `outside-retention` / `not-yet-open` / `gap`; H3 on 15m deliberately stays empty |
| 2.2 two adaptive threshold transforms | ✅ closed | shared `adaptive.scale_threshold` |
| 2.5 gap through stop filled at stop | ✅ closed | gap priced at open; same-candle TP1+stop is `ambiguous` |
| 2.6 JS PF=2.4 constant; no TP2 in JS | ✅ closed | `nbx-core.js` cap 99.99; TP1/TP2 share the contract |
| 2.7 H1/H2 collapse on D | ✅ closed | `journal.horizon_supported` — unsupported horizon absent, not duplicated |
| 2.8 candle gaps unchecked | ✅ closed | `portfolio.py` pairwise on shared timestamps; explicit gaps |
| 2.9 0.2% fee ≠ base tariff | ◐ **partial** | `fee_pct` configurable and hashed; `maker_taker_split=False`, `account_tier_verified=False` — needs an authenticated account read |
| 8.1 Paper GETs with side effects | ✅ closed | Viewer GETs are pure |
| 8.2 optimistic post-TP1 ambiguity | ✅ closed | conservative resolution + `ambiguous` flag |
| 4.2 grid picked from OOS (flattering) | ◐ **partial** | selection from IS; a **frozen holdout** does not exist yet |
| 9.4 auto used the public cache / another TF | ✅ closed | `66e7575` |
| 9.4 response-level `status:"ok"` made rejected orders accepted | ✅ closed | `66e7575`; `order-rejected` excluded from evidence |
| 9.4 short cash/equity sign | ✅ closed | `66e7575` |

### P2 — observability and deployment

| Finding | Status | Evidence |
|---|---|---|
| separate process / probe / freshness / scan | ✅ closed | `health.upstream{age_s,stale,refresh_scheduled}` + `scan_loop.last_freshness` |
| commit/build id in health | ✅ closed in code, **awaiting deploy** | `build_info.py`, `bc11fda`; on production after the soak |
| universe after login / transient 500 | ✅ closed | not reproduced; 200 with an Operator session on 2026-11-05 |
| explicit pending/expired in journal | ✅ closed | same as 2.1 |

### §9.3 limitations (unproven — not closable by code)

| # | Limitation | What closes it | Status 2026-11-05 |
|---|---|---|---|
| 1 | profitability / alpha | prospective independent sample; then ≥30 real orders | open; current journal: IC −0.07, exp −0.39R |
| 2 | composite backtest (TA-only) | point-in-time FA/depth archive | **started:** `market_archive` on production since 09:33Z |
| 3 | OHLC execution model | recorded depth + stress tests | depends on 2 |
| 4 | real fee tier | account read by the owner | open (input needed) |
| 5 | pagination ≠ expired data | local archive from today | covered by 2 |
| 6 | hash ≠ full replay | raw payload archive | **started** (2) |
| 7 | statistical inference | ≥30 real samples | open |
| 8 | deployment unverified after changes | on-host preflight | ✅ closed 2026-11-05 (8 preconditions green) |

### Rejected (false positives)

| Claim | Why rejected |
|---|---|
| "entry-candle risk" (external input 2) | entry is at close; that candle's high/low happened before the position existed |

## Events after remediation (traceability)

| Date | Event |
|---|---|
| 2026-11-04 | releases 2.3.0–2.3.4, 2.4.0 (ADR-0049 price alerts), CI green |
| 2026-11-05 | release 2.5.0 (`dcac952`); production deploy 07:19Z; soak start |
| 2026-11-05 | ops tooling: `update_arvan --backup` via `state_backup` (`45c4f32`), exchange probe aligned with the service (`904d70a`), snapshot + Secure cookie (`255ddc7`), `market_archive` (`c6dc001`), `build` in health (`bc11fda`) |
