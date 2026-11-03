# nginx merge plan for 2.1.10 — capoy.ir

Status: application `2.1.10` is deployed and healthy. The remaining deployment step is the nginx edge-header merge. Do not overwrite the live nginx site blindly.

## Exact merged artifact (2.2.1)

The live `/etc/nginx/sites-available/nobi` file was reviewed and merged into:

```text
deploy/nginx-capoy.ir.conf
```

This is a drop-in replacement for that exact site file only. It preserves the existing upstream `nobi_app`, `log_format nobi`, ACME webroot, certificate paths, timeouts, body limit, and `listen 443 ssl http2;` form used by the live host.

What it changes:

- every proxied location now forwards the full `Host` / `X-Real-IP` / `X-Forwarded-*` set;
- locations that own headers repeat the full security `add_header ... always` set;
- upstream copies of the five edge security headers are hidden;
- `location = /` and `/assets/` also hide upstream `Cache-Control` and emit exactly one cache value;
- `/assets/` no longer combines `expires` with `add_header Cache-Control`, so it cannot emit two cache headers;
- the misleading `X-API-Token` scrub comment was removed; the bearer token is still forwarded untouched.

Do not enable this file as a second site. Replace the existing active `sites-available/nobi` target, keeping the current `sites-enabled` symlink.

### Operator install

Transfer `deploy/nginx-capoy.ir.conf` to the server, for example as `/root/nginx-capoy.ir.conf`, then:

```bash
BACKUP_NGINX="/root/nobi-nginx-before-$(date -u +%Y%m%dT%H%M%SZ).conf"
sudo cp -a /etc/nginx/sites-available/nobi "$BACKUP_NGINX"
sudo install -m 0644 -o root -g root /root/nginx-capoy.ir.conf /etc/nginx/sites-available/nobi
sudo nginx -t
```

Only if `nginx -t` succeeds:

```bash
sudo systemctl reload nginx
curl -sS -D - -o /dev/null 'https://capoy.ir/'
curl -sS -D - -o /dev/null 'https://capoy.ir/assets/css/base.css'
curl -sS -D - -o /dev/null 'https://capoy.ir/api/health?postnginx=2.2.1'
curl -fsS 'https://capoy.ir/api/health?postnginx=2.2.1'
```

If `scripts/check_edge_headers.py` has also been transferred to the server, run it as the structured check:

```bash
python3 /opt/nobi/scripts/check_edge_headers.py --base https://capoy.ir --json /root/edge-headers-post-nginx.json
```

The health version remains `2.1.10` until the application itself is upgraded to the local `2.2.1` build. The nginx change does not require deploying `2.2.0` / `2.2.1`.

### Rollback

```bash
sudo cp -a "$BACKUP_NGINX" /etc/nginx/sites-available/nobi
sudo nginx -t
sudo systemctl reload nginx
```

If the shell session was lost, restore the newest `/root/nobi-nginx-before-*.conf` backup instead of guessing.

### Local validation performed

- `crossplane parse --strict` against an `http { include ...; }` wrapper: status `ok`, no errors.
- `server/tests/test_nginx_capoy_config.py`: structural checks for inheritance, cache ownership, forwarding, TLS/ACME preservation, and no bearer-token stripping.
- Full backend suite, lint, type check, lockfile check, and dashboard build passed.
- Real `nginx -t` must still be run on the server before reload.

## Observed public header issues

From the operator-provided header capture:

- `/`
  - `Cache-Control` appears twice: `no-store, must-revalidate` and `no-store`.
  - `Strict-Transport-Security` is missing.
  - App security headers are present.
- `/api/health` and `/api/auth/me`
  - `X-Content-Type-Options` appears twice.
  - `Referrer-Policy` appears twice.
  - `Strict-Transport-Security` is present.
  - App security headers are present.

This is consistent with nginx `add_header` / `proxy_hide_header` inheritance not yet matching the `2.1.10` template.

## Target contract

For HTTPS responses:

- exactly one `Strict-Transport-Security: max-age=31536000`
- exactly one `X-Content-Type-Options: nosniff`
- exactly one `Referrer-Policy: same-origin`
- exactly one `X-Frame-Options: DENY`
- exactly one `Content-Security-Policy: frame-ancestors 'none'`
- `/` has one `Cache-Control: no-store`
- API/auth JSON keeps `Cache-Control: private, no-store, max-age=0` or another single `no-store` value
- every proxied location forwards the full `X-Forwarded-*` set

## Safe merge rules

1. Find the active HTTPS server block for `capoy.ir`.
2. Keep the existing certificate paths, upstream address/port, ACME/webroot, and custom locations.
3. At the HTTPS server level, ensure:
   - `proxy_hide_header` for the five security headers emitted by the app.
   - `add_header ... always` for the five security headers.
   - full `proxy_set_header` set: `Host`, `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Forwarded-Host`.
4. For every proxied location:
   - If the location defines any `proxy_set_header`, repeat the full forwarded-header set there.
   - If the location defines any `add_header`, repeat the full security-header set there.
   - If the location defines any `proxy_hide_header`, repeat the full required hide list there.
5. For `location = /`:
   - hide upstream `Cache-Control` and security headers,
   - add one `Cache-Control: no-store`,
   - add the full security-header set.
6. For `/assets/`, if present:
   - hide upstream `Cache-Control` and security headers,
   - add one `Cache-Control: public, max-age=604800, immutable`,
   - add the full security-header set.
7. Do not add HSTS to the plain-HTTP redirect server block; HSTS belongs on HTTPS.
8. Run `sudo nginx -t` before reload.
9. Reload nginx, then run the read-only edge checker.

## Read-only edge checker

The repository now includes:

```bash
python3 scripts/check_edge_headers.py --base https://capoy.ir
python3 scripts/check_edge_headers.py --base https://capoy.ir --json edge-headers.json
python3 scripts/check_edge_headers.py --self-test
```

It does not log in and does not send orders. It checks the public header contract for `/`, `/api/health`, and `/api/auth/me`.

## Operator collection commands

When server access is available again:

```bash
sudo grep -RInE 'server_name[^;]*capoy\.ir' /etc/nginx 2>/dev/null || true
sudo ls -l /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null || true
```

Then send the active capoy.ir server block(s), including:

- `listen`
- `server_name`
- certificate/key paths
- all `location` blocks
- `proxy_pass`
- `proxy_set_header`
- `add_header`
- `proxy_hide_header`
- relevant `include` lines

Do not send private key contents. Redact literal `token`, `password`, `secret`, or `Authorization` values if present.

## Reload and verification

After merging:

```bash
sudo nginx -t
sudo systemctl reload nginx

python3 scripts/check_edge_headers.py --base https://capoy.ir
curl -fsS 'https://capoy.ir/api/health?postnginx=2.1.10'
```

Expected health version remains `2.1.10`.

Then run the owner probe from a trusted operator machine:

```bash
python3 capoy_owner_probe.py --role owner --output capoy-owner-probe-post-nginx-2.1.10.json
```

Expected probe results include Secure session/CSRF cookies, single edge security headers, frame protection, authenticated API `no-store`, and global drift cohorts returning 200.

## Rollback

If `nginx -t` fails, do not reload. Restore the previous site file from the pre-deployment backup, then test again.

If reload causes a regression, prefer restoring only the changed site file. The backup archive was created from `/etc`, so its paths are relative to `/etc`, for example `nginx/sites-available/nobi`:

```bash
BACKUP=$(cat /root/nobi-backup-latest.txt)
sudo tar -C /etc -xzf "$BACKUP/nginx-etc.tar.gz" nginx/sites-available/nobi
sudo nginx -t
sudo systemctl reload nginx
```

Only restore the full nginx tree if the backup is known to match the current host layout:

```bash
sudo tar -C /etc -xzf "$BACKUP/nginx-etc.tar.gz" nginx
sudo nginx -t
sudo systemctl reload nginx
```
