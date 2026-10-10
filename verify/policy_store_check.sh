#!/usr/bin/env bash
# U190, both halves: who can reach the policy store and what they can do there.
#
#     wsl -d Munitas -- bash /mnt/c/AIProjects/ClaudeProjects/Munitas/verify/policy_store_check.sh
#
# The two halves have to run in different places. One runs in the API container, which is on the platform's own networks and must not be able to reach the store;
# the other runs in a throwaway container on the store's network with the engine's read-only key, from the data folder. The stack must be in bundle mode with a package
# published. Nothing is written. The throwaway container removes itself.

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Where the engine's key is kept: the data folder, from .env.
DATA="$(grep -E '^\s*MUNITAS_DATA\s*=' .env | head -1 | tr -d '\r' | sed -E 's/^[^=]*=\s*//; s/^["'"'"']//; s/["'"'"']$//')"
case "$DATA" in [A-Za-z]:*) DATA="/mnt/$(echo "${DATA:0:1}" | tr 'A-Z' 'a-z')${DATA:2}" ;; esac
ENVFILE="$DATA/policy-store/opa.env"
[ -f "$ENVFILE" ] || { echo "No $ENVFILE. Run .\\publish-policy.ps1 first."; exit 3; }

status=0
docker exec munitas-munitas-api-1 python /verify/v179_policy_store_access.py outside || status=1
docker run --rm --network munitas_policy --env-file "$ENVFILE" -v "$ROOT/verify:/verify:ro" --entrypoint python munitas-munitas-api \
  /verify/v179_policy_store_access.py inside || status=1
exit $status
