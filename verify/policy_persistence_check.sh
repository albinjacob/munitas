#!/usr/bin/env bash
# Live check that the policy engine, in bundle mode, starts with the last package it accepted when the policy store cannot be reached.
#
#     wsl -d Munitas -- bash /mnt/c/AIProjects/ClaudeProjects/Munitas/verify/policy_persistence_check.sh
#
# It stops the policy store, restarts the engine, asks it a question, and starts the policy store again however it ends. Without persistence the engine would
# have no policy at that point and would refuse everything.

set -u
TOKEN="${MUNITAS_OPA_TOKEN:-dev-opa-token-not-for-production}"
[ "$(docker inspect -f '{{index .Config.Labels "munitas.policy-source"}}' munitas-opa-1 2>/dev/null)" = "bundle" ] || { echo "The engine is not in bundle mode."; exit 3; }

restore() { docker start munitas-policy-store-1 >/dev/null 2>&1; }
trap restore EXIT

ask() {
  curl -s -H "Authorization: Bearer $TOKEN" -X POST localhost:8181/v1/data/munitas/screens/screen_decision \
    -d '{"input":{"screen":"services","roles":["analyst"]}}'
}

docker stop munitas-policy-store-1 >/dev/null
docker restart munitas-opa-1 >/dev/null
sleep 7
answer="$(ask)"
echo "policy store stopped, engine restarted: $answer"
docker logs --since 10s munitas-opa-1 2>&1 | grep -i "Bundle load failed" | tail -1 | sed -E 's/.*"msg":"([^"]*)".*/  the engine logged: \1/'

if echo "$answer" | grep -q '"allow":false'; then
  echo "PASS  the engine started from its saved package and answered"
  exit 0
fi
echo "FAIL  the engine had no policy without the policy store"
exit 1
