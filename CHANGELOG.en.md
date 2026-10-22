# nobitrader changes

[فارسی](CHANGELOG.md) · **English**

## 1.0.20

- **The same rule, on the largest set of numbers on the page.** 1.0.18 reported candle age
  for the threshold card, but the backtest runs on the same candles: a walk-forward whose
  newest candle is three days old describes a window that ended three days ago, not today.
  Both backtest routes (`/api/backtest/run` and `/api/backtest/quick`) now return a
  `freshness` block, and the result card gains a chip that appears only when the data is old.
- **The chip carries the age in it** (title: "the newest candle is 41.2 bars old; this report
  describes that window, not today") — not a generic warning users learn to ignore. On
  current data the chip hides itself.
- **One helper, three outcomes, no exceptions:** `freshness_block()` feeds the adaptive routes
  as well as the backtest routes and turns a failed age check into a stale block with a named
  code, rather than an error that takes the healthy numbers down with it. `bad-timeframe` and
  an unusable timestamp both have tests.
- **Tests:** 3 Python tests (the block on both backtest routes, the helper's non-throwing
  degradation, the chip's three states) + 1 jsdom scenario with three assertions. Two
  deliberate mutations (a chip that never hides, dropping the age from the title) turned a
  test red.

## 1.0.19

- **One word, made plain (Persian docs).** "مصفوفه" became **"جدول ترکیبی (مصفوفه)"** — a
  table whose rows are symbols and whose columns are timeframes, with one independent run per
  cell. The English identifier (`matrix`) is untouched in code, because the code is English;
  what changed is the Persian prose, which should be readable without a glossary.
- `docs/evidence.md` gained a "one word" paragraph saying exactly that, plus why the table is
  needed: a single "favours the higher bar" on one cell is an anecdote; evidence is the same
  pattern repeating across symbols and timeframes.
- No number, verdict or computed behaviour changed — this release is naming only.

## 1.0.18

- **A second kind of staleness, with its own words: "stale data".** Until today the card only
  knew that the *pair* had changed. But data can go stale independently: a percentile computed
  on a series whose newest candle is three days old is a claim about three days ago wearing a
  present-tense sentence. `card.dataset.dataStale` is now separate from `card.dataset.stale`,
  because the two need **different actions**: "re-select / re-fetch" versus "refresh the data".
- **`server/freshness.py` — measured in bars, not seconds.** "Stale" means different things at
  15m and 4h, and a fixed seconds threshold would call every 4h series stale and every 15m
  series fresh. The tolerance is 2 bars, and the reason is written down: the newest candle is
  the *open* one.
- **A named reason, not a boolean:** `behind` / `ahead-of-clock` / `no-candles`. A candle from
  the future has no trustworthy age and must not read as "very fresh"; an empty series is a
  result, not an exception.
- **In the API:** both routes (`thresholds` and `evidence`) attach a `freshness` block, and a
  failure to assess does not take the numbers down with it
  (`{"stale": true, "reason": code}`).
- **In the dashboard:** the chip switches to "stale data" and the sentence carries **the
  arithmetic** ("the feed is 41.2 bars behind"), alongside "not applied". When the pair has
  moved too, the pair sentence comes first and the data staleness is mentioned with it. A
  response without a `freshness` block (an older backend) is treated as neither stale nor
  fresh — the chip simply stays in its normal state.
- **Tests:** 12 Python tests (`test_freshness.py`: the same age is fresh at 4h and stale at
  15m, the boundary is inclusive, a future candle, an empty series, an unknown timeframe,
  `age_bars` agreeing with its own seconds, presence on both routes) + 3 wiring tests + 3
  jsdom scenarios. Two deliberate mutations (collapsing data staleness into pair staleness,
  dropping the number from the sentence) turned a test red.

## 1.0.17

- **An evidence matrix with provenance.** `scripts/evidence_matrix.py` runs the paired
  comparison across 4 symbols × 3 timeframes and writes one artefact
  (`docs/evidence/adaptive-evidence-YYYY-MM-DD.json`) that states where it came from:
  `label`, `promotable`, `applied: false`, and the generation timestamp.
- **The rule the tests enforce:** in a file stamped `promotable: false`, no cell may carry a
  decision-grade verdict — all must stay `inconclusive`. The word `PASS` is banned from these
  files too, because in this repository it belongs to the promotion gate.
- **A named refusal is a row; an unnamed exception is an exit code.** `insufficient-data`,
  `no-fx` and `bad-factor` appear in the table and the run still succeeds; an unnamed failure
  (a dropped socket, say) exits 1 so it cannot hide inside a table.
- **Today's recorded run:** 12 cells, source `REFERENCE_DATA` (Nobitex is unreachable from
  the sandbox), **12 `inconclusive`** because one arm had fewer than 30 out-of-sample trades.
  Which is to say: the method works end to end and the higher bar reduced trades everywhere,
  but there is no verdict about which bar is better — and that is the correct output.
- **Bilingual documentation:** `docs/evidence.md` + `docs/evidence.en.md` (added to the
  bilingual test's `PAIRS`) with the venue command and the list of things these numbers
  cannot prove.

## 1.0.16

- **ADR-0021's technical prerequisite landed: the direction threshold is now a parameter.**
  `ta_engine.direction_for`, `ta_score` and `ta_score_series` accept `long_min` and
  `neutral_min`, **defaulting to the previous constants** — so no behaviour changed and
  every existing path produces the same numbers to the digit. The reason: while the LONG
  label was welded to the constant, testing a lower bar was meaningless ("no difference"
  meant "we changed nothing").
- **The browser twin names the same two numbers:** `nbx-core.js` now has `LONG_SCORE_MIN`
  and `NEUTRAL_SCORE_MIN`, and `TA.signalScore(candles, opts)` accepts overrides; no magic
  number survives in the label line. Two new tests in `test_single_source_constants.py`
  compare the values on both sides and pin the parameter's existence.
- **`lower-bar-untestable` is retired.** `adaptive_evidence` measures both directions, and
  each arm labels signals with the same bar its filter uses — an arm whose signal says LONG
  at 58 while the backtester filters at 68 tests neither number.
- **New named refusal: `bad-factor`.** A factor outside `[0.5, 2.0]` is not a threshold
  experiment any more but a different strategy, and calling it evidence would launder the
  difference; crossed bars (which make NEUTRAL unreachable) are refused at the engine
  boundary with a `ValueError`.
- **One claim withdrawn.** The first assumption was "a lower bar can only add trades". A
  test refused it (34 → 31 on a real sample): the monotonicity holds for **labels**, not for
  the trade count, because the walk-forward re-optimises the stop/target grid on the train
  slice and a different bar makes a different parameter set win. The test moved to the level
  where the claim is true: `LONG` and `LONG ∪ NEUTRAL` do not shrink under a lower bar.
- **CLI:** `scripts/adaptive_report.py --evidence [--factor X]` prints the same comparison
  on the command line, under the same rule: a verdict with its sample size, and "not
  applied" at the end.

## 1.0.15

- **The next question, in code: "would a higher bar have been better out-of-sample?"**
  `server/adaptive_evidence.py` runs a **paired** comparison: identical windows, identical
  parameter grid, identical costs, and exactly **one** variable (`min_score` = 68 versus
  `round(68×factor)`). Only evaluation-window numbers are reported.
- **New route:** `GET /api/adaptive/evidence/{symbol}?tf=&source=&limit=&factor=` — always
  `applied: false` and `promotable: false`.
- **Two named refusals in place of a number:** `no-op` for a factor of 1.0 (two identical
  runs are theatre, not evidence), and `lower-bar-untestable` for a factor below 1 — because
  `ta_engine` derives the LONG label from the constant itself, a lower bar adds no trades,
  and "no difference" there means "we changed nothing". Making it testable needs a
  structural change, written down in ADR-0021.
- **The verdict is conservative:** fewer than 3 windows or 30 out-of-sample trades in either
  arm → `inconclusive`; and naming a winner requires ΔPF ≥ 0.10 **and** Δexpectancy ≥
  0.05R. A profit-factor gain bought with collapsed expectancy is not an improvement.
- **Reference data never certifies:** on `source=reference` the verdict is capped at
  `inconclusive` with the reason attached; the arithmetic verdict, when clear, is kept aside
  in `verdict_capped`.
- **In the dashboard:** an "A/B evidence only" line in the same card — window count **with
  the window geometry**, `PF base ← higher`, ΔPF/ΔR, and the verdict. No verdict is shown
  without its sample size, and when the pair (symbol/timeframe/feed) moves, the evidence line
  says it is stale rather than silently surviving.
- **ADR-0021 "proposed":** the five simultaneous conditions for any future application, plus
  the technical prerequisite for a lower bar. The status is deliberately "proposed" and no
  path writes `LONG_SCORE_MIN`; `test_nothing_is_ever_applied_and_no_promotion_is_offered`
  keeps it that way.
- **Tests:** 9 new Python tests (`test_adaptive_evidence.py`: paired windows, a higher bar
  never adds trades, both named refusals, thin-sample conservatism, the reference-feed cap,
  `applied=false` on every path, determinism) + 4 wiring tests in
  `test_adaptive_ui_wiring.py` + 4 executable jsdom scenarios (a verdict with its sample
  size, a named refusal, going stale with the pair, and the repetition of "not applied" and
  "reference data cannot certify"). Two deliberate mutations (dropping the sample size from
  the window line, removing the reference-feed caveat) turned a test red.

## 1.0.14

- **The threshold card can no longer show one pair's numbers under another pair's header.**
  A measurement belongs to a *pair* — symbol, timeframe, feed — and those three controls sit
  next to the card. The measured pair now travels with the numbers: the moment the selection
  moves, the card says **"stale"** and names the drift
  (`BTCUSDT/15m/reference → BTCUSDT/4h/reference`), then re-measures after 700 ms. The old
  numbers stay on screen — they are still true for their own pair — but they may not wear
  the current header.
- **This is the 1.0.13 rule again, only quieter:** a number from one pair under another
  pair's header is the same lie as an unlabelled proposal, just whispered.
- **Nothing is requested until someone asks for a measurement.** Changing the
  symbol/timeframe before the first measurement does nothing: the card is evidence, not a
  poller.
- **Tests:** three new tests in `server/tests/test_adaptive_ui_wiring.py` (the stale label,
  the debounce, silence before the first request, stale numbers preserved) and three
  executable scenarios in `scripts/test_adaptive_card.cjs` (jsdom: going stale, naming the
  drift, re-measuring with `tf=4h`, and the absence of an unasked request). Both deliberate
  mutations — neutering the staleness check, deleting the silence guard — turned a test red.

## 1.0.13

- **A "thresholds and regime" card in the backtest lab.** The 1.0.12 measurement is now
  visible where it is needed: ATR / price, the ATR percentile, the regime, the entry
  factor, `LONG`/`NEUTRAL` as *live ← proposed*, the LONG share, and the number of scored
  bars. The candle source is read from the control next to the backtest, so measurement
  and backtest cannot end up on two different feeds.
- **The "not applied" label is part of the design, not a footnote.** ADR-0020's fence is
  enforced in the interface too: every card carries a "measurement only" chip and a
  "not applied — evidence only" line. A panel that shows a better-looking number without
  saying so is the moment a measurement becomes a silent policy.
- **A refusal never becomes a number.** When Nobitex is unreachable, or a rial market has
  no toman rate, the cells clear to `—` and the reason is named (`refused: no-fx`) —
  never a zero.
- **Nothing is requested on boot:** one light request when the lab tab is opened or the
  button pressed. `GET /api/adaptive/thresholds/{symbol}` is unchanged.
- **Two new guards:** `scripts/test_adaptive_card.cjs` (executes the real inline block in
  jsdom against a stubbed API: cells filled, the not-applied label, a named refusal, no
  zeros, and the request path) wired into CI; and `server/tests/test_adaptive_ui_wiring.py`
  (8 tests: the ids exist, both languages define the keys, and the card survives into the
  generated `nobitex-mobile.html`).
- **One operational lesson:** while proving the guard bites, `git checkout -- index.html`
  erased the newly written card, because it was not committed yet. It was rebuilt from the
  same scripts — and the experiment did prove the guard: renaming one id turned a test red.

## 1.0.12

- **The CSS build is deterministic.** Tailwind v4 scans every text file in the project,
  Python modules and ADRs included. One docstring sentence ("the factor would invert
  the order") pulled `.invert` into `tailwind.css` and turned CI's "generated files are
  current" step red — the build had become dependent on the wording of documentation.
  With `source(none)` and three explicit `@source` lines the stylesheet is a function of
  the page and its two engines only (7.2 KB of junk utilities dropped).
  `test_the_stylesheet_scans_only_the_page_and_its_engines` guards that boundary.
- **Signal thresholds depend on the timeframe — and nobody could see it (ADR-0020).**
  `LONG_SCORE_MIN = 68` is one number for every symbol and every timeframe, so "few
  signals today" could mean three different things: a quiet market, a low median
  score, or an unreachable threshold. `server/adaptive.py` separates them: the current
  ATR percentile, the volatility regime (calm/normal/volatile), and **how often the
  fixed threshold actually fires**.
- **No decision changed.** Every report returns `applied: false`; the proposal is
  evidence, not a behaviour change. Actually applying it needs its own ADR.
- **Measured on real data (reference feed, 340 bars):** the same 68 makes **40%** of
  15-minute bars LONG, 19.4% on 1h, 21.2% on 4h and 17.3% on the daily. One constant
  for all four is an unintended policy.
- **The proposal is bounded and ordered:** the factor is clamped to `[0.85, 1.25]`, a
  shorter timeframe never gets a lower bar, and a factor that would push `NEUTRAL`
  above `LONG` is refused as `band-collapsed` rather than reported.
- **`GET /api/adaptive/thresholds/{symbol}`** with `tf`, `source=nobitex|reference` and
  `limit`; plus **`scripts/adaptive_report.py`**, which prints the four-timeframe table.
  Named refusals: `insufficient-data`, `flat-market`, `bad-timeframe`, `bad-candles`,
  `band-collapsed`.
- **Fixes a real defect in the reference feed (1.0.10):** KuCoin's candle endpoint takes
  a **window**, not a count — a request for 700 bars quietly returned 100 and nobody
  noticed. It now pages (`startAt`/`endAt`, capped at `MAX_PAGES`), de-duplicates
  overlapping pages, and reports `requested`, `pages` and **`truncated`** in its
  metadata: a short series says it is short.
- 27 new tests: 24 in `server/tests/test_adaptive.py` and 3 paging tests in
  `server/tests/test_reference_feed.py`.

## 1.0.11

- **One page, one artifact (ADR-0019).** `nobitex-mobile.html` is not a second
  version: `scripts/build_standalone.py` builds it from `index.html` + 5 stylesheets +
  `nbx-core.js` + `nbx-app.js`, and all it does is inline. Undo that inlining and the
  two files are identical — there is **one responsive page**, not two versions in
  conflict. The artifact exists for three contexts that block subresources: a phone's
  `file://`, a preview iframe without network, and an air-gapped host.
- **New guard `server/tests/test_single_page.py`** (5 tests, no node needed): artifact
  equality after de-inlining, byte equality of the inlined CSS against the five source
  files, equality of the inlined JS against both engines, absence of mobile-only
  markup, and the online-logo call staying alive. A deliberate hand edit to the
  artifact turned two of them red.
- **A misdiagnosis corrected:** the dashboard does not orphan `setCoinMetadata`. The
  definition is in `index.html`; the call is in `nbx-app.js` (`loadCoinMetadata()`
  after `NBX.boot()` and hourly). Both icon layers are live — 40 bundled SVGs and
  CoinGecko logos from the server's disk cache — and the ten coins without a bundled
  SVG (TON, SHIB, NEAR, AXS, EGLD, HBAR, APT, ARB, OP, PEPE) are covered by the second.
  The new test stops that call from disappearing silently.

## 1.0.10

- **The dependency lock is repaired:** `package-lock.json` pinned an `@types/estree`
  version that was never published, so every `npm ci` in CI died with a 404 — which is
  what turned the `js` and `ui` jobs red. Pinned back to the published `1.0.9`.
- **A reference feed, so development outside Iran is possible again.** `apiv2.nobitex.ir`
  is unreachable (timeout), so `server/reference_feed.py` pulls candles from another public
  exchange. **KuCoin** is the default because it answers from this network; Binance returns
  **451** — the same problem under a different name. Routes: `GET /api/reference/status`,
  `GET /api/reference/candles/{symbol}`, `GET /api/reference/cross-check`.
- **A reference is not a substitute.** Every report from this path carries
  `source="reference"` and the gate returns a new `REFERENCE_DATA` state that can never be
  `PASS`; the arithmetic verdict is reported under `would_be`. A strategy validated on
  another venue's prices has not been validated on ours.
- **A rial market is derived, and only with an explicit rate.** `BTCIRT` has no USD
  counterpart; a series exists only via `toman_per_usdt` or `NBX_TOMAN_PER_USDT`, flagged
  `derived: true` and `quote_unit: "rial"`. Toman→rial goes through `RIAL_PER_TOMAN` so it
  matches the rest of the repository, and the "rial premium is zero" assumption is stated.
- **`POST /api/backtest/run` accepts a candle source:** `candles_source:
  "nobitex"|"reference"` (Nobitex by default). The response now separates `candles_source`,
  `reference` (provider/pair/derived metadata) and `upstream` — previously
  `provenance().source` overwrote the `source` key, losing the boundary between "where the
  candles came from" and "is the exchange alive".
- **`GET /api/reference/cross-check`:** venue price against reference price with `gap_pct`
  and an `aligned|wide|divergent` verdict — it surfaces a stale or mis-scaled feed, not an
  arbitrage.
- **Dashboard:** a "candles" selector in the backtest config, a violet "reference data" chip
  for `REFERENCE_DATA`, and the source named in the metadata line.
- **ADR-0014** (data honesty contract) amended in both languages: the network-access table,
  the four fences, and the two permitted uses.
- 14 new tests in `server/tests/test_reference_feed.py` over a mock transport — symbol and
  timeframe mapping, ascending order, **KuCoin's field order** (`o, c, h, l`, not
  `o, h, l, c`), refusal of a rial market without a rate, the rial scaling, the gate state,
  and the routes.

## 1.0.9

- **`npm run lint` was red and nobody had noticed:** `scripts/ui_smoke_test.js` carried a
  useless escape (`\"`) that eslint rejects. This release introduced it, so it is fixed
  here. The reason it stayed hidden is worth writing down: gate output was read through
  `tail -1`, and the trailing blank line hid the failure. Every gate now reports its own
  exit code.
- **The browser engine answered the intrabar question the opposite way from the server.**
  ADR-0009 §3 resolves intrabar ambiguity pessimistically (the path is unknowable, so the
  stop came first). Python did that; in `nbx-core.js` the test
  `if (tr.tp1 && c.h >= tr.tp1)` came **before** the stop check, so any candle that touched
  both levels was booked as a win. That is the one bias a backtest must never have — and it
  lived in the engine that produces the phone's offline numbers.
- **One rule now:** stop first, exit labelled `sl-ambiguous`, and `ambiguous_exits` counted.
- **`ambiguous_share` in both engines** (per window and in the aggregate) and **a ninth gate
  check:** `ambiguous_share_le_10`. If the assumption decides more than 10 % of exits, the
  expectancy describes the assumption as much as the market. On our own fixture that share is
  **50.9 %**.
- **Dashboard:** "ambiguous 50.9 %" in the gate bar, plus a Persian label for the new check.
- **Two-engine test:** `scripts/js_engine_parity.js` feeds one candle (touching both levels)
  to each engine and `server/tests/test_intrabar_parity.py` compares the answers; both say
  `sl-ambiguous`. To make the comparison possible the browser engine gained a `_signals`
  hook — the equivalent of Python's `signals=`.
- **ADR-0009 third amendment** (Persian + English), and the new check wired into CI and
  `npm run test:intrabar`.
- 5 new tests in `server/tests/test_intrabar_parity.py` and 2 in `scripts/js_broker_test.js`.

## 1.0.8

- **The backtest now says what it measured.** The live decision (ADR-0007) blends three
  components — technical 0.55, fundamentals 0.25, liquidity 0.20 — and vetoes can then pull
  the score below the promotion threshold. The backtest simulator replays the **technical**
  component only, because fundamentals and liquidity have no history in this repository.
  Until 1.0.7 the report left all of that unsaid and read "PF 1.42, gate: PASS" while having
  measured a quarter of the live rule.
- **`decision_basis` in every report and every gate verdict:** `components`, `weights`,
  `vetoes`, `live_weights`, `matches_live`. The config hash includes `basis`, so a report
  cannot drift away from the rule set it tested.
- **A `BASIS_CAVEAT` sentence is appended to the gate message** — after the reason, never
  instead of it. A `PASS` here means "the technical rules passed", not "the live composite
  passed".
- **Dashboard:** an amber "TA-only" chip beside the OOS chip, with the live weights in its
  tooltip.
- **The browser simulator** makes the same declaration and appends the same sentence; silence
  in an offline report made it look complete.
- **ADR-0009 second amendment** (Persian + English): why this was done, why — unlike
  `costs_modeled` — it does not reject the verdict (it is a scope of validity, not a
  numerical error), and what closing the gap requires (storing historical liquidity and
  fundamentals alongside candles).
- 9 new tests in `server/tests/test_backtest_basis.py` and one in `scripts/js_broker_test.js`,
  including one that keeps the declared live weights equal to the engine's `TA_W/FA_W/LIQ_W`.

## 1.0.7

- **Backtests no longer run cost-free.** `slippage_bps` defaulted to zero in three
  places — the `EventBacktester` constructor, `run_and_save`, and the body of
  `POST /api/backtest/run` (`b.get("slippage_bps", 0.0)`) — while `SLIP_BPS = 6` sat
  defined in the same file and read by nothing. Every default report, and every
  ADR-0009 gate verdict built on one, therefore described a market where orders fill
  at the exact price. The default is now 6 bps on every path: the simulator,
  `run_and_save`, `grid_search` and the browser simulator.
- **The gate gained an eighth check: `costs_modeled`.** A `slippage_bps=0` run is
  still allowed (to separate the effect of cost from the effect of the strategy) but
  it can no longer reach `PASS`; it is named in `failed` and the gate message says
  why. A frictionless run is optimistic, not conservative.
- **The report carries its cost model:** `costs: {slippage_bps, fee_pct, modeled}` in
  the report and in the file on disk, `slippage_bps` in the route response, and the
  config hash moves when the cost model moves — two runs that differ only in cost are
  two different experiments.
- **Dashboard:** a "slippage (bps)" input defaulting to 6 in the backtest config, the
  cost model shown in the gate bar (`6 bps`, or "no slippage"), and a Persian label
  for the new gate check.
- **The browser simulator now matches the server:** the same 6 bps, the fee from
  `PAPER_FEE_PCT` (instead of a second copy), and targets read from the incoming
  `tp1_r`/`tp2_r` — previously `1.5/2.5` were hard-coded and the operator's input was
  ignored.
- **ADR-0009 amended** (Persian + English): the "costs modelled" clause is now
  enforced, with a before/after table and the operating procedure at 0 and 25 bps.
- **README:** the current tag was corrected from `v1.0.4` to `v1.0.6` — it had been
  stale since the 1.0.5 release.
- 12 new tests in `server/tests/test_backtest_costs.py` and one in
  `scripts/js_broker_test.js`.

## 1.0.6

- The live take-profit ladder is no longer hidden: `GET /api/risk/state` publishes
  `caps.rrLevels` — the same rule we set for `minRR`, because a number a page warns
  about must come from the engine, not from a local copy.
- `NBX_RR_LEVELS` (e.g. `1.5,2.5`) lets an operator hand the backtest's winning ladder
  to the live plan deliberately. An unusable value (fewer than two levels,
  non-increasing, zero/negative or `nan`) is refused, the service keeps the documented
  default `1.2,2.0` and says so in the log; a wrong ladder is a wrong trade.
- In the no-backend case the browser broker now derives its default targets from that
  same ladder and the stop distance (`1.2R`, `2.0R`); it used to use three percentages
  (`1.045`/`1.075`) that landed near those ratios by luck.
- An ADR-0008 amendment (Persian and English) records why the live ladder and the
  backtest ladder are deliberately not the same, how to connect them operationally, and
  why automating that transfer is overfitting one step removed.
- Added `docs/rr-ladder-tuning.md` (Persian) and its English twin: how to tell whether one
  ladder really is better — the standard-error arithmetic (with 100 trades a gap under
  0.3R is noise), the data horizon per timeframe (2000-candle cap: `4h` ≈ 333 days,
  `15m` ≈ 21 days), the three fields that must be read together (`gate.state`,
  `param_stability`, the out-of-sample `aggregate`), the cross-market agreement rule,
  "a plateau, not a peak", and the revert rule to write before changing anything.
- Three new tests in `server/tests/test_single_source_constants.py` (the parser, reading
  it from the environment, and publishing it in `caps`) plus one in
  `scripts/js_broker_test.js` asserting the fallback targets are exactly `1.2R` and
  `2.0R` on both sides.

## 1.0.5

- Trading fees have one home: `server/fees.py`. The `0.002` was typed into
  `paper_trading.py`, `backtest.py`, `portfolio.py`'s default, the browser bundle
  (`PAPER_FEE_PCT`) and `scripts/check_preflight_parity.py` — five copies with nothing
  measuring that they agreed. One rate for maker and taker is deliberate: the
  exchange's current schedule is not verified in this repository, and inventing a
  split would be the fabricated number ADR-0014 forbids.
- The paper account's starting equity is aligned: `deploy/nobi.env.example` and
  `scripts/deploy_arvan.sh` shipped `10000` while the code, `server/.env.example` and
  the Windows guide said `100000`, so a fresh ArvanCloud install sized every position
  ten times smaller. All of them now come from one default
  (`fees.DEFAULT_EQUITY_USD`); existing installs are untouched.
- The dead `low-rr` gate now works: `_tp` hard-coded the take-profit levels, so the
  ratio was always exactly 2.0 and `MIN_RR=1.5` could never be crossed. The levels
  moved to `RiskConfig.rr_levels` (same default) so the warning can actually fire.
- New gate `server/tests/test_single_source_constants.py` (5 tests): one fee across
  broker/backtester/allocator, Python and JS agreeing on the fee, no new `0.002`
  copy, one starting equity across every install sample, and a `low-rr` warning that
  is reachable.
- Two gaps deliberately left open, recorded in
  `docs/reviews/2026-10-21-shared-constants-fa.md`: nothing feeds the backtest's best
  RR (`1.5/2.5`) into the live plan (`1.2/2.0`), and the browser broker falls back to
  percentage-based targets when there is no backend.

## 1.0.4

- Documentation is bilingual where it matters. The root README, the documentation map,
  the release guide and the changelog now have English twins (`README.en.md`,
  `docs/README.en.md`, `docs/releases.en.md`, `CHANGELOG.en.md`) with a language
  switcher at the top of each pair.
- ADR-0017 ("testing and review from inside the dashboard") had no English text; it now
  does (`docs/adr/en/ADR-0017-testing-and-review-inside-the-dashboard.md`).
- The English ADR-0013 existed twice: a full translation plus a second file that held
  only the 2026-10-20 amendment. The amendment was appended to the full translation and
  the duplicate removed, matching the Persian file where the amendment already lived in
  the same document.
- A new gate, `server/tests/test_docs_bilingual.py`, fails when an ADR number has two
  files in one language (the 0013 duplicate could not happen silently again), when a
  documented pair is missing, when a pair does not link to its other language, or when
  either side of a release-facing document stops carrying the current version number.
- The three guides an English-speaking operator actually needs are translated too:
  `docs/testing.en.md` (measuring against a machine that reaches Nobitex, the 45-panel
  probe, the browser fallbacks), `docs/coin-metadata.en.md` (where specifications come
  from, the logo cache, the security ceilings) and `docs/financial-display.en.md` (the
  display-unit contract). The stale "321 passed" in the testing guide is now 591.
- The documentation map now lists a bilingual coverage table: which documents exist in
  both languages and which are Persian-only today ("pending"), instead of leaving it to
  guesswork.

## 1.0.3

- The `ui smoke — jsdom` gate no longer pins the number of secret cards to 5. Adding
  `NOBITEX_API_SECRET` in 1.0.2 took the count to 6 and CI on `main` had been red ever
  since, while the page was healthy: all six cards rendered and all of them named the
  file they are read from. The gate now checks a floor of five cards, and the real
  condition — the number of cards equals the number of cards that state where their
  value is read from.
- The websocket status poll in `nbx-app.js` no longer throws when `NBX` itself is
  missing: the old guard was `!NBX.core`, which is a `TypeError` when `NBX` is
  undefined, thrown from inside a timer — nothing the page owns can catch it. If
  `nbx-core.js` failed to load, the page died on an uncaught exception (and the smoke
  test, which deliberately blanks `NBX`, failed spuriously about one run in three).
- The smoke harness is no longer killable by asynchronous noise: an exception or an
  unhandled rejection exits with code 2 and the message `harness crash`, and the jsdom
  window is closed before exiting.
- Release publishing converges on an existing release (`gh release edit` plus
  `gh release upload --clobber`) instead of failing on "already exists", and both paths
  verify that the tag points at the checked-out commit.

## 1.0.2

- Fixed the startup failure on a deployment: output directories (`candle_cache` and
  `backtest_reports`) are no longer created next to the source; the path comes from
  `NBX_CANDLE_CACHE` and `NBX_BT_DIR`, and with no writable path the process falls back
  to a temporary directory with a warning instead of a `PermissionError` at import time
  (with `ProtectSystem=strict` the service would not start). Shared logic lives in
  `server/storage.py`.
- Atomic paper-account writes: temporary file + `fsync` + `os.replace`, a `.bak` kept,
  recovery from the backup, and a damaged file quarantined instead of silently starting
  from zero; a `state_recovery` report appears in `/api/paper/metrics`.
- A per-address login rate cap on `/api/auth/login`, `/api/auth/register` and own
  password change (`NBX_LOGIN_RATE_LIMIT`, default 20 per minute) so that rotating
  usernames cannot make scrypt cost unbounded.
- Building the inline page moved out of the request path: `GET /` no longer runs
  `npm`/`build_standalone.py`; `NBX_AUTOBUILD_PAGE=1` builds once at startup and
  otherwise the last valid build is served with a warning.
- Regression tests in `server/tests/test_deploy_hardening.py` for the four items above
  (12 tests).

## 1.0.1

- Added the annotated tagging flow and GitHub Release publishing with changelog notes
  and the standalone mobile file.
- Product naming unified as nobitrader in the documentation, keeping the repository URL
  and the real deployment paths.
