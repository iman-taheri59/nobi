# ADR-0016: Static gates (ruff, mypy, eslint) and CI

- **Status:** Accepted and implemented
- **Date:** 2026-10-20
- **Decision owner:** technical architect
- **Related:** 0002 (stack), 0014 (data-honesty contract), 0015 (unit boundary)
- **Enforced by:** `pyproject.toml`, `eslint.config.mjs`, `scripts/lint_dashboard.mjs`, `.github/workflows/ci.yml`

---

## 1. Context

There were no static gates: no ruff, no mypy, no eslint, no CI. 278 tests were
green while, at the same time:

- ruff reported 255 findings (47 unsorted imports, 31 unused imports, 32 bare
  `except Exception`, 30 `datetime.timezone.utc`, 16 `# noqa` comments whose
  code was no longer selected),
- mypy reported 74 errors across 10 files,
- and 4,000 lines of JavaScript inside `index.html` were read by no tool at all.

The cost was paid exactly where the bugs came from — **cross-cutting seams**.
Four examples, all found by these gates, all previously "green":

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
| `mypy` | `server/` (33 files) | `None` that reaches arithmetic, `dict[str, object]` treated as a number |
| `eslint` | `nbx-core.js`, `nbx-app.js`, `scripts/*.js`, **and the inline block in `index.html`** | undefined names, duplicate keys, dead variables |

The inline block is extracted by `scripts/lint_dashboard.mjs` and linted with the
same config; reported line numbers are `index.html` line numbers.

### Deliberately not enabled

- **`E501` (line length).** 99 lines are longer than 100 columns; shortening them
  would be a whitespace-only commit that makes `git blame` useless. `line-length`
  is still set so a future formatter run produces 100-column output.
- **`BLE001` / `S110` (bare except, pass).** The polling loops catch `Exception`
  on purpose: one bad tick must not kill the socket. `errorlog` is where the
  failure is recorded, and each site keeps its explanation as a plain comment.
- **mypy `--check-untyped-defs`.** Turning it on today would surface hundreds of
  errors in bodies that are not typed yet. That is the next step, not this ADR.

## 3. Consequences

- **Positive:** 255 + 74 + 197 findings went to zero, and the four real bugs above
  were found and fixed along the way. `nobitex-mobile.html` (a generated file) is
  rebuilt and compared in CI so it cannot drift from its sources.
- **Negative:** a new unused import now fails the build. That is the point.
- **Fragile:** `no-undef` needs the page's globals listed explicitly in
  `eslint.config.mjs`. Every new shared name has to be declared there — better
  than switching the rule off, but it is upkeep.

## 4. Evidence

```
ruff check server scripts      → All checks passed
mypy                           → Success: no issues found in 33 source files
npm run lint                   → clean
npm run lint:dashboard         → 0 error(s) in 1 inline block
pytest server/tests            → 278 passed
```

Three jobs in `.github/workflows/ci.yml`: `python` (ruff/mypy/pytest), `js`
(eslint, broker parity, preflight parity, generated file freshness), `ui`
(45 panels against the exchange fixture + the classifier self-test).
