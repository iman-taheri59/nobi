#!/usr/bin/env bash
# Install the Nobi dashboard on an ArvanCloud ابرک (Ubuntu 24.04).
#
# This script is deliberately boring: it installs packages, creates a user, a
# venv, a data directory, a systemd unit and an nginx site — then *stops* and
# tells you what it could not verify. It never writes a password anywhere but
# the 0600 file you point it at, and it never opens a firewall.
#
#   sudo bash scripts/deploy_arvan.sh --domain nobi.example.ir
#   sudo bash scripts/deploy_arvan.sh --domain nobi.example.ir --no-nginx
#   sudo bash scripts/deploy_arvan.sh --domain nobi.example.ir --tls email@x.ir
#
# Safe to re-run: every step checks first. The .env is never overwritten —
# an existing token is kept, because rotating it silently would lock the
# browser out of a working deployment.
set -euo pipefail

DOMAIN=""
EMAIL=""
WITH_NGINX=1
WITH_TLS=0
PYTHON_BIN="${PYTHON_BIN:-}"
APP_DIR=/opt/nobi
DATA_DIR=/srv/nobi-data
ENV_DIR=/etc/nobi
ENV_FILE="$ENV_DIR/nobi.env"
SERVICE_USER=nobi

c_ok=$'\033[32m'; c_bad=$'\033[31m'; c_warn=$'\033[33m'; c_off=$'\033[0m'
step() { printf '\n%s==> %s%s\n' "$c_ok" "$*" "$c_off"; }
warn() { printf '%s  !  %s%s\n' "$c_warn" "$*" "$c_off"; }
die()  { printf '%s  x  %s%s\n' "$c_bad" "$*" "$c_off" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --domain)   DOMAIN="${2:-}"; shift 2 ;;
    --tls)      WITH_TLS=1; EMAIL="${2:-}"; shift 2 ;;
    --no-nginx) WITH_NGINX=0; shift ;;
    -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ "$(id -u)" -eq 0 ] || die "run me with sudo: sudo bash $0 --domain YOUR.DOMAIN"
[ -n "$DOMAIN" ] || die "pass --domain YOUR.DOMAIN (it must already point at this server's IP)"
# Used in sed and written to a root-owned file; reject anything that is not a
# plain DNS name before it reaches either.
[[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]] \
  || die "--domain must be a lowercase DNS name (letters, digits, dots, hyphens)"

# --------------------------------------------------------------------------- #
# 0. Preconditions
# --------------------------------------------------------------------------- #
step "checking the machine"
. /etc/os-release
case "${ID:-}${VERSION_ID:-}" in
  ubuntu24.04|ubuntu24.*|debian12*|debian13*) ;;
  *) warn "this is ${PRETTY_NAME:-unknown}; the guide is written for Ubuntu 24.04. Continuing — the only hard requirement is python >= 3.11." ;;
esac
[ -f /etc/arvancloud ] || warn "not obviously an ArvanCloud instance (no /etc/arvancloud marker) — fine if you are elsewhere"

if [ -z "$PYTHON_BIN" ]; then
  for cand in python3.12 python3.13 python3.11 python3; do
    have "$cand" || continue
    if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)'; then
      PYTHON_BIN="$(command -v "$cand")"; break
    fi
  done
fi
[ -n "$PYTHON_BIN" ] || die "no python >= 3.11 (the code imports datetime.UTC). On 22.04: add-apt-repository ppa:deadsnakes/ppa && apt install python3.12-venv, then PYTHON_BIN=/usr/bin/python3.12"
printf '    python: %s\n' "$("$PYTHON_BIN" -V 2>&1)"

# --------------------------------------------------------------------------- #
# 1. Packages
# --------------------------------------------------------------------------- #
step "installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-dev git nginx curl ca-certificates \
  build-essential libsqlite3-dev sqlite3 >/dev/null
printf '    git nginx python3-venv ok\n'

# --------------------------------------------------------------------------- #
# 2. Service account and code
# --------------------------------------------------------------------------- #
step "creating the service account and checkout"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  adduser --system --group --home "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
  printf '    user %s created\n' "$SERVICE_USER"
else
  printf '    user %s exists\n' "$SERVICE_USER"
fi

if [ -d "$APP_DIR/.git" ]; then
  printf '    checkout exists — leaving it alone (update with: sudo -u %s git -C %s pull)\n' \
    "$SERVICE_USER" "$APP_DIR"
else
  # If this script is running from a checkout, copy that; otherwise clone.
  SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  if [ -d "$SRC/.git" ] && [ "$SRC" != "$APP_DIR" ]; then
    printf '    copying %s -> %s\n' "$SRC" "$APP_DIR"
    mkdir -p "$APP_DIR"
    tar -C "$SRC" --exclude=.git --exclude=.venv --exclude=node_modules \
        --exclude=.mypy_cache --exclude=.pytest_cache --exclude=.ruff_cache \
        -cf - . | tar -C "$APP_DIR" -xf -
  else
    die "run this script from inside the repository, or clone it to $APP_DIR first:
    sudo git clone https://github.com/USER/REPO $APP_DIR && sudo bash $APP_DIR/scripts/deploy_arvan.sh --domain $DOMAIN"
  fi
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

step "installing python dependencies"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
"$APP_DIR/.venv/bin/python" -m pip install --quiet -r "$APP_DIR/server/requirements.txt"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -c 'import fastapi, uvicorn, pandas; print("    deps ok")'

# --------------------------------------------------------------------------- #
# 3. Data directory (survives a rebuild only if it is a mounted volume)
# --------------------------------------------------------------------------- #
step "preparing $DATA_DIR"
mkdir -p "$DATA_DIR/exports" "$DATA_DIR/coin-cache" "$DATA_DIR/candle-cache" \
         "$DATA_DIR/backtest-reports"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"
if ! findmnt -no TARGET "$DATA_DIR" >/dev/null 2>&1; then
  warn "$DATA_DIR is on the root disk. Rebuilding the ابرک from an image loses it."
  warn "Attach a volume (panel → دیسک → اتصال به ابرک), mount it here, and re-run:"
  warn "  mkfs.ext4 /dev/vdb && mount /dev/vdb $DATA_DIR   (add to /etc/fstab)"
fi

# --------------------------------------------------------------------------- #
# 4. Secrets
# --------------------------------------------------------------------------- #
step "writing $ENV_FILE"
install -d -m 750 "$ENV_DIR"
if [ -f "$ENV_FILE" ]; then
  printf '    exists — keeping it (token unchanged)\n'
else
  TOKEN="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(32))')"
  umask 077
  cat > "$ENV_FILE" <<EOF
# managed by scripts/deploy_arvan.sh — $(date -u +%FT%TZ)
NBX_API_TOKEN=$TOKEN
NBX_TRUSTED_PROXIES=127.0.0.1
NBX_JOURNAL_DB=$DATA_DIR/journal.db
NBX_PAPER_STATE=$DATA_DIR/paper_state.json
NBX_AUTH_DB=$DATA_DIR/auth.db
NBX_COIN_CACHE=$DATA_DIR/coin-cache
NBX_CANDLE_CACHE=$DATA_DIR/candle-cache
NBX_BT_DIR=$DATA_DIR/backtest-reports
NBX_EXPORT_DIR=$DATA_DIR/exports
NBX_LIVE_DB=$DATA_DIR/live_execution.db
NBX_MANAGED_ENV=$DATA_DIR/managed.env
NBX_OPERATIONS_AUDIT=$DATA_DIR/operations-audit.jsonl
NBX_SETUP_TOKEN_FILE=$DATA_DIR/setup.token
NBX_INSTALL_HELPER=/usr/local/bin/nobi-helper-client
NBX_BASE_URL=https://apiv2.nobitex.ir
NBX_EQUITY_USD=100000
NBX_RATE_LIMIT=60
NBX_CORS_ORIGINS=
EOF
  unset TOKEN
  printf '    created (32 random bytes)\n'
fi
# Upgrade existing env files without touching any existing key or secret.
if ! grep -q '^NBX_COIN_CACHE=' "$ENV_FILE"; then
  printf '\nNBX_COIN_CACHE=%s/coin-cache\n' "$DATA_DIR" >> "$ENV_FILE"
fi
# The candle cache used to live beside its own source, which `ProtectSystem=strict`
# makes read-only — and `mkdir()` ran at import time, so the service refused to
# start. It belongs on the volume with the rest of the data.
if ! grep -q '^NBX_CANDLE_CACHE=' "$ENV_FILE"; then
  printf 'NBX_CANDLE_CACHE=%s/candle-cache\n' "$DATA_DIR" >> "$ENV_FILE"
fi
if ! grep -q '^NBX_BT_DIR=' "$ENV_FILE"; then
  printf 'NBX_BT_DIR=%s/backtest-reports\n' "$DATA_DIR" >> "$ENV_FILE"
fi
# Wizard-managed settings, the operations audit and the one-time setup token
# default to server/ in code, which ProtectSystem=strict makes read-only.
# Point them at the data volume for older installs too.
for pair in \
  "NBX_LIVE_DB=$DATA_DIR/live_execution.db" \
  "NBX_MANAGED_ENV=$DATA_DIR/managed.env" \
  "NBX_OPERATIONS_AUDIT=$DATA_DIR/operations-audit.jsonl" \
  "NBX_SETUP_TOKEN_FILE=$DATA_DIR/setup.token" \
  "NBX_INSTALL_HELPER=/usr/local/bin/nobi-helper-client"; do
  key="${pair%%=*}"
  grep -q "^${key}=" "$ENV_FILE" || printf '%s\n' "$pair" >> "$ENV_FILE"
done
chown root:"$SERVICE_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"
# 640 root:nobi — the service reads it, nobody else can.
printf '    mode %s owner %s\n' "$(stat -c %a "$ENV_FILE")" "$(stat -c %U:%G "$ENV_FILE")"

# --------------------------------------------------------------------------- #
# 4b. Privileged installer helper (socket-activated, no sudo)
# --------------------------------------------------------------------------- #
step "installing the root-owned installer helper"
# Templates MUST NOT be read from the checkout: it is chowned to the service
# user below. A service-writable systemd unit copied to /etc as root is a root
# escalation, so install them root:root 0444 outside the app tree.
install -d -o root -g root -m 0755 /usr/local/share/nobi-installer
install -o root -g root -m 0444 "$APP_DIR/deploy/nobi.service" /usr/local/share/nobi-installer/nobi.service
install -o root -g root -m 0444 "$APP_DIR/deploy/nginx.conf" /usr/local/share/nobi-installer/nginx.conf
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 "$APP_DIR/deploy/nobi-privileged-helper" /usr/local/libexec/nobi-privileged-helper
install -o root -g root -m 0755 "$APP_DIR/deploy/nobi-installer-helper.py" /usr/local/libexec/nobi-installer-helper.py
install -o root -g root -m 0755 "$APP_DIR/deploy/nobi-helper-client" /usr/local/bin/nobi-helper-client
install -o root -g root -m 0644 "$APP_DIR/deploy/nobi-installer.socket" /etc/systemd/system/nobi-installer.socket
install -o root -g root -m 0644 "$APP_DIR/deploy/nobi-installer.service" /etc/systemd/system/nobi-installer.service
printf '%s\n' "$DOMAIN" > /etc/nobi/domain
chown root:root /etc/nobi/domain
chmod 0444 /etc/nobi/domain
rm -f /etc/sudoers.d/nobi-helper   # legacy sudo path, no longer used
systemctl daemon-reload
systemctl enable --now nobi-installer.socket >/dev/null
"$PYTHON_BIN" /usr/local/libexec/nobi-privileged-helper --protocol 1 check || \
  warn "installer helper check failed — fix the templates/domain file before using the browser installer"

# --------------------------------------------------------------------------- #
# 5. systemd
# --------------------------------------------------------------------------- #
step "installing the systemd unit"
install -m 644 "$APP_DIR/deploy/nobi.service" /etc/systemd/system/nobi.service
mkdir -p "$DATA_DIR"   # ReadWritePaths target must exist or the unit fails
systemctl daemon-reload
systemctl enable nobi >/dev/null
systemctl restart nobi

for i in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    printf '    nobi is up after %ss\n' "$i"; break
  fi
  [ "$i" -eq 30 ] && { journalctl -u nobi -n 30 --no-pager; die "nobi did not answer on 127.0.0.1:8000 — see the log above"; }
  sleep 1
done

# --------------------------------------------------------------------------- #
# 6. nginx
# --------------------------------------------------------------------------- #
if [ "$WITH_NGINX" -eq 1 ]; then
  step "configuring nginx for $DOMAIN"
  sed "s/nobi\.example\.ir/$DOMAIN/g" "$APP_DIR/deploy/nginx.conf" > "/etc/nginx/sites-available/nobi"
  ln -sf /etc/nginx/sites-available/nobi /etc/nginx/sites-enabled/nobi
  rm -f /etc/nginx/sites-enabled/default
  if ! nginx -t 2>/tmp/nginx-test.err; then
    cat /tmp/nginx-test.err >&2
    die "nginx rejected the config — most often the certificate paths (that is expected before --tls)"
  fi
  systemctl reload nginx
  printf '    nginx reloaded\n'

  if [ "$WITH_TLS" -eq 1 ]; then
    step "obtaining a certificate for $DOMAIN"
    apt-get install -y -qq certbot python3-certbot-nginx >/dev/null
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
      --email "${EMAIL:-admin@$DOMAIN}" --redirect || \
      warn "certbot failed — the site still answers on :80. Check that $DOMAIN resolves to this server and that port 80 is open in the ArvanCloud firewall"
    systemctl reload nginx
  else
    warn "no TLS yet: run 'sudo certbot --nginx -d $DOMAIN' before you log in — the token is a bearer credential"
  fi
fi

# --------------------------------------------------------------------------- #
# 7. What only a human can finish
# --------------------------------------------------------------------------- #
step "firewall (ArvanCloud panel → سرور ابری → فایروال)"
cat <<'TXT'
    Rule  Direction  Protocol  Port        Source
    1     ingress    TCP       22          your IP (not 0.0.0.0/0)
    2     ingress    TCP       80          all
    3     ingress    TCP       443         all
    Everything else stays blocked. The panel firewall must be attached to the
    ابرک, otherwise it does not apply. Note that 8000 is *not* in this list:
    the app listens on 127.0.0.1 only.
TXT

step "next steps"
cat <<TXT
    1. Create the first account. The web form cannot do it: on a fresh install
       whoever found the URL first would become the owner. The password is
       typed at the prompt, never echoed and never in your shell history:
         sudo -u $SERVICE_USER NBX_AUTH_DB=$DATA_DIR/auth.db \
             $APP_DIR/.venv/bin/python $APP_DIR/scripts/manage_users.py \
             create YOURNAME --role owner
       From then on the API is closed to anonymous visitors and the dashboard
       asks for a login. Invite everyone else:
         sudo -u $SERVICE_USER NBX_AUTH_DB=$DATA_DIR/auth.db \
             $APP_DIR/.venv/bin/python $APP_DIR/scripts/manage_users.py invite viewer
    2. Sign in at https://$DOMAIN with that account. Nobody needs to paste a
       token into a browser console any more. NBX_API_TOKEN still works, but
       only for scripts:
         curl -H "X-API-Token: \$(sudo grep NBX_API_TOKEN $ENV_FILE | cut -d= -f2)" \
             https://$DOMAIN/api/paper/positions
    3. Prove the door is closed:
         sudo -u $SERVICE_USER $APP_DIR/.venv/bin/python $APP_DIR/scripts/check_deploy.py \
             --public https://$DOMAIN
       Exit code 0, "accounts: 1 account(s), 1 owner(s)" and
       "readonly: anonymous POST -> 401" is the result you want.
    4. Copy the data somewhere else (docs/deploy-arvan.md §بکاپ). That now
       includes auth.db: lose it and nobody can sign in.
TXT
