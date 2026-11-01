#!/usr/bin/env bash
# Waits for Postgres, applies migrations, then runs the given command (the web page by default).
set -euo pipefail
cd /app

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is not set; copy .env.example to .env first" >&2
  exit 1
fi

echo "waiting for the database..."
python - <<'PY'
import os, sys, time
import psycopg
url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
for attempt in range(60):
    try:
        with psycopg.connect(url, connect_timeout=3):
            break
    except Exception as e:  # noqa: BLE001
        if attempt == 59:
            print(f"database not reachable: {e}", file=sys.stderr)
            sys.exit(1)
        time.sleep(2)
PY

if [ "${SKIP_MIGRATIONS:-}" != "1" ]; then
  echo "applying migrations..."
  alembic upgrade head
fi

mkdir -p data/runs data/config_backups
exec "$@"
