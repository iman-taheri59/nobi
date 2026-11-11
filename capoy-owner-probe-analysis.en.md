# Analysis of the owner-probe output from capoy.ir

[فارسی](capoy-owner-probe-analysis.md)

Reviewed file:

```text
/home/user/nobi-review/live-owner-probe/capoy-owner-probe.json
```

Run details:

```text
script_version: 2.0.0
target_app_version: 2.1.8
base_origin: https://capoy.ir
expected_role: owner
generated_at: 2026-11-02T06:30:09Z
request_count: 75
duration_s: 164
aborted: false
```

Overall result:

```text
checks:           pass=10, warn=6, fail=1, info=2
endpoint_outcomes: ok=73, unexpected=1
```

> **Historical note:** This report records only the production probe snapshot for version `2.1.8` on 2026-11-02. Paths such as `/nobitex-mobile.html` reflect that old execution, not routes on the current working branch.

The probe performed only `POST /api/auth/login` and `POST /api/auth/logout` as allowed mutations; all other requests were GETs. No order, settings change, user change, backup/restore/restart, or diagnostics/devsuite execution occurred. The output also contains no password, cookie value, raw log, username, IP, or user-agent; however, its financial/operational metrics may still be sensitive.

> English version of the Persian report `capoy-owner-probe-analysis.md`. This analysis describes the live `2.1.8` probe state. The cookie/header, global drift, and approval-queue fixes were implemented locally in `2.1.9`; deployment is still required for them to affect `capoy.ir`.

---

## 1) The only recorded fail: session cookie is not Secure on HTTPS

In the login response:

```text
nbx_session:
  secure=false
  httponly=true
  samesite=lax
  path=/
  host-only=true
  max-age present

nbx_csrf:
  secure=false
  httponly=false
  samesite=lax
  path=/
  host-only=true
  max-age present
```

Therefore this check failed:

```text
session cookie Secure on HTTPS → fail
```

### Why it matters

The site was reviewed over HTTPS, but the session cookie lacks `Secure`. If a user ever visits `http://capoy.ir`, or an HTTP path is active, the browser can send the session cookie over an HTTP request. For a financial dashboard with an owner account, this is a serious risk.

`HttpOnly=true`, `SameSite=Lax`, and host-only are positive, but they do not replace `Secure`. `nbx_csrf` is intentionally `HttpOnly=false` so JavaScript can return it in the CSRF header; that is not itself a defect, but it should also be `Secure`.

### Likely cause

In code, `server/identity.py`’s `is_secure()` makes the cookie Secure when it sees the request as HTTPS. In a deployment behind nginx/reverse proxy, if the internal application is HTTP and `X-Forwarded-Proto: https` does not reach it, a non-Secure cookie is created.

The reference file `deploy/nginx.conf` also has an important point: proxy headers are defined at server level, but some locations define their own `proxy_set_header`. In nginx, if a location has even one `proxy_set_header`, server-level proxy headers may not be inherited into that location. For example:

```nginx
location / {
    proxy_pass http://nobi_app;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
}
```

This can prevent the server-level `X-Forwarded-Proto` from reaching requests in that location. Also, `location = /` and `/assets/` have their own headers.

### Proposed fix

Create a shared file for proxy headers and include it in every location that proxies to the application:

```nginx
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host  $host;
```

For example:

```nginx
location / {
    proxy_pass http://nobi_app;
    proxy_http_version 1.1;
    include /etc/nginx/snippets/nobi-proxy-headers.conf;
    proxy_set_header Connection "";
}

location = / {
    proxy_pass http://nobi_app;
    include /etc/nginx/snippets/nobi-proxy-headers.conf;
    add_header Cache-Control "no-store" always;
}
```

If TLS terminates at another layer, `$scheme` may not reflect the browser’s actual scheme. In that case, the real protocol header must be forwarded correctly from the TLS edge and trusted only from trusted proxies.

Also, if `NBX_TRUSTED_PROXIES` is configured, it must cover the real proxy IP/CIDR connecting to the application; for nginx on loopback, usually `127.0.0.1` and, if needed, `::1`.

Precise note: in the current code, if `X-Forwarded-Proto: https` arrives, even from an untrusted peer, the cookie becomes stricter; therefore the main cause of this fail is most likely missing/incorrect `X-Forwarded-Proto`, not merely missing `NBX_TRUSTED_PROXIES`.

### Verification after fix

Run the probe again. Expected:

```text
session cookie Secure on HTTPS → pass
```

and both cookies should have `Secure` in the login response. Logout succeeded in this run, and this check passed:

```text
session cleared after logout → pass
```

Severity: **high**.

---

## 2) Six warns: security headers and cache control

Warn checks:

```text
edge header strict-transport-security
edge header x-content-type-options
edge header content-security-policy
edge header referrer-policy
frame protection
authenticated API no-store
```

### Important: HSTS/nosniff/Referrer-Policy are not completely absent

The probe performed these checks on the `/` response. On `/`, these headers were not seen:

```text
Strict-Transport-Security
X-Content-Type-Options
Referrer-Policy
```

But many API responses, as well as `/manifest.json` and `/version-check.html`, had them:

```text
Strict-Transport-Security: max-age=31536000
X-Content-Type-Options: nosniff
Referrer-Policy: same-origin
```

Paths in the probe output that lacked these three headers:

```text
/
/nbx-core.js
/nbx-app.js
/nobitex-mobile.html
```

This pattern is consistent with nginx `add_header` inheritance: if a location has its own `add_header`, server-level `add_header` directives may not be inherited. In the reference file, `location = /` and `/assets/` are in that situation. The actual server configuration may differ; the active config must be inspected.

### CSP and frame protection

On the reviewed paths, these were not seen:

```text
Content-Security-Policy
X-Frame-Options
Permissions-Policy
```

Missing `X-Frame-Options` or `Content-Security-Policy: frame-ancestors` means there is no explicit clickjacking protection.

Minimum suggestion:

```text
X-Frame-Options: DENY
```

or better:

```text
Content-Security-Policy: frame-ancestors 'none'
```

For a full CSP, because the page likely has inline script/style, start with `Content-Security-Policy-Report-Only` and enforce it after fixing violations.

### Cache-Control for authenticated APIs

This check warned:

```text
authenticated API no-store
```

For example, authenticated `/api/auth/me` had no `Cache-Control`. In contrast, some paths such as `/api/health`, `/api/risk/plan`, and `/api/v1/signal-intelligence/evidence` had no-store. The site root also had `no-store, must-revalidate`.

Recommendation: apply this header globally to authenticated API responses, especially account/settings/audit/paper/live/journal:

```text
Cache-Control: private, no-store, max-age=0
```

Severity: **medium**.

---

## 3) One endpoint actually errored: drift/cohorts

The only endpoint with an unexpected outcome:

```text
GET /api/v1/signal-intelligence/drift/cohorts → 422
```

Error-message fingerprint:

```text
41a76e7a6ab612c9
```

This fingerprint exactly matches:

```text
zip() argument 2 is longer than argument 1
```

### Root cause in code

The default mode for this endpoint in `server/main.py` is `mode=global`. In `server/signal_drift.py`, `cohort_inventory()` for global has:

```python
dimensions = ()
cohort_key = "global"
```

But later it runs:

```python
item["dimensions"] = dict(zip(dimensions, str(item["cohort_key"]).split("|"), strict=True))
```

Because:

```python
"global".split("|") == ["global"]
```

there is one item on the right, but `dimensions` is empty; therefore `zip(..., strict=True)` raises and the endpoint returns 422 instead of 200.

### Proposed fix

```python
parts = str(item["cohort_key"]).split("|") if dimensions else []
item["dimensions"] = dict(zip(dimensions, parts, strict=True))
```

And add a regression test for `mode=global`.

Severity: **medium**; it breaks the drift-cohorts section, but does not directly break trading or security.

---

## 4) The approval-queue problem was also visible in live data

In `/api/paper/orders?limit=20`, these rows were seen:

```text
PUMPIRT  REJECTED  QUEUE  2026-11-02 03:36 UTC
ZECIRT   REJECTED  QUEUE  2026-11-02 03:34 UTC
HOTIRT   REJECTED  QUEUE  2026-11-01 19:45 UTC
HOTIRT   REJECTED  QUEUE  2026-11-01 19:18 UTC
ADAIRT   CLOSED    QUEUE
```

The rejection-reason fingerprint for the first four rows:

```text
ce1e216f0847d795
```

This exactly matches:

```text
execution-rr-too-low
```

So the problem is not only HOTIRT. Several approval-queue signals were later rejected by the paper broker.

This is consistent with the earlier analysis:

- The approval queue does not evaluate executability against the current order book before submission.
- The broker checks TP2 R:R at the execution price.
- If the execution price has worsened relative to the plan, the order is rejected.
- In the UI code, after a definite failure, the queue row is not properly removed/locked and can remain actionable.

From a trade-safety perspective, broker behavior is fail-closed: the order is rejected and no position should be created. But from a product-contract and user-experience perspective, the queue should not present a signal as approvable when it is not executable under current conditions.

Suggested action:

1. Server-side execution preview for the queue: `executable`, `blocked_by`, `plan_entry`, `execution_entry`, `execution_rr`, `min_rr`, `depth_ok`, `as_of`.
2. Disable the approve button when `executable=false`.
3. Show execution `R:TP2`, not just `R:TP1`.
4. Remove/lock the row after definite failure.
5. Transfer full `tp1/tp2/rr/min_rr` in rejection.
6. Do not remove the broker execution guard.

Severity: **high for product trust**, but not equivalent to dangerous order execution.

---

## 5) Operational and security state

### Positive items

```text
setup_required=false
/api/docs anonymous → 401
/openapi.json anonymous → 401
login/logout successful
session cleared after logout → pass
live mode=disabled
configured=false
placement_enabled=false
executor=paper
default exec mode=semi
auto.enabled=false
sends_to_exchange=false
scan_loop enabled and last_ok=true
38/38 symbols current
upstream ok, age_s≈6.3
success_rate≈99.9%
```

`GET /api/live/orders?status=all` returned 409, but because live is disabled this response was expected and the probe recorded it as ok.

### Open invite and sessions

```text
users: total=4, active=3
roles: owner=2, operator=2
sessions: 7
invites: total=4, used=3, open=1
invite roles: owner=1, viewer=1, operator=2
```

One open invite remains. The probe output does not show which invite is open; it only gives the role distribution. Therefore it cannot be said definitively that the open invite is owner. It should be checked in the users/invites section. If the open invite is owner or unnecessary, revoke it.

Also, 7 active sessions were seen. Extra/old sessions, especially for owners, should be closed. The probe’s own session was cleared after logout.

### Backups

```text
operations.archive_count=0
archive_bytes_total=0
restore_available=false
helper_available=true
```

No archive was seen in the reported backup path. If backups are elsewhere, that should be documented; otherwise, for a financial app, regular backups and restore testing are essential.

### Install wizard

```text
bootstrap-owner → passed
host-preflight, storage, runtime, reverse-proxy,
service-install, validation, complete → pending
```

This does not necessarily mean the installation is broken; installation may have been manual. But the wizard state is not aligned with the actual deployment, and the validation/reverse-proxy steps should be reviewed and documented.

### Errors

Sample of the last 20 errors in the past 168 hours:

```text
level: warning=20
sources: nbx-app=18, coin_metadata=2
```

Because of sanitization, raw error text is not in the output. This sample shows only warnings, but because limit=20, it cannot be concluded that no error/critical entries exist.

### Telegram

```text
notify.configured=false
transport=disabled
```

If Telegram alerts are expected, they are not configured.

### WebSocket

```text
enabled=true
transport=rest-poll
ws_url=null
ws_connected=false
ticks=0
```

This means REST polling is effectively active, not a real WebSocket. If intentional, it is not a problem; but the UI should not show it as a connected WebSocket.

### Public surface

Hardening items previously observed:

```text
/api/health is public and shows version/auth/scan-loop
/version-check.html is public
/robots.txt → 404
```

These are not fails by themselves, but for a financial app the public surface should be minimized.

---

## 6) Strategy statistical evidence is not positive at this time

This section is not financial advice; it is only the state of recorded evidence.

Signal Intelligence over 30 days, H1 horizon:

```text
signals=16715
resolved=2231
win_rate_pct=40.92
expectancy_r=-0.0837
avg_return_pct=-0.1847
expectancy_ci95_r=[-0.1112, -0.0563]
```

Journal summary:

```text
H1: n=2231, hit≈40.9%, avgr≈-0.0837
H2: n=1527, hit≈28.9%, avgr≈-0.486
MSI: degraded, ratio=0.533
stage: train, read_only=true
```

Backtest promotion gate for the ZECIRT/1h sample:

```text
state=INSUFFICIENT_DATA
windows=2, required>=3
oos_trades=27, required>=30
profit_factor=0.94, required>=1.3
expectancy_r=-0.013
```

Paper metrics:

```text
trades=1
equity=99921.95 USD
expectancy_r=-1.184
```

Live evidence:

```text
ready=false
samples=0
days=0
reasons: insufficient-samples, insufficient-days
```

Result: current data does not support promotion to live. Live and auto being off at this stage is correct. The paper sample is also very small and should not be the basis for a live decision.

---

## Action priorities

### Immediate

1. Fix `Secure` on session and CSRF cookies over HTTPS.
2. Review the open invite; revoke it if it is owner or unnecessary.
3. Close extra owner/operator sessions.
4. Fix the `drift/cohorts` bug in global mode.

### Short term

5. Make security headers consistent across HTML/JS/API.
6. Add `Cache-Control: private, no-store, max-age=0` for authenticated APIs.
7. Fix the approval-queue contract and execution preview.
8. Set up/document backups and test restore.

### Next

9. Improve strategy evidence, reduce execution rejection rate, and review signal quality before any live.
10. Fix WS/REST-polling status display and complete wizard/validation if needed.
11. Minimize the public health/version-check surface.

---

## Limitations of this analysis

- This is an HTTP/API probe, not a rendered-browser test.
- Only one symbol/timeframe was sampled; not all markets and endpoints were tested.
- Several heavy GETs were skipped by default: heavy compute and gate-ledger evidence.
- Some GETs may refresh caches or read models; no controlling/mutating endpoint was called.
- The owner role did not change the probe’s safety boundary; no order or settings/user change was performed.
