# Upgrade to 2.1.10 — controlled deployment checklist

[فارسی](upgrade-2.1.10.md) · **English**

> **Historical note (ADR-0058):** This checklist describes only the 2.1.10 source tree; its file/hash references to `nobitex-mobile.html` belong to that old payload. The working 2.5.7 branch has no standalone HTML output. Do not use this checklist for a current release or deployment.

This document prepares the local `2.1.10` checkpoint for a later, explicitly authorized deployment. It is **not** permission to deploy, push, tag, restart a production service, enable Live/Auto, or place an order.

Target example: `https://capoy.ir` on an ArvanCloud Ubuntu VM with nginx + systemd. Adjust paths/domain to the real installation before running anything.

## 1. What this release changes

- `2.1.9`: Secure session/CSRF cookies on public HTTPS, browser security headers, API/docs `private, no-store`, nginx proxy-header inheritance fix, global `drift/cohorts` fix, and approval-queue execution preview/gating.
- `2.1.10`: English code comments/docstrings, English analysis/review reports, bilingual analysis catalogues, translation parity tests, and this deployment checklist.
- No release step enables Live or Auto, adds an exchange key, or sends a real order.

## 2. Preconditions

- [ ] Operator has shell access and a maintenance window.
- [ ] Current public health/version is recorded.
- [ ] Current deployed commit/archive is recorded.
- [ ] Live mode is disabled, placement is disabled, and Auto is off.
- [ ] The real env file, nginx site, systemd unit, and data directory paths are known.
- [ ] Backup destination has enough space and is not inside the service’s writable data path.
- [ ] Rollback owner and decision criteria are agreed.

Example pre-checks:

```bash
curl -fsS 'https://capoy.ir/api/health?predeploy=2.1.10'
sudo systemctl status nobi --no-pager
sudo nginx -t
sudo -u nobi git -C /opt/nobi rev-parse HEAD
```

## 3. Deployment artifacts

Use the whole `2.1.10` source tree/commit, not isolated file copies. Critical files to verify after transfer:

```text
VERSION
package.json
package-lock.json
server/main.py
server/identity.py
server/paper_trading.py
server/signal_drift.py
index.html
nobitex-mobile.html
deploy/nginx.conf
deploy/nobi.env.example
deploy/nobi.service
```

`deploy/nobi.env.example` is a reference, **not** a replacement for `/etc/nobi/nobi.env`. The live env file contains secrets and must be edited in place.

A local checksum manifest for the critical files is provided at `deploy/upgrade-2.1.10.files`. After transferring the source tree, verify it from the repository root:

```bash
sha256sum -c deploy/upgrade-2.1.10.files
```

The nginx template contains example domain/certificate paths. Adapt it to the real host before installing:

- `server_name`
- certificate/key paths
- upstream address/port
- ACME/webroot path
- any custom locations already required by the live site

## 4. Backup before touching the service

Plan a short downtime. Stop the app first so SQLite files are copied consistently.

```bash
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="/root/nobi-backup-$STAMP"
sudo mkdir -p "$BACKUP"

sudo systemctl stop nobi

sudo tar -C /srv -czf "$BACKUP/nobi-data.tar.gz" nobi-data
sudo install -m 0600 /etc/nobi/nobi.env "$BACKUP/nobi.env"
sudo cp -a /etc/nginx/sites-available/nobi "$BACKUP/nginx-site"
sudo cp -a /etc/nginx/sites-enabled/nobi "$BACKUP/nginx-site-enabled" || true
sudo cp -a /etc/systemd/system/nobi.service "$BACKUP/nobi.service"
sudo -u nobi git -C /opt/nobi rev-parse HEAD | sudo tee "$BACKUP/old-commit.txt"

sudo sha256sum "$BACKUP"/* | sudo tee "$BACKUP/SHA256SUMS"
```

If the installation uses different paths, replace them explicitly. Do not continue if the backup fails.

## 5. Install the new source

If the commit has been published through the normal release process, the documented updater can be used:

```bash
sudo bash /opt/nobi/scripts/update_arvan.sh --check
sudo bash /opt/nobi/scripts/update_arvan.sh --backup --verify --public https://capoy.ir
```

If the checkpoint is still local-only, **do not expect `origin/main` to contain it**. Transfer the checked-out source or archive to the server, then install dependencies as the service user:

```bash
sudo -u nobi /opt/nobi/.venv/bin/python -m pip install -r /opt/nobi/server/requirements.txt
sudo -u nobi /opt/nobi/.venv/bin/python -c 'import fastapi, uvicorn, pandas; print("deps ok")'
cat /opt/nobi/VERSION
```

## 6. Required env change

Edit the real env file, usually `/etc/nobi/nobi.env`:

```bash
sudoedit /etc/nobi/nobi.env
```

Ensure:

```text
NBX_COOKIE_SECURE=always
NBX_TRUSTED_PROXIES=127.0.0.1
NBX_API_TOKEN=<existing secret, do not print or share>
```

Use the real proxy IP/CIDR if nginx is not on loopback. Do not enable Live/Auto. Do not replace the file with the example.

## 7. Install the nginx template

After adapting the template to the real host:

```bash
sudo install -m 0644 /opt/nobi/deploy/nginx.conf /etc/nginx/sites-available/nobi
sudo nginx -t
sudo systemctl reload nginx
```

If the live site uses a different nginx layout, merge the required rules instead of overwriting blindly:

- full `X-Forwarded-*` set in every proxied location
- security headers with `always`
- upstream security-header hiding to avoid duplicates
- explicit `Cache-Control` for `/` and `/assets/`

## 8. Restart and verify

```bash
sudo systemctl daemon-reload
sudo systemctl restart nobi
sudo systemctl status nobi --no-pager
curl -fsS 'https://capoy.ir/api/health?postdeploy=2.1.10'
```

Expected health version: `2.1.10`.

Run the deployment checker. If the service env file is root-only, do not print secrets on a command line; use the updater’s verification path or a shell that already has the `NBX_*` variables safely in its environment:

```bash
sudo -u nobi /opt/nobi/.venv/bin/python /opt/nobi/scripts/check_deploy.py --public https://capoy.ir
```

Then run the owner probe from a trusted operator machine. It is read-only except login/logout:

```bash
python3 capoy_owner_probe.py --role owner --output capoy-owner-probe-post-2.1.10.json
```

Expected probe results:

- `session cookie Secure on HTTPS` → pass
- `edge header strict-transport-security` → pass
- `edge header x-content-type-options` → pass
- `edge header referrer-policy` → pass
- `frame protection` → pass
- `authenticated API no-store` → pass
- `GET /api/v1/signal-intelligence/drift/cohorts` → 200, not 422
- logout clears the session

Manual UI check, without placing an order:

- approval queue shows execution `R:TP2`
- rows with `executable:false` are not approvable
- stale execution previews are repainted/locked
- a definite broker refusal retires the candle instead of inviting an immediate retry

## 9. Rollback

Rollback if health fails, login fails, cookies are still not Secure, headers are missing/duplicated, or the queue/API regresses.

```bash
sudo systemctl stop nobi
# Restore the previous source tree/archive recorded before deployment.
sudo install -m 0600 "$BACKUP/nobi.env" /etc/nobi/nobi.env
sudo install -m 0644 "$BACKUP/nginx-site" /etc/nginx/sites-available/nobi
sudo install -m 0644 "$BACKUP/nobi.service" /etc/systemd/system/nobi.service
sudo -u nobi /opt/nobi/.venv/bin/python -m pip install -r /opt/nobi/server/requirements.txt
sudo systemctl daemon-reload
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl start nobi
```

Restore the data archive only if data corruption is confirmed. Keep the failed deployment’s logs and probe output for diagnosis.

## 10. After deployment

- Keep Live/Auto disabled until a separate, evidence-based decision.
- Review open invites and extra owner/operator sessions.
- Confirm backups exist and a restore test is scheduled.
- Do not share probe JSON publicly if it contains operational/financial metrics.
