#!/usr/bin/env bash
# Nightly pg_dump (custom format) of the database running in the compose `db` service; keeps 14 days.
# Run from anywhere; paths are relative to the checkout this script lives in.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BACKUP_DIR=${BACKUP_DIR:-$ROOT/backups}
RETENTION_DAYS=${RETENTION_DAYS:-14}
mkdir -p "$BACKUP_DIR"
OUT="$BACKUP_DIR/jobagent_$(date +%Y-%m-%d_%H%M%S).dump"
trap 'rm -f "$OUT.partial"' ERR
(cd "$ROOT" && docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc') > "$OUT.partial"
mv "$OUT.partial" "$OUT"
find "$BACKUP_DIR" -maxdepth 1 -name 'jobagent_*.dump' -mtime +"$RETENTION_DAYS" -delete
echo "backup ok: $OUT ($(du -h "$OUT" | cut -f1))"
# Off-box copy: set BACKUP_REMOTE in .env to an rsync target (user@host:/path/) or an rclone
# remote (remote:bucket/path). Resumes are personal data: keep the destination private.
REMOTE=$(grep -E '^BACKUP_REMOTE=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
if [ -n "$REMOTE" ]; then
  case "$REMOTE" in
    *:*/*|*@*) if command -v rclone >/dev/null && rclone listremotes 2>/dev/null | grep -q "^${REMOTE%%:*}:$"; then
                 rclone copy "$OUT" "$REMOTE" && echo "copied to $REMOTE (rclone)"
               else
                 rsync -a "$OUT" "$REMOTE" && echo "copied to $REMOTE (rsync)"
               fi ;;
    *) echo "BACKUP_REMOTE not understood: $REMOTE" >&2 ;;
  esac
fi
