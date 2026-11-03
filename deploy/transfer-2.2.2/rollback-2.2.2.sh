#!/usr/bin/env bash
# Roll back the 2.2.2 delta to the backed-up 2.2.1 files.
# Usage: sudo bash rollback-2.2.2.sh /root/nobi-backup-<stamp>
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP="${1:-}"
APP_DIR="${APP_DIR:-/opt/nobi}"
SERVICE="${SERVICE:-nobi}"
SERVICE_USER="${SERVICE_USER:-nobi}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/health}"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0 <backup-dir>" >&2
  exit 1
fi
[[ -n "$BACKUP" && -d "$BACKUP/files" ]] || { echo "Usage: sudo bash $0 /root/nobi-backup-<stamp>" >&2; exit 1; }
[[ -d "$APP_DIR" ]] || { echo "Missing APP_DIR=$APP_DIR" >&2; exit 1; }

echo "== stop $SERVICE =="
systemctl stop "$SERVICE"

echo "== restore 2.2.1 changed files =="
while IFS= read -r f; do
  [[ -n "$f" ]] || continue
  if [[ -e "$BACKUP/files/$f" ]]; then
    mkdir -p "$(dirname "$APP_DIR/$f")"
    cp -a "$BACKUP/files/$f" "$APP_DIR/$f"
    chown "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/$f"
  fi
done < "$SRC_DIR/DELTA_FILES.txt"

echo "== remove files added by 2.2.2 =="
while IFS= read -r f; do
  [[ -n "$f" ]] || continue
  rm -f "$APP_DIR/$f"
done < "$SRC_DIR/ADDED_FILES.txt"

echo "== start $SERVICE =="
systemctl start "$SERVICE"

echo "== health check =="
ok=0
for _ in $(seq 1 30); do
  if curl -fsS "$HEALTH_URL?rollback=2.2.1-$(date -u +%s)" 2>/dev/null | grep -q '2\.2\.1'; then
    ok=1
    break
  fi
  sleep 1
done
if [[ "$ok" != "1" ]]; then
  echo "Health check did not confirm 2.2.1. Inspect: journalctl -u $SERVICE -n 100 --no-pager" >&2
  exit 1
fi
echo "OK: rolled back to 2.2.1"
