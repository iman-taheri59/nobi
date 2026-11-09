# nobitrader changes

[فارسی](CHANGELOG.md) · **English**

## 2.5.2 (in progress)

- **Performance table: the real outcome per horizon instead of a tick:** the H1/H2/H3 columns showed "✓" for `R > 0` at the horizon's end, which readers took for "target hit" — a +0.1R timeout and a TP2 looked the same. `summary()` now returns per-horizon objects `o1/o2/o3 = {kind, r, tp1, tp2, sl, ambiguous}` (`kind` from the resolver's `exit_reason`: `tp2` · `tp1_trail` · `tp1_timeout` · `timeout` · `stop` · `ambiguous`; derived from the flags for rows written before `exit_reason` existed) plus `r1` (H1 model R). The table shows a named chip with R ("Target 2 +1.60R", "Timeout +0.12R", "Stop −1.00R"), an "H1 model R" column next to "H2 model R", headers now read "+1 hour / +1 day / +7 days", and a one-line legend sits under the table title. The boolean `h1/h2/h3` stay for compatibility and the page falls back to the tick against an older server. Tests: `test_review_phase9_regressions.py` (+11), `test_review_phase9.cjs`.
- **`journal_resolve` no longer asks for `USDTIRT` candles:** the scan never scores that pair (`NOT_A_TA_SYMBOL`), yet the journal resolver spent one 403 request on it every five minutes and logged an `info` line. The same set is now skipped at the top of the `journal_resolve` loop.
- **Nobitex 401/403 is no longer called `AUTH`:** this client reads public endpoints only and sends no key, so the `AUTH` label sent operators checking credentials that were never used. The error code is now `BLOCKED`, with the message "no API key was sent; the pair/endpoint is not public or this IP is blocked" and `meta.public=True`. Breaker behaviour is unchanged (`is_outage` decides by status).
- **Day-of-week slice in the backfill scripts:** `shadow_backfill.py` and `pump_chase_backfill.py` gained `--weekdays-only` (Sat–Wed), `--weekend-only` (Thu–Fri) and `--days 1..7` (ISO), plus `--tz` (default `Asia/Tehran`; the day boundary follows Tehran's clock, not UTC). The slice label appears in the report header and in the JSON (`day_filter`). Test: `test_shadow_backfill.py::test_day_filter_splits_iranian_week_without_mutating_rows`.
- **Approval queue: a stale plan is badged and locked (ADR-0054):** the candle window (`expires_at`) only says the source bar is still the latest closed one; it says nothing about whether anybody re-scored it since. When the page stops polling (hidden tab, lost network) the scan stops too, and a 15m plan could sit "fresh" for up to half an hour. The automatic path refused that (`_auto_signal_refusal`); the human path did not. `queue_policy.describe` now also reports the scan's age (`scanned_at`, `scan_age_s`, `max_scan_age_s`, `stale`, `stale_reason`); budget `NBX_QUEUE_SCAN_MAX_AGE_S` (default 300 s, never below `SIGNALS_TTL_S`). A missing scan clock is stale (fail closed). The preview is still shown, but `POST /api/paper/order` with source QUEUE refuses a stale plan with `queue-signal-stale` — on entry and again in the guard just before the fill. On the page: a "scanned N min ago" chip next to the rank (amber with "stale ·" once over budget), the approve button disabled with a red sentence under the row, the age advancing on the one-second tick (a row goes stale between refreshes too), a server refusal with this code keeps the row locked instead of dismissing the candle; the candle TTL is never extended. An older server without these fields locks nothing. Tests: `test_queue_policy.py` (+4), `scripts/test_queue_stale_plan.cjs` (in `npm run test:queue`).
- **Approval queue: second staleness yardstick = price drift from the plan + «Rescan» button:** a fresh scan does not make a plan current; between two scans a thin pair moves 3 % and the row still says "enter at 100". `GET /api/risk/plan?approval=true` now returns `drift`: `mid` (from the same order book the execution preview read — no extra request), signed `drift_pct`, `drift_atr`, the limits and `exceeded`/`reason`. Limits: `NBX_QUEUE_DRIFT_PCT` (1.5 %) **or** `NBX_QUEUE_DRIFT_ATR` (0.5×ATR), whichever trips first — in a flat market ATR speaks first. Unknown mid = locked (`mid-unavailable`). `POST /api/paper/order` with source QUEUE repeats the same computation before `place_limit` and refuses with `queue-price-moved`. On the page: the same amber chip («price +2.20% (+1.10×ATR) from plan»), the same lock, and a «Rescan» button that appears only on a stale row: `GET /api/signals?refresh=true` (forced background rescan, not sooner than `NBX_SIGNALS_FORCE_MIN_AGE_S`=20 s after the previous one and never alongside a running one), wait for it to land (max 30 s), then the page's normal scan and a queue rebuild. Companion fix: when a queue refresh keeps the older deadline clocks, it now takes the *newer* scan provenance (age rebased onto the kept clocks) so a fresh scan can unlock the row; the TTL is still never extended. Tests: `test_queue_policy.py` (+5, 51 PASS), `scripts/test_queue_stale_plan.cjs` extended.
- **Rejects counted by reason (item 7):** `scripts/journal_diagnostic.py --paper paper_state.json` adds section 6: a reason × source table (QUEUE/AUTO/SEMI/MANUAL), "approvals that never became orders" (and how many were refused before reaching the broker), and a per-day count; the in-process `check_paper` now also reports `rejects_24h` in `data` and a one-line summary in `detail` (`diagnostics.reject_counts`). Two fixes were needed for the count to be honest: (a) all ten broker rejects now carry `source` (previously two did); (b) **pre-broker** refusals on the QUEUE path — queue clock, `queue-signal-stale`, `queue-price-moved`, missing levels — which left no trace in the paper book, are now recorded via `PaperBroker.record_refusal` as `reject` events with `pre_broker: true`. Old events without `source` are counted under "?". Test: `test_queue_policy.py` (+2).
- **Yellow thin-depth label on the card and in the queue (item 10):** `gates_block` adds an advisory label beside the shadow gate: `depth_warn = {usd: 562, applied: false, basis}` and `depth_thin` (`true/false/null`) per symbol. The threshold is the p10 of depth in the 2026-11-08 pump-chase backfill (`NBX_DEPTH_WARN_USD`, default 562); below it the 1h R̄ of LONGs was −0.83 vs −0.25 overall. The signal card writes "thin book: 410 $ (under 562 $)" on the "last candle" line and the approval-queue row gets the same amber chip next to "scanned N ago". `min_depth_usd` stays None, no row is removed or reordered, the approve button is never locked by it; unknown depth = no label, no invented number; an older server without `depth_thin` = no label. Tests: `test_depth_warn.py` (new), `test_signal_card.cjs` (196 checks), `test_queue_stale_plan.cjs`.
- **Shadow arm `E_nochase` (item 8, record only; ADR-0054):** the live verdict, but a LONG is refused when the bar is a chase: `run4h_atr ≥ 2` or `%B ≥ 1.0` (thresholds fixed in `shadow_arms`, not env; `VERSION=2`). So the arm judges the same bar the live verdict did, `ta_score` now records `diagnostics.run4h_pct/run4h_atr` at issue (`ta_engine.run_up` by candle time stamps, not bar count, hence right on every timeframe; short history or zero ATR = None). Unknown inputs cannot refuse: the arm equals the live row and says so with `chase: null`. The arm never promotes and never touches the composite; `signal_shadow_outcomes` and section 5 of the daily diagnostic pick it up unchanged (arm names are read from `features.shadow_arms`). `shadow_backfill` gained `E_nochase` too (rows from before 2.5.2 are judged on `%B` alone). The journal config hash is untouched. Test: `test_shadow_arms.py` (+9).
- **Stop slippage (item 9, record only):** every paper `close/partial` event now carries `stop_model`, `stop_slip_pct` (relative to the stop price; positive = filled worse than the stop) and `stop_slip_r` (relative to the entry→stop distance) (`paper_trading.stop_slip`). `journal_diagnostic.py --paper` adds section 7 (n, mean/median/p90 in % and in R, worse-than-stop share, by `sl-hit`/`trail`; older events without the field are counted and skipped) and `shadow_backfill.py --paper` prints the same block under the arm table with the reminder that "every stop in the table above cost 1 R + this slip, not 1 R". Test: `test_stop_slip.py` (new).
- **Signal explorer card redesign (ADR-0055):** the default row now follows a beginner's questions in order: symbol, direction, composite score, regime, **price, stop, target 1, target 2** (with distance %), and **freshness** (age of that symbol's last cached candle; scan age when no candle is cached), plus a "level ladder" that draws the real distances of stop and targets. Levels are *model* levels (stop = 2×ATR of the scan; targets = the R:R ladder the server publishes at `/api/risk/state` → `caps.rrLevels`) and the card says "not an order"; without the server ladder targets stay "—" instead of a private 1.5/2.5 copy; no targets for non-LONG rows. The engineering evidence — shadow-gate verdict with spread/depth, FA provenance, liquidity (the "turnover proxy" wording lives only here), four weighted TA component bars, RSI/ADX/ATR%/%B and the neutral cached price curve — moved behind a **"Details"** button (`aria-expanded`; open state survives repaints). Clicking the row itself still opens the candle chart. Layout follows the card's own width via container queries (mobile: stacked; ~560px: four-column level table; ~960px: one full line like the guide's old schematic). New jsdom test `scripts/test_signal_card.cjs` (CI and release): level maths, refused targets without ladder/ATR/LONG, freshness, Details toggle, scan rows never mutated.  **Row candle chart (click on the row):** the same model levels are drawn as dashed lines on the price (stop red, targets green; name at the line start, price on the axis; the axis range widens so no level falls off) and the chart gained a **crosshair and tooltip**: hovering or touching a candle shows its time, O/H/L/C with change %, and the price under the cursor (desktop: a box on the side away from the cursor; phone: one line under the plot, a tap elsewhere clears it). On narrow screens the svg canvas shrinks so labels are not squashed; the unit is named once in the header and the axis carries bare numbers. The last published R:R ladder is kept in `live.rrLevels` so a transient failure of the `/api/risk/state` read does not blank the targets. No change to scores, ordering, server or policy.

## 2.5.1

_Tagged and deployed 8 November 2026 after a clean three-day soak (0 restarts, 0 real upstream errors, 842 scan cycles)._

- **UI fix (after the 2.5.1 deploy):** the mobile bottom-nav icons rendered as empty circles. Cause: once `lucide` left the CDN, the inline icon pass did not keep the icon's name on the `<svg>` (the library used to set `class="lucide lucide-<name>"`), and the bottom bar — which derives its icons from the sidebar — found no name and fell back to a circle. The name is now kept in both the class and `data-icon`. New jsdom test `scripts/test_mobile_nav_icons.cjs` (CI and release) loads the real page and requires a named, non-fallback icon on every tab plus the four timeframe chips — in `index.html` and the inlined build. No server behaviour change.
- **Manual deploy from GitHub Actions (`.github/workflows/deploy.yml`):** `workflow_dispatch` only, with a tag input and a `dry_run` switch; the tag must be on `main`; with three secrets (`DEPLOY_HOST`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`) it runs the same `update_arvan.sh --yes --backup --verify` as the `deploy` user and finally requires `/api/health` to report the tag's version. No push ever deploys. Server prerequisite: `sudoers.d/nobi-deploy` for that one script only. `docs/analysis/shadow-backfill-2026-11-08-soak.md`: three-day soak re-read — 799 LONGs / 174 episodes (~58 per day); the positive live 24h is entirely the ONEIRT pump (305→640); without ONE, H1 R̄ −0.40.
- **ADR-0053 — readiness for an FX shock, a connectivity cut and a restart:** (1) new `fx-shock` regime in `regime.py` — `|premium| ≥ 6 %` (`NBX_REGIME_FX_PREMIUM_PCT`) or `|USDTIRT 24h change| ≥ 3 %` (`NBX_REGIME_FX_MOVE_24H_PCT`), checked before breadth and treated like `risk-off` in `apply`; default mode is still `shadow` (recording only). (2) A `fallback` FA profile no longer carries weight in the composite: `fa_weighted:false`, basis `ta+liq`; the number stays for display, vetoes still apply. (3) **Fix:** a manual kill switch was lost on restart; `halted/halt_reasons` are now persisted in `paper_state.json` and restored into both the broker and the risk engine; `paper_halt/resume` save immediately. Tests: `test_regime.py` (+2), `test_fa_fallback_unweighted.py`, `test_halt_survives_restart.py`. The 12 Nov decision criteria and "no real money before 17 Nov" are written in the roadmap.
- **`scripts/shadow_backfill.py` (read-only) + `docs/analysis/shadow-backfill-2026-11-07.{md,en.md}`:** re-scores the ADR-0051 arms (B/D), a TA-only arm and two baselines (all rows, live EXIT) over the existing journal and takes 1h/4h/24h forward returns from the journal's own price series (per price contract so the rial→toman seam cannot fake a return; ±20 min; |return| > 50 % dropped). Twelve-day result: no arm beats the baseline (live LONG 24h −2.27 % vs −1.35 % for all rows), D_comp ≈ live, B less bad, and 88 % of k=2 stops would have survived k=3 but their 24h return is −2.75 %. The shadow-layer decision date is pulled from 24 to **12 November** (roadmap). Test `test_shadow_backfill.py`. `--since/--until` to compare windows (e.g. post-deploy or the holdout). No behaviour change.
- **External review — round two:** version bumped to 2.5.1 and the AGENTS rule made realistic (one bump at the start of each release cycle; later commits accumulate under the same heading; the tag goes on the cycle's last commit). The review archive note was corrected: **40 was right** — the universe cache holds 60 symbols but the scan scores the top 40; that number is now the explicit constant `SCAN_TOP`. `docs/analysis/universe-churn-2026-11-07.md`: a query on the journal shows the count is fixed (40) but membership is not — only 17 symbols in ≥90 % of cycles, 79 transient ones, and transient LONGs at H1 R̄ −0.148 vs −0.089 for the core; input to the 24 Nov decision, no behaviour change. **`lucide` left the CDN:** the 13 missing icons were added to the inline set and `test_inline_icons.py` keeps it complete; no icon script is loaded from outside any more (the only remaining external script is `lightweight-charts@4.2.0`, with a local fallback). **windows (advisory) is no longer always red:** two persistent Windows-only failures fixed (a temp directory name with illegal characters in `test_build_info`; the 0o640 permission test, meaningless on Windows, now skips). `test_git_exec_bits.py` derives its list from every tracked file with a shebang (45 files, all 100755 now).
- **External review of 7 Nov (`docs/analysis/external-review-2026-11-07.en.md`) — the "this week" items:** fixed the four mypy errors that ADR-0051/0052 introduced in two test files and that turned CI on `main` red. The script exec bits — whose "restored" claim in ADR-0052 was wrong (the restore read a `HEAD` that was already empty) — are now recorded in the git index (`git update-index --chmod=+x`; 12 files incl. `update_arvan.sh`, `deploy_arvan.sh`, the install helpers), and a new `test_git_exec_bits.py` asks `git ls-files -s` instead of `os.stat`, so a local `chmod` cannot silence it. Docs aligned: `README.md` (52 ADRs, not 20), `server/README.md` rewritten (the real flat layout instead of the `src/…` package that was never built), the `nbx-core.js` header no longer claims "100% standalone". A decision date for the three shadow layers (regime, stop geometry, weight arms) is now written in the roadmap: **24 November 2026**. No behaviour change.
- **Approval queue — why it is empty:** "filtered" cards with `execution-rr-too-low` now carry a plain-language line: stop distance from the plan price, how far the real fill sits above the plan, and the maximum that keeps `R:R ≥ 1.5` (20 % of the stop distance for the default ladder). This was the cause of two days of empty queue: on a calm tape ATR(15m) — and so the 2×ATR stop — shrinks until one ordinary spread pushes execution R:R under the minimum. The gate was right; it just did not explain itself. No threshold changed.
- **ADR-0052 (no behaviour change):** round-trip fee (`2×fees.FEE_PCT` = 0.4 %) in new columns `return_net_pct`, `r_net`, `fee_pct` on `signal_outcomes` and `signal_shadow_outcomes`; historical rows stay NULL and gross numbers are untouched. Frozen holdout in `journal_meta` (`journal.freeze_holdout`, CLI `scripts/journal_holdout.py`; moving it needs `--force` and keeps the previous value). Read-only weekly timer `deploy/nobi-journal-diagnostic.{service,timer}` → `/srv/nobi-data/reports/`. `journal_diagnostic.py`: holdout header, `R̄net` in §3, tolerates a DB without the shadow table. Script exec bits accidentally dropped in `c4a2b73` restored.
- **ADR-0051 (recording only, no production change):** `server/shadow_arms.py` scores three fixed arms (`B_fa_liq` weights 0.3/0.4/0.3, `C_gate` live verdict + regime gate, `D_comp` current weights thresholded on the composite) from the same inputs and records them in `features.shadow_arms`. The resolver fills a separate `signal_shadow_outcomes` table: production LONGs at alternative k (`NBX_SHADOW_RISK_K`, default 3) and shadow-only LONGs at the live and alternative k; `signal_outcomes` and the resolver count are untouched. `journal_diagnostic.py` §5 compares arms on identical bars. `NBX_SHADOW_ARMS=off` disables recording.
- **ADR-0050 (behind flags, no behaviour change):** `server/regime.py` classifies the tape from the same `market_overview()` (`risk-off/neutral/risk-on/unknown`); `NBX_REGIME_GATE` (`off|shadow|enforce`, default `shadow`) records `market_regime`/`regime_gate` on every snapshot, and in `enforce` a LONG in risk-off becomes NEUTRAL capped at 67 like a veto. The stop multiplier (`×2 ATR`), a literal in three modules, is now one setting `NBX_RISK_K` (default 2.0, bounds 1–5) parsed in `risk_engine.parse_risk_k`. The regime is visible in `/api/signals`, `/api/health`, `/api/market/overview`; `journal_diagnostic.py` §3b splits LONG returns by regime. Open note: `nbx-core.js` still shows `×2`.
- `scripts/journal_diagnostic.py` (read-only) + `docs/analysis/journal-diagnostic-2026-11-06.{md,en.md}`: diagnosis of the negative journal IC — the whole universe fell 3.5–4% in the clean window, TA anti-predictive / FA and liquidity positive at 1h, 93% stops at the 24h horizon with a 2×ATR(15m) stop; proposed 2.6.0 actions: market-regime gate, horizon-consistent stop, shadow A/B.
- **Order ticket listed only 6 symbols:** `initTicket()` filled the select once at page load — before `/api/universe` had answered — and stayed on the 6-symbol fallback while the scanner showed 60. It now refills whenever the universe arrives, keeps the user's pick and groups symbols by IRT/USDT.
- **Click feedback:** Tailwind v4 preflight left `cursor: default` on buttons, so nothing looked clickable. Now: pointer on every control, a visible press, a clear disabled state; plus an activity layer (`nbx-activity`): a 2px progress bar at the top and a spinner on the very button that started a request (`aria-busy`, also blocks double clicks) until it completes. Only click-triggered requests count; background polling never lights the bar.
- Native `<select>` popups opened with a light background and light text on desktop, so options were unreadable: `color-scheme: dark` on `html` + meta, explicit `option` colours.
- **Bug fix: the timeframe selector had not rendered since 2026-10-20.** `injectCandleSelector` located its anchor via the chart's `previousElementSibling`; commit `17be1af` wrapped the chart in `<div class="relative">`, the anchor became null and the function returned silently, pinning the dashboard to 15m. It now anchors on `#candleHeader` and a source test guards it.
- **Header:** name and role removed from the mobile header; the user icon opens a menu with "My account" (jumps to the Account tab) and "Sign out"; the loose sign-out button is gone (desktop shows name/role inside the same button). The language switch shows only the inactive language. `user-round`, `circle-user-round`, `log-out`, `search` added to the offline icon set.
- **Mobile package removed (user-facing):** header button, download dialog, `/download/nobitex-mobile.html`, `/download/nobitex-trader-robat.zip` and the `package_bundle` module are gone. A single file opened from `file://` has no API and is an empty dashboard — the "offline" promise was misleading. The service still serves the inlined page (ADR-0019); dropping the artifact itself and a real PWA are a separate ADR.
- **Signal scanner:** symbol search + direction filter (all/long/neutral/exit) above the list; view-only, shows an "n of t" counter; scan and queue untouched.
- Documentation reorganisation: the two external inputs, the comprehensive audit and the review-of-reviews (fa/en) moved into `docs/reviews/`; `docs/reviews/README*.md` added as the single living finding→status→commit matrix; the soak log split into `docs/ops/*/soak-2026-11.md`; roadmap status table rewritten (wrong B1 label fixed); illustrated user guide `docs/user-guide.fa.html`; README and HANDOFF updated.
- `/api/health` now carries `build {commit, commit_full, source}`: the commit the running process started from, read once at start-up from `NBX_BUILD_COMMIT` or `.git/HEAD` (no subprocess) and unaffected by a later checkout on disk; `null` when unknown, never guessed. Answers review P2 ("health proves the version, not the commit"). Visible on production after the soak.
- `scripts/market_archive.py` + `deploy/nobi-market-archive.*` units: an out-of-process raw market archiver (stats + orderbook/all every 5 minutes, gzip, day-level retention) so payload capture starts during the soak without a deploy.
- The stability soak on `capoy.ir` formally started at `2026-11-05T07:19:52Z`; baseline and daily checklist recorded in `docs/five-day-validation.md`.
- `live_evidence_snapshot.py` with `--url http://127.0.0.1` logged in and then got 401 on the host: the production session cookie is `Secure` and is not sent over http. The tool now detects that, logs out, and asks for the HTTPS entry point instead of a bare 401; the five-day guide was corrected. The v2/v3 × User-Agent matrix on the host returned 200 for all four, so the earlier preflight 403 was transient; aligning the probe with the service stays correct, but the agent was not the cause.
- `check_deploy.py` probed the exchange with `/v2/orderbook` and urllib's default User-Agent and got 403 on the host while the service itself was healthy. It now sends the same `/v3` route and the same `TraderBot/...` agent (`NBX_UA` honoured) as the product client, so a failure is about the network rather than the probe.
- `update_arvan.sh --backup` failed on the real host: a `tar` of the live data directory errored while the candle cache was being rewritten, and a raw copy of a SQLite file under write is not a consistent snapshot anyway. It now runs the guide's documented procedure, `state_backup.py` over SQLite's online backup API, for the three databases (paths from `nobi.env`, no cache, no `.env`). The spurious "half-applied" warning caused by the `v` prefix from `git describe` on every healthy install is fixed too; both are tested by executing the lifted bash fragments.

## 2.5.0

- `/api/health` now carries a `price_alerts` block (the ADR-0024 pattern used by `scan_loop`): tick interval, freshness cap, run count, last tick outcome/error, and the durable `sent/failed/pending` split with the oldest pending timestamp. Failed ticks and unconfirmed deliveries are recorded once in the errors tab under `task.price-alerts`, without the Telegram body.
- `scripts/live_evidence_snapshot.py` (ADR-0034) could not work against a real server: the live routes require an operator session and the tool only sent a token. It now logs in with `NBX_SNAPSHOT_USERNAME/PASSWORD`, logs out after reading, and adds scheduler/alert columns to the CSV; the new test runs the real session contract (no credentials, Viewer, Operator).
- FastAPI did not serve `/nbx-alerts.js`, so canonical mode with `NBX_SERVE_INLINE=0` returned 404; the route was added with a regression asserting every external dashboard module is served byte-exact. `ui_smoke_prep.py` inlines the third module too.
- Real price-alert end-to-end (`scripts/price_alerts_e2e_test.py`, 40 checks) with a scratch FastAPI, scratch SQLite, controlled exchange/Telegram and Chromium: three real role logins, 401/403, bell-only CRUD with CSRF, `baseline → armed → crossing → one event`, replay, `sent/failed/pending` with a sender crash after commit, stale/re-baseline, cache-clearing restart reading history from SQLite, zero trading mutations and zero server egress. Added to CI.
- Connected a bilingual A5 bell panel to the existing price/premium API: rule creation, enable/disable, two-step deletion, event history, and manual refresh are available in canonical and standalone builds. Only Owner/Operator opening or refreshing the panel issues GETs; Viewer/anonymous sessions make no alert request, and mutations consume the existing session/CSRF contract.
- Made arming/freshness, observed value/time, crossing, and `sent/failed/pending` delivery explicit. The badge counts enabled rules after a successful read rather than inventing unread state; identity changes invalidate cached data, late replies are discarded, and deleting a rule retains history.
- Added independent DOM and Chromium regressions for access, CSRF, escaping, races, mobile/desktop layout, and standalone parity. The final gate passed 1808 Python tests, 25 JS/DOM suites, Ruff/mypy, and `80 + 56 + 16` layouts. This change creates no Paper/Live order, risk change, or execution-mode change; no deployment or real activation occurred.

## 2.4.0

- Added the A5 backend phase for conditional price/premium alerts under ADR-0049. Owner/Operator can create `above`/`below` rules for any returned market's `last` price or the USDTIRT reference `premium_pct`, enable/disable or delete rules, and read events. Viewer receives no operational access and every mutation requires CSRF.
- The first fresh observation only arms a rule. A trigger requires a false→true crossing; remaining beyond the threshold or replaying a timestamp cannot repeat it. Missing, invalid, future, or stale data disarms the rule and recovery starts with a fresh baseline. Rules/events persist in `journal.db` and the existing backup, while deleting a rule retains event history.
- The scheduler refreshes `/market/stats` only while a rule is enabled and never calls Paper/Live or risk execution paths. A trigger is stored before Telegram delivery; the delivery result carries a bounded reason without an external error body, and failed delivery is not blindly retried. The bell/management UI is a later phase.
- Verified with 1800 Python tests, mypy/Ruff, and API/scheduler/freshness contracts. The MINOR bump reflects a compatible new capability. Production was not deployed and no real order or Live/Auto change occurred.

## 2.3.4

- Applied six patch-level Dependabot updates together: `pydantic 2.14.2` and `pydantic-settings 2.16.2` at runtime, plus `pytest 9.2.0`, `Ruff 0.17.3`, `mypy 2.4.1`, and `ESLint 10.14.0` in the development toolchain. The Pydantic update includes released fixes for two JSON/TypedDict validation advisories; the product API contract is unchanged.
- Compatibility is verified by 1788 Python tests, mypy/Ruff, `pip-audit`, and every JS/DOM suite on Node 24. `pip-audit` reports no known vulnerabilities and `npm audit` has no high/critical failure; five moderate advisories remain in the minimatch development-only dependency chain, and npm's incompatible forced fix was not applied.
- Runtime authentication, risk, trading, Live/Auto, and real-order behavior are unchanged. Production has not been deployed by this release.

## 2.3.3

- The “your role is read-only” notice is no longer conflated with the upstream-health banner. Previously `upShow(false)` hid it after a healthy scan and an upstream error overwrote its text. An independent `roleBanner` now survives both healthy and failed upstream states, while the Owner view is unchanged.
- A new regression executes the production `paintUser` and `upShow` functions for Viewer and Owner in both upstream states. A local authenticated Viewer integration also passed all 21 API/role checks and all 19 real-page UI checks without mutations or JavaScript errors. Verified with 1788 Python tests and every JS/DOM suite on Node 24. This release changes only role presentation; runtime authentication, risk, trading, Live/Auto, and real-order behavior are unchanged.

## 2.3.2

- Fixed the `--verify` traceback in `check_config`: on hosts where `/etc/nobi/nobi.env` is root-only to the service user, even `stat` raises PermissionError. The checker now retains an unknown mode without crashing and consumes the `NBX_*` values explicitly passed by `update_arvan.sh --verify`; without those values it returns a clear SKIP instead of a traceback or a false empty-config report.
- Added regressions reproducing the production 2.3.1 failure for both supplied values and an unreadable file without environment values. Verified with 1788 Python tests; runtime authentication, risk, trading, Live/Auto, and real-order behavior are unchanged.

## 2.3.1

- `check_deploy.py` now checks the configuration sources systemd actually reads: `/etc/nobi/nobi.env` is valid without `server/.env`, the optional override remains supported, and group/other access to secrets is checked. A placeholder empty `.env` is no longer needed to make preflight green.
- The read-access check now probes real anonymous GETs against positions/metrics/risk instead of guessing from the auth mode. `/api/health` remains public for monitoring; 401/403 on sensitive paths is OK and 200 for account data is FAIL. Large health payloads containing the whole symbol-freshness map are read without truncation.
- Expanded Viewer regressions across positions, metrics, risk, Journal, and Signal Evidence. Verified with 1786 Python tests, mypy, Ruff/ESLint, JS parity, and the DOM suites on Node 24; runtime authentication, risk, and trading behavior are unchanged, and this release enables neither Live/Auto nor real orders.

## 2.3.0

- Aligned the Python and JavaScript TA contracts: RMA/RSI/TR/Bollinger/DI/ADX, warm-up, component scores, direction, regime, and risk now have numerical parity and independent regressions. FA gains depth/premium-sensitive caching, provider provenance/freshness, reweighting of available components, and explicit TVL mappings; missing data is no longer fabricated neutral evidence.
- Universe ranking and liquidity scoring now use USD turnover; IRT without valid FX and unknown quote currencies receive no invented USD value. OHLC history above 500 candles is paginated, deduplicated, sorted, and trimmed, and daily H1 no longer collapses into H2.
- Corrected exits across Paper, Journal, and Backtest: gap-through-stop fills at the open, post-TP1 ambiguity is conservative and recorded, TP2 closes the remainder on both sides, and sell-side TP1 PnL is fixed. Journal return/R includes proceeds from both legs, with `hit_tp2` and `exit_reason` retained in evidence.
- Wired daily/weekly realized-loss and consecutive-stop triggers into Paper, order preflight, `/api/risk/state`, Python Backtest, and JS. A halt blocks only new entries while open-position management continues; a regression proves the third stop blocks a fourth otherwise-valid entry.
- The real-order route accepts only preflight backed by a real, fresh, timestamped account snapshot and fails closed with 503 without a provider. Dry runs and ambiguous executions no longer complete promotion evidence. Paper GETs no longer mutate state, and health reports the last probe with age/staleness semantics.
- Backtest now shares the 1.2R/2R default targets with Live/Paper/Journal, makes `fee_pct` configurable and recorded in the hash/report, and fails the gate for zero fees. Best-grid selection uses IS evidence only; reports also carry dataset/engine identity and execution assumptions.
- Portfolio correlates each pair only on that pair's shared timestamps and latest observations; asset volatility uses its own series, so one gapped asset no longer erases every other pair. Benchmark intentionally retains its previous intersection contract.
- Journal and Backtest carry more precise dataset hash/range and engine/config identity; the new outcome-column migration is idempotent. This identity does not replace original-data archival or prove profitability, complete replay, or Live readiness.
- Verified the remediation with 1776 Python tests, mypy, Ruff, ESLint, JS broker/intrabar, and every DOM suite on Node 22; regenerated `nobitex-mobile.html`. The MINOR bump reflects changed risk and trade-outcome behavior. This release does not itself enable Live/Auto or submit real orders.

## 2.2.2

- The approval queue no longer hides candidates refused by the execution preview: a separate “Filtered candidates (not approvable)” section shows the symbol, rank/score, plan entry, execution entry, execution `R:TP2`, minimum `R:R`, refusal reason (sanitized code plus translated label), absolute expiry, and remaining TTL. The section contains no buttons, inputs, links, or approve/reject/edit controls, and its ids never enter the approvable queue or the order-submission path.
- The “no approvable candidate right now” message now points at the filtered section so an empty queue is not mistaken for “no signals were produced”. Missing numbers render as `—` instead of fabricated zeros, and unsafe reason codes fall back to `execution-blocked`.
- Filtered rows repaint expiry and 20-second preview staleness on the queue tick; expired rows are kept visible as expired rather than pruned. Stale asynchronous responses cannot resurrect the section after a newer scan.
- Added `scripts/test_queue_filtered.cjs` to `test:queue`, covering both `index.html` and `nobitex-mobile.html` in Persian/English for non-actionability, absence of controls, missing numbers, hostile reason escaping, expiry, staleness, clearing, and response races.
- This release is diagnostic/UI only: `MIN_RR`, the execution preview, sizing, targets, server behavior, Live/Auto, and real orders are unchanged, and nothing was deployed.

## 2.2.1

- Added a text-only source transfer package under `deploy/transfer-2.2.1/` for operator-controlled server transfer when individual workspace files cannot be downloaded. It carries a base64 source archive, archive checksum, source manifest, and extraction instructions; it contains no live data, env, database, paper state, or private keys.
- Added `deploy/nginx-capoy.ir.conf`, merged from the operator-provided live `capoy.ir` site file: it preserves the upstream/TLS/ACME/log/timeouts, repeats the full `X-Forwarded-*` set in every proxied location, hides upstream security headers, and adds one edge copy with `always`.
- `location = /` and `/assets/` now repeat the full security-header and `proxy_hide_header` sets; dashboard `Cache-Control` is a single `no-store` value and assets are a single `public, max-age=604800, immutable` value. HSTS is added only in the HTTPS block.
- Added structural tests for the capoy.ir-specific config so nginx inheritance, duplicate-header removal, TLS/ACME preservation, and bearer-token non-stripping do not regress. This file is not installed on the server by this release.

## 2.2.0

- A partial exit in the paper broker no longer closes the whole position by mistake: when the order book can only buy/sell part of the open quantity, that fraction settles against its proportional cost basis and fee, and the residual stays open as `PARTIAL`.
- A new `partial` exit event is retained in order history; `remaining_open`, `retained_exit_qty`, recorded PnL, and open risk budget now agree with the quantity actually exited. A full close is recorded only when the residual is zero (or floating-point dust).
- Realized PnL accumulates across multi-stage exits instead of the final stage replacing earlier stages. The `/api/paper/orders` projection and paper metrics recognize `partial` as a retained exit event.
- Regression tests cover thin depth, full depth, sell-side exits, the next evaluation tick, restart, order projection, and metrics.
- The bump to `2.2.0` is due both to the PATCH ceiling on the `2.1.x` line and to the paper exit-accounting behavior change. This release places no real order, changes no Live/Auto mode, and performs no automatic deployment.

## 2.1.10

- Translated non-English code comments and docstrings across Python/JS/HTML/CSS/shell/systemd into English; UI strings, Persian user-facing messages, i18n tables, and Persian test expectations were left unchanged.
- Added English versions of the analysis/review reports (`nobi-review`, `capoy-review`, `capoy-auth-review`, `capoy-owner-probe-guide`, `nobi-desk-queue-review`, `capoy-owner-probe-analysis`) as `*.en.md` files, plus English twins for the technical/fundamental analysis catalogues under `docs/analysis/`.
- Added language switchers and structural parity tests for the translated reports/catalogues, and corrected the stale lint comment that still described code comments as Persian.
- Added a bilingual controlled-deployment checklist for `2.1.10` (`docs/upgrade-2.1.10*.md`) covering backup, env/nginx installation, restart, read-only verification, and rollback, plus a critical-file checksum manifest (`deploy/upgrade-2.1.10.files`). It authorizes no deployment by itself.
- This release makes no runtime behavior change, deployment, real order, or Live/Auto change.

## 2.1.9

- Session/CSRF cookies on public HTTPS deployments no longer depend on nginx header inheritance: `NBX_COOKIE_SECURE=always` keeps both cookies Secure even when the app sees plain HTTP on loopback; unknown values fail closed instead of weakening. `auto` remains for local HTTP development.
- Browser security headers (`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`, `Content-Security-Policy: frame-ancestors 'none'`, and HSTS on HTTPS) are applied by both app middleware and the nginx edge; nginx hides the upstream copy so headers are not duplicated. API/docs responses default to `private, no-store`.
- `deploy/nginx.conf` repeats the full `X-Forwarded-*` set in every proxied location, because nginx does not inherit server-level proxy headers into a location that defines its own `proxy_set_header`.
- Fixed `zip() argument 2 is longer than argument 1` on `/api/v1/signal-intelligence/drift/cohorts?mode=global`; dimensionless global mode also no longer emits a phantom empty cohort.
- The approval queue now receives a read-only execution preview from the same paper-broker quote path used at submit: execution price, depth, precision and TP2 geometry with the same `MIN_RR`. Queue reward/risk is now `R:TP2` at the execution entry, not `R:TP1` at the stale plan close.
- Rows whose execution preview is `executable:false` are not offered at scan time and their Approve button is disabled; the execution preview has a 20-second display TTL and the queue tick repaints staleness. Editing levels requests fresh capacity and execution previews with `tp1/tp2`.
- A definite broker refusal after submit retires that candle instead of inviting an immediate retry, while keeping the reason in audit/Orders; unknown outcomes remain locked and are not automatically retried.
- Added regression tests for cookies/headers, nginx, global drift, preview/submit parity, and queue behaviour. This release performs no deployment, real order, or Live/Auto change.

## 2.1.8

- The last piece of noise in the preflight is gone: after `--verify` read the NBX_* values from `/etc/nobi/nobi.env` and passed them along, the checker still opened that file itself and, because the `nobi` user could not, printed "unreadable config file" on an otherwise fully green preflight. When the values arrived through the environment, the note is now suppressed — nothing was missing.
- `NBX_UNIT_ENV` itself starts with `NBX_`, so it counted as "the values were supplied" and silenced the warning on **every** host. It is the path override for that same file, not configuration, and is now excluded.
- The logic moved into `_unreadable_note()` so it can be tested, and the test covers both cases. A first version of the test re-implemented the expression instead of exercising the code; it was removed.
- Measured on a live host: values passed → no warning and no traceback; not passed → the warning is named.
- No data, order, setting, account or Live/Auto path changed.

## 2.1.7

- My own Windows test assumed POSIX again: `chmod(0o000)` does not make a file unreadable on Windows, so the assertion failed. This is the **second time** I made that mistake — 2.1.1 believed it was fixed, two runs went green, and I took that as a lesson learned. It is now guarded with the same `skipif` `test_deploy_tls_order.py` uses.
- 2.1.3's diagnosis of the `ui` job was **wrong**, and it should be said plainly: I called it a slow runner and raised the ceilings 60s->180s and 30s->120s. A later run then timed out *at* 120s — the sidebar opened and never closed. Removing a class in JavaScript is synchronous, so a 120-second wait means the click never reached `closeSide`, not that the box was slow. Raising the number only made the failure four times slower.
- `mobile_header_test.py` now names every sidebar-close attempt as it runs — source, direction, width, and whether it was the button or the overlay. The log previously could not say which case failed, and raising a ceiling blind is the same mistake again. The ceiling is back to 30s.
- Three consecutive local runs passed completely (80 layouts, ~100s each) and **did not reproduce the CI failure**. So this release claims no fix — it claims diagnosability.
- No data, order, setting, account or Live/Auto path changed.

## 2.1.6

- The previous release's patch crashed on a live host: `--verify` runs the checker as the `nobi` user, but `/etc/nobi/nobi.env` was not readable by that user there, and `_read_env_file` died with `PermissionError` — the operator got a traceback instead of a verdict. `deploy_arvan.sh` writes that file 0640 root:nobi, but an older or hardened install at 0600 breaks that, and my assumption was wrong.
- Two fixes: reading the file can no longer be fatal (it degrades to "could not read" and **names** it, deduplicated so it is not repeated per check), and `--verify` reads the `NBX_*` values as root and hands them to the checker through `env` — `sudo` resets the environment but `env`'s arguments survive it, and `_cfg` puts an exported variable above the files, which is systemd's own precedence.
- Both paths were measured: an unreadable file produces no traceback and is named once; passed values turn `token` green with no unreadable line. An earlier attempt used `systemctl show -p Environment`, which could not be verified in an environment with no system bus and was dropped.
- No data, order, setting, account or Live/Auto path changed.

## 2.1.5

- `check_deploy.py` read only `server/.env`, but the service is actually configured from `/etc/nobi/nobi.env` — `deploy/nobi.service` says so outright: "systemd's environment outranks server/.env". On a live host that produced two FAILs, both false. One said the journal was "inside the checkout", when `Path.resolve()` does not need the file to exist and that path was only the *default* — the real file was 40 MB on the attached volume. The other said `NBX_API_TOKEN is empty` when the token was set in the unit file. The app was healthy throughout.
- The fix reads both files in the order systemd resolves them: `server/.env` first, the unit's file on top. `NBX_UNIT_ENV` overrides the path for a non-standard install.
- `update_arvan.sh --verify` is now explicit that it passes no environment: `sudo` resets it, and the checker reads the file itself. A first attempt used `systemctl show -p Environment`, but it could not be verified in the test environment (no system bus) and was not kept.
- Three tests: the unit file is read, it outranks `server/.env`, and its absence is not a failure.
- No data, order, setting, account or Live/Auto path changed; this release repairs a preflight that lied.

## 2.1.4

- A bug in `scripts/update_arvan.sh` that a real install exposed: the "dirty checkout" guard read `git status --porcelain` without splitting it, and `git pull --ff-only` **does not care about untracked files at all** unless the incoming commit would overwrite them. Measured on a scratch clone: a checkout holding `.cache/` and `untracked.txt` fast-forwards cleanly, while a *modified tracked* file aborts the merge with "Please commit your changes or stash them". So the guard was blocking a live install over exactly the thing it exists to protect: the first cache directory a server grows.
- The fix: tracked changes always block (they are the work that would be lost), and an untracked path blocks only when origin's tree already holds it (`cat-file -e`). Untracked leftovers are reported so an operator is not surprised later, but they do not stop the run.
- One behavioural test exercises all three cases against a real repository: `.cache/` alone does not block; a modified tracked file blocks and names the file; an untracked path origin would overwrite blocks too. On the previous guard the test fails with a readable reason.
- The `test_platform_portability` guard proved itself again: it caught `text=True` without `encoding=` in the new test — the same Windows failure seen earlier.
- No data, order, setting, account or Live/Auto path changed; this release stops the update script from swallowing real work.

## 2.1.3

- Three timeout ceilings that a slow Windows runner was exceeding, each backed by a measurement rather than a guess: `probe_live.py`'s wait for the `/auth/login` reply and for the login gate to clear went 60s -> 180s (the page it waits for is an 887 KB document plus a fresh scan); `mobile_header_test.py`'s sidebar transform, previously on Playwright's 30s default, is now an explicit 120s; and the `node -e` subprocess in `test_execution_plan.py` went 30s -> 180s, because that same test takes 1.16s on the Linux runner and exceeded 30s on Windows — a 26x outlier that is node.EXE startup on a freshly provisioned runner, not the arithmetic.
- The evidence that these are flaky rather than a regression is recorded in the code: two consecutive runs of one commit stalled at **two different points**, the third run of the same commit was green, and every one of the 68 measured layout values in the failing run was correct.
- No data, order, setting, account or Live/Auto path changed; this release only aligns the ceilings with what was actually measured.

## 2.1.2

- A bug a real install's data exposed: `counts()` in `gate_ledger` built its `ready` flag from the two thresholds alone and **never filtered `UNKNOWN_CODES`**. That install held 16,413 rows over 6 days, 11,144 of them `no-book` and the rest `ok` — zero `spread-wide`, zero `book-thin`. `no-book` means "no order book was read", which is "this row could not be judged": not a block, and not evidence for or against one. `ready` is now True only for `MEASURABLE` codes, and `judgeable` says why not.
- The dashboard was wrong the same way: it displayed whichever code had the most verdicts, which is exactly the unjudgeable one. It now filters on `judgeable` first, and when the ledger holds nothing but unjudgeable rows it says so instead of "evidence collected".
- Two tests reproduce that real ledger's exact shape. Both go red on the old code (`assert True is False`), so this is a bug, not a hypothesis.
- `assets/css/tailwind.css` was rebuilt and came out untouched — confirming this change added no new class to the markup.
- No data, order, setting, account or Live/Auto path changed; this release repairs a lie in a report.

## 2.1.1

- Five real failures in the advisory Windows job, all from tests I had written that assumed POSIX. `test_the_script_is_executable` checks the exec bit, which git does not carry on Windows; three others run `bash`, which Windows does not have (one of them also died with a `UnicodeDecodeError` in the output reader thread). All five are now guarded with the same `skipif` `test_deploy_tls_order.py` already uses. Measured with `bash` removed from PATH: 57 pass, 4 skip.
- The evidence for the transient `ui` redness is recorded too: two consecutive runs of one commit stalled at **two different points** (`probe_live.py:498` and `mobile_header_test.py:99`), every one of the 68 measured layout values was correct, and the third run of the same commit was green. So it is flaky, not a regression — but the 60s login wait and the 30s sidebar wait no longer have room.
- No data, order, setting, account or Live/Auto path changed; this release repairs tests.

## 2.1.0

- A real bug in `scripts/update_arvan.sh`, surfaced by a run against a live host: the service-existence check used `grep -q` inside a pipeline, and under `set -o pipefail` that means exit **141 (SIGPIPE)** — `grep -q` exits the moment it matches, the writer upstream dies, and `pipefail` promotes the 141 to the pipeline's status. The result: **a unit that exists was reported as missing.** It now asks `systemctl show <unit> -p Id --value` — no pipeline, and non-zero when systemd does not know the unit. That is the same call the successful 2.0.3 upgrade tool used; the pattern was learned from it.
- The second finding, also from the live host: the checkout was at `origin/main` but the service was still serving 2.0.3, having never been restarted — and the script said "already up to date" and exited 0. That answer is true and useless. The early exit is now conditional on the service agreeing with the checkout; when it does not, the update path continues and says what is actually needed.
- Two regression tests: no executable `grep -q` remains, and the stale-service case no longer reports "up to date". Both were measured against fake `systemctl` output with 3000 units and nobi last, because the bug only appears with large output.
- No data, order, setting, account or Live/Auto path changed; this release repairs one script.

## 2.0.10

- `scripts/update_arvan.sh` no longer answers a missing service with "is this the right host?". The message was true and useless: the operator has three things they can act on — the unit is named something else, it is not installed, or this is the wrong host — and it distinguished none of them. The check now looks in three places: unit files, loaded units, and the file in `UNIT_DIR`, because `list-unit-files` alone misses a unit installed as a symlink or loaded at runtime. When nothing matches, it prints the units that *do* look like nobi so the name can be passed as `SERVICE=<name>` rather than guessed at.
- Two new tests for it, and both branches measured against fake `systemctl` output: a host whose unit is `nobitrader.service` gets it named and the override suggested, and a host with nothing nobi-like gets the diagnostic pair. The override itself was run end to end.
- No data, order, setting, account or Live/Auto path changed; this release is a better error message.

## 2.0.9

- `scripts/update_arvan.sh` is added: the manual update path on the server. The installer leaves three commands in the guide; this script is those three commands plus the things a human forgets. The most important one: **the installed systemd unit does not update with `git pull`** — the running unit is the copy in /etc, not the one in the checkout, so a hardening change to `deploy/nobi.service` never reached a deployed host. The script compares both before the restart and installs if they differ. The other three guards each came from a measured run: a dirty checkout is refused (`pull --ff-only` breaks on one anyway), local commits are counted (`--reset` silently discarded a local commit, because `status --porcelain` only sees uncommitted work), and if the service does not come back the rollback command is printed with the previous SHA. Flags: `--check` (look only), `--yes`, `--reset` (fork/branch), `--backup`, `--verify`, `--public URL`. `/etc/nobi/nobi.env` is never written, the data directory is untouched, and no firewall is opened.
- 19 new tests (text checks plus `bash -n`) cover the guards, including that no line writes to the env file and that `git clean` cannot delete the data. The script itself was run against fake `sudo`/`systemctl`/`curl` on PATH and every path measured: `--check` unchanged, a full update, already-current, dirty tree, diverged history, `--reset`, and a service that does not come back with the rollback message.
- `docs/deploy-arvan.md` §۱۰ and `docs/deploy-arvan-cloud.md` §۱۳ both point at the script and explain why the "root-owned templates" step is no longer manual.
- No data, order, setting, account or Live/Auto path changed; this release is one command-line tool.

## 2.0.8

- A defect that the measurement itself exposed, now fixed: `--resolve` showed the first eight candidates and hid the rest. When `WBTC` was resolved, the search returned 25 rows — the first 24 are wrapped derivatives (`instadapp-wbtc`, `cWBTC`, `aave-wbtc`, …) and the coin Nobitex means, `wrapped-bitcoin`, is **row 14**. The correct answer was not on screen. Worse: the id a reader expects, `wbtc`, returns **HTTP 404** from `/coins/wbtc`; CoinGecko renamed the coin to `wrapped-bitcoin`, so even the "obvious" id is wrong. The eight-row limit was removed rather than warned about, because a warning can be scrolled past. A new test builds exactly that case: 25 candidates with the right answer at row 14.
- Three unmapped symbols closed, each one a human decision rather than the script's: `DOGS` → `dogs-2` (the first row was `cat-in-a-dogs-world`, a cat), `NOT` → `notcoin`, `WBTC` → `wrapped-bitcoin`. Mapped base assets 51 → **54**. `--check` returns 0 for all three, and the registry took only those three lines — the rest of the diff is sorting, because the file was not sorted before and the atomic write sorts keys.
- ADR-0025 now records this failure, as the reason "every candidate" is load-bearing and "never guess" is a contract rather than a slogan.

## 2.0.7

- `scripts/resolve_fa.py` is added: the offline half of deciding a symbol mapping. `fa_engine` needs a CoinGecko id for every base asset, and a market with no id is skipped — its score is built from five neutral values of 50 plus one global Fear & Greed reading, honestly reported as `profile: "none"`, but with no measurement in it at all. ADR-0025 refused to close that gap during a scan, because 2,626 of 17,425 symbols (15%) match more than one coin. This script closes it offline: `--check` (no network) says which market is unmapped, `--resolve` prints every candidate with its id, name, symbol and market-cap rank and **chooses none of them**, and `--add` writes only after a human has decided. Measured against the live API while writing this: `DOGS` returns seventeen candidates and the first is `cat-in-a-dogs-world` — a cat; `dogs-2` is the second row. The script never runs from the dashboard: `fa_engine` reads the registry, never this. 36 offline tests (network stubbed) cover the paths, including refusing to overwrite an existing mapping without `--force` and the registry surviving a half-finished write.
- Three stale figures in the living documents were corrected, and each now has a test so it cannot go stale again: the panel count (45 instead of 44, in both READMEs, the testing guide, the Windows guide and ADR-0016 — 13 places), the CI job count ("three jobs" instead of four, because the advisory Windows job had not been added to the sentence), and the test count (the "591 passed" line, removed because the suite had reached 1460 and any number would go stale again).
- Two references to scripts that had never existed were removed: `preflight_arvan.sh` in `docs/deploy-arvan.md`, and `scripts/resolve_fa.py` in ADR-0025 — the latter now exists, the former does not and the removal stands.
- No data, order, setting, account or Live/Auto path changed; this release is one command-line tool and a documentation fix.

## 2.0.6

- The "no panel stays blank" contract in `probe_live.py` is now actually enforced: a panel with no meaningful content no longer sits behind an em-dash but says why. Two panels that used to come up empty — paper profit factor (`kPf`) and performance statistics (`pHit`) — now state the not-recorded condition, and `rkAtr`, which has no element with that id in the page, was removed from the probe's list. The result: the probe checks **44 panels**, not 45.
- The panel count is no longer hardcoded in a step name. The name said "45 panels" while the probe verified 44 — an inconsistency left behind when `rkAtr` was removed in `0bc4d3d` without renaming the step. The step is now just `every panel explains itself`, and `probe_live.py --json probe-panels.json` writes structured output; the next step, with `if: always()`, publishes the real panel count, the API cross-checks and the list of broken panels to `$GITHUB_STEP_SUMMARY`. There is no number left to go stale.
- The probe's login wait now tells refusal apart from slowness. Run 183 went red on `#authGate` with a `TimeoutError`: a successful login calls `location.reload()`, and re-reading an 887 KB page on a loaded CI runner takes longer than 10 seconds — so "credentials refused" was indistinguishable from "the runner is slow". The probe now waits for the `/auth/login` reply first: a non-200 is reported immediately with the server's own detail, and the gate then gets 60 seconds, quoting the page's `#authMsg` if it is still up. Both paths were exercised locally.
- The regressions that the phase 1–13 test files themselves introduced were repaired (`f3f5d96`), and the development dependencies were refreshed: playwright 1.64.1, pydantic-settings 2.16.1, pytest 9.1.1, pyarrow 26.0.0, websockets 17.3.
- No data, order, real setting, account, execution policy or Live/Auto path changed; this release only fixes the probe tooling, the CI structure and the version number.

## 2.0.5

- CI red fixes: the two shadow-gate tests (ledger and prefetch) no longer depend on the exact number of spaces before the `except` comment in `server/main.py`; they match the guard's shape, so reformatting the file can no longer turn them red. The portability test now reads and writes the files in two test modules with `encoding="utf-8"` (cp1252 on Windows raised), and the execution-mode load test no longer counts the error log's own journal write as a retry — it failed only in a full-suite run, never alone.
- In the Performance report, when a read succeeds but no horizon has resolved yet, the hit-rate KPI now says "Performance data not recorded" instead of a bare em-dash: the same "no panel stays blank" contract `probe_live.py` checks. The phase 9 regression test covers that state too, and the standalone mobile build is regenerated from the same source.
- The Python floor now matches what actually installs: `numpy==2.5.4` publishes `Requires-Python >=3.12`, so the deploy preflight (`check_deploy.py`) and the Windows/cloud docs no longer accept 3.11. A new test keeps the two in step, and the ruff/mypy targets are 3.12 too. Version references in `AGENTS.md`, `SECURITY.md` and the release examples now say 2.0.5. The advisory Windows job also publishes every failing test name as a check annotation, so its red X is no longer anonymous. That is what surfaced this one: the read-only-journal test created a file whose name contained `?`, which Windows does not allow; that case is now skipped there with a reason, while the space and `#` cases still run everywhere. The mobile-header layout test now prints every case with its measured widths, so an intermittent failure there is traceable.
- No data, order, real setting, account or Live/Auto path changed; this release only fixes tests, one empty UI state, the Python floor and the release number.

## 2.0.4

- Twelfth reviewed-page fixes: on Overview, the Market State card separates ticker snapshot time from browser receipt time, reports ticker and selected-timeframe signal-mix age/staleness, refuses boolean/non-finite/negative-volume or late responses, and clears stale renders on error or invalid payloads. The capital-allocation donut hides zero-share arcs so 100% cash cannot render a false altcoin dot and refuses USD allocation when a valid USDT/IRT conversion rate is unavailable. The open-positions count badge and read-only paper-book refresh button now state their actual behaviour. Users/My Account and execution/account/server policies are unchanged.

- Eleventh reviewed-page fixes: additive read-only Signal Intelligence view shares filters and a read transaction across summary/cohorts/rows. Opt-in export declares actual scope, cap/truncation and read time without changing the default legacy export or removing audit. Selection changes invalidate evidence/export and late responses are refused. as_of bounds issuance, not historical outcomes. Gross model, count threshold 30 and independence caveats remain; invalid/N/A model observations are excluded from metrics, not rewritten. Users/My Account and execution/account/server policies are unchanged.

- Tenth reviewed-page fixes: consistent Error Log snapshot rows/counts with unpersisted-memory merge, explicit storage/scope, scoped export and stale-response/invalid-diagnostic refusal. Self-test verifies a unique SQLite marker, not ID collision; failed clear preserves memory evidence. Strengthen bounded best-effort structured/read-time redaction; journal diagnostics no longer evaluates/writes Stage. No real logs or history cleared/rewritten.

- Ninth reviewed-page fixes: distinguish failed/empty/server-required Performance reads and suppress stale responses; preserve NEUTRAL/EXIT and LONG-model N/A; expose per-horizon provenance, numeric calibration with n, and family/MSI/benchmark limitations. No-loss PF is JSON-safe and labeled as sample-infinite. Stage GET reads recorded state without evaluation or writes; the UI requires the paired summary capability before issuing Stage requests, so legacy mutating servers are not probed; the legacy policy is unchanged and an independently scheduled evaluator remains pending. Correct early modeled exit candle labels for new outcomes only, not historical rows or PnL. Users/My Account, baselines, weights and Live are unchanged.

- First reviewed-page fixes: separate effective/managed/default Settings values and submit edits only; reject controls with a shared loader/writer allowlist; make path probes non-creating; use effective backup paths, private archives and missing/empty-source refusal.
- Block online restore. CLI restore requires offline attestation and explicit destination paths, refuses SQLite sidecars and rolls back ordinary intermediate failures. It is not cross-DB crash atomicity or automatic writer shutdown.
- Release wizard locks on every path; show unknown instead of stale success/green zero for Settings/Error Log failures; escape Settings and error-source summary text and hide connection URLs; separate drift/cohort targets and remove duplicate Secrets.
- Protect journal evidence: top60 cannot authorize automatic VOID; suspend automatic retirement pending an authoritative lifecycle feed, without rewriting history. Retry execution-mode loading after an initial failure.
- Users/My account are excluded. No real configuration/order/Live/Auto/five-day test changes. Local uncommitted work only; version bump, publication and installation remain separate.

- Second reviewed-page fixes: failed server paper/risk reads no longer silently substitute the browser account; local provenance and unknown states are explicit. Label equity change since book start and retained exit-event PF truthfully; use actual initial-stop distance for R, the reported heat cap, and the full open-position count rather than a display slice.
- Avoid PF division by zero on break-even exits; expose undefined/capped PF without rewriting PnL/history. Signal Intelligence separates non-LONG/invalid-input observations from pending/due, explains nominal horizons, the count-only threshold, gross modeling and filter/export scope. Drift errors are not empty-run success; clear stale exports/rendering and suppress obsolete async responses. Canonical detail is flat; add defensive legacy-envelope compatibility, validation and escaping without changing the server contract.

- Third reviewed-page fixes: preserve rejected attempts independently of related position state; separate entry fills, retained exit events and model-reported remaining quantity. Remove arbitrary legacy-position matching; expose unknown/truncated-history scope, exit details, side-normalized adverse slippage, read time, refresh/failure state and escaping without repairing accounts.
- Manual submission is locally single-flight; missing responses mean unknown outcome, not proven non-placement, with no automatic retry. Record one local outcome instead of optimistic/duplicate success; preserve drafts and invalidate stale risk previews. Disable unsupported Market and make unsent TP1 explicitly read-only pending a full target contract. Use only prefills the ticket; owner/CSRF/preflight/Live policies remain unchanged.

- Phase 4: queue countdown patches deadline text only. Valid, unchanged signal identities retain edited nodes, incomplete numeric input, focus and selection through refresh; changed signal/timeframe or expiry cannot inherit the old draft/whatif. Local dismissal/success follows symbol+timeframe+candle, not a permanent symbol blacklist.
- Queue whatif is capacity-only; malformed responses no longer manufacture green zero risk. Same-candle in-flight and unknown-outcome locks survive refresh/material revisions on this page, within the proposal lifetime; this is neither durable idempotency nor resolution of uncertain submissions. Rejection audit is local only. Backend TTL/policy, owner/CSRF, caps and final gates remain unchanged. Standalone rebuilt; 516 offline checks cover both artifacts and languages.

- Phase 5, Risk & Controls: replace independent ATR/guessed-FX sizing and fixed below-25% tick with the server-sized plan, explicit units and no final-acceptance guarantee. Malformed, blocked or obsolete reads cannot retain valid-looking sizes. Missing caps/halt/feed values are neutral/unknown; raw engine P&L is not a reconciled day ledger or an automatic daily-stop guarantee.
- Halt/resume success requires a valid acknowledgement. Single-flight and unknown-result guards require explicit state reread; old polling cannot overwrite newer control results. Cancel/flatten remain unsupported with flatten disabled; scope is new-entry Paper halt, with no restart-persistence guarantee. Backend, unit conversion, strategy and real account unchanged. 1144 new offline UI checks and 234 selected private Python tests pass.

- Phase 6, Fundamentals: reuse complete scan FA snapshots; request only missing details within the existing six-call budget. Distinguish not-requested, failed, unmapped, static, cached and receipt-unverified records without calling unrequested rows provider failures. Covered-sample mean excludes unmapped defaults and scan-only numbers; bar and denominator agree. Veto symbols/replies and reason instances have separate counts.
- Show-all and language redraw do not refetch FA; obsolete scan/timeframe replies cannot repaint current data. Server live/fresh labels are not independent receipt evidence; premium uses the BTC cross reference and page receive time is not source observation age. Unconnected event feed is explicit. FA model/cache, vetoes, registry, weights, units and real account remain unchanged. 500 new JS checks and 256 selected private Python tests pass.

- Phase seven, Backtest Lab: settle each terminal curve point to cash/PnL, seed server MDD from initial capital and retain real zero endpoints. The experimental browser engine reconciles already-paid entry costs and EOD exit fees/slippage, then computes chained MDD over full internal curves before display truncation; entry rules, quantities and raw stop/target levels are unchanged.
- New runs gain distinct run IDs, collision-resistant same-second filenames, generation time and effective base settings. The legacy parameter hash is explicitly not complete run/dataset identity. History/gate readers share the writer's effective directory. Redraw retains freshness envelopes and generation time, names missing age metadata, avoids new-run notifications and separates trade timestamps from prices.
- Backend failure/malformed replies no longer silently invoke a different local execution model; explicit standalone mode remains experimental and labeled. Grid now transmits bars; other Grid/Multi form-contract differences, same-window winner selection and three preliminary checks are disclosed. Correct generic timeframe and factor-one clamp labels; disclose rather than repair the A/B formula/full-history factor-selection limitation. Users/My Account, live strategy, auth, TTL, units and real operations remain untouched.
- The expanded phase-seven icon gate exposed the earlier FA-row icon omission; restore the local logo only. Its structural assertion now checks the helper/size rather than the loop-variable name; update the older missing-age assertion to explicit unknown.

- Phase eight, Signals Explorer: align liquidity labels with the existing 24-hour turnover proxy. Per-row FA provenance and whole-snapshot coverage distinguish reported server/cache/static/unmapped/number-only/absent/mismatched inputs; backend fallback is not necessarily static. Weights, order and directions are unchanged.
- Separate page receipt, server-reported cache age and candle freshness. Read the polling preference used by the scheduler instead of fixed two minutes; name snapshot/selection timeframe drift and missing metadata. Make shadow unknowns, book coverage and overlapping stale-book counts visible; zero objections is not order approval. Empty/failed scans do not validate old evidence or establish an all-market outage.
- Signals sparklines are neutral and use only timeframe-keyed cache; missing prices do not become zero and bare-symbol cache cannot stand in for selected-timeframe data. Show-all retains missing-candle prefetch, without FA/score recomputation. Backend models/caps/TTL, real operations and Users/My Account are unchanged.

- Phase 13, Risk audit page: opt-in audit scope (`/api/audit?scope=all|control|preview`) reads from readonly SQLite within a transaction; the legacy default path without scope is preserved. `control` excludes only the two known preview actions (`risk.whatif`, `portfolio.allocate`); remaining events are not necessarily control actions. Output includes `ok, events, counts, n, total, limit, action, scope, days`. Audit failure is distinguished from empty; notify failure is distinguished from not-configured. Actor/client are displayed with escaping. Telegram `configured` does not mean delivery; standalone mode has a distinct message. The cap label is per-order nominal cap (not cumulative symbol). Audit data is cleared on identity/permission/language change and stale late replies are rejected. 12 VM checks and 40 JSDOM checks (phase 13) passed. Users/My Account out of scope. No settings/account/server/Live/Auto changes.

## 2.0.3

- Fix Paper plan acceptance after execution-price drift. Validate finite, positive, ordered stops/targets against the precision-rounded fill using signed distances: `stop < entry < tp1 <= tp2` for buys, reversed for sells. A target behind entry must not become a reward through an absolute value.
- Recheck the existing `MIN_RR=1.5` policy using TP2 and the final execution price, not the queue card's TP1. Tolerance only covers floating-point boundary noise. This PATCH corrects faulty behavior without introducing a strategy or threshold change; fees remain modelled separately.
- Persist `execution-plan-invalid` / `execution-rr-too-low` rejections with submitted levels and ratio diagnostics before mutating positions, cash or reserved risk. Never re-anchor targets to make an order pass. QUEUE requires all three explicit levels; missing/null levels cannot silently become defaults, and zero/NaN/Inf are invalid.
- Apply the same signed-geometry and RR guard to the browser broker, with deterministic Python/JavaScript parity coverage. Preserve each broker's existing omitted-level defaults for non-QUEUE orders; an invalid combination with a custom stop is rejected.
- Cover slippage crossing TP1, degraded TP2 RR, boundaries, valid TP1 at 1.2R, durable rejection, unchanged existing positions and a synthetic offline exit lifecycle. Independent gate-test fixtures now supply valid explicit TP2 levels. Preserve the original captured book unchanged: consistent units alone do not guarantee execution-price plan validity.
- Do not rewrite, delete or automatically close existing positions/history on load. Risk caps, scans, semi/Auto, Live and exit rules are unchanged. No formal five-day start or Live authorization; publication and installation are separate steps.

## 2.0.2

- Moved the connection badge and its details from a floating bottom-corner overlay into an in-flow disclosure above the main content. Expanded details reserve their own space instead of covering sidebar links or page content; the panel has an opaque background and supports RTL/LTR and mobile layouts.
- Preserved disclosure state and button focus across connection refreshes; added keyboard/Escape and `aria-expanded` support. Explicitly distinguished the last page update from the independent background server scan.
- Added offline browser regression coverage for source and standalone builds, widths 320–1920, short viewports, both directions, sidebar overlap, keyboard access, and long/untrusted strings. Trading behavior, scanning, APIs, caps, Live/Auto and account data are unchanged.
- Isolated the POSIX-absence simulation from the unrelated, newly auto-loaded `platformdirs` pytest plugin. Its Linux backend selection in a simulated environment must not masquerade as a Windows failure; all project modules still collect and helper assertions still run with those POSIX calls removed.

## 2.0.1

- Separate REST UDF Toman prices from raw Rial book/stats; add explicit UDF override and a versioned cache namespace without deleting/rescaling legacy data.
- Rebuild portfolio heat from restored open legs; reserve remaining entry risk proportionally after partial exits without releasing extra budget on stop moves. Historical entry risk remains intact for R-multiples.
- Report final capped risk separately from its allocation budget; avoid applying notional/heat reductions twice and preserve sub-cent USDT price precision.
- Normalize derived IRT reference candles to Toman while preserving reference-only promotion fences.
- Add a journal price contract and qualified-evidence view: preserve/export legacy IRT records, but do not resolve or score them against the new contract. Quality, MSI and drift consumers exclude unknown-scale IRT history; unchanged legacy USDT remains eligible.
- Persist numeric Paper rejection diagnostics for new events; never fabricate missing historical inputs.
- Risk caps, concurrency, Live/Auto, manual defaults and queue deadlines remain unchanged. No formal five-day start or Live authorization.
- Normalize raw IRT options price ticks from Rial to internal Toman exactly once, independently of the book override; preserve raw metadata, reject invalid ticks and use decimal half-up rounding consistently in Python and JavaScript (including `1.005 → 1.01`).
- Correct the WS candle default to Toman using the user’s 2026-10-28 public same-bar capture: DOGEIRT WS/REST OHLCV matches exactly without another division by ten; BTCUSDT stays unchanged. Preserve the raw fixture with a SHA256 regression test. Evidence covers those two 15m markets, not every channel or live execution. Keep an independent alternate-source `NBX_WS_CANDLE_PRICE_UNIT` override and observable invalid/mismatched-candle guards that preserve REST without guessing a scale.
- Document a code-and-state compatible upgrade/rollback plan and add two offline rehearsals for schema and Paper/.bak restoration while preserving post-upgrade snapshots. These are not systemd deployment tests. Run tick/rounding parity in Release validation as well as CI.

## 2.0.0

> Bounded closed-bar approvals and browser-independent validation setup.

- **QUEUE contract change:** plans explicitly request server approval context;
  QUEUE submissions require its timeframe and signal key. Old tabs must reload.
  Regular previews/manual tickets keep their existing request contract. Major
  version follows the incompatible approval contract and the documented MINOR
  ceiling (1.10.x); this is not a Live-trading enablement.
- Use the exact scan's closed source candle, not chart-cache time: validity ends
  at `source_open + 2*timeframe`, one execution candle after close. Refresh cannot
  renew it. Missing, unfinished, invalid, stale or changed sources fail closed.
- Recheck mode, signal and deadline server-side before broker work AND after
  book/FX awaits, immediately before account mutation. Stable persisted
  clientOrderId prevents repeat/concurrent approvals of a source bar, including
  after reload. Existing risk caps, Live settings and state schema are unchanged.
- Exclude unfinished candles before scanning; formulas are unchanged but rankings
  may differ when the prior input included a forming bar. Journal timestamps now
  use the same scored bar instead of another candle fetch.
- Display backend score/time and a server-relative elapsed-time countdown that
  catches up after suspended tabs. Health/plan responses are not cacheable.
- Add scheduled-scan count/freshness telemetry and a separately opted-in,
  backup/rollback-tested systemd helper for 300-second scans on 15m. The default
  remains off; the helper does not change modes or begin the formal five-day test.
- Correct the old integration fixture's milliseconds/seconds mistake and test
  closed/open bars, stale clocks, browser skew, expiry races, duplicate approvals,
  strict timeframe provenance and read-only evidence checks.

## 1.10.4

> Fix approval-queue updates and diagnostics, and the reported error-log issues.

- Refresh Desk approval proposals on the backend's actual `nbx:scan` event;
  load execution mode before picking candidates and ignore stale async replies.
  Pass the active timeframe to plan/edit previews; show queue timeframe and
  rejection reasons. Keep the five-plan budget, LONG/semi filtering, dismissal,
  expiry and order-submission rules. Add offline queue tests to CI and Release.

- Fix `already.remove is not a function` when closing journal signal details;
  add DOM tests for open/close/reopen, switching, caching and delegated clicks.
- Bound repeated known history-refusal warnings in scan/baseline by operation,
  symbol and timeframe: retain the first warning and a 15-minute repeat count.
  Successful retrieval resets the episode; outages and other errors remain visible.
- Add allowlisted public endpoint/symbol/resolution and HTTP status to client
  diagnostics, without adding full URLs, headers or response bodies to its log.
  A 403 remains an error; these fields do not establish its ultimate cause.
- Test bounded logging, diagnostic privacy and unchanged scan results. Existing
  error-log history is retained; no trading logic or Live-mode changes.

## 1.10.3

> Fix mobile horizontal overflow and the partially visible closed sidebar after sign-in.

- Allow authenticated header controls (account name, role and logout) to wrap
  on mobile without hiding or removing controls. Previously the single-row
  header exceeded the viewport; mobile RTL document scrolling exposed part of
  the closed drawer, and clicking close did not repair that document offset.
  The preview fix was confirmed on the reporting user's phone.
- Add `scripts/mobile_header_test.py` to the UI CI job: 80 offline browser cases
  cover source and standalone, ten widths from 320 to 1440, RTL/LTR, and short/long
  account labels. Check document/header overflow, horizontal offset, and drawer
  close via its button and overlay. Account controls are DOM fixtures, not
  authentication/API tests. The test reproduces the failure on the previous code.
- Correct the Arvancloud guide's preflight command to run as root: the `nobi`
  user cannot traverse the parent `/etc/nobi` directory.
- No trading logic, order requests or live-mode defaults changed.

## 1.10.2

> Fix the ArvanCloud installer's TLS ordering and add a full deployment runbook.

- `scripts/deploy_arvan.sh` no longer installs the TLS site before obtaining
  a certificate. Up to 1.10.1, `nginx -t` failed on a fresh host with
  `cannot load certificate ... No such file or directory`, and the
  `certbot --nginx` call that ran *after* it was never reached — so `--tls`
  could not work on a first install. The installer now runs
  `certbot certonly --webroot` over a temporary port-80 ACME site first,
  then writes the TLS site and runs `nginx -t` / `reload`.
- An existing certificate is reused, the script stays idempotent, certbot
  failures produce a clear DNS/CDN/firewall message without leaving a
  half-written config, and `--tls` combined with `--no-nginx` warns instead
  of being silently ignored.
- Six regression tests in `server/tests/test_deploy_tls_order.py` pin the
  order, the `certonly --webroot` choice, the port-80 fallback, and bash
  syntax. The `bash -n` check skips on Windows (no POSIX shell);
  the five text-based checks run on every platform.
- Added `docs/deploy-arvan-cloud.md`: a comprehensive ArvanCloud runbook
  (architecture, DNS/firewall, TLS, accounts, verification, backup,
  upgrades, troubleshooting and a checklist), linked from both docs maps
  and from the older `docs/deploy-arvan.md`.

## 1.10.1

> Dependency refresh and compatibility with the current toolchain, following
> the 1.10.0 release.

- Reviewed and merged all ten Dependabot PRs: `actions/checkout`,
  `actions/setup-python` and `actions/setup-node` to v7, `ruff 0.17.2`,
  `uvicorn 0.54.0`, `numpy 2.5.4`, `tenacity 9.2.1`, `eslint 10.13.0`,
  `jsdom 30.1.3` and `pandas 3.1.0`. No open PRs remain.
- CI and Release moved from Node 20 to **Node 24**: jsdom 30 pulls undici 8,
  which needs `zlib.createZstdDecompress` (Node >= 22.22.2). The requirement
  is documented in `CONTRIBUTING.md`.
- `@eslint/js` is now an explicit devDependency (ESLint 10 no longer brings
  it in transitively), and five dead initialisers flagged by the new
  `no-useless-assignment` rule were removed from `index.html`; the standalone
  build was regenerated.
- `ui_smoke_test.js` now bridges jsdom-realm `AbortSignal` objects into a
  Node-realm `AbortController`. Node >= 22's fetch rejects foreign signals
  with an `instanceof` check, silently breaking twenty requests and leaving
  the dashboard half-rendered.
- Added `journal.epoch_seconds()`. pandas 3 infers `datetime64[us]` (or `[s]`)
  where pandas 2 always used `[ns]`, so dividing `int64` by `10**9` produced
  1970 timestamps. It is used in `simulate_long`, `resolve_symbol` and the
  main backfill path, with a cross-unit regression test.
- 1011 Python tests pass on both pandas 3.1.0 and 2.2.3; ruff, mypy,
  pip-audit, npm audit, nine JS gates and the reproducible build are green.

## 1.10.0

> Deployment hardening and the gaps CI could not see.

- The installer's privileged helper moved from `sudo` to a **socket-activated
  root service**; `NoNewPrivileges=yes` and `ProtectSystem=strict` on the web
  unit no longer conflict with the browser installer.
- systemd/nginx templates are now `root:root 0444` outside the checkout and the
  helper validates their owner, mode and content. The domain comes from
  root-owned `/etc/nobi/domain`, not from a request body.
- The two incompatible helper protocols were unified: `/api/operations/*` uses
  the wizard's protocol 1, `apply-config` is retired (410) and `restart` maps to
  `restart-service`.
- Dependencies upgraded (FastAPI 0.162.0, Starlette 1.10.0, Pydantic 2.14.1,
  cryptography 50.0.2, python-dotenv 1.2.4, pyarrow 23.0.1); `pip-audit` is clean.
- `pip-audit`, `npm audit` and Dependabot are CI gates; the Windows job is
  genuinely advisory and workflow permissions are least-privilege.
- The stale committed ZIP was removed; the download route now builds a fresh
  archive from the running checkout (no `.env`, databases or caches).
- Central DOM escaping for server/exchange strings, fixed the IIFE-scoped
  `onclick="scanNow(true)"` retry button, and added `npm run test:escaping`.
- A read budget for expensive endpoints, bounded rate-limit counters, and no
  `Secure`-cookie downgrade via an untrusted `X-Forwarded-Proto` header.
- 500 responses no longer echo exception text; only a `log_id` is returned.
- Dockerfile sets its data paths via `ENV`, compose binds loopback only, the
  manifest has an icon, and `version-check.html` reads the live version.
- `package-lock.json` version/tarball mismatches fixed and gated by
  `npm run test:lock`; versions/docs aligned; `SECURITY.md` and
  `CONTRIBUTING.md` added. Added an **MIT `LICENSE`**.
- Helper tests are now Windows-safe (`os.name != "posix"`, `raising=False`);
  a regression test re-runs the module with POSIX-only attributes removed so
  the Windows job stays green. 1010 tests pass (1009 pass + 1 skip under the
  simulated-Windows run).
- The daemon's `CLI` is now a plain string: `Path("/usr/...")` renders as
  `\usr\...` on Windows and silently changed the helper's fixed argv.
- **End-to-end tested on a real systemd host** (systemd 257 + nginx 1.26):
  `root:nobi 0660` socket, root-service activation, `LISTEN_FDS`, `check`,
  `install-service`, `install-proxy` (with `nginx -t`), `restart-service`,
  `reload-proxy` and `rollback`, all invoked as an unprivileged user.
- Two bugs found by that test were fixed: `rollback` used to fail after a
  **first-ever** install (no backup existed) and silently restored only one of
  the two managed files. It now records `.absent` markers, removes a dangling
  nginx symlink and surfaces the real failure reason. World-writable,
  non-root-owned and symlinked templates, plus a hostile domain file, are all
  rejected.

## 1.9.0

- Added an owner-only, resumable browser server-installation state machine.
- Added non-mutating host preflight for OS, privileges, Python, disk, paths, ports, helper, systemd, and nginx.
- Added a protocol-versioned privileged helper with exact action/argv allowlists, fixed paths, atomic writes, backups, validation, restart/reload, and rollback.
- Added bounded secret-redacted output, mutation locking, confirmation gates, persisted run history, bilingual UI, and security tests.
- Live trading remains disabled/dry-run; the installer does not promote real trading.

## 1.8.0

> Operational hardening for Signal Intelligence drift monitoring.

- An owner-controlled, disabled-by-default 6/12/24-hour scheduler runs one due analysis without replay storms and records failures.
- Persistent schedule state and config inventory expose next/last run, active hashes and baseline/current continuity.
- The scheduler is supervised with the existing server lifecycle and keeps trading behavior unchanged.

## 1.7.0

> Signal Intelligence v1.2 adds immutable, statistically controlled drift evidence.

- Equal non-overlapping baseline/current windows compare outcome, score-distribution and linked pipeline metrics.
- Benjamini–Hochberg correction, unequal-variance expectancy intervals, PSI and explicit minimum samples prevent unsupported alerts.
- Auditable drift runs/findings, append-only review, role-gated execution and a bilingual dashboard panel were added.

## 1.6.8

> Signal Intelligence v1.1 with deterministic Scan→Gate→Signal linkage and auditable statistics.

- Cycles, candidates and normalized multi-cause gate reasons use an idempotent schema; old data stays explicitly `legacy-unlinked` without inference.
- Versioned summary/pipeline/reasons/cursor APIs and audited, 10,000-row bounded CSV/JSON export were added.
- Wilson 95%, approximate expectancy CI, linkage coverage, and pending-not-due versus resolver backlog are reported.
- The UI exposes coverage, intervals, linked funnel, taxonomy and export; migration, provenance, pagination and CSV-injection tests were added.

## 1.6.7

> Remove an accidentally staged empty runtime database from the 1.6.6 source tree.

- Runtime live databases, setup tokens and operations audit files are now explicitly ignored; no credential or live order existed in the removed empty fixture.

## 1.6.6

> Run both UI harnesses through the real authenticated session contract.

- jsdom now mirrors cookies through its cookie jar, submits the real login form, and validates protected pages with the same short-lived owner as Playwright.

## 1.6.5

> Isolate unauthenticated rendering smoke from the authenticated browser contract in CI.

- jsdom now validates the supported unclaimed local-install state before CI creates its temporary owner; the real-browser probe then logs in and validates protected panels.

## 1.6.4

> Preserve the authenticated API context for the final UI/API equity cross-check.

- Probe preflight now fetches risk state through its authenticated cookie jar and reuses that evidence instead of making one anonymous fallback request.

## 1.6.3

> Final CI correction after reading the authenticated GitHub job logs.

- The probe preflight now authenticates too, so it does not mistake protected 401 responses for an upstream outage after the browser has logged in.
- The remaining empty-snapshot and test SQLite handles are explicitly closed, and the operations audit mode assertion is POSIX-only.

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