# Step-by-step guide to creating review access for capoy.ir

[فارسی](capoy-owner-probe-guide.md)

This guide is for the site owner to run `capoy_owner_probe.py` themselves.

> English version of the Persian guide `capoy-owner-probe-guide.md`.

## Important limitation

In this environment, I cannot directly log in to `capoy.ir` or POST the login/registration form. The practical review access path is:

1. You run the script on your own machine.
2. The script logs in with an authorized account, reads the permitted sections, and logs out.
3. You send me only the sanitized JSON file.
4. I analyze that output.

**Do not send passwords, invite codes, cookies, API tokens, database files, or raw HAR files.**

---

## Step 1: Get the script file

Main file:

```text
/home/user/nobi-auth-review/capoy_owner_probe.py
```

Download it from the viewer and save it in a simple folder, for example:

```text
Downloads/capoy-review/capoy_owner_probe.py
```

No external dependencies are required; the script uses only the Python standard library.

---

## Step 2: Check Python

Recommended version: Python 3.10 or newer.

### Windows PowerShell

```powershell
cd $HOME\Downloads\capoy-review
py -3 --version
```

### Linux / macOS

```bash
cd ~/Downloads/capoy-review
python3 --version
```

If the Windows command does not work, you may need to install Python or use `python` instead of `py -3`.

---

## Step 3: Choose the right account

To review owner sections such as users, install settings, error logs, operations, and the diagnostics catalogue, the `owner` role is required.

You have two options:

### Simpler option: use an existing owner account

Use the existing owner account only on your own machine. Do not send the password in chat.

### Optional option: create a temporary owner on the server

Only if you have shell access to the server and know the installation paths. The example below uses sample paths; do not run it blindly if your installation is different.

```bash
sudo -u nobi env NBX_AUTH_DB=/srv/nobi-data/auth.db \
  /opt/nobi/.venv/bin/python /opt/nobi/scripts/manage_users.py \
  create capoy-review --role owner
```

The password is prompted hidden. After the review, disable the temporary account:

```bash
sudo -u nobi env NBX_AUTH_DB=/srv/nobi-data/auth.db \
  /opt/nobi/.venv/bin/python /opt/nobi/scripts/manage_users.py \
  disable capoy-review
```

Do not disable the last active owner account.

For this review, do not enable Live or Auto, and do not add a new real exchange key.

---

## Step 4: Run the script

### Recommended normal run

Linux / macOS:

```bash
python3 capoy_owner_probe.py --role owner --output capoy-owner-probe.json
```

Windows PowerShell:

```powershell
py -3 capoy_owner_probe.py --role owner --output capoy-owner-probe.json
```

The script asks:

```text
Username:
Password (hidden):
```

Enter the username. The password is not displayed while typing. Do not put the password in the command, chat, or a file.

### Lighter run

If you want fewer heavy market/fundamental calls:

```bash
python3 capoy_owner_probe.py --role owner --skip-upstream --output capoy-owner-probe.json
```

This option does not guarantee that no upstream request occurs; it only removes selected heavy paths.

### If the focus is HOTIRT

If your desk timeframe is `15m`:

```bash
python3 capoy_owner_probe.py --role owner \
  --sample-symbol HOTIRT --tf 15m \
  --output capoy-owner-probe.json
```

If it is another timeframe, change `--tf`.

Do not add `--include-compute` on the first run unless it becomes necessary later.

---

## Step 5: What to expect

The script sends only these requests:

- `GET` to fixed, reviewed paths
- `POST /api/auth/login`
- `POST /api/auth/logout`

It does not:

- place or cancel orders
- halt/resume
- change settings, users, roles, or invites
- run diagnostics/run or devsuite/run
- backup/restore/restart
- upload files

However, login/logout are recorded in the audit log, and some GETs may refresh server caches or read models. If Live is enabled, the exchange order list may be read through the backend; that is a read, not order placement.

At the end you will usually see something like:

```text
Wrote capoy-owner-probe.json (N requests; checks=...)
```

---

## Step 6: If you see an error

- If `login role matches --role` fails, the account role does not match `--role`. For owner, the account must actually be owner.
- If you get `401/403`, check credentials only on your own machine and do not retry repeatedly.
- If you get `429`, wait a bit; rate limiting has activated.
- If you get a TLS/certificate error, stop the review; do not disable TLS validation.
- If logout fails, log out manually or close that account’s sessions.
- If output was produced but `aborted=true`, send that same file so the cause can be identified.

---

## Step 7: Review and send the output

Output file:

```text
capoy-owner-probe.json
```

Before sending, open the file. The script is designed not to print passwords, cookie values, CSRF, keys, usernames, IPs, User-Agent, or raw logs. However, the output may still contain financial/operational indicators.

Attach only this JSON file.

Do not send:

- passwords
- invite codes
- session cookies
- exchange or server API tokens
- raw HAR
- `auth.db`, `journal.db`, or `live.db` files

If you do not want financial numbers sent, say so before sending and a more minimal/summary version can be produced.

---

## Step 8: Cleanup after review

1. Check in the output that logout succeeded:
   - `POST /api/auth/logout` with status `200`
   - check `session cleared after logout` with status `pass`
2. If you created a temporary account, disable it.
3. If extra sessions remain, close them from the account sessions section.
4. After analysis is complete, delete the local JSON file if desired.

---

## Summary

This method does not give me your browser session or password. It gives only a sanitized HTTP report that lets me analyze owner sections, settings, errors, orders, journal, intelligence, and market state from the site’s real responses.
