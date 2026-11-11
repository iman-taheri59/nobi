# Local cryptocurrency logos

Source: https://github.com/spothq/cryptocurrency-icons
Pinned revision: `1a63530be6e374711a8554f31b17e4cb92c25fa5`
Original paths: `svg/color/*.svg`. License: CC0 (see LICENSE.md).

These are vendored brand assets, not generated illustrations. No price or market
information is bundled with them. Unsupported tickers deliberately show a text
fallback rather than a different coin's logo. Wrapped/renamed tokens are not
silently mapped to another brand.

Run `python scripts/build_coin_icons.py` after modifying the SVG set. It embeds
the local images as data URIs in the marked block of the canonical `index.html`,
so the responsive dashboard needs no image CDN or logo requests. Run `npm run css`
to refresh the compiled utility stylesheet; `npm run build` runs both steps.

The server can additionally download CoinGecko logos into a separate ignored
runtime cache. Those downloaded images are NOT covered by this folder's CC0
license; see ../../docs/coin-metadata.md for configuration and provenance.
