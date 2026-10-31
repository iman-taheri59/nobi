# Tests on a machine that can reach Nobitex

[فارسی](testing.md) · **English**

CI and most of the tests in this repository run somewhere `apiv2.nobitex.ir` is not
reachable, so they only measure half of the contract: "when there is no data, say so".
The other half — "when there is data, it is a real number" — is only measured on a
machine that reaches the exchange. This document is that path, ordered from cheap to
expensive.

---

## 0) Once

```bash
cd /path/to/nobitrader

# Python (server + test tooling)
pip install -r server/requirements-dev.txt

# JavaScript (eslint + jsdom), then CSS and the bundle
npm ci
npm run css

# A browser for the three UI scripts (probe_live / layout_test / fa_label_test)
# If this download fails, §3.3 has three other ways.
python -m playwright install chromium
```

---

## 1) Static gates — no network, a few seconds

All four commands must return **zero**. A non-zero value means the build should be
red.

```bash
ruff check server scripts      # All checks passed!
mypy                           # Success: no issues found in N source files
npm run lint                   # no output
npm run lint:dashboard         # 0 error(s), 0 warning(s) in 1 inline block
```

---

## 2) Contract tests — no server, ~10 seconds

```bash
python -m pytest -q server/tests
```

Expect `591 passed` (this number grows with every new test; what matters is zero
failed). Three of them need neither a browser nor a server and are worth running on
their own:

```bash
python scripts/probe_live.py --selftest        # the panel classifier, 8 states
node scripts/js_broker_test.js                 # browser broker == server
python scripts/check_preflight_parity.py       # 28 cases × 10 gates
```

---

## 3) Against a real server — the half only you can measure

First start the server (a separate window; leave it open):

```bash
cd server
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Wait 15–30 seconds for the first scan to finish. Three lines you should see in the
console:

```
INFO:nbx-app:universe: 60 rows loaded (premium 0.xxx%)
INFO:nbx-app:rescan 15m: NN symbols, FA NN/NN
INFO:nbx-app:journal: NN new prediction(s) on 15m
```

If `NN symbols` is far below the universe size (say 5 of 40), you have lost the scan
half — see §5.

### 3.1 The 45-panel probe — the most important command in this document

```bash
python scripts/probe_live.py --url http://127.0.0.1:8000 \
       --json probe-live.json --md probe-live.md
```

For every panel across ten tabs it asks one question: did a real number come from the
exchange, does it honestly say "I can't", or is it blank with no explanation? The third
is always a bug.

| Exit | Meaning |
|---|---|
| `0` | PASS — all 44 panels explain themselves |
| `1` | At least one panel has a bug; the report names the tab and the panel |
| `2` | Nobitex was unreachable; only the "error" half was measured |

Open `probe-live.md`: a table of every panel with `DATA / EMPTY / EXPLAINED / BLANK`
columns. Search for `BLANK`.

### 3.2 Fundamental labels

```bash
python scripts/fa_label_test.py --url http://127.0.0.1:8000 --case all
```

It measures three cases: `live` (from the provider), `cached` (the cache), `fallback`
(a fixed profile). The important one: in the fallback case the page must **say** "fixed
profile, no live call" — not present the number as if it were live data.

### 3.3 Layout in a real browser

```bash
python scripts/layout_test.py --url http://127.0.0.1:8000
```

Three widths (390 / 768 / 1440) × ten tabs: horizontal scroll, a tab that will not
open, a row whose chart is empty. jsdom does not see these; only this script does.

#### If the browser will not start

Two different errors, with two different fixes. Work out which one you have:

| Message | Means | Fix |
|---|---|---|
| `the playwright package is not installed for this interpreter` | The Python **package** is missing — not the browser | Use the path the message prints: `"...\python.exe" -m pip install playwright` |
| `could not start a browser: …` | The package is there, the browser is not | One of the three ways below |

⚠️ The most common Windows trap: a bare `python` that is not the project venv. If you
installed the package into the venv and run the script with `python`, you get the "not
installed" message. The script now prints the interpreter path it is running under in
that same message; use it with `-m pip install`. **`--channel` does not replace
installing the package** — it only changes where the browser is downloaded from.

If the package is there and only Chromium fails to download: Playwright fetches a
**specific build** from its own CDN; a network that blocks that domain (or a corporate
proxy/antivirus that swallows the executable) stops this step. You do not have to fight
it — **all three browser scripts** have two other ways:

```bat
:: 1) A browser that is already installed. On Windows, that is Edge.
python scripts\layout_test.py   --url http://127.0.0.1:8000 --channel msedge
python scripts\probe_live.py    --url http://127.0.0.1:8000 --channel msedge --json probe-live.json --md probe-live.md
python scripts\fa_label_test.py --url http://127.0.0.1:8000 --case all --channel msedge

:: 2) Any Chromium binary you downloaded yourself
python scripts\layout_test.py --url http://127.0.0.1:8000 --executable C:\tools\chrome-win64\chrome.exe

:: or once per window (for all three scripts):
set NBX_CHROME=C:\tools\chrome-win64\chrome.exe
python scripts\layout_test.py --url http://127.0.0.1:8000
```

**About a zip you downloaded yourself:** Playwright does not require its own folder
layout (`chromium-<build>/chrome-win/chrome.exe`); it only wants the path to one
executable. So:

1. Unpack the zip anywhere — it does not have to be inside the Playwright folder.
2. Find `chrome.exe` inside it (usually one level below the root).
3. Pass that path to `--executable`.

If that build is far too old or too new, Playwright may struggle at the CDP protocol
level; in that case `--channel msedge` is the safer choice. The error message in this
case is explicit and shows all three ways:

```
FAIL
 - could not start a browser: BrowserType.launch: ...
one of these fixes it:
  python -m playwright install chromium     # Playwright's own build
  --channel msedge                          # Edge, already on Windows
  --executable C:\path\to\chrome.exe       # a build you unpacked
```

### 3.4 DOM and the MSI card

```bash
python scripts/ui_smoke_prep.py
node scripts/ui_smoke_test.js
```

The server must be up (the page gets its data from it). Expect `PASS`. The temporary
file path can be changed with `NBX_SMOKE_PAGE`.

---

### 3.5 The same tests, from inside the dashboard

The **error log** tab now has two cards:

| Card | What it measures | When |
|---|---|---|
| System health check | **This running process** — log, journal, paper account, risk caps, freshness of the USDT/IRT rate, market list, keys, websocket, and with the "with the exchange" button: `/market/stats`, the BTCIRT candle and the order book | Any time |
| Project tests | **This code** — the same `pytest` / `ruff` / `mypy` that CI runs | Only when the server runs from the source folder |

Four things these cards say, and that must be taken seriously:

- **Healthy / broken / unknown / the check itself failed** are four separate states.
  "Unknown" means it was *not* measured — not that it is healthy. (For instance, the
  process has not talked to the exchange yet, so there is no rate to judge.)
- **The headline is the worst state, not the majority.** Ten green and one red = red.
- **Every row says what it saw**: `wrote #812, read it back` is a result; "healthy" is
  not.
- **Running the tests does not touch live memory.** The subprocess runs with separate
  `NBX_JOURNAL_DB` and `NBX_PAPER_STATE`; otherwise a test that deliberately fails
  would write a fake row into this very log.

From the command line, the same two jobs without a browser:

```bash
curl -s -X POST http://127.0.0.1:8000/api/diagnostics/run \
     -H 'Content-Type: application/json' -d '{"kinds":["core","storage"]}' | python -m json.tool
curl -s http://127.0.0.1:8000/api/devsuite          # available? if not, why?
curl -s -X POST http://127.0.0.1:8000/api/devsuite/run -d '{}' -H 'Content-Type: application/json'
curl -s http://127.0.0.1:8000/api/devsuite/<run_id>  # until state leaves running
```

`NBX_DEV_TOOLS=0` turns off running tests from the dashboard; in a deployed install
(no `server/tests/`) it is off by itself and the reason is stated.

---

## 4) What only your own eyes see

These have no assertions; they are four numbers that mean someone is lying somewhere
if they are wrong.

```bash
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/universe
curl -s http://127.0.0.1:8000/api/risk/state
curl -s "http://127.0.0.1:8000/api/signals?tf=15m"
```

| What to look at | Healthy | Broken |
|---|---|---|
| `health.upstream.ok` | `true` | `false` ⇒ market panels must give a sentence, not a number |
| `health.stats.success_rate` | above 95 | lower ⇒ some requests are being rejected |
| `universe.count` | 60ish | zero ⇒ scanning with no markets |
| `risk.state.fx.age_s` | under 300 | over 300 ⇒ a stale rate must not produce `equity_irt` |
| `risk.state.caps.riskPct` | 0.75 | any other number ⇒ a third version of the risk rules came back |
| `signals.count` | close to the universe minus 1 (USDTIRT) | far lower ⇒ see §5 |
| `signals.scored[].data_source` | `live` | `cache` for everything ⇒ network or 429s |

---

## 5) Five regressions this project has already had — and what reveals them

| Sign in the log/page | The bug that came back |
|---|---|
| `circuit breaker is open` for unrelated symbols, or `rescan: 5 symbols` after a 403 | A per-symbol error is opening the global breaker (`is_outage`) |
| `rate from usdt-irt is NNNNs old` as a 500 | A stale rate is being served as a rate again |
| An IRT order **is recorded** while the exchange is down | `no-fx-rate` was bypassed |
| A warning about "over the 0.25%% cap" (two percents) or a warning with a number other than `risk.state.caps` | The risk rule split into two versions again |
| A panel with a bare "—" and no sentence | The panel does not explain itself (`probe_live` catches it) |

---

## 6) A long run (half an hour) — what CI never sees

Leave the server on a real feed for half an hour, then:

```bash
curl -s http://127.0.0.1:8000/api/health        # stats.fail, success_rate
curl -s http://127.0.0.1:8000/api/risk/state    # fx.age_s
curl -s http://127.0.0.1:8000/api/journal/summary | python -m json.tool | head -40
```

Healthy: `success_rate` above 95, `fx.age_s` always under 300, the journal filling with
`source: live`. If `fail` climbs, the `nbx-client` messages in the console say which
route.

---

## 7) If you want to reproduce exactly what CI sees

```bash
# The contract-shaped fixture (does not need Nobitex)
python scripts/mock_upstream.py --port 8010 &
python scripts/run_with_mock_exchange.py --port 8000 --exchange http://127.0.0.1:8010 &
python scripts/probe_live.py --url http://127.0.0.1:8000
```

With `NBX_RATE_TTL_S` and `NBX_UNIVERSE_TTL_S` you can deliberately create the "dead
feed" case and watch the `no-fx-rate` path (see §5).

---

## 8) The gates: shadow, and their evidence (ADR-0022)

Keep two things apart: the **verdict** (what a gate *would* have blocked today) and the
**evidence** (whether that block was right).

```bash
# The verdicts, on every scan: applied must always be false
curl -s "http://127.0.0.1:8000/api/signals?tf=15m" | python -c \
  "import json,sys; g=json.load(sys.stdin)['gates']; print(g['mode'], g['applied'], g['counts'], g['ledger']['ready'])"

# The evidence: the ledger joined to the candles that came after
curl -s "http://127.0.0.1:8000/api/gates/evidence?tf=4h&horizon=4" | python -m json.tool | head -40
```

| What to look at | Healthy | Broken |
|---|---|---|
| `gates.applied` | always `false` | `true` ⇒ a gate acted without an ADR |
| `gates.counts.rows` | equal to `len(scored)` | smaller ⇒ a row was removed (never allowed) |
| `gates.verdicts[].code` | one of the seven codes | an unknown code ⇒ the contract broke |
| `gates.ledger.ready` | `[]` until the sample fills | a gate "ready" within a day ⇒ the days rule broke |
| `evidence.gates[].verdict` | `insufficient` in the first days | `supports` with `blocked.verdicts < 30` ⇒ manufactured sample |
| `evidence.applied` | `false` | `true` ⇒ evidence became an instruction |
