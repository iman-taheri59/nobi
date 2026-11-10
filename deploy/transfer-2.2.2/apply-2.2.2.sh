#!/usr/bin/env bash
# Fast operator-executed delta upgrade: nobi 2.2.1 -> 2.2.2.
# UI/diagnostic only. Does not enable Live/Auto, does not send orders,
# does not replace env/nginx/unit files, and does not migrate data.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-/opt/nobi}"
SERVICE="${SERVICE:-nobi}"
SERVICE_USER="${SERVICE_USER:-nobi}"
ENV_FILE="${ENV_FILE:-/etc/nobi/nobi.env}"
NGINX_SITE="${NGINX_SITE:-/etc/nginx/sites-available/nobi}"
UNIT_FILE="${UNIT_FILE:-/etc/systemd/system/nobi.service}"
DATA_DIR="${DATA_DIR:-/srv/nobi-data}"
DATA_BACKUP="${DATA_BACKUP:-0}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/health}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="${BACKUP:-/root/nobi-backup-$STAMP}"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 1
fi
[[ -d "$APP_DIR" ]] || { echo "Missing APP_DIR=$APP_DIR" >&2; exit 1; }
[[ -f "$SRC_DIR/nobi-2.2.2-delta.tar.gz.b64" ]] || { echo "Missing delta payload next to script" >&2; exit 1; }
[[ "$(cat "$APP_DIR/VERSION" 2>/dev/null || true)" == "2.2.1" ]] || {
  echo "Refusing: $APP_DIR/VERSION is not 2.2.1. This delta is only for 2.2.1 -> 2.2.2." >&2
  exit 1
}

echo "== precheck live runtime files =="
(cd "$APP_DIR" && sha256sum -c "$SRC_DIR/RUNTIME_PRECHECK.sha256")

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "== decode and verify delta =="
base64 -d "$SRC_DIR/nobi-2.2.2-delta.tar.gz.b64" > "$TMP/nobi-2.2.2-delta.tar.gz"
(cd "$TMP" && sha256sum -c "$SRC_DIR/nobi-2.2.2-delta.tar.gz.sha256")
mkdir -p "$TMP/src"
tar -xzf "$TMP/nobi-2.2.2-delta.tar.gz" -C "$TMP/src"
(cd "$TMP/src" && sha256sum -c "$SRC_DIR/DELTA_MANIFEST.sha256")
[[ "$(cat "$TMP/src/VERSION")" == "2.2.2" ]] || { echo "Delta VERSION is not 2.2.2" >&2; exit 1; }

echo "== backup changed files and config =="
mkdir -p "$BACKUP/files"
while IFS= read -r f; do
  [[ -n "$f" ]] || continue
  if [[ -e "$APP_DIR/$f" ]]; then
    mkdir -p "$(dirname "$BACKUP/files/$f")"
    cp -a "$APP_DIR/$f" "$BACKUP/files/$f"
  fi
done < "$SRC_DIR/DELTA_FILES.txt"
[[ ! -f "$ENV_FILE" ]] || install -m 0600 "$ENV_FILE" "$BACKUP/nobi.env"
[[ ! -f "$NGINX_SITE" ]] || cp -a "$NGINX_SITE" "$BACKUP/nginx-site"
[[ ! -f "$UNIT_FILE" ]] || cp -a "$UNIT_FILE" "$BACKUP/nobi.service"
cat "$APP_DIR/VERSION" > "$BACKUP/old-version.txt"
cp "$SRC_DIR/nobi-2.2.2-delta.tar.gz.sha256" "$BACKUP/delta.sha256"
find "$BACKUP" -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > "$BACKUP/SHA256SUMS"

echo "== stop $SERVICE =="
systemctl stop "$SERVICE"

if [[ "$DATA_BACKUP" == "1" && -d "$DATA_DIR" ]]; then
  echo "== optional data backup =="
  tar -C "$(dirname "$DATA_DIR")" -czf "$BACKUP/nobi-data.tar.gz" "$(basename "$DATA_DIR")"
fi

echo "== overlay 2.2.2 delta =="
tar -C "$TMP/src" -cf - . | tar -C "$APP_DIR" -xf -
while IFS= read -r f; do
  [[ -n "$f" ]] || continue
  [[ ! -e "$APP_DIR/$f" ]] || chown "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/$f"
done < "$SRC_DIR/DELTA_FILES.txt"

echo "== start $SERVICE =="
systemctl start "$SERVICE"

echo "== health check =="
ok=0
for _ in $(seq 1 30); do
  if curl -fsS "$HEALTH_URL?upgrade=2.2.2-$STAMP" 2>/dev/null | grep -q '2\.2\.2'; then
    ok=1
    break
  fi
  sleep 1
done
if [[ "$ok" != "1" ]]; then
  echo "Health check did not confirm 2.2.2. Inspect: journalctl -u $SERVICE -n 100 --no-pager" >&2
  echo "Rollback: sudo bash $SRC_DIR/rollback-2.2.2.sh $BACKUP" >&2
  exit 1
fi

echo "OK: upgraded to 2.2.2"
echo "Backup: $BACKUP"
echo "Verify public: curl -fsS 'https://capoy.ir/api/health?upgrade=2.2.2'"
echo "Live/Auto were not changed; no real orders were sent."
