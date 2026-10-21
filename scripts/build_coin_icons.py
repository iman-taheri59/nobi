#!/usr/bin/env python3
"""Embed the locally vendored coin logos into index.html (no runtime network)."""
from __future__ import annotations

import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START = '<!-- COIN-ICONS-START -->'
END = '<!-- COIN-ICONS-END -->'


def main() -> None:
    icons = {
        p.stem.upper(): 'data:image/svg+xml;base64,'
        + base64.b64encode(p.read_bytes()).decode('ascii')
        for p in sorted((ROOT / 'assets' / 'coins').glob('*.svg'))
    }
    script = '''<script id="nbx-coin-icons">
// Vendored cryptocurrency-icons (CC0); see assets/coins/README.md.
(function () {
  'use strict';
  const logos = ICON_DATA;
  window.coinIcon = function coinIcon(symbol, size = 24) {
    const raw = String(symbol || '').toUpperCase().trim();
    // Split pairs first, then strip only a terminal quote (never the base).
    let base = raw.split(/[/_:\\s-]/)[0];
    if (!Object.prototype.hasOwnProperty.call(logos, base)) {
      base = base.replace(/(USDT|USDC|IRT|IRR|TMN|RLS)$/, '');
    }
    if (!/^[A-Z0-9]{1,24}$/.test(base)) base = '?';
    const px = Math.min(48, Math.max(16, Number(size) || 24));
    const style = `display:inline-flex;align-items:center;justify-content:center;vertical-align:middle;flex-shrink:0;width:${px}px;height:${px}px;margin-inline-end:6px`;
    const src = logos[base];
    if (src) return `<img class="coin-logo" src="${src}" width="${px}" height="${px}" alt="" title="${base}" style="${style}" decoding="async">`;
    // Honest textual fallback, not an invented brand logo. Safe alphabet only.
    return `<span class="coin-logo coin-logo-fallback" role="img" aria-label="${base}" title="${base}" style="${style};border-radius:50%;background:#27272a;color:#d4d4d8;font-size:9px;font-weight:700;direction:ltr">${base.slice(0, 3)}</span>`;
  };
})();
</script>'''.replace('ICON_DATA', json.dumps(icons, separators=(',', ':')))
    page = ROOT / 'index.html'
    html = page.read_text(encoding='utf-8')
    block = START + '\n' + script + '\n' + END
    if START in html:
        before, rest = html.split(START, 1)
        _, after = rest.split(END, 1)
        html = before + block + after
    else:
        html = html.replace('</title>', '</title>\n' + block, 1)
    page.write_text(html, encoding='utf-8')
    print(f'Embedded {len(icons)} local coin logos into index.html')


if __name__ == '__main__':
    main()
