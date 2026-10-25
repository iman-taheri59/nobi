# nobitrader changes

[فارسی](CHANGELOG.md) · **English**

## 1.6.2

> Complete the cross-platform CI repair with authenticated browser fixtures and fully closed SQLite test handles.

- Browser CI creates and signs in a short-lived fixture owner instead of inspecting an uninitialized dashboard behind the setup dialog.
- Every SQLite connection in backup tests and the empty-snapshot path is explicitly closed, allowing Windows replace/unlink semantics to pass.

## 1.6.1

> CI portability follow-up for the 1.6.0 release.

- SQLite backup and restore now explicitly close every connection, preventing Windows from retaining locks on temporary database snapshots.
- POSIX permission-bit assertions run only where POSIX modes exist; Windows still verifies token secrecy and allowlisted content.
- The browser panel probe can inspect the dashboard behind the intentional first-run owner dialog without bypassing any API authorization.

## 1.6.0

> Signal Intelligence v1 with real metrics, funnel, cohorts, detail and review.

- A new all-role tab reports summary, win rate, expectancy/average R, return and MFE/MAE.
- Judged/data-ready/gate-passed/generated/resolved funnel and real gate reasons were added.
- Cohorts group by symbol/timeframe/regime/config hash with day/horizon/symbol/TF filters.
- Detail exposes feature snapshots, contributions and H1/H2/H3 outcomes.
- Review/tag requires operator/owner+CSRF, fixed tags and central audit; viewers read only.
- Sample sufficiency below 30 is explicit and no synthetic metric/conversion is generated.
- Hardening makes resolved/unresolved/void mutually exclusive, names component scores honestly, exposes dedicated account capabilities, validates review bodies strictly, enables journal foreign keys, and persists all Docker state on one non-root data volume.

## 1.5.1

> My Account dashboard for viewer/operator/owner.

- Profile, role, state, creation and last-login details are visible.
- Backend-computed capabilities, read-only live policy and daily usage are shown.
- Personal session listing/revocation and logout-all require CSRF.
- Password change with CSRF and all-session invalidation is available in UI.
- Personal activity and allowlisted language/timezone/theme/default-page preferences were added.
- Public session IDs are double-hashed and personal endpoints accept no arbitrary user_id.

## 1.5.0

> Bounded `live_place` capability for selected operators with atomic financial limits.

- Owners grant/revoke per-order/day amount, daily count and symbol allowlists from Users.
- General operators still cannot place live orders; policy targets operators only and machine tokens are denied.
- Only limit orders are permitted; promotion, preflight, CSRF, confirmation and idempotency remain mandatory.
- Daily usage is atomically reserved under unique clientOrderId; identical retries do not consume twice.
- Authorized operators get the real-order form on the Trading Desk.
- Tests cover policy, limits, symbols, revocation, owner override and end-to-end operator submission.

## 1.4.4

> Full role review and explicit viewer/operator/owner/token matrix.

- Settings, cache internals and Telegram probe are owner-only; mutation requires CSRF.
- Operational audit, notification status and private live reads require operator/owner.
- `require_operator` models operational GETs without misusing a mutation dependency.
- Settings nav is owner-only and the audit panel operator/owner; backend enforces independently.
- Viewers retain market/paper/report reads; paper/backtest/journal operations remain operator-capable.
- Role-matrix and no-human-role-for-machine-token tests were added.

## 1.4.3

> Traceability and UX hardening after making troubleshooting owner-only.

- An explicit Owner-only chip was added to the tab header.
- Error self-test, diagnostics and devsuite execution now append central audit events.
- Audit stores only run id, gate names, check count and summary result; no output or secret.
- Tests pin CSRF on every troubleshooting mutation and self-test audit creation.

## 1.4.2

> The System Troubleshooting tab and all its data/controls are now owner-only.

- Troubleshooting navigation appears only for an owner session.
- Error logs, diagnostics and devsuite are independently owner-only in the backend.
- Run/clear/self-test mutations require owner+CSRF.
- Viewer/operators receive 403 and anonymous/bearer tokens receive 401.
- Automatic browser error ingestion remains; only owners can read or delete reports.

## 1.4.1

> Dedicated owner-only user management and activity tab.

- User role/state/session/invite management moved into its own tab.
- Append-only activity records login/logout/register and administrative actions.
- New owner-session-only `GET /api/auth/activity` endpoint.
- Bearer tokens are removed from user administration and mutations require CSRF.
- Viewer/operators cannot see the tab and independently receive API 403; no sensitive values are logged.

## 1.4.0

> Real placement endpoint added with owner/CSRF, promotion gate and explicit confirmation; not operationally enabled yet.

- `POST /api/live/place` works only for owners and only in confirmed live mode.
- The server enforces 30 dry runs over 5 days with zero ambiguous outcomes.
- Dry/live share preflight and reserve ledger/idempotency before network I/O.
- Ambiguous timeout is never blindly retried and requires reconciliation; responses contain no secret/raw request.
- Owner UI shows evidence and a real-order form disabled until the gate is ready.
- Tests pin the gate, exact confirmation and one mocked exchange submission.

## 1.3.3

> W3b: owner UI backup/restore and constrained systemd/nginx/restart management.

- Owner+CSRF API lists/creates backups, restores with exact confirmation, applies config and restarts.
- Archives are constrained to the managed directory/name, checksummed and SQLite-integrity checked, with no secrets.
- A separate root-owned helper exposes two schema-validated actions; no shell or free command/argument exists.
- systemd/nginx configuration is verified before reload and rolled back on failure.
- Audit is append-only mode 0600; helper execution has timeout and bounded output.
- A bilingual owner card exposes backup, restore, domain, apply and restart.

## 1.3.2

> W3 operations center: all Python/JS gates, build and deploy-check from UI, owner-only.

- ADR-0036 records the fixed allowlist and remote-shell refusal.
- Devsuite grows from 3 gates to 13 operations: pytest/ruff/mypy, two JS lints, broker, intrabar, display, adaptive, icons, preflight parity, build and deploy-check.
- Runners are fixed `python` or `npm` argv; no user command/argument and no `shell=True` exist.
- Status/run/result endpoints require an owner session; operator and API token are insufficient.
- Single-run locking, timeout, bounded output tail and isolated test databases remain.
- Four tests pin allowlist coverage, no metacharacters and role access.

## 1.3.1

> W2 web installation: owners probe and save non-secret data paths/settings without code.

- `install_api.py` accepts only allowlisted paths, scan, TTL and disabled/dry-run values; unknown keys and relative paths fail closed.
- `managed.env` is atomically written as 0600 and loads below real environment and `.env`; secrets are not accepted.
- Owner-only bilingual UI provides path fields, writable probe and save/restart feedback.
- Operators cannot even read installation paths; POST requires CSRF.
- Three tests cover owner probe/save, command/relative-path refusal and operator denial.

## 1.3.0

> W1 setup wizard: create the first owner in UI with a one-time console token.

- ADR-0035 records the three-stage wizard, installation settings and owner operations center.
- `/api/setup/status` reports setup need only; `/api/setup/claim` uses a strong token, rate limit and password policy to create owner and establish session/CSRF.
- The token lives in a 0600 file beside auth DB, is shown in the console and deleted after claim; it never returns through API/DB/audit.
- Before login the page checks setup status and shows a bilingual token/username/password/confirmation form; normal installation no longer needs CLI.
- Three tests cover token permissions/secrecy, owner session and wrong-token refusal.

## 1.2.7

> Soak operations pack: complete execution-ledger backup and secret-free daily snapshots.

- ADR-0034 defines backup format 2 and evidence CSV.
- `state_backup.py` now snapshots/restores journal, auth and live execution; legacy format 1 remains compatible and leaves the live destination untouched.
- Checksums and integrity for all three databases are verified before replacement.
- `live_evidence_snapshot.py` GETs health/status/evidence only and appends an aggregate CSV row; token comes only from the environment.
- Four new tests cover three-DB round-trip, legacy compatibility, secret-free CSV and no write on failure.

## 1.2.6

> B5: dry-run evidence for live promotion, with no automatic activation.

- ADR-0033 requires 30 samples over 5 days and zero ambiguous outcomes.
- The ledger reports state distribution, sample/day counts and refusal reasons.
- `/api/live/evidence` is private and remains `applied:false`.
- Two tests cover sample/day floors and the unknown-state veto.

## 1.2.5

> B4: dry-run placement only after real server preflight; no network submission or real-place route was added.

- ADR-0032 records mandatory preflight and responses without raw payloads.
- `POST /api/live/dry-run` requires `NBX_LIVE_MODE=dry-run` and `require_client`.
- Size and entry come from server `risk_whatif`; blocked gates fail before the ledger.
- IRT intent converts Toman entry to Rial at the order boundary; raw body and intent JSON are omitted from responses.
- Two route tests cover success and blocked preflight with no record.

## 1.2.4

> B3 routes: private list/cancel/reconcile controls are exposed; a real-placement route deliberately does not exist.

- ADR-0031 records the private-router contract and absence of `/api/live/place`.
- `server/live_api.py` protects status/orders/cancel/reconcile with `require_client`.
- Default mode is disabled and incomplete configuration fails closed; status reveals no credential or database path.
- The `httpx` sender transmits the exact signed raw body; transport failures expose only the exception class.
- Three tests cover OpenAPI, secret-free status and disabled-mode control refusal.

## 1.2.3

> First B3 slice: fail-closed observation and cancellation before exposing real placement routes.

- ADR-0030 requires exit/observation controls to precede entry routes.
- `list_orders` signs the full query path and sends a bodyless GET.
- `cancel` accepts only ledger-known IDs; timeout becomes `cancel-unknown` and is not sent again.
- Final cancellation becomes `canceled` or `cancel-rejected`; transport error text and secrets are not stored.
- Four tests cover listing, unknown IDs, no-retry timeout and durable cancellation.

## 1.2.2

> Stage B2: a recoverable execution client with dry-run and idempotency; no public route or signal-to-live wiring is added.

- ADR-0029 records unknown-state handling, no blind retry and `clientOrderId` reconciliation.
- `server/live_execution.py`: disabled/dry-run/live modes, mandatory live confirmation phrase and a durable SQLite ledger.
- Intent and raw body are recorded before networking; the same key/payload never sends twice and a changed payload fails closed.
- Timeout becomes `unknown`; repeating place does not call the network and only status reconciliation may resolve it.
- Seven tests cover the interlock, network-free dry-run, conflicts, unknown state and reconciliation.

## 1.2.1

> Live-trading stage B1: the Ed25519 signing boundary is implemented, but no route or real-order submission is enabled.

- Bilingual ADR-0028 records the signing contract, secret handling and separate live-client boundary.
- `server/nobitex_signing.py` creates the official `timestamp + METHOD + full_path + raw_body` payload and API-key headers without networking.
- The raw body is built once and those exact bytes must be signed and transmitted; the query string belongs to the signed path.
- Five tests cover real Ed25519 verification, payload mutation and secret-safe configuration failures.
- `cryptography==44.0.0` is pinned for signing; live remains disabled.

## 1.2.0

> Phased completion under ADR-0027: this is the stable analysis and paper-trading line; real trading remains off by default and belongs to phase 2.

- Bilingual ADR-0027 records stable-phase completion and the non-negotiable prerequisites for real trading.
- `docs/HANDOFF.md` and its English peer are living status, verification and next-action records.
- `scripts/state_backup.py` creates consistent online SQLite snapshots for journal/auth with a manifest and SHA-256, plus guarded atomic `--force` restore; environment files and keys are deliberately excluded.
- `scripts/fetch_history.py` adds paginated UDF ingestion, checkpoint/resume, deduplication, OHLCV-array validation and gap reporting; tests are network-free.
- Windows CI changes from advisory to a blocking release gate.
- Product version and release docs are aligned; adaptive gates remain shadow-only and `applied:false`.

## 1.1.4

> **A gate's threshold comes from the data, not from taste** (ADR-0026). This release is
> **measurement only**: no threshold is applied, no gate blocks anything, and `applied` stays `false`.

- **Why S2 was not enough.** Reviewing a real install produced two facts from the code: (1) `days` in
  the shadow ledger is derived from each verdict's `ts`, and `ts` is the close time of the bar it
  judged — so backfill would jump `days` from 2 to 5 without a day of market having passed, and if it
  stayed honest and excluded backfilled rows from the day count, the lock would not move at all.
  (2) `gates_block` calls `gates.evaluate(rows)` **without limits**, and `GateLimits` defaults
  `max_spread_bps`/`min_depth_usd` to `None` ("do not judge") — so `spread-wide` and `book-thin` can
  never fire and read zero forever. The bottleneck was not the sample size; it was **the absence of
  a number**.
- **The real distribution of the three gate inputs.** `gate_ledger.distributions()` reads the same
  ledger table and reports, for `spread_bps`, `depth_usd` and `age_bars`: `n`, `days`, median, `p05`,
  `p90`, `p95`, `min`, `max`, and `sufficient` (≥30 samples over ≥5 days). It ships in
  `/api/signals` under `gates.ledger.distribution` on the existing 30-second cache — one query, not
  one per row.
- **A proposal, named and labelled.** `max_spread_bps = p95(spread_bps)` and
  `min_depth_usd = p05(depth_usd)` — the gate would have removed roughly 5% of rows. Every proposal
  carries `applied: false`, `n`, `days` and its `basis`. When the sample is thin the proposal is
  **`None`** with a reason, not a number somebody copies into `.env`.
- **Two refusals, in the code itself:** rows with `source='backfilled'` are excluded from the
  distribution (a spread taken from reconstructed verdicts is not a measured spread), and percentiles
  are linearly interpolated rather than nearest-rank — with thirty samples a nearest-rank p95 *is*
  the maximum and puts the threshold on the single worst observation.
- **A distribution line in the gates chip's tooltip:** "spread median X and p95 Y bp over N samples ·
  depth p05 $Z", labelled "measurement only, no threshold applied", in both languages.
- **Thirteen new tests**, including one pinning that `GateLimits` defaults are still `None` and one
  pinning that `gates.py` never reads `propose_limits`.
- **And one defect found while testing live:** the shadow ledger stored `spread_bps`/`depth_usd`
  only inside the detail of a **failing** verdict. With the limits at `None` nothing failed, so every
  row was `ok`, its spread and depth columns were `NULL`, and the ADR-0026 distribution could never
  be built either. `gates.evaluate` now returns the measured inputs separately (`inputs`) and the
  ledger stores them; live run: `spread n=10, median 12 bp · depth n=10 · age n=16`.
- **Gates:** ruff 0 · mypy 0 (91 files) · pytest **890 passed** · eslint/dashboard/five JS gates · parity ·
  notes · build — all with exit code zero.

## 1.1.3

> **Fundamental coverage now counts honestly, the ID map is one map, and each coin's own Toman
> premium is measured in shadow.** No score, threshold, gate or ranking moved, and `applied` is still
> `false`.

- **An unmapped symbol is no longer "live".** `FAEngine.score()` wrote `profile: "live"` whenever the
  provider call had not failed; "no provider is mapped for this symbol" is never a failure, so an
  unmapped coin came out `live` and was counted in "live FA". Measured on this repository before the
  fix: `score 52.6 · fresh True · profile live` with `{'fear_greed': 51}` as its only evidence — a
  score built from five neutral 50s and one market-wide Fear&Greed value. There is now a third state,
  `profile: "none"`, `fresh` is `False` for it, and `/api/fa/{symbol}` answers
  `source: "no-provider"`. **The score did not change** — only the claim about it.
- **`fa_coverage` gained `unmapped`** so that `live + fallback + unmapped = total`. Note that the
  `live` number is **lower** than in 1.1.2, and that is correct: the old number was a lie.
- **The ID map went from 14 to 51 with no extra request.** The engine kept a private map of fourteen
  while `server/coin_registry.json` already held fifty verified IDs for coin icons. The engine now
  reads that file; the built-in literals remain only for the case where the file cannot be read. A
  test pins that every registry ID is present in the engine's map.
- **Dynamic symbol search at runtime was refused, with numbers:** 2,626 of 17,425 symbols (15%)
  match more than one coin on CoinGecko (`DOGS` matches four, `NOT` two) and the free tier answers
  429 after three calls in a row. Taking the first result is a guess, and a guess produces a
  fundamental score that gets read. New mappings are resolved offline with human confirmation
  (`scripts/resolve_fa.py` → `coin_registry.json` → commit).
- **Each coin's own Toman premium, as evidence.** Nobitex quotes a coin in two pairs; the ratio is
  that coin's premium: `(XIRT / XUSDT) / USDTIRT − 1`. Until today only the market-wide premium on
  the three BTC legs was computed and handed to everybody. Now `pair_premium` ships beside
  `/api/signals` and in `/api/gates/evidence`, with a median in basis points and counts for
  `pairs`/`outliers`/`missing_legs`; a premium above 5% is marked `outlier` and kept out of the
  median. **It takes no weight**; entering the score follows ADR-0021.
- **The fundamentals tab also drew six rows.** The same defect as the signals list (1.1.2), one tab
  over: an unnamed `.slice(0, 6)`. It now uses `SIG_ROW_LIMIT` and the same show-all control. And the
  message that said "the Nobitex server is unreachable" no longer accuses the exchange: "no
  fundamental provider is mapped for this coin" is now separate from "the provider did not answer".
- **Nineteen new tests** (13 + 6) pin all of it, including that the premium never reaches
  `composite`. One defect in the tests themselves was found and fixed too: `test_core` assigned its
  double to `main.FA_ENGINE.score` and never restored it, so every later test saw the fake answer.
- **Gates:** ruff 0 · mypy 0 (90 files) · pytest **874 passed** · eslint/dashboard/five JS gates ·
  parity · notes · build — all with exit code zero.

## 1.1.2

> Display only. No score, threshold, gate or ranking moved; `applied` is still `false`.

- **The signals list no longer stops at twelve rows.** `renderSignalsLive` carried an unnamed
  `.slice(0, 12)`: the strip above the list said "39 symbols scanned" while rows 13..39 had no row,
  no detail and no way to be seen — even though the engine had scored them and the shadow gates had
  already written verdicts about them. A reader could not tell a small scan from a short page.
- **The number has a name now:** `SIG_ROW_LIMIT = 15`, and below the list a **"show all (39)"**
  button brings the rest and turns into "show fewer". The total is printed on the button, because a
  control that only says "more" hides the same fact.
- **The sparklines follow.** Their candle prefetch was cut at the same twelve; it now uses
  `SIG_ROW_LIMIT` and, when "show all" is pressed, requests the newly visible rows too — a row with
  an empty chart is a half-drawn row.
- **Eight new tests** pin it: the size must be a named constant and not a literal, the renderer must
  not slice on a magic number, the hidden count must be computed and not guessed, the control must
  name the total, the prefetch must cover the rows it reveals, and both languages must be able to
  say it.
- **Gates:** ruff 0 · mypy 0 · pytest **855 passed** · eslint/dashboard/five JS gates · parity ·
  notes · build — all with exit code zero.

## 1.1.1

> Evidence must not need an open tab (ADR-0024). The content is **collection and display** only:
> no threshold, weight, risk gate or ranking moved, and `applied` is still `false`.

- **The server can scan by itself.** Until 1.1.0 the only trigger after startup was
  `GET /api/signals`, which only an open dashboard tab calls — so the ledger's `days` count (the
  binding constraint of the S2 decision in ADR-0023) depended on the habit of leaving a tab open.
  There is now a light, supervised loop that scans every `NBX_SCAN_LOOP_S` seconds; **default 0 =
  off**, because turning it on spends the ADR-0003 rate budget and that is the user's decision, not
  an upgrade's side effect. The floor is 60 s and a smaller value is **refused** (one error in the
  log, loop off, service healthy) — the rule ADR-0023 set for N.
- **The loop's state is visible:** `scan_loop` in `/api/health` (on/off, interval, last run, run
  count, success). A loop that died silently is worse than no loop, and `off` next to a rising `days`
  means a browser is still doing the work.
- **Two ages, not one.** `gates.inputs.oldest_book_s` is the age of the oldest book **in the cache**,
  including books dropped in the same pass for exceeding the TTL; on a real install it printed
  `499.6` while `BOOK_GATE_TTL_S = 300` — a number that contradicted itself. The new
  `oldest_usable_s` is the age of the oldest book that can actually judge a bar and is bounded by
  the TTL by construction. The old key was not removed (no consumer is broken), but it is no longer
  the number to quote.
- **The same six numbers, in the gates chip's tooltip.** `books`, `stale_books`, `oldest_usable_s`
  and `prefetch(ok/asked)` were API-only and needed a Python one-liner to see. They now live in the
  title of the "gates (shadow)" chip on the Signals tab. **No new chip, no new card** (ADR-0019).
- **The measurement tool is in the repository:** `server/tools/daily_snapshot.py` — one CSV row per
  run with `days`, `ready`, `books`/`stale_books`, the prefetch pass and the loop state. `GET` only,
  writes no secret, and when the server does not answer it **refuses to write a row**.
- **Eighteen new tests** pin all of it: off by default, an invalid value refused without taking the
  service down, a successful tick and a failing one, `oldest_usable_s <= BOOK_GATE_TTL_S` even with
  hours of stale books, two runs producing two rows under one header, and the tool issuing `GET`
  only.
- **Gates:** ruff 0 · mypy 0 (87 files) · pytest **847 passed** · eslint/dashboard/five JS gates ·
  parity · notes · build — all with exit code zero.

## 1.1.0

> **This release is a MINOR because the number ceiling forced it, not because behaviour changed.**
> `1.0.30` was the last PATCH on the `1.0.x` line (`docs/releases.md`). The content here is PATCH
> work — two test fixes and one new guard — and no trading behaviour moved.

- **The tests no longer depend on the network.** Three tests failed on a machine that can reach
  Nobitex and were green on CI, which cannot: `test_js_parity::test_both_brokers…` and two
  `test_paper_order_route` tests that expect a below-minimum order to be refused. Cause: two other
  tests start the app with `with TestClient(main.app)`; the lifespan reads `/v2/options` and
  `units.apply_options()` writes the exchange's **live** minimums (100,000 Rial / 1 USDT) over the
  **documented** ones (3,000,000 Rial / 11 USDT) — process-wide. With 1 USDT as the floor a $7.30
  order is legal, so the broker filled it and three tests broke three files away.
- **The fix has two halves:** `units.reset_documented_defaults()` restores the shipped tables from a
  frozen copy, and `conftest` runs it **before and after** every test. The "before" half matters: a
  pollution that happened prior to the first test would otherwise still reach it. Every test now
  starts from the documented numbers, and order no longer matters.
- **Five new tests** pin it: the shipped numbers are the documented SmallOrder, the exchange can
  still override them, the restore really restores, the same $7.30 order is refused under the
  documented floor and legal under the live one (the exact trap), and the `min-notional` gate
  judges with the documented numbers.
- **The codec guard no longer walks the venv.** `test_no_file_is_read_or_written_with_the_locale_codec`
  used to report fifty offenders inside `server\.venv\Lib\site-packages` (pip, numpy, pytest) on
  Windows, where the venv lives inside the checkout. `.venv`, `site-packages`, `node_modules`,
  `build`, `dist` and the caches are skipped now.
- **Gates:** ruff 0 · mypy 0 (84 files) · pytest **829 passed** · eslint/dashboard/five JS gates 0 ·
  parity 0 · release_notes v1.1.0 0 · build 0.
- **One honest note:** `test_diagnostics::test_a_real_gate_run_leaves_the_configured_database_untouched`
  runs the `ruff` gate from `PATH`; if `ruff` is installed in the interpreter but not on `PATH`, that
  test fails — correctly and honestly. I saw it once in a full run, green in the three after.

## 1.0.30

- **A stale book is not evidence any more.** `gates_block` judged every book in the cache
  regardless of its age, and the cache was never pruned. So a symbol that dropped out of the top N
  kept its last book for hours, and that book **wrote a fresh verdict on every bar**: `ok`, from a
  price that no longer existed. A book older than `BOOK_GATE_TTL_S` (default 300s,
  `NBX_BOOK_GATE_TTL_S`) now counts as unseen — the row is `no-book`, not `ok`.
- **Two TTLs that were being confused.** `BOOK_PREFETCH_TTL_S` answers "when to fetch again";
  `BOOK_GATE_TTL_S` answers "may this book still judge a bar". Collapsing them meant any book kept
  to save a request also counted as current — which is exactly the bug.
- **The card now says how many books it refused.** `gates.inputs` gained `stale_books`,
  `book_gate_ttl_s` and `oldest_book_s`; without them "11 books" could not tell a fresh scan from an
  hour-old cache.
- **The cache is pruned too.** At the end of each prefetch, books older than `BOOK_EVICT_TTL_S`
  (default 3600s, `NBX_BOOK_KEEP_S`) are dropped and counted in `prefetch.evicted`, so a symbol that
  left the list does not sit in memory.
- **The error direction is under-counting.** Doubt about an old book produces "no verdict from this
  symbol", never "a fresh verdict from an old price". An under-count can be seen and fixed later;
  polluted evidence cannot.
- **Five new tests** cover it: past the gate TTL ⇒ `no-book` and `stale_books`; inside it ⇒ still
  judged; the two TTLs in the right order; eviction; and that books going stale still cannot move
  `applied` off `false`.
- **The number ceiling is now full.** Per `docs/releases.md`, `1.0.30` is the last PATCH on the
  `1.0.x` line; the next release, whatever it contains, will be `1.1.0`.
- **The dashboard's "project tests" card now runs on Windows too.**
  `server/tests/test_deploy_hardening.py` called `os.geteuid()` at line 79 — an attribute that does
  not exist on Windows — and because a `skipif` condition is evaluated **at import**, the whole
  suite stopped with `AttributeError`: one error and zero tests. The card went red and said nothing
  about the other 816 tests. The condition is now read with `getattr`, and the test that reproduces
  `ProtectSystem=strict` (which has no Windows equivalent) is skipped with a stated reason instead of
  taking the module down. The hand-written `:` in `PYTHONPATH` became `os.pathsep`.
- **A new guard, running on the machine CI actually has.**
  `server/tests/test_platform_portability.py` removes `os.geteuid`/`getegid`/`fork`/`forkpty`/
  `killpg`/`setsid` in a subprocess and asks pytest to collect the suite; if any module touches one
  at import it fails here, on Linux, with the message a Windows user would have seen. The real cause
  of this bug was that CI only ran Linux.
- **And a `windows` job in CI.** `.github/workflows/ci.yml` now runs the same python gate on
  `windows-latest` (ruff, mypy, pytest). It is **advisory** for now (`continue-on-error`) so one
  small Windows difference cannot lock the repository on day one; it becomes blocking once it has
  run green.
- **A 403 on one pair no longer paints the log red every five minutes.** The errors export of a
  real install carried two fresh lines every five minutes: `error nbx-client … 403` and
  `warning nbx-app resolve USDTIRT skipped`. The cause is real and **already known** — Nobitex does
  not publish `USDTIRT`'s public history — but the client had no memory: every `resolve` pass sent
  the same doomed request again. A tab that is always red is a tab nobody opens, and that is where a
  real failure gets lost. A per-symbol refusal is now remembered for `UDF_REFUSAL_TTL_S` (default
  6h, `NBX_UDF_REFUSAL_TTL_S`): the request is not repeated, the request counter does not climb, and
  the message is logged once at `info`.
- **The memory lives only as long as its evidence.** A success clears it, and after the TTL the
  client tries again — so a pair that becomes public is seen the same day. A network error or timeout
  is **never** recorded as a refusal: an outage says nothing about that pair.
- **`warning` stays reserved for something to act on.** `resolve` logs `info` for a known refusal
  and `warning` for everything else; the `refused` key in `meta` separates the two.
- **Five new tests** (`test_udf_refusal_memory.py`): the refusal is not repeated, the client breaker
  stays closed and other symbols keep working, the memory expires, success clears it, and a timeout
  is not recorded as a refusal.

- **The Windows job found three more real defects on its first run.** None of them could be seen
  on Linux, and all of them happen on the machine `docs/run-windows.md` describes:
  - **The locale codec instead of UTF-8:** reading the CHANGELOG or a Persian page with
    `read_text()` and no `encoding` used cp1252 on Windows and raised `UnicodeDecodeError: 'charmap'
    codec can't decode byte 0x81`. The same defect sat in `subprocess(text=True)` (a UTF-8 em dash
    came back as mojibake). Twenty files fixed.
  - **Printing Persian to a cp1252 console:** `scripts/release_notes.py` died with
    `UnicodeEncodeError`, so "release validation failed" for a reason that had nothing to do with the
    release. Every script that prints non-ASCII output now reconfigures `stdout`/`stderr` to UTF-8
    (twelve scripts).
  - **A test id longer than an environment variable may be:** the `PNG * 50000` parameter in
    `test_coin_metadata` pushed the test's *id* past 32 767 characters, and pytest then raised
    `ValueError` while writing `PYTEST_CURRENT_TEST`. The oversized payload is now built inside the
    test and the id stays short.
- **A guard for the whole class.** `test_no_file_is_read_or_written_with_the_locale_codec` walks
  `server/` and `scripts/` with `ast` and finds any `read_text`/`write_text` or
  `subprocess(text=True)` without `encoding`. `test_docs_bilingual` now decodes file names out of
  their surrogate escapes, so it also holds under `LANG=C`.

## 1.0.29

- **The version rule is now numeric and tested.** It used to say "PATCH for a fix, MINOR for a
  feature", but in practice shadow-period capabilities (the shadow ledger in 1.0.26, the book
  prefetch in 1.0.28) also took a PATCH and nobody said why. The rule is now explicit: as long as no
  gate actually rejects a row, it is the shadow period and a PATCH is enough; the first gate that
  filters is a MINOR — in this project, step S6 of ADR-0023.
- **A numeric ceiling, so the number stays readable.** PATCH runs up to **30** before the `1.0.x`
  line is crossed: `1.0.30` is allowed and the release after it is `1.1.0`. From `1.1.0` on every
  field tops out at **10**: `1.1.10` → `1.2.0` and `1.10.4` → `2.0.0`.
- **A ceiling is the highest value, not a count of releases.** So we do not spend thirty releases to
  get there; `1.0.30` is still inside the rule and the next step is forced.
- **The ceiling is enforced by a test, not by memory.**
  `test_the_release_number_stays_inside_its_ceiling` reads the current `VERSION` and makes CI reject a
  number outside the rule; a second test keeps the documented tables (fa and en) identical to the
  numbers the test enforces.
- **A PATCH-to-MINOR jump means the change was not large.** When a ceiling fills and forces a MINOR,
  that version's CHANGELOG says "bumped because of the ceiling, not because behaviour changed" — so
  the project's history does not lie later.

## 1.0.28

- **Step S1 of ADR-0023 shipped: the scan reads the order book.** Until now a book was only read
  when somebody opened the book panel for one symbol, so most rows stayed `no-book` and the shadow
  ledger built in stage 2 of ADR-0022 was effectively empty. Every scan now fills the books of the
  **top N symbols** before it writes the ledger.
- **The ceiling is a refusal, not a trim.** `gate_prefetch_n()` rejects a value above
  `GATE_PREFETCH_MAX=20`, a negative number, and non-numeric input with `ValueError`. Trimming
  silently was the worst option: the gates card would look populated while nobody could see why the
  sample was not growing. Default `10`; `0` means off.
- **One broken symbol neither kills the scan nor lies in the counts.** A symbol's error only lands
  in `failed`, and that symbol stays out of the cache — so its gate keeps saying `no-book`. An empty
  book (no bids or no asks) counts as a failure, not as zero depth.
- **The ranking is untouched.** This step only fills the input cache: no row is removed, added or
  reordered, and `applied` stays `false`. A test compares the list's order and length before and
  after the pass.
- **One arithmetic, two consumers.** `book_stats()` and `book_record()` were lifted out of the
  `/api/orderbook/{symbol}` route and the prefetch uses the same functions, so the panel and the gate
  cannot disagree about one book. Depth of a toman-quoted symbol is converted with the live rate, and
  stays `None` when there is no rate — tomans are never reported as dollars.
- **Reuse and a time budget.** A book newer than `BOOK_PREFETCH_TTL_S` (default 120s) is not read
  again, and a pass stays inside `GATE_PREFETCH_BUDGET_S` (default 8s); both are settable through
  `NBX_BOOK_TTL_S` and `NBX_BOOK_BUDGET_S`. Concurrency is 4.
- **Visible in the output.** `/api/signals` reports the last pass under `gates.inputs.prefetch`
  (`asked/ok/failed/fresh/budget_s/at`), so “there is no book” is distinguishable from “nobody ever
  asked”.
- **17 new tests** cover exactly this step: the ceiling, the TTL, one failing symbol, an empty book,
  an unchanged order, route/prefetch arithmetic agreement, and `unknown` shrinking once the books
  land.

## 1.0.27

- **ADR-0023 is written: “Smarter only with evidence”.** The fifth document in this series, and
  instead of a list of tools it defines an **admission filter**: any change claiming intelligence
  must (1) produce a number that does not exist today, (2) have a state in which it is wrong, and
  (3) not change behaviour in its first step. Anything failing that is rejected, however modern it
  looks.
- **Six steps, in dependency order:** prefetch the order book on every scan (without it the shadow
  ledger stays empty), an honest backfill (candle-derived codes only, labelled `backfilled`), score
  calibration (from number to probability, with a minimum sample per bucket), multi-timeframe
  confluence, the correlation gate (wiring `portfolio.py` into the ranking), and finally applying
  thresholds with evidence (executing ADR-0021, under its own ADR).
- **Five explicit refusals, with reasons:** a machine-learning ranker without out-of-sample
  evidence, reinforcement learning for sizing (a decision that cannot be audited), lowering the 68
  threshold for “more signals”, price prediction with a single number, and enabling order submission
  to feel “complete”.
- **A hard boundary on the backfill:** `spread-wide` and `book-thin` are never reconstructed — past
  order books do not exist and inventing them is fabricating data (ADR-0014). Nor can a backfill
  move any gate's verdict from `insufficient` to `supports`.
- **Acceptance for the document:** the ADR becomes “accepted” when S1 and S2 ship and, within one
  working week, the shadow ledger brings at least one gate to ≥30 verdicts over ≥5 days. Until then
  it is **Proposed**.
- **Zero behaviour change:** this version is documentation only. No executable line was touched, and
  `test_docs_bilingual` (5 tests) is green.

## 1.0.26

- **The gates now have a memory.** Stage 2 of ADR-0022: `server/gate_ledger.py` records each
  symbol's verdict **once per bar** (keyed on symbol + timeframe + the bar's close time) and
  only reads afterwards. The scan runs every 90 seconds; a repeated verdict is never written,
  so the sample grows with the *market*, not with the *polling rate*.
- **The exit rule is now code:** one gate, **≥30 verdicts**, **≥5 days**, and the blocked
  names must have done worse than the ones that passed. `MIN_VERDICTS`, `MIN_DAYS`,
  `DELTA_BP=25` and `MIN_SHARE=0.6` are constants, not numbers chosen in a report.
- **The comparison is paired inside each bar:** blocked mean minus allowed mean at the
  **same** timestamp and the **same** horizon, averaged over bars afterwards. Comparing
  against the market mean measures whether the market rose, not whether the gate helps.
- **"Not known yet" is not zero:** a row whose future bar does not exist is counted in
  `unresolved` and kept out of the means. Imputing zero drags every mean toward the centre
  and makes a gate look innocent.
- **"I do not know" is not evidence for a block:** asking about `no-data`/`no-book` is
  refused by name (`refused`). Verdicts: `insufficient` · `inconclusive` · `supports` ·
  `contradicts` — with **`applied: false` on all of them**, including the one that favours the
  gate. Reference data (ADR-0009/ADR-0014) can never promote it.
- **New route:** `GET /api/gates/evidence?tf=4h&horizon=4` — joins the ledger against the
  candles that came *after* and returns one verdict per gate with its own sample size. It
  only reads.
- **`/api/signals` stayed cheap:** only the counts (`gates.ledger`) were added; the expensive
  join lives elsewhere. The dashboard chip says them in its title: "evidence so far:
  spread-wide — 12 verdicts over 2 day(s) (needs 30 over 5)" — and when the sample is ready
  the sentence changes, not the chip.
- **Tests:** 31 in `test_gate_ledger.py` (rescanning does not inflate the sample, the first
  verdict for a bar stands, 29 verdicts are `insufficient`, 30 verdicts in one day are too,
  the comparison is paired, unknown is not zero, reference cannot promote, the evidence route
  never writes) + jsdom scenario 13. Four deliberate mutations — allowing duplicate rows,
  dropping the days rule, unpaired means, counting unknown as a zero return — each turned the
  suite red.

## 1.0.25

- **Five hard gates now have named verdicts — and still none of them blocks anything.** Stage 1
  of ADR-0022 is implemented: `server/gates.py` judges every row with seven codes (`halted`,
  `no-data`, `stale-data`, `no-book`, `spread-wide`, `book-thin`, `ok`) and `/api/signals`
  returns a `gates` block with `mode: "shadow"` and `applied: false`. The ranked list is **the
  same list**: this layer reports, it does not remove.
- **Precedence is a contract, not an accident:** first "can this be evaluated" (`halted`,
  `no-data`), then "is the input valid" (`stale-data`), then "is the market tradeable"
  (`no-book`, `spread-wide`, `book-thin`). `code` is the first, `codes` keeps all of them —
  because someone who fixes the spread deserves to know the candles were stale too.
- **"I do not know" is not "fine":** `no-data` and `no-book` land in `unknown`, counted apart
  from `would_block`. A symbol whose book was never read is neither healthy nor blocked.
- **Zero network requests:** every input already exists — the per-symbol age (1.0.23), the
  closed-market set, and the spread/depth the `/api/orderbook/{symbol}` route already read and
  now keeps. An IRT book's depth is converted to dollars with the USDTIRT rate (a USDT book
  already is dollars); a missing rate gives `no-book` rather than comparing tomans to dollars.
- **The thresholds are parameters, and their default is off:** `max_age_bars=2` (the same
  `DEFAULT_TOLERANCE_BARS`), `max_spread_bps=None`, `min_depth_usd=None`, `enforcing=False` — and
  `enforcing` is **never read** on the reporting path.
- **Dashboard:** a chip reading "gates (shadow): 4 of 24", with the symbol names and the sentence
  "nothing was removed" in its title; a clean scan is said in words rather than with a permanent
  warning.
- **Tests:** 13 Python tests (`test_gates.py`: precedence, each code with its number, unknown
  separate from blocked, thresholds off by default, the caller's rows untouched, and a test
  asserting this module **has no way to remove rows**) + jsdom scenario 12. Three deliberate
  mutations — a layer that filters, "unknown" counted as blocked, and a route that drops the
  blocked rows — each turned the tests red.
- Documentation: an implementation amendment in ADR-0022 (fa/en) and a cross-reference in
  ADR-0007's gap note (fa/en).

## 1.0.24

- **ADR-0022 is written: how gates move from labels to gates — and why not today.** ADR-0007
  defined six hard gates, and its own review recorded that only the fundamental veto actually
  changes a decision today. This document records that gap with code references: data age,
  symbol suspension, spread, depth and the daily stop — **five gates out of six** — wear a gate's
  clothing without stopping anyone.
- **This version changes no behaviour** — it is a proposed document. The proposed decision: a pure
  evaluation layer (`server/gates.py`) with seven named verdicts (`ok`, `stale-data`,
  `spread-wide`, `book-thin`, `halted`, `no-book`, `no-data`), which first runs in **shadow mode**:
  `mode: "shadow"` and `applied: false`, with the response saying what *would* have happened
  without removing a single row from the list.
- **The thresholds are parameters, and their default is off.** `max_age_bars` defaults to 2 (the
  same `DEFAULT_TOLERANCE_BARS` — one number, one home); `max_spread_bps` and `min_depth_usd`
  default to `None`, meaning off, because we do not invent a number with no provenance.
- **The evidence needed to leave shadow mode, registered in advance:** one variable at a time; the
  **forward** return of blocked names against the same-window median of allowed ones; at least 30
  verdicts across 5 trading days; an effect of ≥ 0.5 standard deviations (written **before** any
  result is seen); and a real feed. On `source=reference` the verdict is always `inconclusive`.
- **"I do not know" is not "fine":** the `no-book` code (no book arrived) is counted separately
  from `would_block`, and the "never an empty list" rule is written down: if everything is ever
  blocked, the answer says "everything was blocked" with per-code counts, not a list that looks
  like "nothing today".
- Bilingual document: `docs/adr/fa/ADR-0022-…` and `docs/adr/en/ADR-0022-…` (each links to the
  other), and row 0022 in the status table of `docs/adr/README.md`.

## 1.0.23

- **Twenty-three healthy symbols cannot hide a dead one.** 1.0.21 read the age from the
  *newest candle across the whole scan* — the optimistic end of the list. If one symbol's feed
  died three days ago, the list stayed "current" and that symbol's row still carried a score.
  The age is now kept **per symbol** (`_cached["signals_bars"][tf][symbol]`) and **the worst
  symbol decides**: a list with one stale name is stale.
- **The `freshness` block keeps its shape** so no card breaks, and gains two keys: `worst`
  (which symbol, how many bars) and `symbols` (counts of `current` / `stale` / `no_candles`).
  The chip now **names the symbol** instead of averaging it away: "the worst symbol in this
  scan is DEADIRT: its newest candle is 288 bars old (2 more symbols are stale too)" — and if
  several are stale, the count is said.
- **A symbol with no candles outranks any age:** an unknown age is not a small one, so
  `no-candles` sits above any `age_bars`. A symbol with an unusable timestamp refuses only
  **itself**, not the list: the healthy names keep their measurement.
- **Two sides, one rule:** `NBX.freshness.assessScan()` performs the same aggregation in the
  browser, and the card prefers the server's block when it has one (`live.scanFreshness`),
  because the server measured every symbol and that is what the list on screen was built from.
- **`nan` in a timestamp has a name, not an exception:** `freshness.assess_bar()` now refuses
  non-finite values with `bad-candles`; before, the failure surfaced one frame lower as a
  `ValueError` instead of the named reason.
- **Documentation debt closed:** `README.md` and `README.en.md` claimed `mypy` runs over "58
  source files" while the real number is 76. The number is gone from the document and the
  reason is written in its place: a number that goes stale with every new file is a trap, not
  documentation.
- **Tests:** 5 Python tests (one dead symbol among healthy ones, an all-current list, a symbol
  with no candles, one refused symbol without refusing the list, an empty scan) + 1 wiring test
  + jsdom scenario 11 (naming the worst symbol, counting the other stale ones, the server block
  winning over a local recomputation, and the browser aggregation). Two deliberate mutations
  (aggregating by the newest candle; picking the best instead of the worst) turned the tests
  red.

## 1.0.22

- **The ATR percentile is drawn now, not only named.** Since 1.0.13 the card said "ATR sits at
  the 12th percentile", and that number was all a reader had: 12 can be the tail of a quiet
  month or one calm day inside a volatile one. Opening a symbol now draws that same rank over
  the last 60 bars, with two dashed lines marking where "calm" and "volatile" begin — those cuts
  come from the server's response — and a marker on the current point.
- **One formula, not two.** `_percentile_of_last()` was extracted, and both the headline number
  and every point of the strip are built from it; `atr_percentile()` returns exactly the numbers
  it returned before (the 1.0.13 tests are untouched and green). A drawing computed by a slightly
  different rule would contradict the number printed next to it.
- **`GET /api/candles/{symbol}` now returns `volatility` and `freshness`.** The block carries the
  percentile, ATR as a share of price, the regime, the regime cuts and 60 strip points — with
  `applied: false` and the sentence that this describes the past rather than forecasting it. A
  named refusal replaces an exception: `insufficient-data` (below 120 bars) and `flat-market`
  (zero ATR over the window), both with their counts ("63 of 120") so the page never draws a zero
  where a rank belongs.
- **The window travels with every point.** `windows: {min, max}` reports the shortest window that
  actually produced a rank; a strip built on fewer than 200 bars says so instead of claiming the
  full lookback.
- **The age of the bars comes with the strip:** if the data is old, the "stale data" chip appears
  exactly where 1.0.18 defined it for the threshold card and 1.0.20 for the backtest card — with
  the age in its title.
- **Tests:** 9 Python tests (the rank's definition written out by hand, the last point equal to
  the headline number, the window reported, a short history refused, a flat window refused, the
  block degrading instead of raising, its presence on the route, the page wiring) + jsdom scenario
  10 (one segment per point the server sent, the marker placed by its value, two regime cuts only
  when the server sends them, a named refusal with no bars drawn, the stale chip with its age).
  Two deliberate mutations (geometry that ignores the value; a formula that counts ties as
  "below") turned the tests red.

## 1.0.21

- **The last numeric card without an age now has one.** The "Nobitex Universe Ranking" card
  prints "Cycle: 15m candle close" in its header, yet nothing asked how old the candles behind
  the ranking were: a queue of candidates whose newest candle closed a day ago is a claim about
  yesterday wearing a present-tense sentence. The 1.0.18 and 1.0.20 rule now covers this card
  too: a **"stale data"** chip that is visible only when the data is old and carries the age in
  its `title` ("the newest candle in this scan is 41.2 bars old; this ranking describes that
  window, not today"). On current data the chip hides itself, and a response without the block
  is treated as neither stale nor fresh.
- **One rule, two doors.** `freshness.assess()` (a candle series) and `freshness.assess_bar()`
  (a single timestamp) are the same arithmetic, because `assess()` is built on `assess_bar()`.
  Why: the scan fetches 260 rows per symbol and throws them away; keeping a series just to
  measure its age would have been a second copy of the rule — the kind that drifts, as the fee
  and the starting equity already did once.
- **Server:** `rescan` keeps the newest candle it saw, per timeframe (`_cached["signals_bar"]`),
  and `/api/signals` returns a `freshness` block from **both** of its answers — ranked and
  empty. A failed age check becomes a stale block with a named code (`bad-timeframe`,
  `bad-candles`), never an exception that takes a healthy ranking down with it.
- **Browser:** `NBX.freshness.assessBar()` and `NBX.TF_MINUTES` mirror the rule and the bar
  length, and `MarketEngine.scan` keeps the newest candle of the scan so the card can say how
  many bars old it is without keeping the candles. The two sides are pinned together by
  `test_single_source_constants`.
- **Tests:** 5 Python tests (the two doors agree; a missing bar is an answer while an unknown
  timeframe is a refusal; the scan records the newest bar; the helper degrades; the chip's three
  states) + 2 Python/JavaScript parity tests (bar lengths and tolerance) + jsdom scenario 9. Two
  deliberate mutations (a chip that never hides, "15 minutes = 60 seconds" in the browser) turned
  the test red.

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
