#!/usr/bin/env python3
"""Prepare a temporary JSDOM fixture from the canonical index.html.

JSDOM has no resource loader, so this test-only copy drops optional CDN scripts
and inlines the three local modules. It is written to the system temp directory
(override with NBX_SMOKE_PAGE), is never served or published, and is not a
second product HTML artifact; `ui_smoke_test.js` reads the same path.

    python3 scripts/ui_smoke_prep.py
    node scripts/ui_smoke_test.js
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# `Path("/tmp/...")` is not "/tmp" on Windows: it resolves against the current
# drive (F:\tmp\page.html) and the directory usually does not exist. The
# system temp dir is the same directory for python and node on every platform.
OUT = Path(os.getenv("NBX_SMOKE_PAGE") or Path(tempfile.gettempdir()) / "page.html")
LOCAL = ("nbx-core.js", "nbx-app.js", "nbx-alerts.js")


def main() -> None:
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    html = re.sub(r'<script src="https?://[^"]+"[^>]*></script>\s*', "", html)
    for name in LOCAL:
        tag = f'<script src="{name}"></script>'
        if tag not in html:
            raise SystemExit(f"index.html does not reference {name}")
        js = (ROOT / name).read_text(encoding="utf-8").replace("</script>", "<\\/script>")
        html = html.replace(tag, f"<script>\n{js}\n</script>")
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({len(html.encode()):,} bytes)")


if __name__ == "__main__":
    main()
