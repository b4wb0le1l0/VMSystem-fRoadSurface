#!/usr/bin/env bash
set -e

echo "Wait for Postgres at ${DATABASE_URL} ..."
python - <<'PYCODE'
import os, time, sys
import psycopg
url = os.environ.get("DATABASE_URL")
for i in range(60):
    try:
        with psycopg.connect(url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                print("DB is ready")
                sys.exit(0)
    except Exception as e:
        print(f"DB not ready yet: {e}")
        time.sleep(2)
print("DB connection timed out")
sys.exit(1)
PYCODE

echo "Run migrations..."
alembic -x alembic_database_url="${ALEMBIC_DATABASE_URL}" upgrade head

echo "Start app..."
exec uvicorn app.main:app --host ${APP_HOST:-0.0.0.0} --port 8000 --proxy-headers
