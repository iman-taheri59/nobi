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
    script = r'''<script id="nbx-coin-icons">
// Vendored cryptocurrency-icons (CC0); see assets/coins/README.md.
(function () {
  'use strict';
  const logos = ICON_DATA;
  const metadata = Object.create(null);
  const broken = new Set();
  const escape = (value) => String(value).replace(/[&<>"']/g,
    (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  window.coinIcon = function coinIcon(symbol, size = 24) {
    const raw = String(symbol || '').toUpperCase().trim();
    // Split pairs first, then strip only a terminal quote (never the base).
    let base = raw.split(/[/_:\s-]/)[0];
    if (!(Object.prototype.hasOwnProperty.call(logos, base) || metadata[base])) {
      base = base.replace(/(USDT|USDC|IRT|IRR|TMN|RLS)$/, '');
    }
    if (!/^[A-Z0-9]{1,24}$/.test(base)) base = '?';
    const px = Math.min(48, Math.max(16, Number(size) || 24));
    const style = `display:inline-flex;align-items:center;justify-content:center;vertical-align:middle;flex-shrink:0;width:${px}px;height:${px}px;margin-inline-end:6px`;
    const remote = metadata[base];
    const title = escape(remote && remote.name ? remote.name : base);
    const src = remote && remote.src && !broken.has(remote.src) ? remote.src
      : (broken.has(logos[base]) ? null : logos[base]);
    const attrs = `data-coin="${base}" data-coin-size="${px}"`;
    if (src) return `<img class="coin-logo" ${attrs} src="${escape(src)}" width="${px}" height="${px}" alt="" title="${title}" style="${style}" decoding="async">`;
    // Honest textual fallback, not an invented brand logo. Safe alphabet only.
    return `<span class="coin-logo coin-logo-fallback" ${attrs} role="img" aria-label="${base}" title="${title}" style="${style};border-radius:50%;background:#27272a;color:#d4d4d8;font-size:9px;font-weight:700;direction:ltr">${base.slice(0, 3)}</span>`;
  };
  window.setCoinMetadata = function setCoinMetadata(rows, apiBase) {
    let origin;
    try {
      const url = new URL(apiBase || '/api', document.baseURI);
      if (!['http:', 'https:'].includes(url.protocol)) return;
      origin = url.origin;
    } catch (_) { return; }
    for (const row of (Array.isArray(rows) ? rows : [])) {
      if (!row || !/^[A-Z0-9]{1,24}$/.test(row.symbol)
          || !/^[a-z0-9][a-z0-9-]{0,99}$/.test(row.id)
          || row.image_url !== `/api/coins/${row.id}/logo`) continue;
      metadata[row.symbol] = {
        name: typeof row.name === 'string' ? row.name.slice(0, 160) : row.symbol,
        src: origin + row.image_url + '?v=' + encodeURIComponent(row.updated_at || 0),
      };
      // A successful hourly metadata probe permits one new image attempt.
      broken.delete(metadata[row.symbol].src);
    }
    // Also upgrade placeholders already painted before the metadata arrived.
    document.querySelectorAll('[data-coin]').forEach((el) => {
      el.outerHTML = window.coinIcon(el.dataset.coin, el.dataset.coinSize);
    });
  };
  document.addEventListener('error', (event) => {
    const el = event.target;
    if (!el || el.tagName !== 'IMG' || !el.dataset.coin) return;
    broken.add(el.getAttribute('src'));
    el.outerHTML = window.coinIcon(el.dataset.coin, el.dataset.coinSize);
  }, true);
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
