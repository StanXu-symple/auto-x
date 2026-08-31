#!/bin/sh
set -eu

case "${MYSQL_EXPORTER_PASSWORD:-}" in
  ""|*[!A-Za-z0-9_.-]*)
    echo >&2 "MYSQL_EXPORTER_PASSWORD must use only letters, numbers, dot, underscore, or hyphen"
    exit 1
    ;;
esac

MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" mysql --protocol=socket --user=root <<-EOSQL
CREATE USER IF NOT EXISTS 'exporter'@'%' IDENTIFIED BY '${MYSQL_EXPORTER_PASSWORD}' WITH MAX_USER_CONNECTIONS 3;
ALTER USER 'exporter'@'%' IDENTIFIED BY '${MYSQL_EXPORTER_PASSWORD}' WITH MAX_USER_CONNECTIONS 3;
GRANT PROCESS, REPLICATION CLIENT, SELECT ON *.* TO 'exporter'@'%';
EOSQL
