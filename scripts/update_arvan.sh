#!/usr/bin/env bash
# Update an existing Nobi install on an ArvanCloud cloud VM (Ubuntu 24.04).
#
# The installer (scripts/deploy_arvan.sh) leaves the update as three commands in
# the guide. This is those three commands, plus the four things a human does
# around them: see what is about to land, refuse to destroy local edits, prove
# the service actually came back, and say how to get out if it did not.
#
#   sudo bash scripts/update_arvan.sh              # show what is incoming, then ask
#   sudo bash scripts/update_arvan.sh --check      # look, and touch nothing
#   sudo bash scripts/update_arvan.sh --yes        # unattended, no prompt
#   sudo bash scripts/update_arvan.sh --reset      # fork/branch: fetch + reset --hard
#   sudo bash scripts/update_arvan.sh --force      # with --reset, also discard uncommitted edits
#   sudo bash scripts/update_arvan.sh --backup     # run the documented backup first
#   sudo bash scripts/update_arvan.sh --verify     # then run check_deploy.py
#   sudo bash scripts/update_arvan.sh --public https://nobi.example.ir   # same, with a URL
#
# It never touches /etc/nobi/nobi.env, the data directory, or the firewall. The
# service is restarted, so plan a minute of downtime.
#
# Safe to re-run: every step checks first, and an already-current install is a
# no-op that says so rather than restarting for nothing.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/nobi}"
DATA_DIR="${DATA_DIR:-/srv/nobi-data}"
ENV_FILE="${ENV_FILE:-/etc/nobi/nobi.env}"
SERVICE_USER="${SERVICE_USER:-nobi}"
SERVICE="${SERVICE:-nobi}"
BRANCH="${BRANCH:-main}"
# Where the installer puts the root-owned pieces. Overridable so a non-standard
# install — and the test suite, which has no /etc to write to — can point elsewhere.
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
SHARE_DIR="${SHARE_DIR:-/usr/local/share/nobi-installer}"
LIBEXEC_DIR="${LIBEXEC_DIR:-/usr/local/libexec}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/health}"
HEALTH_WAIT="${HEALTH_WAIT:-30}"
PUBLIC_URL="${PUBLIC_URL:-}"

DO_CHECK=0
DO_RESET=0
DO_BACKUP=0
DO_VERIFY=0
ASSUME_YES=0
ALLOW_DIRTY=0

c_ok=$'\033[32m'; c_bad=$'\033[31m'; c_warn=$'\033[33m'; c_off=$'\033[0m'
step() { printf '\n%s==> %s%s\n' "$c_ok" "$*" "$c_off"; }
warn() { printf '%s  !  %s%s\n' "$c_warn" "$*" "$c_off"; }
die()  { printf '%s  x  %s%s\n' "$c_bad" "$*" "$c_off" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
runas() { sudo -u "$SERVICE_USER" -- "$@"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --check)   DO_CHECK=1; shift ;;
    --reset)   DO_RESET=1; shift ;;
    --backup)  DO_BACKUP=1; shift ;;
    --verify)  DO_VERIFY=1; shift ;;
    --yes|-y)  ASSUME_YES=1; shift ;;
    --force)   ALLOW_DIRTY=1; shift ;;
    --public)  PUBLIC_URL="${2:-}"; DO_VERIFY=1; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) die "unknown argument: $1 (--help lists them)" ;;
  esac
done

# --------------------------------------------------------------------------- #
# 0. Preconditions — fail here, before anything is touched
# --------------------------------------------------------------------------- #
step "checking the machine"
[ "$(id -u)" -eq 0 ] || die "run me with sudo: sudo bash $0"
[ -d "$APP_DIR/.git" ] || die "no git checkout at $APP_DIR — run scripts/deploy_arvan.sh first"
[ -x "$APP_DIR/.venv/bin/python" ] || die "no venv at $APP_DIR/.venv — run scripts/deploy_arvan.sh first"
id "$SERVICE_USER" >/dev/null 2>&1 || die "service user $SERVICE_USER does not exist"
# The service must exist, and this is how it is asked. `systemctl show` takes no
# pipeline, so it cannot be broken by `set -o pipefail` turning an early
# `grep -q` exit into SIGPIPE (141) — which made a *present* unit look absent.
# It also returns non-zero when systemd does not know the unit, which is the
# existence test, and it is the same call the v2.0.3 upgrade tool uses.
if ! systemctl show "${SERVICE}.service" -p Id --value >/dev/null 2>&1 \
   && [ ! -f "$UNIT_DIR/${SERVICE}.service" ]; then
  # Capture first, then filter. A plain `grep` reads all of its input, so there
  # is no early exit and no SIGPIPE; `grep -q` is what caused the bug.
  UNIT_LIST="$(systemctl list-unit-files --no-legend 2>/dev/null || true)"
  near="$(printf '%s\n' "$UNIT_LIST" | awk '{print $1}' | grep -iE 'nobi' | sort -u || true)"
  printf '\n%s  x  systemd does not know %s.service.%s\n' "$c_bad" "$SERVICE" "$c_off" >&2
  if [ -n "$near" ]; then
    printf '     units that do look like nobi:\n' >&2
    printf '%s\n' "$near" | sed 's/^/       /' >&2
    printf '     re-run with: SERVICE=<name> %s\n' "$0" >&2
  else
    printf '     nothing matches "nobi" at all. Check that the install is on this\n' >&2
    printf '     host (APP_DIR=%s) and that the unit is installed:\n' "$APP_DIR" >&2
    printf '       ls -l %s/ | grep -i nobi\n' "$UNIT_DIR" >&2
    printf '       systemctl list-unit-files | grep -i nobi\n' >&2
  fi
  exit 1
fi
have git || die "git is not installed"
have curl || die "curl is not installed (the health check needs it)"

# The venv must be writable by the service user. A pip run as root by an older
# update leaves root-owned files, and then every later install fails with EACCES
# — which reads as "the update broke" when the cause is ownership.
if [ -n "$(find "$APP_DIR/.venv" ! -user "$SERVICE_USER" -print -quit 2>/dev/null)" ]; then
  warn "$APP_DIR/.venv has files not owned by $SERVICE_USER (an earlier pip ran as root)"
  if [ "$DO_CHECK" -eq 1 ]; then
    warn "would fix with: chown -R $SERVICE_USER:$SERVICE_USER $APP_DIR/.venv"
  else
    chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/.venv"
    printf '    ownership fixed\n'
  fi
fi

# --------------------------------------------------------------------------- #
# 1. Where we are, before anything moves
# --------------------------------------------------------------------------- #
step "recording the current state"
OLD_SHA="$(runas git -C "$APP_DIR" rev-parse HEAD)"
OLD_VER="$(runas git -C "$APP_DIR" describe --tags --always 2>/dev/null || echo "$OLD_SHA")"
RUNNING_VER="$(curl -fsS --max-time 3 "$HEALTH_URL" 2>/dev/null \
  | runas "$APP_DIR/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin).get("version","?"))' 2>/dev/null \
  || echo "(service not answering)")"
printf '    commit  %s\n' "$OLD_SHA"
printf '    tag     %s\n' "$OLD_VER"
printf '    /api/health reports version %s\n' "$RUNNING_VER"
if [ "$RUNNING_VER" != "(service not answering)" ] && [ "$RUNNING_VER" != "?" ]; then
  case "$RUNNING_VER" in
    "v$OLD_VER"|"$OLD_VER") printf '    (the running service matches the checkout)\n' ;;
    *) warn "the service runs $RUNNING_VER but the checkout is at $OLD_VER — a previous update may have half-applied" ;;
  esac
fi

# --------------------------------------------------------------------------- #
# 2. Fetch, and show exactly what is about to land
# --------------------------------------------------------------------------- #
step "fetching from origin"
# Fetching only writes refs under .git; the checkout is untouched. It is the one
# thing --check also does, because you cannot see what is incoming without it.
runas git -C "$APP_DIR" fetch --quiet --tags origin "$BRANCH" \
  || die "git fetch failed. If the repository is private, the service user needs
    credentials: sudo -u $SERVICE_USER git -C $APP_DIR config credential.helper store
    then run one fetch by hand. Nothing was changed."

REMOTE_SHA="$(runas git -C "$APP_DIR" rev-parse "origin/$BRANCH")"
# A checkout at origin/main is not the same as a *service* at that version. On a
# host that had been reset to a new commit without ever restarting, the process
# was still serving 2.0.3 while the code on disk said 2.0.10 — and the script
# answered "already up to date" and exited 0, which is the one answer that is
# both true and useless. So the early exit is conditional on the service
# agreeing with the checkout, not just on the ref.
STALE_SERVICE=0
if [ "$REMOTE_SHA" = "$OLD_SHA" ]; then
  CHECKOUT_VER="$(runas git -C "$APP_DIR" show "HEAD:VERSION" 2>/dev/null | tr -d '[:space:]' || true)"
  if [ -n "$CHECKOUT_VER" ] && [ "$RUNNING_VER" != "$CHECKOUT_VER" ] \
     && [ "$RUNNING_VER" != "(service not answering)" ] && [ "$RUNNING_VER" != "?" ]; then
    step "the checkout is current, but the service is not"
    printf '    %s is at origin/%s (%s)\n' "$APP_DIR" "$BRANCH" "${OLD_SHA:0:7}"
    warn "the running service is on $RUNNING_VER; the code on disk is $CHECKOUT_VER"
    warn "nothing to pull — what it needs is the restart at the end of this run"
    STALE_SERVICE=1
  else
    step "already up to date"
    printf '    %s is at origin/%s (%s)\n' "$APP_DIR" "$BRANCH" "${OLD_SHA:0:7}"
    if [ "$DO_VERIFY" -eq 1 ]; then
      printf '    --verify was asked for, so the checks still run\n'
    else
      exit 0
    fi
  fi
else
  printf '    incoming: %s -> %s\n' "${OLD_SHA:0:7}" "${REMOTE_SHA:0:7}"
  echo
  echo "    what lands:"
  runas git -C "$APP_DIR" --no-pager log --oneline --no-decorate \
    "$OLD_SHA..$REMOTE_SHA" | sed 's/^/      /'
  echo
  # The version the operator will end up on, read from the incoming tree rather
  # than guessed from the tag list.
  NEW_VER="$(runas git -C "$APP_DIR" show "origin/$BRANCH:VERSION" 2>/dev/null | tr -d '[:space:]' || true)"
  [ -n "$NEW_VER" ] && printf '    VERSION after the update: %s\n' "$NEW_VER"
fi

# --------------------------------------------------------------------------- #
# 3. A dirty checkout is the one thing that can lose work
# --------------------------------------------------------------------------- #
# `git status --porcelain` prints untracked files with `??`, and `git pull
# --ff-only` does not care about those unless the incoming commit would
# overwrite them. Measured on a scratch clone: a checkout holding `.cache/` and
# `untracked.txt` fast-forwards cleanly, while a *modified tracked* file aborts
# the merge with "Please commit your changes or stash them". A working install
# grows a pip or pytest cache within hours of booting, so refusing to update over
# one is a guard that blocks the thing it is meant to protect.
#
# So: tracked changes always block (they are the work that would be lost), and
# an untracked path blocks only when origin's tree already contains it.
STATUS="$(runas git -C "$APP_DIR" status --porcelain 2>/dev/null || true)"
BLOCKING="$(printf '%s\n' "$STATUS" | grep -v '^?? ' || true)"
COLLIDE=""
for path in $(runas git -C "$APP_DIR" ls-files --others --exclude-standard 2>/dev/null || true); do
  if runas git -C "$APP_DIR" cat-file -e "origin/$BRANCH:$path" 2>/dev/null; then
    COLLIDE="${COLLIDE}${path}
"
  fi
done
if [ -n "$BLOCKING" ] || [ -n "$COLLIDE" ]; then
  printf '\n    the checkout has local changes:\n'
  if [ -n "$BLOCKING" ]; then
    printf '%s\n' "$BLOCKING" | sed 's/^/      /'
  fi
  if [ -n "$COLLIDE" ]; then
    printf '%s\n' "$COLLIDE" | sed 's/^/      ?? (origin would overwrite) /'
  fi
  if [ "$DO_RESET" -eq 1 ]; then
    warn "--reset will DESTROY the changes listed above"
  else
    die "refusing to update a dirty checkout. 'git pull --ff-only' would fail here
    anyway. Commit the changes, or stash them (git -C $APP_DIR stash), or pass
    --reset to discard them deliberately (that is the fork/branch path)."
  fi
  [ "$ALLOW_DIRTY" -eq 1 ] || [ "$ASSUME_YES" -eq 1 ] || die "pass --force to discard them anyway"
fi
# Untracked leftovers are not work and not a merge conflict; say they are there
# so an operator is not surprised by them later, and move on.
LEFTOVER="$(printf '%s\n' "$STATUS" | grep '^?? ' || true)"
if [ -n "$LEFTOVER" ]; then
  printf '    untracked and harmless (not tracked, not in origin):\n'
  printf '%s\n' "$LEFTOVER" | sed 's/^/      /'
fi

# Committed local work is invisible to `status --porcelain`, and `--reset` throws
# it away just the same. Measured here: a diverged checkout with one local commit
# fast-forward-refuses (correct), then --reset drops that commit with no warning
# at all. So count it and say so, before the question is asked.
AHEAD="$(runas git -C "$APP_DIR" rev-list --count "origin/$BRANCH..HEAD" 2>/dev/null || echo 0)"
if [ "${AHEAD:-0}" -gt 0 ]; then
  warn "$AHEAD local commit(s) on $BRANCH that origin does not have:"
  runas git -C "$APP_DIR" --no-pager log --oneline --no-decorate \
    "origin/$BRANCH..HEAD" | sed 's/^/      /'
  if [ "$DO_RESET" -eq 1 ]; then
    warn "--reset will DISCARD the commit(s) listed above"
  else
    warn "they stay where they are: a fast-forward cannot include them"
  fi
fi

if [ "$DO_CHECK" -eq 1 ]; then
  step "--check: nothing was changed"
  printf '    to apply: sudo bash %s\n' "$(basename "$0")"
  exit 0
fi

# --------------------------------------------------------------------------- #
# 4. Ask, unless told not to
# --------------------------------------------------------------------------- #
if [ "$ASSUME_YES" -eq 0 ]; then
  step "about to update $APP_DIR and restart $SERVICE"
  if [ "$STALE_SERVICE" -eq 1 ]; then
    printf '    no new code to pull; this restarts the service onto the code already here\n'
  fi
  printf '    a minute of downtime; /etc/nobi/nobi.env is not touched\n'
  printf '    continue? [y/N] '
  read -r reply || reply=""
  case "$reply" in
    y|Y|yes|YES) ;;
    *) printf '    stopped. Nothing was changed.\n'; exit 0 ;;
  esac
fi

# --------------------------------------------------------------------------- #
# 5. Backup first, if asked — the guide's own procedure, not a new one
# --------------------------------------------------------------------------- #
if [ "$DO_BACKUP" -eq 1 ]; then
  step "backing up the data directory"
  STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  TARGET="${BACKUP_DIR:-/root}/nobi-data-$STAMP.tar.gz"
  tar -C "$DATA_DIR" -czf "$TARGET" . || die "backup failed — stopping before the update"
  chmod 600 "$TARGET"
  printf '    %s (%s)\n' "$TARGET" "$(du -h "$TARGET" | cut -f1)"
  warn "this is a file copy, not a tested restore. Keep it somewhere else too."
fi

# --------------------------------------------------------------------------- #
# 6. The update itself
# --------------------------------------------------------------------------- #
step "updating the checkout"
if [ "$DO_RESET" -eq 1 ]; then
  # The documented path for a fork or a branch deployment: `pull --ff-only`
  # cannot fast-forward a diverged history.
  runas git -C "$APP_DIR" reset --hard "origin/$BRANCH" >/dev/null
  runas git -C "$APP_DIR" clean -fd \
    -e .venv -e '*.db' -e '*.db-*' -e '*.sqlite*' -e '*.token' -e '*.jsonl' \
    >/dev/null 2>&1 || warn "git clean reported something; the data lives in $DATA_DIR, not here"
  printf '    reset to origin/%s\n' "$BRANCH"
else
  runas git -C "$APP_DIR" merge --ff-only "origin/$BRANCH" >/dev/null \
    || die "fast-forward refused — history has diverged. Re-run with --reset, or inspect
    with: sudo -u $SERVICE_USER git -C $APP_DIR log --oneline --graph --all | head -30"
  printf '    fast-forwarded to %s\n' "$(runas git -C "$APP_DIR" rev-parse --short HEAD)"
fi
NEW_SHA="$(runas git -C "$APP_DIR" rev-parse HEAD)"

step "installing python dependencies"
# As the service user, not as root: a root-owned file in the venv breaks the
# next update, and step 0 already repaired any that an older run left behind.
runas "$APP_DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
runas "$APP_DIR/.venv/bin/python" -m pip install --quiet \
  -r "$APP_DIR/server/requirements.txt" \
  || die "pip install failed — the checkout is already at $NEW_SHA.
    Restart is still pending: sudo systemctl restart $SERVICE"
runas "$APP_DIR/.venv/bin/python" -c 'import fastapi, uvicorn, pandas; print("    deps import ok")'

# --------------------------------------------------------------------------- #
# 7. Root-owned pieces the installer put outside the app tree
# --------------------------------------------------------------------------- #
# A hardening change to nobi.service or the installer helper is invisible to a
# plain `git pull`: the running unit is the copy in /etc, not the one in the
# checkout. Without this step such a fix never reaches a deployed host.
step "checking the installed systemd unit and helper files"
refresh_unit=0
if [ -f "$UNIT_DIR/$SERVICE.service" ]; then
  if ! cmp -s "$APP_DIR/deploy/$SERVICE.service" "$UNIT_DIR/$SERVICE.service"; then
    refresh_unit=1
    printf '    the installed unit differs from deploy/%s.service\n' "$SERVICE"
  else
    printf '    unit unchanged\n'
  fi
else
  refresh_unit=1
  warn "no $UNIT_DIR/$SERVICE.service — will install one from the checkout"
fi

if [ -d "$SHARE_DIR" ]; then
  for f in nobi.service nginx.conf; do
    [ -f "$APP_DIR/deploy/$f" ] || continue
    # No `-o root -g root`: the script already refuses to run as anything but
    # root, so install creates them root-owned anyway. Spelling it out only
    # breaks the test run, which has no root to chown to.
    cmp -s "$APP_DIR/deploy/$f" "$SHARE_DIR/$f" || {
      install -m 0444 "$APP_DIR/deploy/$f" "$SHARE_DIR/$f"
      printf '    refreshed installer template %s\n' "$f"
    }
  done
fi
if [ -f "$LIBEXEC_DIR/nobi-privileged-helper" ]; then
  if ! cmp -s "$APP_DIR/deploy/nobi-privileged-helper" "$LIBEXEC_DIR/nobi-privileged-helper"; then
    install -m 0755 "$APP_DIR/deploy/nobi-privileged-helper" \
      "$LIBEXEC_DIR/nobi-privileged-helper"
    printf '    refreshed the privileged helper\n'
  fi
fi

if [ "$refresh_unit" -eq 1 ]; then
  install -m 644 "$APP_DIR/deploy/$SERVICE.service" "$UNIT_DIR/$SERVICE.service"
  mkdir -p "$DATA_DIR"   # ReadWritePaths must exist or the unit fails to start
  printf '    installed the new unit\n'
fi

# --------------------------------------------------------------------------- #
# 8. Restart and prove it
# --------------------------------------------------------------------------- #
step "restarting $SERVICE"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1 || true
systemctl restart "$SERVICE"

up=0
for i in $(seq 1 "$HEALTH_WAIT"); do
  if curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
    printf '    answered on %s after %ss\n' "$HEALTH_URL" "$i"
    up=1; break
  fi
  sleep 1
done

if [ "$up" -ne 1 ]; then
  step "the service did not come back"
  journalctl -u "$SERVICE" -n 40 --no-pager || true
  cat <<TXT

  The update itself succeeded — the checkout is at $NEW_SHA and the
  dependencies installed. What failed is the start.

  To go back to what was running:
    sudo -u $SERVICE_USER git -C $APP_DIR reset --hard $OLD_SHA
    sudo systemctl restart $SERVICE

  Before you do, read the log above. The most common cause is the
  NBX_TRUSTED_PROXIES / NBX_API_TOKEN guard in $ENV_FILE: the new version
  saw an incomplete environment, not a broken one.
TXT
  exit 1
fi

# --------------------------------------------------------------------------- #
# 9. What only a human can finish
# --------------------------------------------------------------------------- #
step "update complete"
printf '    %s -> %s\n' "${OLD_SHA:0:7}" "${NEW_SHA:0:7}"
[ -n "${NEW_VER:-}" ] && printf '    VERSION %s\n' "$NEW_VER"

if [ "$DO_VERIFY" -eq 1 ]; then
  step "verifying the deployment"
  # The checker reads the configuration from files, and the values that matter
  # live in the unit's EnvironmentFile — which is root-owned. `sudo` resets the
  # environment, so exporting here would not survive the hop; instead read the
  # file as root and hand the NBX_* pairs to `env`, which sets them for the
  # checker. `_cfg` puts an exported variable above the files, exactly as the app
  # does, so this is the same precedence the service resolves.
  verify_env=()
  if [ -r "$ENV_FILE" ]; then
    while IFS= read -r pair || [ -n "$pair" ]; do
      case "$pair" in ''|\#*) continue ;; NBX_*=*) verify_env+=("$pair") ;; esac
    done < "$ENV_FILE"
  fi
  if [ ${#verify_env[@]} -gt 0 ]; then
    printf '    with %d NBX_* setting(s) from %s\n' "${#verify_env[@]}" "$ENV_FILE"
  else
    warn "no NBX_* settings readable in $ENV_FILE — the checker will see the defaults"
  fi
  if [ -n "$PUBLIC_URL" ]; then
    runas env "${verify_env[@]}" "$APP_DIR/.venv/bin/python" \
      "$APP_DIR/scripts/check_deploy.py" --public "$PUBLIC_URL"
  else
    runas env "${verify_env[@]}" "$APP_DIR/.venv/bin/python" \
      "$APP_DIR/scripts/check_deploy.py"
  fi
else
  cat <<TXT

  Now re-run the deployment verification (guide §۸):
    sudo -u $SERVICE_USER $APP_DIR/.venv/bin/python $APP_DIR/scripts/check_deploy.py \\
        --public https://YOUR.DOMAIN

  Exit code 0 with "accounts: 1 account(s), 1 owner(s)" and
  "readonly: anonymous POST -> 401" is the result you want.

  Rollback, if something is wrong later:
    sudo -u $SERVICE_USER git -C $APP_DIR reset --hard $OLD_SHA
    sudo systemctl restart $SERVICE
TXT
fi
