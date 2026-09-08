#!/bin/sh
set -eu

case "${POSTGRES_EXPORTER_PASSWORD:-}" in
  ""|*[!A-Za-z0-9_.-]*)
    echo >&2 "POSTGRES_EXPORTER_PASSWORD must use only letters, numbers, dot, underscore, or hyphen"
    exit 1
    ;;
esac

PGPASSWORD="${POSTGRES_PASSWORD}" psql \
  --username="${POSTGRES_USER}" \
  --dbname="${POSTGRES_DB}" \
  --no-password \
  --set=ON_ERROR_STOP=1 \
  --command="DO \$\$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'exporter') THEN
      CREATE ROLE exporter LOGIN PASSWORD '${POSTGRES_EXPORTER_PASSWORD}';
    ELSE
      ALTER ROLE exporter PASSWORD '${POSTGRES_EXPORTER_PASSWORD}';
    END IF;
  END \$\$; GRANT pg_monitor TO exporter;"
