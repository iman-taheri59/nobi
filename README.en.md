# nobitrader

[فارسی](README.md) · **English**

Current version: **1.6.1** · release tag: **v1.6.1**

See [versioning and GitHub releases](docs/releases.en.md) for the rules on bumping
the version, tagging and shipping the mobile file, and the
[changelog](CHANGELOG.en.md).

A bilingual (Persian/English) dashboard with a technical-analysis engine, a
fundamental engine, risk management, paper trading, walk-forward backtesting, a
signal journal and a FastAPI backend.

---

## Getting into the dashboard

The dashboard has accounts: several people, one shared trading account, three roles
(`owner` / `operator` / `viewer`). Joining requires an **invite code**. On first run the server prints a one-time console token and the page automatically opens the owner-creation wizard; a successful claim deletes that token. The CLI remains a recovery path only:

```bash
python scripts/manage_users.py create YOURNAME --role owner
```

After that, `/api/*` is closed to anonymous callers and the page shows a sign-in form
instead of data. `NBX_API_TOKEN` stays — for scripts, not for the browser. Details:
[`docs/adr/en/ADR-0018`](docs/adr/en/ADR-0018-accounts-and-sign-in.md) and
[deployment guide §7](docs/deploy-arvan.md) (Persian).

## The principle everything is built on: no number is invented

Every panel, at every moment, is in one of three states, and all three are tested:

| State | Correct behaviour |
|---|---|
| There is data | The real number, from a source that can be named |
| There is no data (exchange down) | One explicit sentence saying why |
| The data is ours (our own server) | Shown whether or not the exchange is reachable |

A bare "—" is none of these; it is the thing that hid bugs for weeks.

---

## Quick start

### 1) Dashboard with the backend (full mode)

```bash
cd server
python -m venv ../.venv
../.venv/bin/pip install -r requirements.txt
../.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```

Browser: `http://localhost:8000`

Optional settings: `cp server/.env.example server/.env`. No key is needed while you
are only reading; writes to state (paper order, emergency stop, backtest) are allowed
from loopback and need `NBX_API_TOKEN` from a remote address. No secret is ever
written from the browser.

### 2) Single-file page on a phone

`nobitex-mobile.html` carries all the styling, icons and calculation engines and opens
from `file://` too. **However**, it takes market data from the backend: without a
reachable server the market panels say "the Nobitex server is unreachable" — not a
substitute number. (The old README claim that "100% of the features work offline" was
not true and has been removed.)

---

## Build

```bash
npm install                 # tailwind + jsdom
npm run css                 # builds assets/css/tailwind.css
python scripts/build_standalone.py   # nobitex-mobile.html
```

The single-file bundle is built from `index.html`; if you change the styles and forget
`npm run css`, `build_standalone.py` warns that the CSS is behind the markup.

---

## Tests — each one proves one specific thing

| Command | What it proves |
|---|---|
| `python -m pytest server/tests -q` | Backend logic + the contract "every panel can explain itself" |
| `python scripts/probe_live.py --selftest` | The panel classifier itself (8 states) |
| `python scripts/probe_live.py --url http://127.0.0.1:8000` | The "no data" state: 45 panels must explain themselves |
| `python scripts/mock_upstream.py --port 8010` + probe against 8010 | The "there is data" state: 45 panels must show a real number |
| `python scripts/layout_test.py` | 3 viewports × 10 tabs: horizontal overflow, empty charts |
| `python scripts/ui_smoke_prep.py && node scripts/ui_smoke_test.js` | DOM and MSI with no JS errors |
| `python scripts/check_preflight_parity.py` | The ten risk gates agree in Python and in JS |
| `node scripts/js_broker_test.js` | The browser broker: exchange rate, exchange minimum, closed market, precision |

### Static gates (ADR-0016)

These are exactly what CI runs, and they run the same way locally:

```bash
ruff check server scripts        # → All checks passed
mypy                             # → Success: no issues found
npm run lint                     # bundles + scripts
npm run lint:dashboard           # the inline block in index.html
```

The `mypy` file count is deliberately absent here: until 1.0.22 the line claimed
"58 source files" while the real number was 76, and a number that goes stale with
every new file is a trap rather than documentation. The criterion is the line
"Success: no issues found".

Configuration lives in `pyproject.toml` and `eslint.config.mjs`, not on the command
line. What is deliberately **not** enabled, and why, is in ADR-0016 (line length,
`except Exception`). `check_untyped_defs` is on; test bodies are exempted by a
directive on their own first line.

CI has three jobs: `python` (ruff + mypy + pytest), `js` (eslint + broker and preflight
parity + freshness of `nobitex-mobile.html`), and `ui` (45 panels against the exchange
fixture + a jsdom smoke test with 7 KPI cells, MSI card honesty and zero JS errors).

---

## Verifying the live data path

From most machines outside Iran, `apiv2.nobitex.ir` is not reachable. That is why
`probe_live.py` measures the two cases separately and sets an exit code:

```bash
python scripts/probe_live.py --json probe.json --md probe.md
```

| Exit code | Meaning |
|---|---|
| 0 | The live path was verified |
| 1 | At least one panel has a bug (the report names the tab and the panel) |
| 2 | Nobitex was unreachable; only the "error" half was measured |

To exercise the live half on a machine that cannot reach Nobitex, `mock_upstream.py`
serves a contract-shaped fixture (`source: "mock"`, none of its numbers appear in the
bundle):

```bash
python scripts/mock_upstream.py --port 8010
python scripts/probe_live.py --url http://127.0.0.1:8010
```

---

## Layout

```
index.html              dashboard (single page, bilingual)
nbx-core.js             TA/FA engines, API client, offline fallback
nbx-app.js              scanning, signals, performance, desk
nobitex-mobile.html     single-file bundle (generated, do not edit by hand)
assets/css/             tailwind (src + built) and base styles
server/                 FastAPI: main, ta/fa/risk/portfolio/benchmark,
                        market_overview, paper, backtest, journal, errorlog,
                        notify, security, settings_view
server/tests/           pytest
scripts/                probe_live, mock_upstream, layout_test, ui_smoke, build
docs/                   ADRs, analysis, ops, reviews
```

---

## The unit and symbol boundary (ADR-0015)

The exchange names markets `btc-rls`, the trade symbol is `BTCIRT`, and rial market
prices are **in rial**; the dashboard says "toman" everywhere. All three conversions
live in one file and only the API client calls them:

| File | Job |
|---|---|
| `server/units.py` | `btc-rls → BTCIRT`, rial→toman, amount/price precision, minimum order |
| `NobitexClient` | The only place those conversions run |
| `risk_engine.preflight` | The exchange's `min-notional` and `market-closed` gates |
| `nbx-core.js` (`NBX.Units`, `NBX.Risk`) | The same rules for the backend-less bundle; with a parity test |

The result: the dashboard's number equals the exchange's number, and an order below
the exchange minimum is rejected in the preview as well. Details (Persian):
[`docs/reviews/2026-10-20-nobitex-api-conformance-fa.md`](docs/reviews/2026-10-20-nobitex-api-conformance-fa.md).

## The "before you trade" routes

| Route | What it says | Changes state |
|---|---|---|
| `POST /api/risk/whatif` | If I place this order, what happens to heat/cash/gates | Never |
| `POST /api/portfolio/allocate` | How the heat budget splits across LONG candidates (with correlation) | Never |
| `POST /api/benchmark/relative` | Beta/alpha/tracking error of each symbol against a benchmark | Never |
| `GET /api/market/overview` | Breadth, dispersion and turnover concentration of the market | Never |

All of them read `risk_engine.preflight` — the same function `PaperBroker.place_limit`
runs — so a preview cannot promise something the broker would reject. Details
(Persian): `docs/reviews/2026-10-20-whatif-preflight-fa.md`,
`docs/reviews/2026-10-20-a3-portfolio-allocator-fa.md`,
`docs/reviews/2026-10-20-a7-benchmark-a6-market-fa.md`.

## Operations and recovery

Create a consistent backup of live databases (excluding `.env` and API keys):

```bash
python scripts/state_backup.py backup --output backups/
python scripts/state_backup.py restore backups/nobitrader-state-....zip --force

# daily soak snapshot; NBX_API_TOKEN is read from the environment only
python scripts/live_evidence_snapshot.py --csv data/live-evidence.csv
```

Fetch paginated history with checkpointing, deduplication and gap reporting:

```bash
python scripts/fetch_history.py --symbol BTCIRT --resolution 15 \
  --from 2026-01-01 --to 2026-10-25 --output data/BTCIRT-15.json \
  --checkpoint data/BTCIRT-15.checkpoint.json
```

## Documentation

Start at the [documentation map](docs/README.en.md). The short version:

- [`docs/README.md`](docs/README.md) — the map (Persian) · [`English`](docs/README.en.md)
- [`docs/adr/`](docs/adr) — 20 ADRs, **each in Persian and English** (data honesty
  ADR-0014, unit boundary ADR-0015, one page / one artifact ADR-0019)
- [`docs/adr/README.md`](docs/adr/README.md) — which ADR is implemented, with the code
  that proves it
- [`docs/releases.en.md`](docs/releases.en.md) — versioning and release process
- [`CHANGELOG.en.md`](CHANGELOG.en.md) — what changed in each version

### Coin logos

Names, symbols, networks and trading specs come from Nobitex `/v2/options` and are
cached. CoinGecko only supplies logos and alternative names; the browser fetches
images from our own server. The API states the source and fetch time of each part. The
logo mapping for 50 coins is explicit, and unmatched symbols are not substituted
automatically. [Guide and settings](docs/coin-metadata.md) (Persian).

40 coin logos ship with the project locally, with no CDN request; the single-file
mobile build carries the images inside itself. For symbols without a logo, the short
letter form of the coin name is shown. The source and licence of the images are
recorded in `assets/coins/README.md`.

- Rebuild: `npm run build`
- Logo tests (web and mobile): `npm run test:icons`
