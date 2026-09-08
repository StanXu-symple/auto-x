#!/usr/bin/env bash
set -Eeuo pipefail
networks="${NETWORK_GROUPS:-x-sentinel-control,x-sentinel_app,x-sentinel_data,x-sentinel_monitoring}"
IFS=',' read -r -a groups <<< "$networks"
for network in "${groups[@]}"; do
  [[ "$network" =~ ^[a-zA-Z0-9_.-]+$ ]] || { echo "Invalid network name: $network" >&2; exit 1; }
  docker network inspect "$network" >/dev/null 2>&1 || docker network create --driver bridge "$network" >/dev/null
  echo "network group ready: $network"
done
