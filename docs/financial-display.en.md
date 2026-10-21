# Amount display contract and localisation

[فارسی](financial-display.md) · **English**

This change is purely nobitrader's display layer; `nbx-core.js`, the server, the order
model and the conversion rates are unchanged.

## Units

| Value | Display unit |
| --- | --- |
| Capital, capital change and paper-account allocation | USD / dollar |
| Price, stop loss, targets, order value and order-book depth | The quote currency of that market |
| Buy/sell amount and filled amount | The base currency of the market |
| Backtest input capital, equity curve and profit/loss | The quote currency of the backtest market, not the paper account's USD |
| Returns, risk and drawdown | Percent |

`IRT`/`TMN` means toman; `IRR`/`RLS` means rial; `USDT` is tether, not the account
dollar. No currency conversion happens in the formatter, and toman is not divided by ten
again. An unknown quote unit is not guessed. A valid zero is displayed; an empty,
invalid or infinite value is shown as `—`.

## Language, digits and input

- Text and page direction follow the selected language.
- `numFmt=auto` aligns the digits with the language; an explicit `fa` or `en` choice
  takes precedence over the language and is not cleared by switching languages.
- Dates follow the language's calendar, but the digit system of a date follows this same
  number setting.
- Amounts, signs and units are isolated from surrounding text with Unicode bidi
  isolation. Table values do not break apart, and tables still scroll horizontally.
- Numeric input is converted to text with a decimal keyboard, because the native
  `number` input does not accept Persian digits. An explicit parser turns Persian,
  Arabic and Latin digits and common separators into a real number; the order request is
  still JSON with numeric values.
- The caret position and composition are preserved; the explicit min/max and step
  constraints are checked on blur. This validation does not replace the server's risk
  gates.
- Currency symbols, the product name, identifiers, API keys, versions and raw technical
  details are not translated. Free-form data and messages received from services are not
  subject to automatic translation.

## Implementation and tests

The shared formatter `window.NBXDisplay` in `index.html` and the renderers in
`nbx-app.js` are used. The explicit unit is passed to each renderer; the unit is never
guessed from text or DOM structure. The backtest report and the account response are
kept for re-rendering in the new language, without re-running a trade or a backtest.

```sh
npm run build
npm run test:display
npm run test:js
python scripts/check_preflight_parity.py
npm run test:icons
npm run lint
npm run lint:dashboard
python -m pytest -q server/tests
ruff check server scripts
mypy
```

The new test walks both the web and the standalone file across the Persian/English ×
auto/fa/en matrix, covering units, zero/empty values, bidi, input/caret/composition,
immediate re-rendering of the account when the language changes, and equality of the
order payload in both languages. The tests are offline and jsdom-based; pixel layout
evaluation in a real browser or a live exchange connection was not done at this stage.
The fixture data exists only in the tests and was not added to the page or the server.
