# How to work out the right `NBX_RR_LEVELS` range

[فارسی](rr-ladder-tuning.md) · **English**

> Background: the [ADR-0008 amendment](adr/en/ADR-0008-risk-management-and-position-sizing.md)
> explains why the live ladder and the backtest ladder are deliberately not the same.

## The short answer

"The best range" is not a universal number. What you *can* determine is whether one
candidate ladder performs better out of sample, with enough trades, by a margin that is
larger than noise. When the data is too thin, the honest answer is "not decidable yet" —
and the repository says exactly that with `gate.state = INSUFFICIENT_DATA` instead of a
green number.

## 1. The arithmetic first

The ladder is a **trade-management** parameter, not a forecast. Its effect shows up in
the R-multiple distribution, and the difference between two nearby ladders is usually
small.

The standard deviation of R on real trades is roughly 1.5R. The standard error of the
mean after `n` trades:

| Trades | Standard error of mean R |
|---|---|
| 30 | ≈ 0.27R |
| 100 | ≈ 0.15R |
| 400 | ≈ 0.075R |

With 100 trades, a difference under about 0.3R (two standard errors) is **noise**. In
practice: `(1.2,2.0)` and `(1.5,2.5)` cannot be separated on 80 trades. If a report says
one is 0.05R better, it has said nothing.

## 2. The data horizon — the real constraint

The backtest route caps at **2000** candles. What that means per timeframe:

| Timeframe | 2000 candles ≈ | For judging a ladder |
|---|---|---|
| `15m` | 21 days | **Unsuitable** — one market regime, many trades but no variety |
| `1h` | 83 days | Borderline — depends on trade count |
| `4h` | 333 days | **Best choice** — several regimes, enough trades |
| `D` | 5.5 years | Good for stability, but few trades |

Rule: decide on `4h`, then **confirm** on `1h` and `D`. If the ladder does not hold on
all three, what you picked was a property of that market or that period.

## 3. The method — five steps

### Step 1: keep the candidate list small

Three to five candidates, not a continuum. Suggestion:

| Candidate | `tp1_r, tp2_r` | Logic |
|---|---|---|
| Conservative | `1.0,1.5` | More wins, each smaller |
| **Live default** | `1.2,2.0` | The control (baseline) |
| Backtest default | `1.5,2.5` | What the simulator prefers |
| Patient | `2.0,3.5` | Fewer wins, each larger |

Candidates should satisfy `tp2 ≥ MIN_RR = 1.5`; otherwise every live plan carries the
`low-rr` warning and the page flags it — meaning you are knowingly trading below the risk
policy.

### Step 2: a walk-forward with an explicit grid

The default `DEFAULT_PARAM_GRID` already sweeps `tp1_r/tp2_r`; for a controlled
comparison, hold the stop distance fixed and vary only the ladder:

```bash
curl -s -X POST http://127.0.0.1:8000/api/backtest/run \
  -H 'Content-Type: application/json' -d '{
    "symbol": "BTCIRT", "tf": "4h", "bars": 2000,
    "atr_stop_k": 2.0, "slippage_bps": 6,
    "grid": [
      {"atr_stop_k": 2.0, "tp1_r": 1.0, "tp2_r": 1.5},
      {"atr_stop_k": 2.0, "tp1_r": 1.2, "tp2_r": 2.0},
      {"atr_stop_k": 2.0, "tp1_r": 1.5, "tp2_r": 2.5},
      {"atr_stop_k": 2.0, "tp1_r": 2.0, "tp2_r": 3.5}
    ]
  }' | python -m json.tool > btc-4h.json
```

Pass `slippage_bps=6` explicitly: the route defaults to zero while the simulator itself
carries 6 bps. Without it, the ladder with the nearer target wins artificially.

Repeat across several markets and timeframes — never just one:

```bash
for S in BTCIRT ETHIRT SOLUSDT; do for TF in 4h 1h D; do
  curl -s -X POST http://127.0.0.1:8000/api/backtest/run -H 'Content-Type: application/json' \
    -d "{\"symbol\":\"$S\",\"tf\":\"$TF\",\"bars\":2000,\"atr_stop_k\":2.0,\"slippage_bps\":6,
         \"grid\":[{\"atr_stop_k\":2.0,\"tp1_r\":1.0,\"tp2_r\":1.5},
                 {\"atr_stop_k\":2.0,\"tp1_r\":1.2,\"tp2_r\":2.0},
                 {\"atr_stop_k\":2.0,\"tp1_r\":1.5,\"tp2_r\":2.5},
                 {\"atr_stop_k\":2.0,\"tp1_r\":2.0,\"tp2_r\":3.5}]}" \
    > "bt-$S-$TF.json"
done; done
```

### Step 3: read three things, in this order

Every report has three parts that must be read **together**:

**a) `gate.state`** — the ADR-0009 promotion gate:

| State | What it means for this decision |
|---|---|
| `PASS` | Every check holds on live candles → citable |
| `FAIL` | One check failed; `gate.failed` names it |
| `INSUFFICIENT_DATA` | Fewer than 3 windows or fewer than 30 out-of-sample trades — **not decidable yet**; `gate.bars_needed` says how many candles are needed |
| `SYNTHETIC_DATA` | Candles came from the simulated fallback; no decision may rest on it |

**b) `param_stability`** — the most relevant evidence for this question:

```json
"param_stability": {"distinct_param_sets": 3, "most_common_share": 66.7}
```

Two thirds of the windows independently chose the same ladder. How to read it:

- `most_common_share ≥ 60%` → the ladder is stable; a serious candidate.
- `30–60%` → unstable; the market is torn between two modes.
- `< 30%` → every window wants something else; **keep the default**.

**c) `aggregate`** — and only its out-of-sample part: `expectancy_r`, `profit_factor`,
`max_drawdown`, `profitable_windows`. Do not select on the in-sample number (`is`); that
is what the ladder was fitted to.

### Step 4: agreement across markets, not a win in one

Each (market, timeframe) pair has one vote. A proposed acceptance rule:

- the ladder is in the best or second-best slot in **at least half** of the pairs,
- and no pair reports `gate.state = FAIL` with it.

If it wins in exactly one pair, that is a property of that market, not a policy.

### Step 5: a plateau, not a peak

Pick the ladder whose **neighbours** are also acceptable. A sharp peak (`2.0/3.5`
excellent, `1.5/2.5` disastrous) usually means fitting noise. A plateau means being
slightly wrong costs little.

## 4. The decision table

| Finding | Decision |
|---|---|
| `INSUFFICIENT_DATA` in most pairs | Do not change; satisfy `bars_needed` (higher timeframe or more history) |
| `param_stability` scattered | Do not change; keep `1.2,2.0` |
| One stable ladder, `PASS`, best in ≥ half the pairs | Set it with `NBX_RR_LEVELS` |
| The winning ladder has `tp2 < 1.5` | Reject; it goes below `MIN_RR` |
| The winning ladder only wins with `slippage_bps=0` | Reject; at 6 bps it loses, i.e. cost eats it |

## 5. After adopting it

```bash
# in the service env (systemd: /etc/nobi/nobi.env)
NBX_RR_LEVELS=1.5,2.5
# restart, then confirm:
curl -s http://127.0.0.1:8000/api/risk/state | python -m json.tool | grep -A3 rrLevels
```

Then **write the revert rule before you need it** — otherwise the change stays forever:

- if after 30 closed live trades `expectancy_r` is negative, go back to the default;
- the journal keeps each trade's `r_multiple`; `/api/journal/summary` is what the final
  judgement should rest on, not the backtest report.

## 6. What this document does not say

This method does not find an "optimal" ladder, and does not claim to. What it does is
stop a number from a backtest report from silently changing live trading behaviour. When
the data is not there, the right answer is "not yet" — and the repository says so.
