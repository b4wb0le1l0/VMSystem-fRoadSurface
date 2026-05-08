#!/bin/bash
set -e

until pg_isready -U "$POSTGRES_USER" -d postgres; do
    echo "Waiting for postgres..."
    sleep 2
done


psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    SELECT 'CREATE DATABASE "$POSTGRES_DB"'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$POSTGRES_DB')\gexec
EOSQL

echo "Database $POSTGRES_DB is ready"
