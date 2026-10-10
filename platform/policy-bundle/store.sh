#!/usr/bin/env bash
# Putting a package in the policy store, and asking what is in it. Run inside the WSL distro.
#
#     platform/policy-bundle/store.sh ready            wait until the store answers
#     platform/policy-bundle/store.sh upload FILE      put FILE in the store as policy.tar.gz
#     platform/policy-bundle/store.sh status           say what is in the store
#
# The write goes in through a command run in the store's own container, so whoever can do this holds the machine; no write key exists anywhere. The engine can read
# the package but cannot write it (it holds a read-only key). Used by publish-policy.ps1 and by verify/policy_signing_attacks.sh.

set -eu
STORE="${MUNITAS_POLICY_STORE_CONTAINER:-munitas-policy-store-1}"

case "${1:-}" in
  ready)
    for _ in $(seq 1 60); do
      if docker exec "$STORE" wget -q -O /dev/null http://policy-store:8888/ 2>/dev/null; then exit 0; fi
      sleep 2
    done
    echo "The policy store did not answer within two minutes." >&2
    exit 1
    ;;
  upload)
    file="${2:?upload needs a file}"
    docker cp "$file" "$STORE:/tmp/policy.tar.gz"
    docker exec "$STORE" weed filer.copy /tmp/policy.tar.gz http://policy-store:8888/buckets/policy-bundles/ >/dev/null
    docker exec "$STORE" rm -f /tmp/policy.tar.gz
    ;;
  status)
    listing="$(docker exec "$STORE" wget -q -O- --header 'Accept: application/json' http://policy-store:8888/buckets/policy-bundles/)"
    size="$(echo "$listing" | grep -o '"FileSize":[0-9]*' | head -1 | cut -d: -f2)"
    written="$(echo "$listing" | grep -o '"Mtime":"[^"]*"' | head -1 | cut -d'"' -f4)"
    if [ -z "$size" ]; then echo "policy-bundles/policy.tar.gz: nothing is published"; exit 1; fi
    echo "policy-bundles/policy.tar.gz: $size bytes, written $written"
    ;;
  *)
    echo "usage: store.sh ready | upload FILE | status" >&2
    exit 2
    ;;
esac
