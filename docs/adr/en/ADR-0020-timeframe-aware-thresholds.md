# ADR-0020: Signal thresholds depend on the timeframe — measured, not applied

[فارسی](../fa/ADR-0020-آستانه-های-وابسته-به-تایم-فریم.md) · **English**

- **Status:** Accepted; only the measurement half is implemented (applying is deliberately off)
- **Date:** 2026-10-22 · **Version:** 1.0.12
- **Related:** 0005 (TA engine), 0009 (walk-forward backtest), 0014 (data-honesty contract)
- **Enforced by:** `server/tests/test_adaptive.py`, `scripts/adaptive_report.py`,
  `GET /api/adaptive/thresholds/{symbol}`

---

## 1. The problem

`ta_engine.py` holds two numbers:

```python
LONG_SCORE_MIN = 68.0      # score >= 68 -> LONG
NEUTRAL_SCORE_MIN = 55.0   # score >= 55 -> NEUTRAL, else EXIT
```

They are identical for **every symbol and every timeframe**. So when the dashboard
says "few signals today", three very different things can be behind that sentence:

1. the market is genuinely quiet;
2. the score rarely clears the bar on this symbol (a low median);
3. on this timeframe, 68 is close to unreachable.

Until today none of the three could be separated, because nobody counted.

## 2. The decision

1. **Measurement is added.** `server/adaptive.py` computes and reports three things:
   the current ATR percentile inside its own history, the volatility regime
   (calm/normal/volatile), and **how often the fixed threshold actually fires** on
   those same candles.
2. **No decision changes.** Every report returns `applied: false`. The number is
   evidence, not a behaviour change — the same rule the reference feed follows
   (ADR-0014).
3. **The proposal is bounded and ordered.** The entry factor is clamped to
   `[0.85, 1.25]`, `NEUTRAL` can never overtake `LONG` (if it would, the code
   refuses with `band-collapsed` instead of reporting a wrong number), and a shorter
   timeframe always gets a bar **at least as high** as a longer one.
4. **Applying it, if ever, needs its own ADR** with a before/after walk-forward
   comparison. This record is not that permission.

## 3. The bases, and why

| Factor | Value | Why |
|---|---|---|
| `TF_EXPONENT` | 0.35 | Noise grows roughly with the square root of time; `sqrt(15/60)` says a 15-minute bar carries about half the information of an hourly one |
| `ANCHOR` | 40 | Where the four score components cancel. Anchoring at 0 collapses the LONG/NEUTRAL band to 3 points at a 1.25× factor; anchoring at the line itself freezes the number being scaled |
| `REGIME_ENTRY` | calm 1.0 · volatile 1.1 | In a volatile regime the same score is weaker evidence |
| `REGIME_STOP` | calm 0.85 · volatile 1.25 | An ATR-based stop must be wider in high volatility or noise fills it |
| `MIN_BARS` | 120 | An ATR percentile below this is noise wearing a number |

## 4. The real measurement (this build, reference feed, 340 scored bars)

```
BTCUSDT — source: reference
  tf  bars    ATR%  pctile    regime  factor        long     neutral  LONG share
 15m   340    0.17     12%      calm    1.25   68 → 75.0   55 → 58.8       40.0%
  1h   340    0.45     45%    normal    1.00   68 → 68.0   55 → 55.0       19.4%
  4h   340    1.02     62%    normal    0.85   68 → 63.8   55 → 52.8       21.2%
   D   340    2.58     36%    normal    0.85   68 → 63.8   55 → 52.8       17.3%
```

The last column is what nobody could see before: **the same 68 produces twice as many
signals on 15 minutes as on the daily.** Neither number is "right"; a single constant
for both is an unintended policy.

## 5. Consequences

- The dashboard can say "few signals because the median score is 42.5", not "the
  market is quiet".
- Actually applying this needs an out-of-sample walk-forward comparison with and
  without the factor; until then `applied` stays `false`.
- Cost: two new surfaces (`/api/adaptive/thresholds/{symbol}` and a CLI script) that
  must stay in step with future engine changes; the tests hold that line.

## Amendment — 2026-10-22 (version 1.0.13): the visible half, with the same fence

Until 1.0.12 the measurement lived behind an API route and a CLI script. The
"thresholds and regime" card in the backtest lab shows the same numbers — and that is
where this decision's main risk appears: **a panel that proposes a better-looking number
without saying it was not applied is exactly the moment a measurement becomes a silent
policy.**

So the fence was extended into the interface:

1. Every card carries a fixed "measurement only" chip and a "not applied — evidence only"
   line, read from the same i18n key whose bilingual definition a test keeps honest.
2. **A refusal never becomes a number:** an unreachable venue or a missing toman rate
   clears the cells to `—` and prints the named code (`no-fx`, `upstream_unavailable`).
3. **One candle source:** the card reads the same `btSrc` control as the backtest beside
   it, or two panels start disagreeing about one symbol.
4. **Nothing is requested on boot** — only when the tab is opened or the button pressed.
5. Executable guard: `scripts/test_adaptive_card.cjs` runs the real inline block in jsdom
   and asserts those four properties.

## Second amendment — 2026-10-22 (version 1.0.14): a number belongs to a pair

The 1.0.13 amendment printed the fence on the card itself. One hole remained, and the
quietest hole is always the most dangerous: the card showed the numbers of one **pair**
(symbol, timeframe, feed) while the three controls for that pair sat next to it, live.
The result: `BTCUSDT/1h` numbers under a header reading `ETHIRT/4h` — the same lie as an
unlabelled proposal, only whispered.

From this version:

1. The measured pair is stored with the numbers and compared against the live selection.
2. When they differ, the chip moves from "measurement only" to **"stale"** and the drift
   itself is named (`asked → now`).
3. Stale numbers are kept — they remain true for their own pair — but they may not wear the
   new pair's header, and a fresh measurement runs after 700 ms.
4. Before the first request, changing the selection does nothing: the card is evidence,
   not a poller.
5. The staleness state is observable on `#adCard[data-stale]` and via
   `window.adCardState()`, so the executable (jsdom) test can assert what a person sees.

## Third amendment — 2026-10-22 (version 1.0.18): a number has to say *when* it is from

The 1.0.14 amendment tied the numbers to a **pair** (symbol, timeframe, feed). One axis was
missing: **time**. A percentile computed on a series whose newest candle is three days old is
not wrong — its label is; and that is the same family of error that `applied: false` and the
`REFERENCE_DATA` stamp exist to prevent.

From this version:

1. `server/freshness.py` measures the newest candle's age in **bars**, not seconds
   (tolerance: 2 bars).
2. The reason is named: `behind`, `ahead-of-clock`, `no-candles` — a boolean is not enough,
   because "stale" starts an argument about taste and `age_bars: 41.2` does not.
3. Both API routes return a `freshness` block next to the numbers.
4. On the card, data staleness is **separate** from pair staleness (`data-stale` versus
   `stale`), because the two ask the user for different actions.
5. When both are stale, both are said; neither hides the other.

### Appendix to the third amendment — version 1.0.20

The rule "a number has to say when it is from" was extended from the threshold card to the
**backtest result card**: both backtest routes return a `freshness` block, and the card gains
a chip that is visible only when the data is old and carries the age in its title. A failed
age check never takes the numbers down (`freshness_block()` → a stale block with a named code).

### Second appendix to the third amendment — version 1.0.21

Until 1.0.20, the cards that named the age of their candles were the ones holding the candles.
The **market-scan card** was not: it builds its ranking from 260-bar series per symbol and then
throws the series away, so asking "how many bars old is this?" would have required either a
second copy of the rule or no answer at all.

From this version:

1. `freshness.assess_bar()` sits beside `assess()`, and `assess()` is built on it: one
   computation, two doors. Any change to the tolerance or to the meaning of "stale" moves both.
2. The scan keeps the newest candle it saw, per timeframe, and `/api/signals` returns a
   `freshness` block from both of its answers — ranked and empty.
3. The browser twin (`NBX.freshness`) carries the same rule and the same bar length, and the
   possible drift between the two sides is closed by `test_single_source_constants` — the same
   place the fee and the starting equity were pinned.
4. No threshold, weight or verdict changed: this version only puts a time label on numbers that
   were already there.
