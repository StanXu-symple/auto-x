#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(CDPATH= cd -- "${script_dir}/../.." && pwd)"
env_file="${COMPOSE_ENV_FILE:-${project_root}/.env}"

if [[ ! -r "${env_file}" ]]; then
  echo >&2 "Cannot read ${env_file}; create .env from .env.example first."
  exit 1
fi

env_value() {
  local key="$1"
  local value
  local double_quoted_re='^"([^"]*)"[[:space:]]*(#.*)?$'
  local single_quoted_re="^'([^']*)'[[:space:]]*(#.*)?$"
  local unquoted_comment_re='^(.*[^[:space:]])[[:space:]]+#.*$'
  value="$(awk -F= -v key="${key}" '$1 == key {sub(/^[^=]*=/, ""); value=$0} END {print value}' "${env_file}")"
  value="${value%$'\r'}"
  if [[ "${value}" =~ ${double_quoted_re} || "${value}" =~ ${single_quoted_re} ]]; then
    value="${BASH_REMATCH[1]}"
  elif [[ "${value:0:1}" == '"' || "${value:0:1}" == "'" ]]; then
    value="__INVALID_DOTENV_VALUE__"
  elif [[ "${value}" =~ ${unquoted_comment_re} ]]; then
    value="${BASH_REMATCH[1]}"
  fi
  printf '%s\n' "${value}"
}

backup_setting="${BACKUP_DIR:-}"
if [[ -z "${backup_setting}" ]]; then
  backup_setting="$(env_value BACKUP_DIR)"
fi
if [[ "${backup_setting}" == "__INVALID_DOTENV_VALUE__" ]]; then
  echo >&2 "BACKUP_DIR uses unsupported or ambiguous dotenv quoting."
  exit 1
fi
backup_setting="${backup_setting:-./backups}"
if [[ "${backup_setting}" = /* ]]; then
  backup_root="${backup_setting}"
else
  backup_root="${project_root}/${backup_setting#./}"
fi

mkdir -p -- "${backup_root}"
work_dir="$(mktemp -d "${backup_root}/.x-sentinel-backup.XXXXXX")"
compose=()
redis_cleanup_pending=false
redis_verification_key=""
cleanup() {
  if [[ "${redis_cleanup_pending:-false}" == true && ${#compose[@]} -gt 0 ]]; then
    "${compose[@]}" exec -T redis sh -ec \
      'redis-cli --raw --no-auth-warning -a "$REDIS_PASSWORD" DEL "$1" >/dev/null' \
      sh "${redis_verification_key}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${work_dir:-}" && -d "${work_dir}" ]]; then
    rm -rf -- "${work_dir}"
  fi
}
trap cleanup EXIT

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
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target_dir="${backup_root}/${timestamp}"
redis_verification_key="xsentinel:backup:verify:${timestamp}"
redis_verification_value="${timestamp}"

if [[ -e "${target_dir}" ]]; then
  echo >&2 "Backup target already exists: ${target_dir}"
  exit 1
fi

for service in mysql redis; do
  if [[ "$("${compose[@]}" ps --status running --services "${service}")" != "${service}" ]]; then
    echo >&2 "Service '${service}' is not running."
    exit 1
  fi
done

echo "Creating transaction-consistent MySQL dump..."
"${compose[@]}" exec -T mysql sh -ec \
  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump --user=root --host=127.0.0.1 --single-transaction --quick --routines --events --triggers --hex-blob --set-gtid-purged=OFF "$MYSQL_DATABASE"' \
  | gzip -9 > "${work_dir}/mysql.sql.gz"
alembic_revision="$("${compose[@]}" exec -T mysql sh -ec \
  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --batch --skip-column-names --user=root --host=127.0.0.1 "$MYSQL_DATABASE" -e "SELECT version_num FROM alembic_version LIMIT 1" 2>/dev/null || printf unknown' \
  | tr -d '\r\n')"
database_defaults="$("${compose[@]}" exec -T mysql sh -ec \
  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --batch --skip-column-names --user=root --host=127.0.0.1 "$MYSQL_DATABASE" -e "SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = DATABASE()"' \
  | tr -d '\r')"
mysql_character_set="$(printf '%s\n' "${database_defaults}" | awk '{print $1; exit}')"
mysql_collation="$(printf '%s\n' "${database_defaults}" | awk '{print $2; exit}')"
if [[ ! "${mysql_character_set}" =~ ^[A-Za-z0-9_]+$ || ! "${mysql_collation}" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo >&2 "Could not determine safe MySQL database charset/collation."
  exit 1
fi
mysql_database="$("${compose[@]}" exec -T mysql sh -ec 'printf %s "$MYSQL_DATABASE"')"
if [[ ! "${mysql_database}" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo >&2 "MYSQL_DATABASE contains unsafe characters."
  exit 1
fi
if [[ ! "${alembic_revision}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  alembic_revision="unknown"
fi
image_tag="${IMAGE_TAG:-latest}"
if [[ ! "${image_tag}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  image_tag="unknown"
fi

echo "Creating Redis snapshot..."
idle_before_save=false
for _ in $(seq 1 60); do
  persistence_info="$("${compose[@]}" exec -T redis sh -ec \
    'redis-cli --raw --no-auth-warning -a "$REDIS_PASSWORD" INFO persistence' | tr -d '\r')"
  bgsave_running="$(printf '%s\n' "${persistence_info}" | awk -F: '$1 == "rdb_bgsave_in_progress" {print $2; exit}')"
  if [[ "${bgsave_running}" == "0" ]]; then
    idle_before_save=true
    break
  fi
  sleep 1
done
if [[ "${idle_before_save}" != true ]]; then
  echo >&2 "Redis already had a BGSAVE running for more than 60 seconds."
  exit 1
fi

baseline_saves="$(printf '%s\n' "${persistence_info}" | awk -F: '$1 == "rdb_saves" {print $2; exit}')"
if [[ ! "${baseline_saves}" =~ ^[0-9]+$ ]]; then
  echo >&2 "Redis INFO returned an invalid rdb_saves value: ${baseline_saves}"
  exit 1
fi
"${compose[@]}" exec -T redis sh -ec \
  'redis-cli --raw --no-auth-warning -a "$REDIS_PASSWORD" SET "$1" "$2" >/dev/null' \
  sh "${redis_verification_key}" "${redis_verification_value}"
redis_cleanup_pending=true
bgsave_reply="$("${compose[@]}" exec -T redis sh -ec \
  'redis-cli --raw --no-auth-warning -a "$REDIS_PASSWORD" BGSAVE' | tr -d '\r')"
if [[ "${bgsave_reply}" == ERR* ]]; then
  echo >&2 "Redis BGSAVE failed: ${bgsave_reply}"
  exit 1
fi

snapshot_ready=false
for _ in $(seq 1 60); do
  persistence_info="$("${compose[@]}" exec -T redis sh -ec \
    'redis-cli --raw --no-auth-warning -a "$REDIS_PASSWORD" INFO persistence' | tr -d '\r')"
  current_saves="$(printf '%s\n' "${persistence_info}" | awk -F: '$1 == "rdb_saves" {print $2; exit}')"
  bgsave_running="$(printf '%s\n' "${persistence_info}" | awk -F: '$1 == "rdb_bgsave_in_progress" {print $2; exit}')"
  bgsave_status="$(printf '%s\n' "${persistence_info}" | awk -F: '$1 == "rdb_last_bgsave_status" {print $2; exit}')"
  if [[ "${current_saves}" =~ ^[0-9]+$ \
    && "${current_saves}" -gt "${baseline_saves}" \
    && "${bgsave_running}" == "0" \
    && "${bgsave_status}" == "ok" ]]; then
    snapshot_ready=true
    break
  fi
  sleep 1
done

if [[ "${snapshot_ready}" != true ]]; then
  echo >&2 "Redis did not finish BGSAVE within 60 seconds."
  exit 1
fi

"${compose[@]}" cp redis:/data/dump.rdb "${work_dir}/redis.rdb" >/dev/null
"${compose[@]}" exec -T redis sh -ec \
  'redis-cli --raw --no-auth-warning -a "$REDIS_PASSWORD" DEL "$1" >/dev/null' \
  sh "${redis_verification_key}"
redis_cleanup_pending=false

cat > "${work_dir}/metadata.json" <<EOF
{
  "application": "X Sentinel",
  "created_at_utc": "${timestamp}",
  "mysql_database": "${mysql_database}",
  "mysql_character_set": "${mysql_character_set}",
  "mysql_collation": "${mysql_collation}",
  "alembic_revision": "${alembic_revision:-unknown}",
  "image_tag": "${image_tag}",
  "redis_verification_key": "${redis_verification_key}",
  "redis_verification_value": "${redis_verification_value}",
  "contents": ["mysql.sql.gz", "redis.rdb"]
}
EOF

(
  cd "${work_dir}"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum mysql.sql.gz redis.rdb metadata.json > SHA256SUMS
  else
    shasum -a 256 mysql.sql.gz redis.rdb metadata.json > SHA256SUMS
  fi
)

chmod 600 "${work_dir}"/*
mv -- "${work_dir}" "${target_dir}"
work_dir=""
trap - EXIT

echo "Backup completed: ${target_dir}"
