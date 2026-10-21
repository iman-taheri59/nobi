# nobitrader changes

[فارسی](CHANGELOG.md) · **English**

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
