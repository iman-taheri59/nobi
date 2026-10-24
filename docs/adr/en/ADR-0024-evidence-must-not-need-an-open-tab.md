# ADR-0024 — Evidence must not need an open tab

[فارسی](../fa/ADR-0024-شاهد-نباید-به-تب-باز-گروگان-باشد.md) · **English**

**Status:** Accepted (implemented in 1.1.1)
**Date:** 2026-10-24
**Related:** ADR-0003 (rate budget), ADR-0014 (data honesty), ADR-0017 (testing from inside the dashboard), ADR-0022 (gates and the shadow ledger), ADR-0023 (smarter only with evidence — S1)

## Context

ADR-0023 says the next decisions (S2 onward) must be taken on evidence from the shadow ledger, and
sets acceptance at "≥30 verdicts over ≥5 days". Evidence finally arrived in 1.1.0, and the first
thing it exposed was a structural defect in the code, not in the measurement:

| Fact in the code (before 1.1.1) | Consequence |
|---|---|
| `rescan()` was called once, from `lifespan` (startup) | with no outside request, the server never scanned |
| the only later trigger was `/api/signals` | and only an open dashboard tab called it (every 120 s) |
| gate verdicts are written only in `rescan` (`gate_write`) | with no tab open, the ledger did not grow |

So the real lock on "S2" was **an open browser**. That is wrong for three reasons:

1. **Evidence must come from the process, not from a user's habit.** Every night the machine sleeps,
   or the tab closes, hours of the five-day window go unrecorded — and whoever reads the number
   cannot tell which days went missing.
2. **A measurement that depends on an observer is not a measurement.** ADR-0014 forbids this at the
   data layer; it has to be forbidden at the evidence-collection layer too.
3. **`days` grows with scanning on *different* days, not with scanning more often.** One record per
   symbol per closed bar (`INSERT OR IGNORE`), so a scheduled scan does not inflate the sample — it
   just stops bars from being missed while no tab was open.

The same review found three smaller defects of one kind: **a number whose name does not match its
meaning.**

- `gates.inputs.oldest_book_s` is the age of the oldest book **seen**, including books dropped in the
  very same pass for exceeding the TTL. On a real install it printed `499.6` while
  `BOOK_GATE_TTL_S = 300`. A reader concludes the gates judged a bar on a 500-second-old book; they
  did not. The number contradicted itself.
- `books`/`stale_books`/`prefetch`/`oldest_book_s` were rendered in no card and no tooltip; the only
  way to see them was a Python one-liner in a terminal.
- the daily tool for exactly this measurement had been built outside the repository, so `git pull`
  would lose it.

## Decision

### 1. A scheduled scan inside the server, off by default

A light, supervised loop in `lifespan` that calls `rescan(tf)` every `NBX_SCAN_LOOP_S` seconds.

- **Default 0 = off.** Turning it on is an operational decision, not something an upgrade switches
  silently: every scan makes network requests, and the rate budget from ADR-0003 belongs to the user.
- **Floor of 60 seconds, and above the floor it raises `ValueError` rather than shrinking silently** —
  the same rule ADR-0023 set for N. An invalid value leaves the loop **off** and writes **one error
  line** to the log.
- **What it does not do:** ranking, scores, portfolio, execution and `applied: false` are untouched.
  The scan is the same function an `/api/signals` request runs; only its trigger stops being a browser.
- **Its state is visible:** `scan_loop` in `/api/health` (last run, result, interval) and in the daily
  tool's output. A loop that died silently is worse than no loop.

### 2. Two ages, not one

| Field | Meaning |
|---|---|
| `oldest_book_s` | age of the oldest book **in the cache**, including the ones just dropped (unchanged, for compatibility) |
| `oldest_usable_s` | age of the oldest book that is **usable** — one that can actually produce a verdict |

The rule: any number that can be read must be the thing its name says. `oldest_usable_s` is never
larger than `BOOK_GATE_TTL_S`, and a test pins that.

### 3. The same six numbers, in the existing chip's tooltip

The "gates (shadow)" chip on the Signals tab used to say only `would_block` of `rows`. It now carries
`books`, `stale_books`, `oldest_usable_s` and `prefetch(ok/asked)` in its title. Deliberate: **no new
chip, no new card.** One page, one artifact (ADR-0019); an operational number attaches to the existing
chip rather than to a card nobody opens.

### 4. The measurement tool lives in the repository

`server/tools/daily_snapshot.py` — one CSV row per run. `GET` only; it writes no secret and changes
nothing. A measurement that lives outside the repository is not a measurement.

## Consequences

- **Positive:** the evidence window depends on an environment variable and a process, not on a daily
  habit. `days` can fill without anyone remembering to leave a tab open.
- **Positive:** `oldest_usable_s` closes the numeric lie; the tooltip moves three operational numbers
  from a terminal onto the page.
- **Negative:** with `NBX_SCAN_LOOP_S` on, request consumption against the exchange rises; that is why
  the default is off and why there is a floor. Setting it to 60 means sharing the rate budget with
  every other consumer.
- **No trading effect:** `applied` stays `false`; no threshold, weight or risk gate changed.

## Refusals

| Proposal | Why it was refused |
|---|---|
| Turning the loop on at 900 s in the same release | changes network consumption without a user decision; an upgrade must not change operational behaviour |
| Scanning every 15 s "to fill faster" | one record per bar; faster than the bar close only burns rate budget and does not move `days` at all |
| Writing verdicts for past bars (automatic backfill) | that is S2, and ADR-0023 forbids it without evidence; this ADR only makes *collection* independent |
| Removing `oldest_book_s` and replacing it with `oldest_usable_s` | breaks an existing consumer; adding is not removing |
| A new card for the evidence numbers | ADR-0019 and ADR-0017: one page, one artifact; operational numbers go in the existing chip's tooltip |

## Acceptance

1. `NBX_SCAN_LOOP_S=0` (default): no scan tick appears in the log and `/api/health` reports
   `enabled: false`.
2. `NBX_SCAN_LOOP_S=61`: one scan runs with no `/api/signals` request from a browser, and
   `scan_loop.last_at` in `/api/health` is filled.
3. `NBX_SCAN_LOOP_S=30`: the loop stays **off**, one error is logged, and the server starts anyway
   (a bad setting must not take the service down).
4. `oldest_usable_s <= BOOK_GATE_TTL_S` always, even with hours of stale books sitting in the cache.
5. The daily tool produces two CSV rows with one header in two consecutive runs and issues no
   non-`GET` request.
