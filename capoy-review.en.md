# Public review of capoy.ir

[فارسی](capoy-review.md)

- Review date: 2026-11-02
- Domain: `https://capoy.ir`
- Review type: public, non-destructive, no login, no orders, no load testing.
- Observed backend version: `2.1.8`
- Overall result: the site is up, HTTPS responds, initial setup is claimed, sensitive APIs do not return data without login, and the backend is scanning the market. Several improvements were observed around public health details and exchange-probe freshness.

> English version of the Persian report `capoy-review.md`. This is a historical public review of the deployed `2.1.8` state.

---

## 1. Positive observations

### Availability and version

- `https://capoy.ir` returned the `nobiTrader` page.
- In the review tooling, the final address for `http://capoy.ir` was `https://capoy.ir/`; this observation alone does not prove a 301 or HSTS.
- `https://www.capoy.ir` also reached the same main page on the apex domain.
- `/api/health` reported version `2.1.8`, matching the reviewed repository version.
- `/version-check.html` also showed served version `2.1.8`.

### Public setup and authentication state

- `/api/setup/status`:

```json
{"ok":true,"setup_required":false}
```

This means initial setup is complete and the owner-claim form should not be active.

- `/api/auth/me` without a cookie:

```json
{"auth_required":true,"user":null,"method":null,"can_mutate":false,"is_owner":false}
```

- `/api/health` reported:

```json
"auth": {
  "mode": "token",
  "token_required": true,
  "cors_origins": [],
  "accounts": true
}
```

This means user accounts are enabled, token/API protection is on, and no external CORS origin is listed.

### Protection of sensitive APIs

These paths did not return sensitive data without login and returned a login-required message:

- `/api/settings`
- `/api/live/status`
- `/api/universe`
- `/api/signals?tf=15m`
- `/api/risk/state`
- `/api/market/overview`
- `/api/paper/metrics`
- `/docs`
- `/openapi.json`

Example response, translated from Persian:

```json
{"detail":"You must sign in to view this section","auth":"login"}
```

This behavior is consistent with the repository security model.

### Backend operational state

In a fresh health sample:

```json
"scan_loop": {
  "enabled": true,
  "interval_s": 300,
  "runs": 106,
  "last_ok": true,
  "tf": "15m",
  "last_scored": 38,
  "last_freshness": {
    "stale": false,
    "reason": "current",
    "symbols": {"count":38,"current":38,"stale":0,"no_candles":0}
  }
}
```

And exchange-client statistics:

```json
"stats": {
  "total": 5335,
  "ok": 5329,
  "fail": 6,
  "success_rate": 99.9
}
```

Therefore the backend was alive, periodic scanning was enabled, and data for 38 symbols was reported fresh at review time.

---

## 2. Findings and risks

### Finding 1 — Medium: health can show upstream `ok=true` with a stale probe

In the first `/api/health` sample:

```json
"upstream": {
  "ok": true,
  "latency_ms": 23.2,
  "age_s": 2881.6,
  "error": null,
  "degraded": false
},
"provenance": {
  "source": "live"
}
```

`age_s=2881.6` means the last exchange probe was about 48 minutes old. In repository code, the default probe TTL is twenty seconds:

```text
server/main.py
UPSTREAM_TTL_S = int(os.getenv("NBX_UPSTREAM_TTL_S", "20"))
```

However, when the probe is stale, `_upstream_snapshot()` only starts a new probe in the background and returns the previous response with `ok=true`:

```text
server/main.py::_upstream_snapshot
```

In subsequent requests with different query parameters to reduce tool-cache effects, probe age decreased:

```json
"age_s": 102.2
```

and then:

```json
"age_s": 0.8
```

**Impact:** A monitor or UI may briefly, or on the first request after a long idle period, see exchange status as “live/healthy” while the measurement is not fresh. This is different from candle freshness: the candles themselves were reported fresh in `scan_loop`, but the upstream health probe was stale.

**Recommendation:**

- If `age_s > UPSTREAM_TTL_S`, public health should not return a definitive `ok=true`.
- Add one of these states: `stale: true`, `ok: null`, or `source: "stale"`.
- `provenance()` should not return `live` solely based on `client.degraded_mode` when the health probe is stale.
- Regression test: a probe older than TTL should return an explicit refreshed or stale state, not definitive success.

---

### Finding 2 — Low/Medium: `/api/health` exposes operational details publicly

The public health path returns:

- exact backend version
- auth state and `token_required`
- whether accounts exist
- CORS origin list
- scan-loop details, run count, last time, symbol count, and freshness
- exchange call statistics and latency
- upstream state

This is useful for internal monitoring, but on a public site it can increase version/operations fingerprinting.

**Recommendation:**

- Make public health minimal, for example:

```json
{"ok":true,"version":"2.1.8"}
```

or even just `{"ok":true}`.

- Move `scan_loop`, `stats`, `upstream`, and `auth` details to an authenticated path such as `/api/health/details` or `/api/diagnostics`.
- If the exact version is not needed publicly, remove it or show it only on an authenticated page.

---

### Finding 3 — Low: the version diagnostic page is public

`/version-check.html` opens without login and shows backend version and server time.

This page is useful for debugging, but it does not need to be public in production.

**Recommendation:**

- Put it behind authentication.
- Or disable it in production.
- Or show only a short hash/build identifier, not the full version and server time.

---

### Finding 4 — Low: `robots.txt` is missing

`/robots.txt` returned 404:

```json
{"detail":"Not Found"}
```

This is not a security vulnerability, but for a site that is mostly behind login, crawler policy should be explicit.

**Recommendation:** If the site is not public:

```text
User-agent: *
Disallow: /
```

If only the login page is public, allow that page and disallow the rest.

---

### Finding 5 — Needs verification, not proven: edge security headers

In this public review, response headers such as these were not directly measured:

- `Strict-Transport-Security`
- `Content-Security-Policy`
- `X-Content-Type-Options`
- `Referrer-Policy`
- `X-Frame-Options` or `frame-ancestors`
- `Permissions-Policy`
- session cookie flags after login

The repository `deploy/nginx.conf` contains some of these, but one cannot assume the exact configuration is applied on the live server.

**Recommended follow-up on the server:**

```bash
curl -I https://capoy.ir
curl -I http://capoy.ir
```

And after a test login:

- Session cookie should have `Secure`, `HttpOnly`, `SameSite=Lax`.
- Authenticated paths should not be publicly cached.
- HSTS should be enabled on HTTPS.
- A minimal CSP should be defined for scripts and connections.

---

## 3. Relationship to the code review

The online reported version `2.1.8` matches the repository version; however, matching version numbers alone does not prove all deployed files are exactly that commit. With that assumption, the earlier code findings remain relevant, especially:

- daily loss halt is only a warning
- timeframe fallback in auto
- sell/short accounting
- live preflight dependence on the paper account, if live is enabled
- promotion evidence counting rejected attempts

But from outside and without login, it is not possible to confirm:

- whether `NBX_LIVE_MODE` is enabled on the server
- whether auto is enabled for a specific symbol
- whether risk/env.production settings differ from the repository
- whether online caches/databases exhibit exactly the same behavior as the code

Fresh `scan_loop` and high success statistics are good operational signs, but they do not replace trading-logic tests.

---

## 4. Limitations of this review

- No login was performed.
- No paper or real order was placed.
- No load testing, fuzzing, brute force, or aggressive vulnerability scanning was performed.
- HTTP headers and post-login cookies were not directly inspected.
- Full browser/mobile rendering and post-login UI interaction were not reviewed.
- Owner/operator paths were checked only for public protection, not internal behavior.

---

## 5. Suggested next review steps

For a deeper and safe review:

1. Create a test account with minimal access, preferably viewer/read-only.
2. Do not put a real exchange key or real balance in that account.
3. If order paths are to be tested, do so only in paper/dry-run mode with explicit limits.
4. With that account, review:
   - CSRF and session/logout
   - role access to settings/live/users/operations
   - auto and queue behavior
   - correctness of risk state and daily PnL display
   - errors and logs for information leakage
   - mobile and PWA behavior
   - cache and version mismatch

---

## Conclusion

The public site state is good: up, HTTPS, setup claimed, sensitive APIs protected, and market scanning active. The most important technical observation was health reporting `upstream.ok=true` with a probe about 48 minutes stale. After that, public health details, the public version-check page, and missing `robots.txt` are lower-priority items.
