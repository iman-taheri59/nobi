# Coin specifications from Nobitex; logos from CoinGecko with a local cache

[فارسی](coin-metadata.md) · **English**

> The product name is nobitrader; the service name and the legacy deployment paths are
> real technical identifiers and have not changed. [Note on technical names and
> versioning](releases.en.md).

## Behaviour

- The primary source for names, symbols, networks, deposit/withdrawal status, display
  precision and trading specifications is **Nobitex `/v2/options`**. The request the
  app already made, with no duplicate call, feeds both the trading-rules adapter and the
  metadata cache.
- CoinGecko (`/coins/markets`) supplies **only the logo and an alternative name**. The
  Nobitex name wins over the CoinGecko name, even when the Nobitex cache is old; the
  time and `stale` flag of that choice are visible. If no Nobitex name exists, the
  CoinGecko name is used with its source stated explicitly.
- The documented `/v2/options` response has no logo field; we do not use a guessed
  internal path or an undocumented Nobitex image URL.
- The specification cache is display-only: a stored file from a previous run is not
  re-applied to the order engine at startup; the existing `apply_options` path still
  applies only a fresh Nobitex response. CoinGecko metadata never enters pricing,
  signals, fundamentals or orders.
- `server/coin_registry.json` holds 50 explicit mappings from base-coin symbol to
  CoinGecko id. There is no automatic lookup by short name and no "take the first
  result".
- Both the id and the returned symbol must match the mapping. Renaming a project's
  name/symbol requires an explicit review of the mapping; another brand's logo is never
  substituted automatically.
- Nobitex specifications refresh every **hour**; a failure is retried every **5
  minutes**. A full refresh of CoinGecko logos is every **24 hours**; errors and
  incomplete logo coverage are retried every **hour**. In the hourly retry, images that
  already downloaded successfully are not fetched again.
- Previous healthy data is not deleted on a network error, a rate limit or a write
  error. Image files and JSON are written with atomic replacement and read back after a
  restart.
- Coins outside the 50-item logo list are still returned by the API with their real
  name and specifications if Nobitex returns them; a missing logo does not mean a
  missing coin. Networks / activity status / rules that are absent from the response are
  `null` — never a guess and never a trading default.
- The browser reads cached metadata from our own server once at startup and then hourly;
  a user request never triggers a fresh fetch from the external service.

Image display order:

1. the image downloaded into the server cache;
2. one of the 40 logos shipped with the project;
3. the short letters of the symbol, without inventing a fake logo.

In the offline mobile build only the last two steps exist. If the mobile file is
connected to the server, using cached images also requires valid authentication for the
image endpoint; failing that does not break either local fallback.

## Settings

In `server/.env` or the service's real environment (for systemd: `/etc/nobi/nobi.env`):

```dotenv
NBX_COIN_METADATA_ENABLED=1
NBX_COIN_CACHE=/srv/nobi-data/coin-cache
# Put the Demo key on the server only; never in HTML/the browser or the repository.
# NBX_COINGECKO_API_KEY=
```

- `NBX_COINGECKO_API_KEY` is a **Demo** key, not Pro; it is sent only in the request
  header for metadata API calls — not in a query string and not to the image host.
- Without a key, public access is attempted; success is not guaranteed. An error/429
  means keep the cache and retry, not show a green status.
- The development default is `server/coin_cache` (git-ignored). On ArvanCloud the path
  must be inside the service's writable path, i.e. `/srv/nobi-data`. The env sample and
  the install script set this path; re-running the install script adds the cache path to
  an older env as well, without touching existing keys.
- Docker Compose has a separate named volume for this cache.
- With `NBX_COIN_METADATA_ENABLED=0` only the **CoinGecko** fetch stops; fetching
  Nobitex public options stays on. The previous cache and the local logos remain usable.
- Restart the service after changing the env.

From the project root, on Windows/Laragon or Linux:

```bash
python scripts/refresh_coin_metadata.py
```

This command fetches Nobitex specifications and CoinGecko logos once and prints only the
number of Nobitex coins, the time/error of that fetch, and the number of mappings,
cached images, stale items and the error code. Exit code zero means both sources
succeeded and the logos are fully refreshed; incomplete coverage also exits non-zero,
even if some images were stored successfully. In a deployment run it with the same user
and environment as the service, not as root with the development default path.

## Internal API and health view

- `GET /api/coins`: the two sources combined with Nobitex taking priority. Each row
  carries `name_source`, `name_updated_at`, `name_stale` and `symbol_source`. The
  `exchange` section for that coin holds networks, display precision, activity status,
  source and fetch time from Nobitex.
- `id` is the CoinGecko id for the **logo** and stays `null` for a coin with no mapping;
  that id never rewrites the exchange's name or identity.
- The old `updated_at` and `stale` stay attached to the **logo** for compatibility;
  `logo_source`, the logo error code and a local `image_url` are available too. A fresh
  Nobitex name does not make an old logo look fresh.
- The upstream `exchange.trading_rules` section holds `minOrders`, `amountPrecisions`
  and `pricePrecisions`. Nobitex's own keys and units are preserved; for example the rial
  value of `rls` is not relabelled as toman. Numeric values are strings so that display
  conversion cannot destroy precision.
- `GET /api/coins/{id}/logo`: only the image already in the cache, not an arbitrary
  proxy. Unknown/invalid ⇒ 404.
- Both endpoints sit behind the dashboard's sign-in gate. The CoinGecko key or an
  external image URL never appears in a response.
- The settings tab shows a separate `nobitex_metadata` health card with the number of
  exchange-specification coins; the `coin_metadata` card independently shows the number
  of cached images. The not-measured state is "unknown"; incomplete coverage or a stale
  cache is yellow, not green.
- `last_success` is the time of the last **complete** refresh; with incomplete coverage,
  read each coin's real time from `updated_at`.

Error codes per coin:

| Code | Meaning |
|---|---|
| `coin_not_returned` | The service did not return the registered id |
| `identity_mismatch` | The returned symbol does not match the expected one; automatic substitution is forbidden |
| `invalid_metadata` | The name or image URL is invalid |
| `image_unavailable` | Fetching, format or storing the image failed |

## Security and source limits

- The image hosts are only `coin-images.coingecko.com` and `assets.coingecko.com`, over
  HTTPS, with a `/coins/images/` path; redirects are not followed.
- Images are at most 512 KiB and only PNG/JPEG/WebP; external SVG, HTML and unknown
  content are refused. Responses carry `nosniff`.
- A metadata response is at most 2 MiB. The file name is built only from a valid mapping
  id; the client cannot impose a URL or a file path on the server.
- Keys, upstream error text and the bodies of failed responses are not logged. Error
  status carries fixed codes only.
- CoinGecko logos are the property of the respective projects; downloading them does not
  grant them a CC0 licence. The CC0 licence of `assets/coins` covers only the local
  fallback set. Before redistributing cached images, review CoinGecko's terms and
  trademark rights; the cache is not committed to Git.

Service documentation: https://docs.coingecko.com/reference/coins-markets

## Repeatable tests

```bash
python -m pytest -q server/tests/test_coin_metadata.py server/tests/test_nobitex_metadata.py
npm run build
npm run test:icons
```

The automated tests simulate upstream so that they are repeatable. The live test run in
this environment on 2026-10-21 stored **49 images out of 50 mappings**. For `TON`, the
registered id returned a response with a different symbol; `identity_mismatch` was
recorded deliberately, and its logo comes from the fallback path instead of guessing a
brand. This is an observation at test time, not a guarantee of future coverage or a
verification on your server.

### The limit of the live Nobitex test

In this change's run on 2026-10-21, the live `/v2/options` request from the development
environment failed (`refresh_failed`, zero confirmed coins, fetch time `null`). The live
Nobitex specification fetch is therefore **not** verified in this environment; no
invented name, network or specification was stored in its place. The source priority,
cache retention, API output and the shared-request connection to the rules engine are
tested with repeatable tests and sample responses. Running
`python scripts/refresh_coin_metadata.py` on your own system/server shows the real
connection result there.

Nobitex specification reference: https://apidocs.nobitex.ir/openapi/options.yaml
