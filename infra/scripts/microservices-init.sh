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
  python3 - "$root_dir/infra/microservices/clients.example.json" "$data_dir" <<'PYINIT'
import hashlib, json, secrets, sys
from pathlib import Path
clients = json.loads(Path(sys.argv[1]).read_text())
directory = Path(sys.argv[2])
for client_id, basename in {"auth-center": "agent", "monitor": "monitor", "backend": "backend"}.items():
    secret = secrets.token_hex(32)
    clients[client_id]["secret_sha256"] = hashlib.sha256(secret.encode()).hexdigest()
    (directory / (basename + ".secret")).write_text(secret + "\n")
(directory / "clients.json").write_text(json.dumps(clients, indent=2) + "\n")
PYINIT
fi
if [[ -s "$data_dir/clients.json" ]] && command -v python3 >/dev/null 2>&1; then
  python3 - "$data_dir/clients.json" <<'PY'
import hashlib, json, secrets, sys
from pathlib import Path
path = sys.argv[1]
with open(path, encoding="utf-8") as stream:
    clients = json.load(stream)
grants_by_client = {
    "xhs-worker": {"camoufox-worker": "browser:execute", "backend": "xhs:result"},
    "screenshot-worker": {"camoufox-worker": "browser:execute"},
    "runtime-logs": {"xhs-worker": "logs:read", "camoufox-worker": "logs:read"},
    "ai-worker": {"xhs-worker": "xhs:execute"},
    "skill-backend": {"skill-worker": "skills:read skills:manage"},
    "skill-poller": {"skill-worker": "skills:read"},
    "skill-ai-worker": {"skill-worker": "skills:read"},
}
for client_id in grants_by_client:
    secret_path = Path(path).with_name(client_id + ".secret")
    client = clients.get(client_id)
    # Placeholders exist only in the bundled example used by a new install.
    # Preserve an existing client's credentials and deliberately revoked grants.
    if client is None or client.get("secret_sha256") == "REPLACE_WITH_SHA256":
        secret = secret_path.read_text().strip() if secret_path.is_file() else secrets.token_hex(32)
        secret_path.write_text(secret + "\n")
        clients[client_id] = {
            "secret_sha256": hashlib.sha256(secret.encode()).hexdigest(),
            "grants": grants_by_client[client_id],
        }

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
