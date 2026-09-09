#!/usr/bin/env bash
set -Eeuo pipefail
networks="${NETWORK_GROUPS:-}"
if [[ -z "$networks" && -n "${NETWORK_GROUPS_ENV_FILE:-}" && -f "${NETWORK_GROUPS_ENV_FILE}" ]]; then
  # Read only the one comma-separated setting; do not source an operator's
  # dotenv file as shell code.
  networks="$(sed -n 's/^NETWORK_GROUPS[[:space:]]*=[[:space:]]*//p' "${NETWORK_GROUPS_ENV_FILE}" | head -n 1)"
  networks="${networks%\"}"
  networks="${networks#\"}"
  networks="${networks%\'}"
  networks="${networks#\'}"
fi
networks="${networks:-x-sentinel-control,x-sentinel_app,x-sentinel_data,x-sentinel_monitoring}"
IFS=',' read -r -a groups <<< "$networks"
created=0
for network in "${groups[@]}"; do
  # Accept the common "a, b" spelling while keeping the actual Docker name
  # validation strict.
  network="${network#"${network%%[![:space:]]*}"}"
  network="${network%"${network##*[![:space:]]}"}"
  [[ -n "$network" ]] || continue
  [[ "$network" =~ ^[a-zA-Z0-9_.-]+$ ]] || { echo "Invalid network name: $network" >&2; exit 1; }
  docker network inspect "$network" >/dev/null 2>&1 || docker network create --driver bridge "$network" >/dev/null
  echo "network group ready: $network"
  created=$((created + 1))
done
(( created > 0 )) || { echo "NETWORK_GROUPS must contain at least one network name" >&2; exit 1; }
