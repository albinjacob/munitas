#!/bin/bash
# Create the extra databases listed in POSTGRES_MULTIPLE_DATABASES.
# One PostgreSQL instance serves the platform, Temporal, Label Studio and
# MLflow, because running four is a waste of memory on a single machine.
set -eu

if [ -n "${POSTGRES_MULTIPLE_DATABASES:-}" ]; then
  for db in $(echo "$POSTGRES_MULTIPLE_DATABASES" | tr ',' ' '); do
    echo "creating database $db"
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
      CREATE DATABASE "$db";
      GRANT ALL PRIVILEGES ON DATABASE "$db" TO "$POSTGRES_USER";
EOSQL
  done
fi
