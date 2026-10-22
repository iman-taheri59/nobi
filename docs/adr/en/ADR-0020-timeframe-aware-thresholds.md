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
