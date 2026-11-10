# ADR-0056 — Two-tier universe: a traded core plus a journal-only watch tier

[فارسی](../fa/ADR-0056-کائنات-دولایه-لایهٔ-دیده‌بان-فقط-ژورنال.md) · **English**

**Status:** proposed — no code changed; decision no earlier than 24 Nov 2026, on holdout data (ADR-0052)
**Date:** 2026-11-10
**Related:** ADR-0003 (rate budget), ADR-0007 (`SCAN_TOP = 40`), ADR-0022 (shadow gate), ADR-0052 (frozen holdout), ADR-0054 (`E_nochase` arm), `docs/analysis/universe-churn-2026-11-07.md`

## Context

Nobitex lists about 240 markets (every asset twice: `-IRT` and `-USDT`, so ~120 assets). nobi today:

1. reads all of them from `/market/stats`, ranks by **24h USD turnover** and caches the top 60 as the "universe" (`_load_universe_locked`);
2. scores only the **top 40** every scan cycle (`SCAN_TOP`); `USDTIRT` is the FX rate, not a candidate → 38–39 scored symbols.

The 40 rests on three reasons that still hold: request budget (one UDF call per symbol per cycle plus an order book for the gates; ADR-0003), liquidity (outside the top ~60 many markets turn over a few thousand dollars a day, and the depth gate already fires inside the top 40), and data quality (thin markets have gappy candles).

The real question is not *how many* but *which*. `universe-churn-2026-11-07.md` (12 days, 3,847 cycles) found:

| group | present in cycles | symbols | share of rows | H1 R̄ (LONG) |
|---|---|---|---|---|
| core | ≥ 90 % | **17** | 41 % | −0.089 |
| middle | 50–90 % | 11 | 20 % | −0.070 |
| transient | < 50 % | **79** | 39 % | **−0.148** |

107 distinct symbols passed through a 40-slot window; nearly half of every cycle's slots are transient, and their LONGs are 60 % worse at the 1-hour horizon. The mechanism is plain: a symbol that enters the top 40 on a volume spike enters precisely when most of its move is done — the pattern ADR-0054 tracks with `E_nochase`, one level up: in **symbol selection** rather than entry timing.

Two opposing pressures: the user wants more coverage (39 of 240 looks thin); the data says extra slots have so far lowered quality. Raising `SCAN_TOP` worsens both: symbols 41–60 are transient by nature and the request load rises 50 %.

## Decision (proposed)

Split the universe into two tiers with two different contracts.

### Tier 1 — traded core (`core`)

- **Membership by hysteresis, not instantaneous rank.** Entry: present in the top 40 in ≥ 80 % of cycles over the **last 7 days**. Exit: < 50 % over the last 7 days. Expected size 25–30 (today's 17 core + 11 middle); no hard cap, because the criterion is behaviour, not a number.
- Only this tier reaches the **approval queue** and paper/live. Gates, R:R, journal — unchanged.
- Membership is recomputed once a day at `07:20Z` (the holdout cutoff hour) and recorded in `scan_cycles.universe_tier`; not every two minutes.

### Tier 2 — journal-only watch (`watch`)

- The **top 100** by USD turnover not in `core`, scored with the same TA + liquidity, but:
  - scan timeframe **1h** instead of 15m → ¼ the requests per symbol; 100 × ¼ ≈ 25 symbol-equivalents, total ≈ 40 + 25 = 65 per cycle (+60 % over today, under the ADR-0003 ceiling with `sem=6`). No order book is read for this tier (gates answer `mode:"unavailable"`).
  - signals are journaled with `origin = "watch"` and **never** enter the approval queue, the default explorer card, or paper. The Performance page's existing origin filter (2.5.4) shows them separately.
  - horizons (H1/H2/H3) resolve for them as usual; the point is to hold ≥ 30 days of journaled evidence **before** any symbol joins `core`.

### Promotion from `watch` to `core`

Not automatic. A weekly report (appended to the existing `nobi-journal-diagnostic`) lists `watch` symbols that (a) meet the 80 % presence rule, (b) have spent ≥ 30 days in `watch`, (c) have an H2 R̄ in `watch` no worse than the `core` median. Promotion is a manual decision recorded in its own ADR — the same contract ADR-0054 set for shadow-arm promotion.

### What does not change

- `SCAN_TOP`, the 15m timeframe, scoring, gates, R:R, queue policy — none of it.
- **No code** is deployed before 24 Nov; not even the "record only" `watch` tier, because it changes request load and cycle time and would contaminate the soak/holdout series.

## Rejected options

| option | why not |
|---|---|
| `SCAN_TOP` 40 → 60 or 100 | the added symbols are transient (−0.148 vs −0.089); +50–150 % requests; adds no way to learn before trading |
| a fixed hand-picked list (e.g. 20 majors) | blind to new markets; 12 days showed Nobitex liquidity differs from global (XAUT, DOOD, GRAM sit in the core) |
| all 240 markets every 15m | 6× load; the cycle no longer fits in 5 minutes; 120 assets counted twice; most of the tail is unexecutable |
| drop transients without a watch tier | quality up, coverage down, and no route to discover the next symbol — the very complaint this ADR starts from |

## Consequences

- **Positive:** the journal separates two populations (today's reported IC and R̄ are a blend); ~130 symbols covered at a controlled load; trading only on symbols with a known 7-day presence; a documented path for new symbols.
- **Negative:** a new column (`universe_tier`), a second scan loop on a different timeframe, and "today's hot symbol" is deliberately traded later — a conscious cost; the `E_nochase` data and the table above say those symbols produced the most loss.
- **Neutral:** the dashboard gets a `core/watch` badge per row; the default explorer card shows `core` only.

## Acceptance criteria (24 Nov)

Reviewed again on holdout data (10–24 Nov). Becomes "accepted" if:

1. the core/transient gap reproduces in holdout (transient H1 or H2 R̄ worse than core, n ≥ 200 per group);
2. the measured 14-day rate budget (cycle duration `completed_at − started_at` and `source_summary` in `scan_cycles`) leaves room for +60 % without approaching the ADR-0003 ceiling.

If (1) does not reproduce, only membership hysteresis without a watch tier is considered. If (2) fails, the `watch` tier shrinks to 4h or 50 symbols.

## Verification queries

```sql
-- 7-day presence per symbol (hysteresis input)
with cyc as (select id from scan_cycles where timeframe='15m' and started_at >= strftime('%s','now','-7 days'))
select symbol, count(distinct scan_cycle_id)*1.0/(select count(*) from cyc) as presence
from signal_log where scan_cycle_id in (select id from cyc) group by symbol order by presence desc;

-- repeat the core/transient table on holdout
-- (the universe-churn-2026-11-07.md query with ts >= 1762759200)
```
