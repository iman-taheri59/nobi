#!/usr/bin/env python3
"""Prepare /tmp/page.html for the jsdom UI smoke test.

jsdom has no resource loader, so the page must be self-contained: drop the
CDN <script> tags and inline the two local bundles.

    python3 scripts/ui_smoke_prep.py
    cd /tmp && node /home/user/nobi/scripts/ui_smoke_test.js
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path("/tmp/page.html")
LOCAL = ("nbx-core.js", "nbx-app.js")


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
