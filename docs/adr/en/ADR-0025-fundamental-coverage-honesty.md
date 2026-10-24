# ADR-0025 — Fundamental coverage: honest counting, one registry, and the pair premium as evidence

[فارسی](../fa/ADR-0025-صدق-پوشش-بنیادی.md) · **English**

**Status:** Accepted (implemented in 1.1.3)
**Date:** 2026-10-24
**Related:** ADR-0006 (fundamental engine), ADR-0014 (data honesty contract), ADR-0021 (apply only with out-of-sample evidence), ADR-0022 (gates and the shadow ledger), ADR-0023 (smarter only with evidence), ADR-0024 (evidence must not need an open tab)

## Context

A real install exposed two defects in the fundamental layer of one kind: **a number claiming something
that is not true.**

1. **An unmapped symbol is labelled "live".** `FAEngine.score()` ends with:

```python
ev["profile"] = "fallback" if used_fallback else "live"
```

and `used_fallback` only becomes true when something **fails** or the symbol is in
`FALLBACK_FA_PROFILES`. "This symbol is mapped by no provider" is never a failure, so an unmapped
symbol gets `profile: "live"` and is counted in "live FA: N of M". Measured in this repository:

```
FA_ENGINE.score("XYZIRT")  ->  score 52.6 · fresh True · profile "live"
                              evidence {'fear_greed': 51}
```

That 52.6 is built from nothing: the five neutral 50s, one Fear&Greed value that is the same for every
coin, and the market premium. This is exactly what ADR-0014 forbids.

2. **The ID map existed twice.** `fa_engine.KNOWN_CG_IDS` holds fourteen symbols and keeps its own
copy; meanwhile `server/coin_registry.json` holds **fifty verified** IDs used for coin icons. So 36
mappable symbols sat unused — and two maps can drift apart silently. (A real example from the same
review: `TON → the-open-network` is the right ID, but CoinGecko now reports its symbol as `gram`; a
fixed map goes stale too and needs a test.)

3. **The one fundamental figure Nobitex can supply was unused.** Nobitex quotes every coin in two
pairs (`BTCIRT` and `BTCUSDT`). The ratio between them is that **coin's own Toman premium**:

```
premium(base) = (last(baseIRT) / last(baseUSDT)) / last(USDTIRT) − 1
```

Today only the market-wide premium on the three BTC legs is computed and handed to every symbol
(`_premium_pct`), while this number exists per coin and the spread between coins is itself
information: local Toman demand, exit pressure, or simply thin liquidity.

## Decision

### 1. Three coverage states, not two

`evidence.profile` and `source` had three states (live / cached / fallback) and needed a fourth:

| profile | Meaning | Counted as "live" |
|---|---|---|
| `live` | fetched from a provider just now | ✅ |
| `fallback` | built from the hard-coded profile (14 symbols) | ❌ |
| **`none`** | **no provider is mapped for this symbol** | ❌ |

In `/api/fa/{symbol}` the `source` for that state is **`no-provider`**, not `live`. `fa_coverage`
gains an `unmapped` key so that "39 symbols scored fundamentally" can no longer be assembled out of
default scores.

**What it does not do:** it does not zero the score and does not change the composite. An unmapped
symbol keeps the neutral score it has today; it simply stops being called a measurement. Zeroing it
would be a trading-behaviour change and needs its own ADR.

### 2. One registry, one source

`KNOWN_CG_IDS` is not removed (it has a consumer) but is now built from `coin_registry.json`, and
`FALLBACK_FA_PROFILES` remains only for "the provider is unreachable". That takes coverage from 14 to
50 **with no extra network request**, and a test pins that every ID in the registry is present in the
engine's map.

### 3. Dynamic symbol search at runtime: refused

The obvious temptation is to call `/search?query=` for every unmapped symbol. It is refused, and the
reason is measured:

| Measure | Number |
|---|---|
| Symbols matching more than one coin on CoinGecko | 2,626 of 17,425 (**15%**) |
| CoinGecko free tier | after three calls in a row → **429** |

A real example: `DOGS` matches four coins (`doggsnout-skeptic`, `dogs-2`, …) and `NOT` matches two
(`notcoin`, `nothing-3`). Taking the first is a **guess**, and a guess produces a fundamental score
that gets read. So new mappings are resolved **offline and confirmed by a human**
(`scripts/resolve_fa.py`, output → `coin_registry.json` → commit), never during a scan.

### 4. The pair premium: measured in shadow, given no weight

- On every scan, for each base asset whose **both** legs are present in the scan, the premium is
  computed in **basis points** and shipped as `pair_premium` beside `/api/signals` and in
  `/api/gates/evidence`.
- A premium outside a sane range (above 5%, the same ceiling `_premium_pct` already uses for data
  errors) is marked `outlier` and left out of the median — one stale quote must not move the market's
  middle.
- **It gets no weight.** It does not become a fifth FA component, does not change any score, and
  `applied: false` is untouched. Entry into the score follows ADR-0021: evidence for days first, then
  a decision.

## Consequences

- **Positive:** coverage counting stops being inflated by default scores; coverage goes from 14 to 50
  without one extra request; each coin's own premium — the only fundamental figure Nobitex itself
  supplies — becomes visible.
- **Negative:** `FA_COVERAGE.live` will be **lower** than before, and that is correct: the previous
  number was a lie. Anyone watching it will see a drop, which the CHANGELOG has to explain.
- **No trading effect:** no symbol's score changes; only labels and counts.

## Refusals

| Proposal | Why it was refused |
|---|---|
| Zeroing the score of an unmapped symbol | a trading-behaviour change; needs its own ADR and evidence |
| Searching for symbols at runtime | 15% ambiguity and a 429 wall; a guess instead of a mapping |
| Synthesising an IRT/USDT pair for single-leg symbols | a number from two non-synchronous sources — the stale-quote trap again |
| Weighting the pair premium now | ADR-0021: evidence first, weight later |
| A new card for fundamental coverage | ADR-0019/0024: one page, one artifact; the number goes in the existing chip |

## Acceptance

1. `FA_ENGINE.score("XYZIRT")` → `evidence["profile"] == "none"`, `fresh is False`, and
   `/api/fa/XYZIRT` → `source == "no-provider"`.
2. `fa_coverage.unmapped` equals the number of unmapped symbols and
   `live + fallback + unmapped == total`.
3. Every ID in `coin_registry.json` is present in the engine's `KNOWN_CG_IDS` (test).
4. For the three BTC legs the pair premium is computed correctly; if one of the three legs is missing
   the block is `ok: false` with a reason, never zero.
5. A 5.1% premium is marked `outlier` and excluded from the median.
6. No test on the composite score moves: `composite` keeps its formula.
