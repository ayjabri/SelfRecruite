#!/usr/bin/env bash
# Nightly pg_dump (custom format) of the jobagent DB; keeps 14 days.
set -euo pipefail
BACKUP_DIR=/srv/jobagent/backups
RETENTION_DAYS=14
mkdir -p "$BACKUP_DIR"
OUT="$BACKUP_DIR/jobagent_$(date +%Y-%m-%d_%H%M%S).dump"
trap 'rm -f "$OUT.partial"' ERR
docker exec jobagent-db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$OUT.partial"
mv "$OUT.partial" "$OUT"
find "$BACKUP_DIR" -maxdepth 1 -name 'jobagent_*.dump' -mtime +"$RETENTION_DAYS" -delete
echo "backup ok: $OUT ($(du -h "$OUT" | cut -f1))"
