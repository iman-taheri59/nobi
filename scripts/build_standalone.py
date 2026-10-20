#!/usr/bin/env python3
"""Build the standalone mobile file from the canonical sources.

`index.html` links the local stylesheets and JS files so that the server
build (and local development) has a single source of truth:

    assets/css/layers.css        (cascade order: utilities beat components)
    assets/css/tailwind.css      (compiled locally, see tailwind.src.css)
    assets/css/{base,components,dashboard}.css
    nbx-core.js
    nbx-app.js

`nobitex-mobile.html` must work when opened from a phone file manager
(`file://` / `content://`), so this script inlines those same files into a
single self-contained HTML document.

Run from anywhere:

    python3 scripts/build_standalone.py

Never edit `nobitex-mobile.html` by hand — it is generated.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS_FILES = ("layers", "tailwind", "base", "components", "dashboard")
JS_FILES = ("nbx-core.js", "nbx-app.js")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check_css_freshness() -> None:
    """Refuse to inline a stylesheet compiled before the markup it styles.

    Tailwind only emits classes it can find at build time. Adding `md:w-60` to
    a template and skipping `npm run css` silently ships a stylesheet without
    that class: the element falls back to `w-full` and a 192px column becomes
    the whole row. That failure looks like a layout bug, not a build bug.
    """
    css = ROOT / "assets" / "css" / "tailwind.css"
    if not css.exists():
        raise SystemExit("assets/css/tailwind.css is missing — run: npm run css")
    sources = [ROOT / "index.html", ROOT / "nbx-app.js"]
    newest = max(p.stat().st_mtime for p in sources if p.exists())
    # One second of tolerance: a fresh checkout (or a restored workspace) can
    # stamp every file with the same second, and a sub-second difference is not
    # evidence that a class was added without rebuilding the CSS.
    if css.stat().st_mtime + 1.0 < newest:
        raise SystemExit(
            "assets/css/tailwind.css is older than the markup it styles.\n"
            "Classes added since the last build are missing from the CSS.\n"
            "Run:  npm run css   (then rebuild)"
        )


def main() -> None:
    check_css_freshness()
    html = read(ROOT / "index.html")

    css_parts = []
    for name in CSS_FILES:
        path = ROOT / "assets" / "css" / f"{name}.css"
        if not path.is_file():
            raise SystemExit(
                f"missing {path.relative_to(ROOT)} — run the Tailwind build:\n"
                "  npx @tailwindcss/cli -i assets/css/tailwind.src.css "
                "-o assets/css/tailwind.css --minify"
            )
        css_parts.append(f"/* ---- {name}.css ---- */\n" + read(path))
        html = html.replace(
            f'<link rel="stylesheet" href="assets/css/{name}.css">\n', ""
        )
        html = html.replace(f'<link rel="stylesheet" href="assets/css/{name}.css">', "")

    # The webfont comes from a CDN. On a phone with the standalone file and no
    # connection it would silently fall back to a system font mid-layout; make
    # the fallback explicit instead of waiting on a dead request.
    for remote in (
        '<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css">\n',
        '<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css">',
    ):
        html = html.replace(remote, "")

    html = html.replace('<link rel="manifest" href="manifest.json">\n', "")
    html = html.replace('<link rel="manifest" href="manifest.json">', "")

    css_block = "<style id=\"nbx-standalone-css\">\n" + "\n\n".join(css_parts) + "\n</style>\n"
    if "</head>" not in html:
        raise SystemExit("index.html has no </head>")
    html = html.replace("</head>", css_block + "</head>", 1)

    for js in JS_FILES:
        code = read(ROOT / js)
        if "</script" in code.lower():
            raise SystemExit(f"{js} contains a literal </script> and cannot be inlined")
        tag = f'<script src="{js}"></script>'
        if tag not in html:
            raise SystemExit(f"index.html does not reference {js}")
        html = html.replace(tag, f'<script id="nbx-inline-{js.replace(".", "-")}">\n{code}\n</script>')

    leftover = [n for n in CSS_FILES if f'href="assets/css/{n}.css"' in html]
    if leftover:
        raise SystemExit(f"stylesheet(s) not inlined: {leftover}")

    out = ROOT / "nobitex-mobile.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
