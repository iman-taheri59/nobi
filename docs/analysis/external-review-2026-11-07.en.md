# nobi (nobitrader) — Unified Review of the Project and the Market-Analysis Engine

Source: external review received 2026-11-07 (English only; archived verbatim). Note: the "universe (≤ 40)" figure is correct — `/api/universe` caches the top 60 by turnover, but the scan scores the top 40 (`SCAN_TOP`); an earlier note here claiming 60 was wrong.

Repository: `github.com/iman-taheri59/nobi` · branch `main` · commit `61bc2be` · version `2.5.0` · MIT

> **Method:** the repository was cloned and, beyond reading its documentation, **the project
> itself was executed**: dependencies installed, `ruff`, `mypy`, 1,853 pytest tests, uvicorn
> started and its routes called, the GitHub Actions result queried, and the analysis engine run
> against synthetic series with a fixed seed. Every number below comes from a real run and is
> reproducible per §11. Journal figures are quoted from the project's own
> `docs/analysis/` reports, because `journal.db` is not in the repository.

---

## 1. Executive summary

The project is a crypto trading dashboard for the Nobitex exchange: FastAPI on Python 3.12 with
95 routes, a 600 KB single HTML page, 56k lines of Python, 1,853 tests and 52 bilingual ADRs.
Its **engineering discipline** is above that of many team projects; its **proof of trading edge**
— by its own admission — is still zero.

| Measure | Value |
|---|---|
| `ruff check server scripts` | **All checks passed** |
| `mypy` (175 files) | **4 errors in 2 test files** → CI red |
| `pytest server/tests` | 1,851 passed / **2 failed** (exec bits) in 77 s |
| Live server run | 200 on `/`, `/api/health` healthy and **honest** with the exchange unreachable |
| Composite IC (post-era journal) | **+0.05** — indistinguishable from noise |
| Stop rate at the 24 h horizon | **89%** |

**One sentence:** this system is far ahead of average at engineering *"never say a number you
cannot source"*, and still at zero at engineering *"build an edge"*. Both halves of that
sentence are evidenced below.

---

## 2. What the project is

A bilingual (Persian/English) dashboard with a technical-analysis engine, a fundamental engine,
risk management, paper trading, walk-forward backtesting, a signal journal, conditional price
alerts, and a three-role account system (`owner`/`operator`/`viewer`). Live execution sits
behind several gates.

| Part | Size |
|---|---|
| `server/main.py` | 5,966 lines, 95 routes, longest function 219 lines (`rescan`) |
| All server + script Python | ~56k lines |
| `index.html` | 600 KB, 462,175 chars of inline JS (largest block 406 KB) |
| `nobitex-mobile.html` | 932 KB (generated; do not edit by hand) |
| Documentation | ~29k lines of Markdown, 52 bilingual ADRs |
| Tests | 1,853 Python + 25 JS/DOM suites |

---

## 3. Gate status: what actually ran

The project claims its gates are green and that CI runs them. My run, on a clean clone:

### 3-1. CI on `main` is red

```
run 42364935892  (commit 61bc2be, 2026-11-07T10:40Z)
  python             → failure   (step: mypy)
  windows (advisory) → failure   (step: mypy)
  js                 → success
  ui                 → success
```

Local reproduction, the same four errors:

```
server/tests/test_shadow_arms.py:89  Incompatible types in assignment (list[Any] → bool)
server/tests/test_shadow_arms.py:92  "bool" has no attribute "__iter__"
server/tests/test_fees_holdout.py:24  dict[str, Any] | None → not indexable
server/tests/test_fees_holdout.py:54  dict[str, Any] | None → not indexable
Found 4 errors in 2 files (checked 175 source files)
```

These were introduced by the last two commits (`02232e4` = ADR-0051 and `6dedf91` = ADR-0052):
two of the three most recent commits broke the gate the README advertises. The fix is under ten
minutes; the more important message is that "the gate is green" has become a habit rather than a
guarantee.

### 3-2. Executable bits are not in Git

```
FAILED server/tests/test_update_script.py::test_the_script_is_executable
FAILED server/tests/test_resolve_fa.py::test_the_script_is_executable
  assert 33188 & 0o111 → 0
```

`git ls-files -s` shows **no `100755` file anywhere in the repo** — including
`scripts/deploy_arvan.sh`, `scripts/update_arvan.sh`, `deploy/nobi-privileged-helper` and
`deploy/nobi-helper-client`. The CHANGELOG says the bit was dropped in `c4a2b73` and "restored";
it was not. Every clean clone meets non-executable scripts, and because the `python` job already
fails at mypy, this failure never shows up in CI.

### 3-3. The live server run: healthy and honest

The server came up. `GET /` → **200** with 953,254 bytes. `/api/health` returned the
`build{commit, source}`, `market_regime` and `price_alerts` blocks. Nobitex was unreachable from
this sandbox and the response, instead of a fabricated number, was:

```json
"upstream": {"ok": null, "degraded": true, ...}
"provenance": {"source": "unavailable"}
"market_regime": {"state": "unknown", "reasons": ["overview-not-ok"]}
WARNING: Nobitex /market/stats unreachable (Nobitex timeout)
```

This is exactly the ADR-0014 contract, and it is honoured in practice.

### 3-4. Documentation drift

| Item | Documentation | Reality |
|---|---|---|
| ADR count in the main README | "20 bilingual ADRs" (`README.md:231`) | **52** (correct in `docs/README.md`) |
| `server/README.md` | describes `data_store.py`, `decision.py`, `src/exchange/`, `src/risk/` … | **none of these exist**; the layout is flat |
| `nbx-core.js` header | "100% standalone on mobile" | the README itself has retracted this |

---

## 4. The market-analysis pipeline: from candle to order

```
 /v3/market/stats + /v2/options
          │                            ▼
          │                    ┌───────────────────┐
          │                    │  universe (≤ 40)  │  ranked by 24h USD turnover
          │                    └─────────┬─────────┘
          ▼                              ▼
 /market/udf/history ──────► queue_policy.closed_rows  ← finished candles only
  (260 candles)                        │
                                      ▼
                          ┌───────────────────────────┐
                          │ ta_engine.ta_score        │  4 components → score 0..100
                          │ trend/momentum/volume/    │
                          │ structure + RANGE gate    │
                          └────────────┬──────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────┐
        ▼                              ▼                          ▼
  liq = 50+4·log10(turnover)   FA_ENGINE.score()          regime.assess()
  (effective band 12..17)       CoinGecko/DefiLlama/F&G    breadth + median 24h
        │                              │  vetoes                │ (shadow by default)
        └──────────────┬───────────────┘                          │
                       ▼                                          │
        composite = 0.55·TA + 0.25·FA + 0.20·liq                  │
        (FA missing → renormalised over 0.75)                     │
                       │                                          │
                       ▼                                          ▼
        apply_vetoes(composite)  ── cap 67 / 45 ──►  regime.apply()
                       │
                       ▼
        direction  ← ← ← ← ← ← ← ← ← ← ← ←  ⚠ comes from TA, not from composite  (§7-1)
                       │
        ┌──────────────┴───────────────┬─────────────────┬──────────────┐
        ▼                              ▼                 ▼              ▼
   gates (shadow)              journal.resolve         shadow_arms   paper/live desk
   age/spread/depth            H1/H2/H3 with 2×ATR     B/C/D arms    10 preflight gates
   removes nothing             (LONG-only)             (recording)   (+ MIN_RR 1.5)
```

Three structural facts fall straight out of that diagram:

1. **The verdict comes from TA; the ranking comes from composite.** Two different numbers doing
   two different jobs.
2. **The engine's output is "buy" or "don't buy".** NEUTRAL and EXIT are both "don't buy"; there
   is no active-flat or short path anywhere. The whole system is long-only.
3. **Three "shadow" layers are stacked** (the ADR-0050 regime gate, the ADR-0022 market gates,
   the ADR-0051 A/B arms). None of them changes a decision today. That restraint is deliberate,
   but it means live behaviour is still the original version plus three recording layers.

---

## 5. Layer 1 — the technical engine (`server/ta_engine.py`, 322 lines)

### 5-1. What is actually computed

| Component | Weight | Inputs | Scoring (point by point from the code) |
|---|---|---|---|
| `trend` | 0.40 | EMA 9/21/50/200, ADX/DI 14 | 20 if close>EMA21, 20 if EMA9>EMA21, 25 if EMA21>EMA50, 15 if close>EMA200, 20 if ADX>22 and +DI>−DI |
| `momentum` | 0.25 | RSI 14, MACD(12,26,9) histogram | from a base of 50: 55<RSI<72 → +20, RSI≥72 → +5, RSI<45 → −15; histogram positive and rising → +25, negative and falling → −10 |
| `volume` | 0.15 | bar *j* volume vs 29-bar mean | 50 + 30 if >1.4×, −15 if <0.6×, +10 if price up and volume above mean |
| `structure` | 0.20 | %B Bollinger(20,2σ), 19-bar high | 50 + 25 if %B>0.7, −15 if <0.25, +10 on a 19-bar breakout |

```
regime = "TREND" if ADX > 25 else "RANGE"
score  = (0.40·trend + 0.25·momentum + 0.15·volume + 0.20·structure) × (1.0 | 0.85)
direction = LONG if score ≥ 68 else NEUTRAL if score ≥ 55 else EXIT
stopDistance = k × ATR(14)        # k = NBX_RISK_K, default 2.0
```

At least 60 candles are required. EMA200 is warmed from a 260-bar window; I measured the warm-up
error at **0.11%** — negligible. ✔

### 5-2. Finding: the system is in practice a trend detector, not a composite score

I raised each component to its maximum with the other three held at a neutral 50:

| Component (max) | in TREND | in RANGE | Reaches 68? |
|---|---|---|---|
| trend (100) | **70.0** | 59.5 | only trend, and only in TREND |
| momentum (95) | 61.3 | 52.1 | ✗ |
| volume (90) | 56.0 | 47.6 | ✗ |
| structure (85) | 57.0 | 48.5 | ✗ |

**Three of the four components are structurally incapable of producing a LONG.** In RANGE even a
perfect trend is not enough (the theoretical ceiling of the whole score is 80.1, so anything above
68 requires near-perfection). Practical consequence: momentum/volume/structure mostly move the
**ranking** and almost never the **verdict**. If the intent was multi-factor confirmation, this
arithmetic does not deliver it.

### 5-3. Finding: score distribution on pure noise

Over 300 pure random walks with no edge at all:

| Case | Result |
|---|---|
| Perfectly flat market | score = **25.5** → EXIT |
| Monotonic uptrend with no down bar | 77.0 → LONG (momentum = 50 because RSI = NaN) |
| Random walk, 300 samples | median **41.9**, p90 = 75.0, **13% ≥ 68**, 69% < 55 |
| Share of the TREND regime | **20%** ⇒ 80% of bars take the 0.85 multiplier |

In a market with no information, the engine says "buy" 13% of the time. That is normal for any
threshold; the real question is whether the same 13% on real data beats 13% on noise — and the
composite IC (+0.05 over the whole post-era) says "barely".

### 5-4. Finding: the 68 threshold disagrees with the project's own adaptive model

`server/adaptive.py` proposes a per-timeframe factor relative to 1h (exponent 0.35):

| tf | factor | proposed LONG bar | live bar |
|---|---|---|---|
| 15m | 1.25 | **75.0** | 68 |
| 1h | 1.00 | 68 | 68 |
| 4h | 0.85 | 63.8 | 68 |

Production runs on **15m**. The project's own model therefore says the live threshold is about
seven points **more permissive** than the measurement; on random walks that difference moves the
LONG rate from 13% to 6%. `adaptive.report()` deliberately returns `applied: False`, and that
restraint is right — but "unapplied" is an open finding, not a harmless choice.

### 5-5. Finding: the 0.85 multiplier is a hidden penalty

`RANGE_GATE=0.85` multiplies **every** score in RANGE, while the 68 threshold was calibrated for
TREND. The system therefore works with an **effective bar of 80** on 80% of bars while the UI
shows 68. If that is intentional (buy only in a trend) it is fine — but it is written nowhere and
no test compares the LONG share in RANGE against TREND.

---

## 6. Layer 2 — liquidity and fundamentals

### 6-1. Liquidity: a 20% weight with a 5% effect

`liq = 50 + 4·log10(turnover_usd)`, clipped to 0..100:

| 24h turnover | liq | contribution to composite |
|---|---|---|
| $1k | 62 | 12.4 |
| $100k | 70 | 14.0 |
| $1M | 74 | 14.8 |
| $1B | 86 | 17.2 |

The whole effective range is five points out of a hundred. (The project's own 7 November report
notes liquidity IC fell from +0.11 to +0.07 and demoted the liquidity filter to a shadow arm —
consistent with this arithmetic.)

An important positive: IRT and USDT turnover are both converted to USD, and a symbol without an
FX rate is excluded. There is no rial-versus-dollar comparison anywhere. ✔

### 6-2. Fundamentals (`server/fa_engine.py`, 451 lines)

| Component | Weight | Source | Thresholds |
|---|---|---|---|
| market | 0.25 | CoinGecko mcap + vol/mcap | mcap ≥50B→85, ≥10B→74, ≥1B→62, ≥100M→50, else 38; vol/mcap>0.5 → −10, >0.05 → +6 |
| tokenomics | 0.25 | mcap/FDV | `min(100, ratio×130)`; if total/max < 0.6 → −12 |
| onchain | 0.20 | DefiLlama TVL with **exact-name** matching | 40+18·log10(TVL); TVL≤10M → `thin-tvl` veto; PoW → +12 |
| sentiment | 0.20 | Fear & Greed | 40+0.4·FGI; FGI≤15 → `extreme-fear` veto, ≥85 → −10 |
| macro | 0.10 | USDT/IRT premium | >+4% → 40 and `premium-stretched` veto, <−4% → 62, else 55 |

**Vetoes** are the only place where FA actually decides: `new-listing<90d`, `thin-tvl`,
`extreme-fear`, `premium-stretched`, `unlock-7d>5%`. The severe ones
(`thin-tvl`, `extreme-fear`, `unlock-7d>5%`) → cap 45 and EXIT; the rest → cap 67.

Engineering worth praising:

- **Weight renormalisation**: with two components available, the score divides by the available
  weight, not by 1. "Unknown = neutral" has been removed.
- **Four profile states**: `live` / `fallback` / `unavailable` / `none`. A symbol no provider maps
  no longer scores 52.6 labelled "live".
- **The cache key carries the premium bucket**, so a "clean" score cannot survive a premium spike
  for ten minutes.
- A shared circuit breaker; stale data is explicitly labelled `stale-cache`.

Two defects:

1. **`FALLBACK_FA_PROFILES` — 14 hand-written profiles** (BTC mcap 1.44T, ETH 405B …) tagged
   `"as_of": "unversioned-legacy-profile"`. Whenever CoinGecko is unreachable, 25% of the
   composite comes from a number nobody can date. `fallback` is labelled honestly, but the number
   still multiplies into the decision. The right fix: weight zero (renormalisation handles it) or
   a real `as_of` per profile.
2. **`VETO_CACHE` is written and never read** — two references in the whole file: the definition
   and the write. Dead code.

---

## 7. Layer 3 — fusion, vetoes, regime

```
composite = 0.55·TA + 0.25·FA + 0.20·liq          (FA available)
composite = (0.55·TA + 0.20·liq) / 0.75            (FA missing)
```

Two cap layers then apply to the **composite**, while the **direction was decided by TA**:

| Layer | Condition | Effect |
|---|---|---|
| `apply_vetoes` | any FA veto | composite ≤ 67 and LONG→NEUTRAL; severe veto → ≤ 45 and EXIT |
| `regime.apply` (ADR-0050) | `risk-off` regime | default `shadow`: records `would_block` only; in `enforce`: LONG→NEUTRAL with cap 67 |

### 7-1. Key finding: the gap between verdict and rank

| Row | TA verdict | composite | What happens |
|---|---|---|---|
| TA=90, FA=10, liq=10 | **LONG** | 54.0 | LONG with a composite of 54 — below any NEUTRAL |
| TA=69, FA=10, liq=10 | **LONG** | 42.5 | same problem, worse |
| TA=67, FA=100, liq=100 | NEUTRAL | 81.8 | best fundamentals+liquidity, verdict "don't buy" |
| TA=50, FA=100, liq=100 | EXIT | 72.5 | EXIT with a composite above the LONG bar |

The desk (`/api/portfolio/allocate`, `auto_tick`) sorts **LONG** rows by **composite**: the
admission criterion is one measure and the ordering is another. The 81.8 row is never a candidate
because its TA is 67, and the 42.5 row becomes a candidate and sits first in the queue.

This is not a typed bug; it is the consequence of `direction` never having been wired to the
composite in ADR-0007. The project itself built the `D_comp` arm (ADR-0051) to measure exactly
this — so it has seen the problem, and seen it correctly.

---

## 8. Layer 4 — trade geometry, risk, journal

### 8-1. Risk

```
stop = entry − k·ATR      k = NBX_RISK_K, default 2.0, bounds [1, 5]
tp1  = entry + 1.2·stopDist
tp2  = entry + 2.0·stopDist        ⇒ RR = 2.0
size = (equity × risk_pct/100) / stopDist
```

Caps are **enforced**, not merely warned: single-asset 25%, heat 6%, risk 2%, daily stop −3%,
weekly −6%, three consecutive stops. The ten `preflight` gates are shared with
`/api/risk/whatif` — the preview cannot promise something the broker rejects. That design is
correct.

### 8-2. Most important finding: stop geometry is horizon-blind

`simulate_long` replays the live geometry: half off at TP1, stop to breakeven with a +0.2% trail,
the rest at TP2/trail/horizon end. Horizons: H1=1h, H2=24h, H3=7d.

But `stopDist = k·ATR(15m)` — a fifteen-minute stop distance is used for the 24-hour horizon too.
ATR(15m) in a calm market is ≈0.6% of price, so the stop is ≈1.2%, while the normal 24-hour move
of a coin is several times that. Naturally **89% of H2 trades close at the stop**.

The project found this on 6 November (93%) and confirmed it on 7 November (89% over the whole
period 89%), calling it "the only stable finding of the two reports". I confirm both the number
and the sentence: **this is a geometry bug, not market bad luck.** The fix is cheap: align `k` or
the ATR with the horizon (`ATR(1h)` for H2, or `k=3` on 15m) while shrinking size to keep risk
constant — exactly what `NBX_RISK_K` and the shadow arms were built to test.

### 8-3. Fees (ADR-0052, new)

`FEE_PCT = 0.002` per leg, **without a verified account tier**
(`FEE_ACCOUNT_TIER_VERIFIED = False`). This release adds `return_net_pct`/`r_net` and leaves
historical rows NULL. A 0.4% round trip against a 1.2% stop is **0.33R of fixed cost per trade**;
at a 50% win rate that eats a third of the edge. It is labelled honestly; it now needs to be
visible in the decision — and it is.

---

## 9. Layer 5 — backtesting and measurement

- **The backtest measures TA only** (`BACKTEST_COMPONENTS = ("ta",)` and `matches_live: false`
  in every report). Honest and correct, because fundamentals and liquidity have no historical
  series. But it means **no backtest has ever measured the live strategy** — only a fifth of it,
  and precisely the fifth whose IC has been zero or negative.
- Walk-forward uses fixed windows (tb=288, eb=144) with parameter selection on train.
  `start += eb`, so evaluation windows are contiguous and leak-free ✔ — but the training windows
  overlap almost entirely, making it "one fit, repeated" rather than independent re-testing.
- Intrabar ambiguity is resolved pessimistically in favour of the stop ✔, gaps fill at
  `min(open, stop)` ✔, slippage 6bps and a promotion gate that refuses results without a cost
  model ✔. This part is professional.

**Gates that do nothing today:** `GateLimits` defaults to `max_spread_bps=None` and
`min_depth_usd=None` and is never constructed with other values in production (only in tests).
So the spread and depth gates always return `ok`, and 30% of the "is this symbol tradeable"
decision is inert. Only `max_age_bars=2` actually works. The code says so itself; the README
counts it as "ten risk gates". The difference between a *measured* gate and an *existing* gate
needs to be stated.

---

## 10. Strengths

| Topic | Evidence |
|---|---|
| **The three-state contract is implemented in code** | `insufficient_data`, `no-book`, `profile: none`, `stale-cache`, `degraded`. The "—" and the neutral 50 are gone. |
| **Causal caching** | `ta_score_series` is tested bar-by-bar against `ta_score`; without it the backtest would be O(n²) and leaky. |
| **"Measure first, apply later"** | Three real shadow layers (regime, gates, A/B arms) with code and tests, not slogans. |
| **Honesty in the analysis reports** | The 7 Nov report effectively retracts the 6 Nov finding: "reweighting on yesterday's report would have been wrong." |
| **A security boundary that was measured** | loopback-only plus "trusted proxy ⇒ token mandatory", documented with numbers rather than assumptions. |
| **Execution geometry** | Shared preflight, conservative stops, fees and slippage, realistic risk simulation. |
| **Python↔JS parity** | `check_preflight_parity` (28×10), `js_broker_test`, `test_tick_parity`, `js_engine_parity`. |
| **Zero marked debt** | `TODO/FIXME/HACK` in the code: **0**. |

---

## 11. Unified findings (ordered by impact)

| # | Finding | Severity | Evidence | Fix |
|---|---|---|---|---|
| 1 | **Verdict from TA, rank from composite** | high | TA=90→LONG with composite 54; TA=67→NEUTRAL with 81.8 (§7-1) | One rule: threshold on composite, or rank by TA too. The `D_comp` arm answers it. |
| 2 | **Stop geometry is horizon-blind** | high | 89% stops at H2 (§8-2) | Horizon-aligned `k`/ATR plus proportional size. `NBX_RISK_K` and the arms are ready. |
| 3 | **CI on main is red** | high | mypy: 4 errors in 2 files (§3-1) | Fix four lines + run `mypy` before merge. |
| 4 | **The 15m threshold disagrees with the project's own model** | high | 75 vs 68 (§5-4) | Apply `adaptive` with evidence, or surface the LONG rate in the UI. |
| 5 | **Executable bits are not in Git** | high | zero `100755` files; 2 tests fail (§3-2) | `git update-index --chmod=+x` + a test that checks `git ls-files -s`. |
| 6 | **Three of four components cannot make a verdict** | medium | component ceiling table (§5-2) | If multi-factor confirmation is the goal: a "2 of 4" gate or a soft threshold. |
| 7 | **Fallback profiles are hand-written numbers** | medium | `FALLBACK_FA_PROFILES`, `as_of: unversioned` (§6-2) | Zero weight when data is missing, or a real `as_of`. |
| 8 | **Spread/depth gates are inert yet counted** | medium | `GateLimits()` with `None` (§9) | Measure the distribution, or document them as disabled. |
| 9 | **Documentation lags the code** | medium | 20 vs 52 ADRs; `server/README` describes files that do not exist (§3-4) | The ADR-0014 rule — applied to prose. |
| 10 | **`index.html` is 600 KB with 462 KB inline JS** | medium | largest block 406 KB (§2) | Minify in the build; split the blocks. |
| 11 | **Floating-version CDN without SRI** | medium | `lucide@latest`, `lightweight-charts@4.2.0` | Vendor locally like Tailwind and the icons + add `script-src` to the CSP. |
| 12 | **CSP is `frame-ancestors` only** | medium | `server/main.py:689` | `script-src` with nonce/hash, `object-src 'none'`. |
| 13 | **A 20% weight with a 5% effect** | low | band 62–86 for $1k–$1B (§6-1) | Steeper slope (`8·log10`) or an honest weight. |
| 14 | **`main.py` is 5,966 lines, one function 219** | low | §2 | Split into `routes/` after test coverage exists. |
| 15 | **`--workers 1` caps scale** | low | `deploy/nobi.service:51` | A conscious decision; document it. |
| 16 | `VETO_CACHE` dead; JS still has a literal `2×ATR` | low | zero readers; zero `NBX_RISK_K` references in JS | Delete; parameterise the bundle. |

---

## 12. Does it have an edge? — the honest answer

From the project's own data (`scripts/journal_diagnostic.py`, post-era):

| Measure | Whole period | Crash window (38h) | Calm window (25h) |
|---|---|---|---|
| Composite IC (1h) | +0.05 | 0.00 | +0.13 |
| 1h return: LONG vs EXIT | −0.17 / −0.02 | −0.35 / −0.13 | +0.24 / +0.04 |
| H1: R̄ | −0.17 | −0.22 | −0.10 |
| H2: stop share | 89% | 93% | 78% (n=9, meaningless) |

Three conclusions:

1. **The edge is at noise level.** A composite IC of +0.05 over a few weeks proves nothing, and it
   drops to zero in the crash window.
2. **The direction can be right and still not make money.** In the calm window LONG beat the
   universe (+0.24 vs 0.00) yet R̄ stayed negative (−0.10): the geometry ate it.
3. **With fees, even that +0.24% goes to zero** (0.4% round trip).

**So: at present there is no demonstrated edge — and the project knows it and says so.** The
problem is not the algorithm; the problem is that three large changes (regime, geometry, weights)
are all in shadow mode waiting for data, and while data is missing none of them is being tested.
That state is not stable and needs to end with a dated decision.

---

## 13. Recommended plan

### This week — "make the gate green again and fix the geometry"

1. **The four mypy errors** and **the exec bits** (§3-1, §3-2). Under a day; until the gate is
   green, every new failure is meaningless.
2. **Horizon-aligned stop geometry** (finding 2). The only finding repeated in both regimes and
   independent of the weights debate. Testable today with `--risk-k` against the existing
   `journal.db`; it needs no new data.
3. **Align the documentation**: `README.md:231`, `server/README.md`, the `nbx-core.js` header.

### This month — "choose one of the two measures"

4. **Unify verdict and rank** (finding 1). Today there are two measures and nobody knows which is
   "the strategy". The `D_comp` arm you built measures precisely this.
5. **Apply the 15m threshold with evidence**, or at least surface the LONG rate in the UI so that
   "quiet today" is not confused with "strict today".
6. **Vendor the two CDNs locally** and add `script-src` to the CSP.

### After that

7. Fees and n>30 days, then the weights debate. Split `main.py` only after test coverage exists.

### What I would not do

Reweight FA/TA on the 7 November report (you already concluded that would have been wrong), add
indicators (your own catalogue states plainly what is not there), or add ML.

---

## 14. Reproduction

```bash
git clone https://github.com/iman-taheri59/nobi && cd nobi
python -m venv .venv && .venv/bin/pip install -r server/requirements-dev.txt

ruff check server scripts      # → All checks passed
mypy                           # → 4 errors in 2 files
python -m pytest -q server/tests   # → 1851 passed, 2 failed
git ls-files -s | awk '$1=="100755"' | wc -l   # → 0

# the analysis engine: component ceilings, score distribution, adaptive threshold
cd server && ../.venv/bin/python - <<'PY'
import numpy as np, ta_engine as T, adaptive
def mk(c):
    rows=[{"t":i*900,"o":float(x),"h":float(x*1.002),"l":float(x*0.998),
           "c":float(x),"v":100.0} for i,x in enumerate(c)]
    return T.candles_to_df(rows)
rng=np.random.default_rng(11)
sc=np.array([T.ta_score(mk(100*np.exp(np.cumsum(rng.normal(0,0.0025,300)))))["score"]
             for _ in range(300)])
print("LONG share >=68:", (sc>=68).mean())          # ~0.13
print("median:", np.median(sc))                      # ~41.9
f=adaptive.timeframe_factor("15m")
print("15m bar:", adaptive.scale_threshold(68,f))    # 75.0
PY
```

---

## 15. Limits of this review

- **No live run against Nobitex** was performed (unreachable from this sandbox); behaviour on real
  data is quoted from the project's own reports.
- **`journal.db` is not in the repository** — IC and R̄ figures are quoted from `docs/analysis/`
  and were not recomputed.
- **Test coverage was not measured** (`pytest-cov` is not installed); 1,853 tests is *volume*, not
  *coverage*.
- **Scan speed/cost** for 40 symbols × FA was not measured.
- **Security** was reviewed at the code and configuration level, not with a penetration test.
