# ADR-0058: One responsive HTML source; remove the standalone output

[فارسی](../fa/ADR-0058-حذف-خروجی-مستقل.md) · **English**

- **Status:** Accepted and implemented on the working branch; not released or deployed
- **Date:** 2026-11-03
- **Decision owner:** the operator; explicit choice of complete removal after scope clarification
- **Related:** ADR-0019 (previous decision, now superseded), ADR-0016 (CI)
- **Implementation/evidence:** `index.html`, `server/main.py`, `server/tests/test_single_page.py`, responsive tests in `scripts/`, CI and Release workflows

---

## Decision

`index.html` is the dashboard's only HTML source and the same responsive page for phones and desktops. Both `/` and `/index.html` serve that exact file.

Delete the generated `nobitex-mobile.html` and its builder `scripts/build_standalone.py`. Remove its file route, selection/fallback logic for `/`, related autobuild and staleness checks, its CI freshness gate, and the mobile HTML attachment to GitHub Releases. `npm run build` still builds the local coin-icon block and compiled CSS; CSS compilation and its freshness check are not part of the deleted standalone-HTML output and remain in place.

## Test and delivery consequences

- Remove parity tests that compared two HTML files. Keep real responsive-layout tests at phone and desktop viewport sizes, run against `index.html`.
- Move DOM and wiring tests that previously read the generated HTML to the canonical source.
- Local CSS and JavaScript continue to be served through their asset routes; `/` never builds a page or selects a second file while answering a request.
- The release workflow publishes release notes only; it no longer attaches a mobile HTML file. GitHub's source archive is separate from that attachment.
- This decision does not authorize a deploy or a release; operational approvals remain separate.

## Scope

This decision concerns only the HTML source/bundling, routes, CI/Release, tests, and documentation. Trading logic, order behavior, and WebSocket behavior are explicitly out of scope and must not be changed as part of this work.

## Rationale

The second HTML file is generated rather than hand-maintained, but it still requires a tracked output, builder, route, release pipeline, and a large set of parity tests. A single responsive canonical page with local assets can serve the same product surface; keeping the second output adds operational work without enough benefit and risks drift or confusion over which response is authoritative.

ADR-0019 records why an inlined output of the same page was retained at the time. This ADR changes that choice: keep one HTML source, remove the standalone output.
