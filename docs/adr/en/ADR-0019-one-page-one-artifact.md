# ADR-0019: One page, one artifact — not a mobile build and a desktop build

[فارسی](../fa/ADR-0019-یک-صفحه-یک-آرتیفکت.md) · **English**

- **Status:** Accepted and implemented
- **Date:** 2026-10-22
- **Decision owner:** the operator (a direct question: "why two versions with this
  much conflict?")
- **Related:** 0016 (static gates and CI), 0017 (reviewing from inside the dashboard)
- **Enforced by:** `server/tests/test_single_page.py`, `scripts/build_standalone.py`,
  `npm run build` + `git diff --exit-code -- nobitex-mobile.html` in CI

---

## 1. The problem

There are two HTML files in the repository:

```
index.html            6,021 lines — markup + links to 5 stylesheets and 2 scripts
nobitex-mobile.html  10,064 lines — the same page with those 7 files inlined
```

From the outside it looks like two versions, and that appearance produces a
legitimate fear: conflict, duplicated work, a bug fixed in one and not the other.
The operator's question was why there are two versions, and the proposal was to
keep a single, fully responsive page.

## 2. The decision that had already been made

`nobitex-mobile.html` is **not hand-written**. `scripts/build_standalone.py`
builds it from `index.html` + five CSS files + `nbx-core.js` + `nbx-app.js`, and
all it does is inline: drop the `<link>`s, drop the CDN font, drop
`manifest.json`, and substitute `<style id="nbx-standalone-css">` and two
`<script id="nbx-inline-…">` blocks.

Measurement confirms it: undo exactly that inlining and the two files become
**identical** — the only residue is blank lines left where the `<link>`s were. So
there is no "mobile version"; there is **one responsive page** plus one
**generated artifact** of it.

The artifact is necessary, and for the very reason the operator raised — previewing
on a phone. In these contexts subresources do not load at all:

- a file opened from `file://` or `content://` (a phone's download folder)
- a preview iframe with `sandbox="allow-scripts"` and no network
- an air-gapped host

In all three, the linked page arrives with zero CSS and zero JavaScript: no
charts, no data.

## 3. The decision

1. **`index.html` is the only source.** Markup, styles and logic are written
   there. Responsiveness comes from Tailwind variants (`sm:`/`md:`) and
   `injectMobileNavAndDownload()`, not from a second document.
2. **`nobitex-mobile.html` is an artifact, not a version.** It is never edited by
   hand; a change there is erased by the next build, so any difference from the
   source is, by definition, a defect.
3. **Conflict is structurally impossible.** CI and the release both fail on
   `git diff --exit-code -- nobitex-mobile.html` when the artifact is behind, and
   `server/tests/test_single_page.py` checks equality itself without needing node.
4. **The name is historical and stays.** `/download/nobitex-mobile.html` and
   `filename="nobiTrader-mobile.html"` are referenced throughout the docs and the
   tests; renaming adds broken-link risk with no technical gain. Explicit
   documentation (this record) takes the place of a rename.

## 4. Consequences

- A bug is fixed in one place, and both contexts get the fix.
- A new Tailwind class needs `npm run css`, or it is not in the compiled
  stylesheet; `build_standalone.py` refuses to inline a stale stylesheet through
  `check_css_freshness()`.
- Cost: a 10,000-line committed artifact. Accepted, because offline download for
  a phone is a feature, not a leftover.
- If the artifact were ever unnecessary, deleting `build_standalone.py` and its
  guards would be enough; `index.html` would keep serving unchanged. The decision
  is **reversible**.

## 5. The two icon layers (correcting a misdiagnosis)

While investigating this, it looked as though the dashboard defined
`setCoinMetadata` without ever calling it. **That was wrong.** The definition is
in `index.html`, but the call is in `nbx-app.js`:

```js
await NBX.boot();
loadCoinMetadata();                                  // fill the online logos
setInterval(loadCoinMetadata, 60 * 60 * 1000);       // hourly
```

and `index.html` loads that same `nbx-app.js`. The line seen inside
`nobitex-mobile.html` was the inlined copy of that file, not a separate trick.

So both layers are live, as the operator said: bundled SVGs (40 files, base64, no
network) and CoinGecko logos through the server
(`/api/coins` and `/api/coins/{id}/logo`, disk cache, 24 h TTL). The ten curated
coins with no bundled SVG — TON, SHIB, NEAR, AXS, EGLD, HBAR, APT, ARB, OP, PEPE
— are covered by that second layer. So that deleting the call cannot go unnoticed,
`test_the_dashboard_asks_for_online_coin_metadata` was added.
