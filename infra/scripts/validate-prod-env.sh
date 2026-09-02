#!/usr/bin/env bash
set -Eeuo pipefail

env_file="${1:-.env}"
mode="${2:-full}"
if [[ ! -r "${env_file}" ]]; then
  echo >&2 "Cannot read production environment file: ${env_file}"
  exit 1
fi

value_for() {
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

required=(MYSQL_DATABASE MYSQL_USER MYSQL_PASSWORD JWT_SECRET_KEY ADMIN_USERNAME ADMIN_PASSWORD X_TOKEN_ENCRYPTION_KEY CORS_ORIGINS)
if [[ "${mode}" == "external" ]]; then
  required+=(MYSQL_HOST MYSQL_PORT REDIS_HOST REDIS_PORT)
else
  required+=(MYSQL_ROOT_PASSWORD MYSQL_EXPORTER_PASSWORD REDIS_PASSWORD GRAFANA_ADMIN_USER GRAFANA_ADMIN_PASSWORD)
fi

failed=false
if [[ "${mode}" == "external" ]]; then
  environment_value="$(value_for ENVIRONMENT)"
  debug_value="$(value_for DEBUG)"
  if [[ -n "${environment_value}" && "${environment_value}" != "production" ]]; then
    echo >&2 "ENVIRONMENT must be production for external deployment"
    failed=true
  fi
  if [[ -n "${debug_value}" && "${debug_value}" != "false" ]]; then
    echo >&2 "DEBUG must be false for external deployment"
    failed=true
  fi
fi

for key in "${required[@]}"; do
  value="$(value_for "${key}")"
  if [[ -z "${value}" ]]; then
    echo >&2 "${key} is missing or empty"
    failed=true
    continue
  fi
  case "${value}" in
    __INVALID_DOTENV_VALUE__)
      echo >&2 "${key} uses unsupported or ambiguous dotenv quoting"
      failed=true
      ;;
    change-me-*|development-only-*|replace-with-*)
      echo >&2 "${key} still contains a placeholder value"
      failed=true
      ;;
  esac
done

jwt_secret="$(value_for JWT_SECRET_KEY)"
admin_password="$(value_for ADMIN_PASSWORD)"
x_token_encryption_key="$(value_for X_TOKEN_ENCRYPTION_KEY)"
mysql_database="$(value_for MYSQL_DATABASE)"
if (( ${#jwt_secret} < 32 )); then
  echo >&2 "JWT_SECRET_KEY must contain at least 32 characters"
  failed=true
fi
if (( ${#admin_password} < 12 )); then
  echo >&2 "ADMIN_PASSWORD must contain at least 12 characters"
  failed=true
fi
if (( ${#x_token_encryption_key} < 32 )); then
  echo >&2 "X_TOKEN_ENCRYPTION_KEY must contain at least 32 characters"
  failed=true
fi
if [[ ! "${mysql_database}" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo >&2 "MYSQL_DATABASE may contain only letters, numbers, and underscores"
  failed=true
fi

if [[ "${mode}" != "external" ]]; then
  exporter_password="$(value_for MYSQL_EXPORTER_PASSWORD)"
  if [[ ! "${exporter_password}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo >&2 "MYSQL_EXPORTER_PASSWORD may contain only letters, numbers, dot, underscore, or hyphen"
    failed=true
  fi
fi

if [[ "${failed}" == true ]]; then
  echo >&2 "Production environment validation failed."
  exit 1
fi

echo "Production environment validation passed."
