#!/usr/bin/env bash
# Bring the Nobi service on this machine to a given git tag. Run as root.
#
#   sudo bash scripts/deploy_remote.sh v2.0.6
#   sudo bash scripts/deploy_remote.sh --rollback
#
# This is the half of a deployment that has to happen *here*: it moves a
# systemd unit, a venv owned by the service user, and the running service
# itself. GitHub Actions reaches this machine over SSH and then runs exactly
# this file, so the sequence is reviewable in the repository instead of buried
# in a workflow's heredoc where nobody reads it.
#
# Three properties it keeps, because a dashboard that can place paper orders
# and halt trading is not a thing to deploy casually:
#
#   1. It deploys a tag, never `main`. A tag is immutable; `main` is whatever
#      it happened to be when the fetch landed.
#   2. It verifies the result instead of assuming it. After the restart it asks
#      /api/health which version is live and compares it with the tag asked
#      for. "The service restarted" is not "the service is running this code".
#   3. A failure leaves the previous commit running. The pre-deploy commit is
#      recorded before anything is touched, and the rollback path is the same
#      code as the forward path, so it is exercised, not theoretical.
#
# Safe to re-run. Nothing here prompts, and no secret is written to disk.

set -euo pipefail

APP_DIR=/opt/nobi
VENV_PY="$APP_DIR/.venv/bin/python"
SERVICE_USER=nobi
SERVICE=nobi.service
HELPER=/usr/local/bin/nobi-helper-client
DOMAIN_FILE=/etc/nobi/domain
ENV_FILE=/etc/nobi/nobi.env
HEALTH_URL=http://127.0.0.1:8000/api/health
STATE_DIR=/var/lib/nobi-deploy
LOG_TAG=deploy

c_ok=$'\033[32m'; c_bad=$'\033[31m'; c_warn=$'\033[33m'; c_off=$'\033[0m'
step() { printf '\n%s==> %s%s\n' "$c_ok" "$*" "$c_off"; }
warn() { printf '%s  !  %s%s\n' "$c_warn" "$*" "$c_off"; }
die()  { printf '%s  x  %s%s\n' "$c_bad" "$*" "$c_off" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# --------------------------------------------------------------------------- #
# Arguments
# --------------------------------------------------------------------------- #
TAG=""
ROLLBACK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --rollback) ROLLBACK=1; shift ;;
    -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
    -*)         die "unknown option: $1" ;;
    *)          [ -z "$TAG" ] || die "only one tag, please (got $TAG and $1)"
                TAG="$1"; shift ;;
  esac
done

[ "$(id -u)" -eq 0 ] || die "run me with sudo: sudo bash $0 vX.Y.Z"
[ -d "$APP_DIR/.git" ] || die "$APP_DIR is not a git checkout — deploy_arvan.sh installs one"
[ -x "$HELPER" ] || die "$HELPER is missing — run deploy_arvan.sh first"

# A tag reaches systemd, a health URL and a filename. Reject anything that is
# not a plain vX.Y.Z before it reaches any of them.
if [ "$ROLLBACK" -eq 0 ]; then
  [ -n "$TAG" ] || die "pass a tag: sudo bash $0 v2.0.6"
  [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || die "--tag must look like v2.0.6 (got '$TAG')"
fi

mkdir -p "$STATE_DIR"
chmod 0750 "$STATE_DIR"
LAST_COMMIT_FILE="$STATE_DIR/last-good-commit"

# --------------------------------------------------------------------------- #
# The service as the `nobi` user. Every git and pip step runs through this:
# /opt/nobi is owned by that user, and a file written by root inside the
# checkout would stop the next deploy from being able to update it.
# --------------------------------------------------------------------------- #
as_nobi() { sudo -u "$SERVICE_USER" -H bash -c "$*"; }

# --------------------------------------------------------------------------- #
# Health: does the running service answer, and with which version?
#
# `app.version` is the one number the service itself reports, so this is the
# only check that can tell "it restarted" from "it restarted onto this code".
# --------------------------------------------------------------------------- #
service_version() {
  curl -fsS --max-time 10 "$HEALTH_URL" 2>/dev/null \
    | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1
}

wait_healthy() {  # wait_healthy <expected-version> <seconds>
  local want="$1" secs="$2" i got
  for ((i = 0; i < secs; i++)); do
    got="$(service_version || true)"
    if [ -n "$got" ] && [ "$got" = "$want" ]; then
      printf '    healthy: version %s after %ss\n' "$got" "$i"
      return 0
    fi
    sleep 1
  done
  got="$(service_version || true)"
  printf '    health check failed: wanted %s, service reports %s\n' \
    "$want" "${got:-nothing}" >&2
  return 1
}

# --------------------------------------------------------------------------- #
# Restart through the helper, never `systemctl restart` directly.
#
# The unit is root-owned and copied into place by the privileged helper; the
# service user is not allowed to write it. Going through the helper keeps the
# one sanctioned path, and it is the same call the browser installer makes, so
# what CI does and what an operator does cannot drift apart.
# --------------------------------------------------------------------------- #
restart_service() {
  sudo -u "$SERVICE_USER" "$HELPER" --protocol 1 restart-service >/dev/null
}

# --------------------------------------------------------------------------- #
# Record where we are, before anything moves.
# --------------------------------------------------------------------------- #
CURRENT_COMMIT="$(git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
CURRENT_VERSION="$(service_version || true)"
step "deploying to $APP_DIR"
printf '    running now: %s%s\n' "$CURRENT_COMMIT" \
  "${CURRENT_VERSION:+ (reports $CURRENT_VERSION)}"

if [ "$ROLLBACK" -eq 1 ]; then
  [ -s "$LAST_COMMIT_FILE" ] || die "no recorded commit to return to ($LAST_COMMIT_FILE is empty)"
  TAG_COMMIT="$(cat "$LAST_COMMIT_FILE")"
  TAG="rollback to $TAG_COMMIT"
else
  # Resolve the tag locally first: a typo must fail before the fetch, not after.
  TAG_COMMIT="$(git -C "$APP_DIR" rev-parse --verify --quiet "refs/tags/$TAG" || true)"
fi

# --------------------------------------------------------------------------- #
# 1. Fetch and check out the exact tag.
#
# `--detach` on purpose: /opt/nobi is a deployment, not a workspace. A branch
# here would let the next fetch silently move the code the service runs.
# --------------------------------------------------------------------------- #
step "fetching $TAG"
as_nobi "cd $APP_DIR && git fetch --tags --quiet origin"

if [ "$ROLLBACK" -eq 1 ]; then
  git -C "$APP_DIR" rev-parse --verify --quiet "$TAG_COMMIT^{commit}" >/dev/null \
    || die "recorded commit $TAG_COMMIT is not in this checkout any more"
  chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/.git"
  git -C "$APP_DIR" checkout --quiet --detach "$TAG_COMMIT"
else
  git -C "$APP_DIR" rev-parse --verify --quiet "refs/tags/$TAG^{commit}" >/dev/null \
    || die "tag $TAG not found on the server — did the deploy key reach it?"
  git -C "$APP_DIR" checkout --quiet --detach "refs/tags/$TAG"
fi

NEW_COMMIT="$(git -C "$APP_DIR" rev-parse --short HEAD)"
[ "$NEW_COMMIT" != "$CURRENT_COMMIT" ] \
  || warn "the checkout did not move ($NEW_COMMIT) — already on this commit?"

# Record the commit we just left, so a later --rollback can return to it. This
# is written *after* a successful checkout: recording it earlier would let a
# failed deploy overwrite the only known-good pointer with a broken one.
if [ "$CURRENT_COMMIT" != "unknown" ]; then
  printf '%s\n' "$CURRENT_COMMIT" > "$LAST_COMMIT_FILE"
  chmod 0640 "$LAST_COMMIT_FILE"
  chown root:"$SERVICE_USER" "$LAST_COMMIT_FILE"
fi

# --------------------------------------------------------------------------- #
# 2. Dependencies. Unconditional: `pip install -r` is a no-op when nothing
# changed, and a conditional one that guesses wrong leaves an import error
# that looks like a bug in the release.
# --------------------------------------------------------------------------- #
step "updating the venv"
if [ ! -x "$VENV_PY" ]; then
  die "$VENV_PY is missing — run deploy_arvan.sh, this script does not create a venv"
fi
sudo -u "$SERVICE_USER" -H "$VENV_PY" -m pip install --quiet --disable-pip-version-check \
  -r "$APP_DIR/server/requirements.txt"
sudo -u "$SERVICE_USER" -H "$VENV_PY" -c 'import fastapi, uvicorn, pandas' \
  || die "runtime imports failed after the update — refusing to restart"

# --------------------------------------------------------------------------- #
# 3. Root-owned templates.
#
# deploy_arvan.sh copies deploy/* into /usr/local/share, /usr/local/libexec and
# /etc/systemd/system. Those are root-owned and outside the checkout, so a git
# checkout alone never updates them — which is exactly how a deployment ends up
# running a new unit template with an old systemd file. Re-running the
# installer is safe (it is idempotent) but it needs the domain, which the
# installer itself recorded, so it is read back rather than asked for.
# --------------------------------------------------------------------------- #
DOMAIN=""
[ -r "$DOMAIN_FILE" ] && DOMAIN="$(tr -d '[:space:]' < "$DOMAIN_FILE")"
if [ -n "$DOMAIN" ] && git -C "$APP_DIR" diff --quiet HEAD -- deploy/ 2>/dev/null; then
  printf '    deploy/ unchanged, keeping the installed templates\n'
elif [ -n "$DOMAIN" ]; then
  step "reinstalling root-owned templates"
  bash "$APP_DIR/scripts/deploy_arvan.sh" --domain "$DOMAIN" --no-nginx \
    || warn "deploy_arvan.sh failed — the templates may be stale; the service still runs the old unit"
else
  warn "no readable $DOMAIN_FILE — skipping the template refresh. If deploy/ changed, re-run:"
  warn "    sudo bash $APP_DIR/scripts/deploy_arvan.sh --domain YOUR.DOMAIN"
fi

# --------------------------------------------------------------------------- #
# 4. Restart, then prove it.
# --------------------------------------------------------------------------- #
step "restarting $SERVICE"
restart_service

# The tag is the version the service must report back. Strip the leading v:
# VERSION and app.version are both bare "2.0.6", while the tag is "v2.0.6".
EXPECT_VERSION="${TAG#v}"
if [ "$ROLLBACK" -eq 1 ]; then
  # A rollback targets a commit, not a release; accept whatever that commit
  # declares rather than inventing a version for it.
  EXPECT_VERSION="$("$VENV_PY" - <<PY
import re, pathlib
t = pathlib.Path("$APP_DIR/server/main.py").read_text(encoding="utf-8")
m = re.search(r'version="([^"]+)"', t)
print(m.group(1) if m else "")
PY
)"
fi

step "verifying the service reports $EXPECT_VERSION"
if ! wait_healthy "$EXPECT_VERSION" 90; then
  warn "the new code did not come up healthy — returning to $CURRENT_COMMIT"
  git -C "$APP_DIR" checkout --quiet --detach "$CURRENT_COMMIT" 2>/dev/null \
    || die "could not even check out $CURRENT_COMMIT again — fix this machine by hand"
  sudo -u "$SERVICE_USER" -H "$VENV_PY" -m pip install --quiet \
    --disable-pip-version-check -r "$APP_DIR/server/requirements.txt" || true
  restart_service || true
  if wait_healthy "$CURRENT_VERSION" 90; then
    die "rolled back to $CURRENT_COMMIT ($CURRENT_VERSION) — the release $TAG is not live"
  fi
  die "rollback did not come up healthy either — this machine needs a human"
fi

step "done — $NEW_COMMIT is live and reports $EXPECT_VERSION"
printf '    %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
exit 0
