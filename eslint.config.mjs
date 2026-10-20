// ESLint flat config.
//
// Two populations of JavaScript live here and they are not the same:
//
//   nbx-core.js / nbx-app.js  browser code, `window` is real
//   scripts/*.js              node scripts, `require`/`process` are real
//   index.html's inline block the dashboard logic (linted through
//                             scripts/lint_dashboard.mjs, which extracts it)
//
// `no-undef` is on. It is the rule that would have caught `wsPoll` being used
// after a `return`, and `filled_pct` half-renamed.

import js from "@eslint/js";

const browserGlobals = {
  window: "readonly", document: "readonly", navigator: "readonly",
  location: "readonly", localStorage: "readonly", sessionStorage: "readonly",
  fetch: "readonly", WebSocket: "readonly", EventSource: "readonly",
  setTimeout: "readonly", clearTimeout: "readonly", setInterval: "readonly",
  clearInterval: "readonly", requestAnimationFrame: "readonly",
  CustomEvent: "readonly", Event: "readonly", FormData: "readonly",
  URLSearchParams: "readonly", URL: "readonly", Blob: "readonly",
  AbortController: "readonly", TextDecoder: "readonly", TextEncoder: "readonly",
  structuredClone: "readonly", crypto: "readonly", console: "readonly",
  performance: "readonly", matchMedia: "readonly", alert: "readonly",
  confirm: "readonly", prompt: "readonly", HTMLElement: "readonly",
  IntersectionObserver: "readonly", ResizeObserver: "readonly",
  MutationObserver: "readonly", requestIdleCallback: "readonly",
  cancelAnimationFrame: "readonly", queueMicrotask: "readonly",
  getComputedStyle: "readonly", Image: "readonly", Option: "readonly",
  // --- the page's own cross-file globals -------------------------------
  // `window.NBX = ...` creates a global binding, but ESLint cannot see that
  // through the property assignment, so the three below are declared here.
  // They are *writable*: each file extends the same object.
  NBX: "writable",      // nbx-core.js, nbx-app.js, and the inline block
  NBX_APP: "writable",  // nbx-app.js
  live: "writable",     // index.html's polling state, read by the inline block
  // --- CDN libraries, loaded by <script src> in index.html ------------
  LightweightCharts: "readonly",
  lucide: "readonly",
};

// Functions and values the inline block defines and the two module files
// read. Declared one by one rather than switched off with `/* global */`, so
// a new cross-file name still has to be added here on purpose.
//
// They are *not* declared for `index.inline.js`: that file is where they are
// declared, and `no-redeclare` would fire on every one of them.
const pageGlobals = {
  LANG: "writable", T: "writable", EQUITY: "writable", toast: "readonly",
  RISK_LIMITS: "writable",   // copied from /api/risk/state by nbx-app.js
  refreshIcons: "readonly", renderPositions: "readonly", renderKills: "readonly",
  renderCaps: "readonly", positions: "writable", kills: "writable",
  caps: "writable", equitySeries: "writable", candleSeries: "writable",
  lastCandle: "writable", spark: "writable", t: "readonly",
};

const nodeGlobals = {
  require: "readonly", module: "writable", exports: "writable",
  process: "readonly", __dirname: "readonly", __filename: "readonly",
  console: "readonly", Buffer: "readonly", setTimeout: "readonly",
  clearTimeout: "readonly", setInterval: "readonly", clearInterval: "readonly",
  URL: "readonly", TextDecoder: "readonly", global: "readonly",
  // Node 18+ ships `fetch`; ESLint still needs to be told.
  fetch: "readonly",
};

const shared = {
  "no-unused-vars": ["error", { args: "none", varsIgnorePattern: "^_",
                                caughtErrors: "none" }],
  "no-undef": "error",
  "no-redeclare": "error",
  "no-dupe-keys": "error",
  "no-dupe-args": "error",
  "no-duplicate-case": "error",
  "no-unreachable": "error",
  "no-cond-assign": ["error", "except-parens"],
  "no-self-assign": "error",
  "no-constant-condition": ["error", { checkLoops: false }],
  "eqeqeq": ["error", "smart"],
  "no-var": "off",              // the dashboard predates let/const in places
  "no-empty": ["error", { allowEmptyCatch: true }],
};

export default [
  js.configs.recommended,
  {
    files: ["nbx-core.js", "nbx-app.js"],
    languageOptions: { ecmaVersion: 2022, sourceType: "script",
                       globals: { ...browserGlobals, ...pageGlobals } },
    rules: shared,
  },
  {
    // Extracted from index.html by scripts/lint_dashboard.mjs.
    files: ["index.inline.js"],
    languageOptions: { ecmaVersion: 2022, sourceType: "script",
                       globals: browserGlobals },
    rules: shared,
  },
  {
    files: ["scripts/**/*.js"],
    languageOptions: { ecmaVersion: 2022, sourceType: "commonjs",
                       globals: nodeGlobals },
    rules: { ...shared, "no-var": "off" },
  },
  {
    files: ["scripts/**/*.mjs", "eslint.config.mjs"],
    languageOptions: { ecmaVersion: 2022, sourceType: "module",
                       globals: nodeGlobals },
    rules: { ...shared, "no-var": "off" },
  },
];
