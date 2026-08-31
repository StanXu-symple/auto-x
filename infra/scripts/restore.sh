#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(CDPATH= cd -- "${script_dir}/../.." && pwd)"
env_file="${COMPOSE_ENV_FILE:-${project_root}/.env}"
backup_dir="${1:-}"
confirmation="${2:-}"

if [[ -z "${backup_dir}" || "${confirmation}" != "--yes" ]]; then
  echo >&2 "Usage: COMPOSE_ENV_FILE=.env $0 /absolute/path/to/backup --yes"
  echo >&2 "This replaces the current MySQL database and Redis dataset."
  exit 2
fi
if [[ ! -r "${env_file}" ]]; then
  echo >&2 "Cannot read ${env_file}."
  exit 1
fi
if [[ ! -d "${backup_dir}" ]]; then
  echo >&2 "Backup directory does not exist: ${backup_dir}"
  exit 1
fi
for file in mysql.sql.gz redis.rdb metadata.json SHA256SUMS; do
  if [[ ! -f "${backup_dir}/${file}" ]]; then
    echo >&2 "Backup is incomplete; missing ${file}."
    exit 1
  fi
done

echo "Verifying backup checksums..."
(
  cd "${backup_dir}"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum --check SHA256SUMS
  else
    shasum -a 256 --check SHA256SUMS
  fi
)

compose=(docker compose --project-directory "${project_root}" --env-file "${env_file}")
compose_files_value="${COMPOSE_FILES:-${project_root}/docker-compose.yml}"
IFS=':' read -r -a compose_files <<< "${compose_files_value}"
for compose_file in "${compose_files[@]}"; do
  if [[ ! -r "${compose_file}" ]]; then
    echo >&2 "Cannot read Compose file: ${compose_file}"
    exit 1
  fi
  compose+=(-f "${compose_file}")
done

if [[ "${SKIP_PRE_RESTORE_BACKUP:-0}" != "1" ]]; then
  echo "Creating a safety backup of the current state..."
  COMPOSE_ENV_FILE="${env_file}" COMPOSE_FILES="${compose_files_value}" "${script_dir}/backup.sh"
fi

echo "Stopping application writers..."
"${compose[@]}" stop worker ai-worker backend

echo "Replacing and restoring MySQL database..."
mysql_character_set="$(awk -F'"' '/"mysql_character_set"/ {print $4; exit}' "${backup_dir}/metadata.json")"
mysql_collation="$(awk -F'"' '/"mysql_collation"/ {print $4; exit}' "${backup_dir}/metadata.json")"
if [[ ! "${mysql_character_set}" =~ ^[A-Za-z0-9_]+$ || ! "${mysql_collation}" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo >&2 "Backup metadata has no safe MySQL charset/collation; application services remain stopped."
  exit 1
fi
"${compose[@]}" exec -T mysql sh -ec '
  case "$MYSQL_DATABASE" in
    ""|*[!A-Za-z0-9_]*) echo >&2 "Unsafe MYSQL_DATABASE identifier"; exit 1 ;;
  esac
  case "$1:$2" in
    *[!A-Za-z0-9_:]*) echo >&2 "Unsafe MySQL charset or collation"; exit 1 ;;
  esac
  MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --user=root --host=127.0.0.1 -e \
    "DROP DATABASE IF EXISTS \`$MYSQL_DATABASE\`; CREATE DATABASE \`$MYSQL_DATABASE\` CHARACTER SET $1 COLLATE $2"
' sh "${mysql_character_set}" "${mysql_collation}"
gzip -dc -- "${backup_dir}/mysql.sql.gz" \
  | "${compose[@]}" exec -T mysql sh -ec \
      'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --user=root --host=127.0.0.1 "$MYSQL_DATABASE"'

echo "Restoring Redis RDB and replacing the current append-only log..."
"${compose[@]}" stop redis
"${compose[@]}" run --rm --no-deps -T --user redis --entrypoint sh redis -ec \
  'rm -rf /data/appendonlydir; mkdir -p /data/appendonlydir; umask 077; cat > /data/appendonlydir/appendonly.aof.1.base.rdb; printf "%s\n" "file appendonly.aof.1.base.rdb seq 1 type b" > /data/appendonlydir/appendonly.aof.manifest' \
  < "${backup_dir}/redis.rdb"
"${compose[@]}" up -d redis

echo "Waiting for Redis health..."
redis_ready=false
for _ in $(seq 1 60); do
  if [[ "$("${compose[@]}" ps --status running --services redis)" == "redis" ]] \
    && "${compose[@]}" exec -T redis sh -ec \
      'redis-cli --no-auth-warning -a "$REDIS_PASSWORD" ping' | grep -q PONG; then
    redis_ready=true
    break
  fi
  sleep 1
done
if [[ "${redis_ready}" != true ]]; then
  echo >&2 "Redis did not become healthy after restore; application services remain stopped."
  exit 1
fi

redis_verification_key="$(awk -F'"' '/"redis_verification_key"/ {print $4; exit}' "${backup_dir}/metadata.json")"
redis_verification_value="$(awk -F'"' '/"redis_verification_value"/ {print $4; exit}' "${backup_dir}/metadata.json")"
if [[ -z "${redis_verification_key}" || -z "${redis_verification_value}" ]]; then
  echo >&2 "Backup metadata has no Redis verification marker; application services remain stopped."
  exit 1
fi
restored_marker="$("${compose[@]}" exec -T redis sh -ec \
  'redis-cli --raw --no-auth-warning -a "$REDIS_PASSWORD" GET "$1"' \
  sh "${redis_verification_key}" | tr -d '\r\n')"
if [[ "${restored_marker}" != "${redis_verification_value}" ]]; then
  echo >&2 "Redis verification marker is missing after restore; application services remain stopped."
  exit 1
fi
"${compose[@]}" exec -T redis sh -ec \
  'redis-cli --raw --no-auth-warning -a "$REDIS_PASSWORD" DEL "$1" >/dev/null' \
  sh "${redis_verification_key}"

echo "Applying current database migrations..."
"${compose[@]}" run --rm migrate

echo "Starting application services..."
"${compose[@]}" up -d backend worker ai-worker frontend
echo "Restore completed from ${backup_dir}"
