# nobitrader changes

[فارسی](CHANGELOG.md) · **English**

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
