# ADR-0017: Testing and review from inside the dashboard (error log tab)

[فارسی](../fa/ADR-0017-تست-و-بررسی-از-داخل-داشبورد.md) · **English**

- **Status:** Accepted and implemented
- **Date:** 2026-10-21
- **Decider:** Technical Architect
- **Related ADRs:** 0014 (data honesty contract), 0016 (static gates and CI)
- **Surfaces:** `server/diagnostics.py`, `server/devsuite.py`, the `/api/diagnostics*` and `/api/devsuite*` routes, `server/tests/test_diagnostics.py`

---

## 1. Context

The user asked: "can I run the tests in the error-log section and see the result?" That
question contains two different questions, and separating them is the whole of this ADR:

1. **"Is this running process healthy?"** — does the exchange answer? Is the USDT/IRT
   rate fresh? Does the paper account hold a finite number? Does the journal open?
2. **"Is this code correct?"** — the same three commands CI runs: `pytest`, `ruff`,
   `mypy`.

Before this, answering the second question meant getting a venv, the right directory
and a shell right at the same time — and the last few days showed exactly how that
gets lost in those three steps (a bare `python` that is not the venv, `uvicorn` not
found, a patch applied to older code).

The only thing the log tab had was a "test the log itself" button that wrote and read
one marker line. That is a check, not a checking system.

---

## 2. Decision

### 2.1 Two modules, not one

`diagnostics.py` measures process health and answers **synchronously**: its most
expensive check is three HTTP calls with a 20-second ceiling, so a two-second answer
is simpler than a run id plus a polling loop.

`devsuite.py` runs the real gates in a separate process and is **asynchronous**: a run
takes ~20 seconds, and holding one request open for 20 seconds is exactly what makes a
proxy in between return 504.

Had these been one module, a button labelled "health check" would take twenty seconds,
and the next person would conclude that the health check is slow and stop pressing it.

### 2.2 Four states, and "I don't know" is one of them

| State | Meaning | What produces it |
|---|---|---|
| `ok` | Measured, and healthy | The check, with a description of what it saw |
| `fail` | Measured, and broken | The check, with a description of what it saw |
| `skip` | **Not** measured, with a reason | The process has not talked to the exchange yet; there is no rate to judge |
| `error` | The check itself threw | The exception in `detail` + a row in the same error log |

`skip` never counts as `ok`, and is not `fail` either. The first would be a lie, the
second a false alarm that teaches people to ignore red. All four counts are returned
separately and **the headline is the worst state, not the majority**: ten green and
one red means the system is red, not "91% healthy".

### 2.3 Every `ok` must say what it saw

"The error log is healthy" is not a result. `wrote #812, read it back, 1129 rows` is a
result. `detail` is mandatory for this, and the tests fail without it.

### 2.4 `devsuite` only runs against source code

If `pyproject.toml` and `server/tests/` are not next to the code, this is a deployed
install and `available: false` comes back with the reason. `NBX_DEV_TOOLS=0` turns it
off in a source checkout too. The routes sit behind `require_client` like every other
write (loopback or token).

### 2.5 One run at a time

A second request during a run gets `409` with the id of the current run. Two `pytest`
runs at once compete over one database and make both results meaningless.

### 2.6 Tests in a temporary store, never in the live database

This clause came from **being seen**, not from caution: while building this module I
pressed the "run" button and a row with the text `the check itself is broken` appeared
in the live log on that very page — from a test that deliberately breaks a check.
`errorlog` writes through `journal.connect()`, so the test wrote into the same database
the user was reading.

Fix: `devsuite._gate_env()` **overrides** `NBX_JOURNAL_DB` and `NBX_PAPER_STATE` to a
temporary directory for every run (not `setdefault`), and `server/tests/conftest.py`
fills the same two with `setdefault` for a manual run. Two tests lock this down: one
that the path is overridden, and a real `ruff` run that must not create the configured
database.

### 2.7 A gate's result is what the tool said

`summary` is extracted from the tool's own output. If the pattern is not found,
`parsed: false` comes back and the UI shows the raw text instead of a number. A gate
that could not be summarised is not declared "green"; only `rc` decides, and the UI
says honestly that there is no summary.

The same rule caught a real bug on the first real run: `mypy` from inside the
dashboard returned 5 errors in that module's own test file (indexing `dict | None`),
which the manual `mypy` had not seen because it ran before the test was written. The
number came from the tool, not from my memory.

---

## 3. What was not built, and why

| What | Why not |
|---|---|
| Running each test file from the UI | `pytest server/tests/test_x.py` is already one command; a button per file grows the API surface without answering a new question |
| Running `npm run lint` from the dashboard | It depends on `node_modules`, which a deployed install does not have; `available` must reflect reality, not a wish |
| Streaming output live (SSE/WebSocket) | One `GET` with an id is enough; streaming adds a new path to fail without changing any decision |
| Writing the run result into the error log | The log is for **breakage**, not for "358 tests green". One row per green run buries the real rows |
| A single `ok` for "the checks ran" | That is the lie this ADR exists to avoid |

---

## 4. Consequences

- Without a venv or a shell, the user sees both process health and code state in the
  same tab where they read errors.
- Cost: a new write route (`POST /api/devsuite/run`) that can take 20 seconds of CPU.
  Limited to loopback/token, one run at a time, and source checkouts only.
- Cost: `diagnostics` depends on a few objects from `main`. They are **injected**
  (`Ctx`) rather than imported; otherwise `diagnostics` would import `main`, which
  imports `diagnostics`.
- The numbers this ADR claims are verified in `server/tests/test_diagnostics.py`
  (23 tests) and by a real run against the fixture, and are reproducible with
  `POST /api/devsuite/run`.
