#!/usr/bin/env bash
set -Eeuo pipefail
root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
data_dir="${1:-$root_dir/data/control-plane}"
mkdir -p "$data_dir"
umask 077
if [[ ! -s "$data_dir/private.pem" ]]; then openssl genrsa -out "$data_dir/private.pem" 3072; fi
if [[ ! -s "$data_dir/public.pem" ]]; then openssl rsa -in "$data_dir/private.pem" -pubout -out "$data_dir/public.pem"; fi
if [[ ! -s "$data_dir/services.json" ]]; then cp "$root_dir/infra/microservices/services.example.json" "$data_dir/services.json"; fi
if [[ ! -s "$data_dir/clients.json" ]]; then
  monitor_secret="$(openssl rand -hex 32)"; backend_secret="$(openssl rand -hex 32)"; agent_secret="$(openssl rand -hex 32)"
  sha256() { printf '%s' "$1" | openssl dgst -sha256 -r | awk '{print $1}'; }
  sed -e "0,/REPLACE_WITH_SHA256/s//$(sha256 "$agent_secret")/" -e "0,/REPLACE_WITH_SHA256/s//$(sha256 "$monitor_secret")/" -e "0,/REPLACE_WITH_SHA256/s//$(sha256 "$backend_secret")/" "$root_dir/infra/microservices/clients.example.json" > "$data_dir/clients.json"
  printf '%s\n' "$monitor_secret" > "$data_dir/monitor.secret"
  printf '%s\n' "$backend_secret" > "$data_dir/backend.secret"
  printf '%s\n' "$agent_secret" > "$data_dir/agent.secret"
fi
if [[ -s "$data_dir/clients.json" ]] && command -v python3 >/dev/null 2>&1; then
  python3 - "$data_dir/clients.json" <<'PY'
import json, os, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as stream:
    clients = json.load(stream)
clients.setdefault("backend", {}).setdefault("grants", {})["xhs-worker"] = "xhs:execute"
with open(path, "w", encoding="utf-8") as stream:
    json.dump(clients, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
PY
fi
chmod 600 "$data_dir"/*
# The bundled control-plane containers run as the backend's unprivileged UID.
# Keep the directory private while allowing only that UID to read its secrets.
chown -R 10001:10001 "$data_dir" 2>/dev/null || true
echo "Control-plane files initialized in $data_dir"
