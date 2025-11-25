#!/usr/bin/env bash
set -e

# Wait for Postgres (service name 'db' from docker-compose)
if [ -n "$POSTGRES_HOST" ]; then
  echo "Waiting for Postgres at $POSTGRES_HOST:$POSTGRES_PORT..."
  until python - <<'PY'
import sys, socket, os
s = socket.socket()
host = os.environ.get("POSTGRES_HOST","db")
port = int(os.environ.get("POSTGRES_PORT","5432"))
try:
    s.connect((host, port))
except Exception:
    sys.exit(1)
PY
  do
    echo "Postgres not ready yet..."
    sleep 1
  done
fi

echo "Applying database migrations..."
python manage.py migrate --noinput

echo "Starting Gunicorn..."
exec gunicorn mysite.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120
