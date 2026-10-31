# ADR-0016: Static gates (ruff, mypy, eslint) and CI

[فارسی](../fa/ADR-0016-دروازه‌های-ایستا-و-CI.md) · **English**

- **Status:** Accepted and implemented
- **Date:** 2026-10-20
- **Decision owner:** technical architect
- **Related:** 0002 (stack), 0014 (data-honesty contract), 0015 (unit boundary)
- **Enforced by:** `pyproject.toml`, `eslint.config.mjs`, `scripts/lint_dashboard.mjs`, `.github/workflows/ci.yml`

---

## 1. Context

There were no static gates: no ruff, no mypy, no eslint, no CI. 278 tests were
green while, at the same time, measured on the parent commit with the
configuration this ADR ships (`git worktree add /tmp/pre 18e7a2f~1`):

- ruff reported **277** findings — 53 of them `# noqa` comments whose code was no
  longer selected, 41 unsorted imports, 31 unused imports, 30
  `datetime.timezone.utc`, 26 quoted annotations,
- mypy reported **80** errors across 13 files,
- eslint reported **29** errors — 25 in the 4,000-line inline block in
  `index.html` that no tool read, 4 in the two bundles.

An earlier draft of this ADR quoted 255 / 74 / 197. Those were taken at different
points in the work, not from a single reproducible state, and the 197 could not be
reproduced at all; they are corrected above rather than quietly dropped.

The cost was paid exactly where the bugs came from — **cross-cutting seams**.
Four examples, all previously "green". Note where each was actually caught: only
the last one came from these gates; the other three came from the invariant tests
(ADR before this one), the money refactor, and the live probe. The
`18e7a2f` commit message attributes all four to the gates — that attribution is
wrong, and this paragraph replaces it.

1. `main.py`'s `auto_tick` still contained `equity_quote = eq * fx if fx else eq`:
   with no fx rate, a USD balance was read as Toman and every automatic order was
   ten times too large.
2. `_position_payload` passed a Toman amount through `from_api`, which reads Rial:
   `value_usd` came out ten times too small — the same factor of ten, arriving
   through the helper written to prevent it.
3. `let candleSeries, lastCandle;` was declared in `index.html` and written in
   `nbx-app.js`. Removing the "unused" declaration turned that write into a
   `ReferenceError` swallowed by `catch (e) {}`; the last-price chip stayed
   silently at "—".
4. Three translation tables had duplicate keys; the second silently overwrote the
   first (`perf.origin` carried two different translations).

## 2. Decision

Three gates, all in CI, all able to fail the build:

| Tool | Scope | What it catches |
|---|---|---|
| `ruff` | `server/`, `scripts/` | unused/unsorted imports, undefined names, conditional compares, `zip` without `strict` |
| `mypy` | `server/` (35 files) | `None` that reaches arithmetic, `dict[str, object]` treated as a number |
| `eslint` | `nbx-core.js`, `nbx-app.js`, `scripts/*.js`, **and the inline block in `index.html`** | undefined names, duplicate keys, dead variables |

The inline block is extracted by `scripts/lint_dashboard.mjs` and linted with the
same config; reported line numbers are `index.html` line numbers.

### Deliberately not enabled

- **`E501` (line length).** 97 lines in `server/` are longer than 100 columns
  (re-count: `awk 'length>100' $(find server -name '*.py')`); shortening them
  would be a whitespace-only commit that makes `git blame` useless. `line-length`
  is still set so a future formatter run produces 100-column output.
- **`BLE001` / `S110` (bare except, pass).** The polling loops catch `Exception`
  on purpose: one bad tick must not kill the socket. `errorlog` is where the
  failure is recorded, and each site keeps its explanation as a plain comment.
- ~~**mypy `--check-untyped-defs`.**~~ **Now enabled** (see §5). Turning it on
  surfaced 90 findings: 4 in `server/main.py` (one untyped `rows` list made a
  sort and a `join` unverifiable — now annotated) and 86 inside test doubles.
  Test modules opt out on their first line; every server module is checked body
  and signature.

## 3. Consequences

- **Positive:** 277 + 80 + 29 findings went to zero, and the four real bugs above
  were found and fixed in the same period. `nobitex-mobile.html` (a generated file) is
  rebuilt and compared in CI so it cannot drift from its sources.
- **Negative:** a new unused import now fails the build. That is the point.
- **Fragile:** `no-undef` needs the page's globals listed explicitly in
  `eslint.config.mjs`. Every new shared name has to be declared there — better
  than switching the rule off, but it is upkeep.

## 4. Evidence

Measured on `main` at the commit that carries this revision (2026-10-20):

```
ruff check server scripts      → All checks passed
mypy                           → Success: no issues found in 35 source files
npm run lint                   → clean
npm run lint:dashboard         → 0 error(s) in 1 inline block
pytest server/tests            → 304 passed
```

Three jobs in `.github/workflows/ci.yml`: `python` (ruff/mypy/pytest), `js`
(eslint, broker parity, preflight parity, generated file freshness), `ui`
(44 panels against the exchange fixture, the classifier self-test, and the jsdom
smoke test).

The jsdom smoke test (`scripts/ui_smoke_test.js`) used to be run by hand. It was
kept out of CI because two of its assertions described a world this project no
longer has — a synthetic-candle banner on the performance page. Both were
replaced with assertions that hold in every state: the MSI badge must agree with
the sample count printed next to it, `#perfWarn` may only appear when it has
something to say, and the provenance chip may never collapse into a bare number.

## 5. What the gates do not cover

- **`check_untyped_defs` is on for `server/`, off for test modules.** A test
  double that passes `None` on purpose is answering to pytest. The directive is
  the first line of each test file, visible in the diff that adds a test.
- **No formatter.** `ruff format` is configured (`line-length = 100`) but not
  enforced; adding it now would rewrite files this ADR is trying to stabilise.
- **No coverage floor.** Coverage is reported (85%) but a drop does not fail CI.
- **eslint checks syntax and names, not behaviour.** A panel that renders a
  wrong number passes lint; `probe_live.py` and the jsdom smoke test are what
  catch that.
