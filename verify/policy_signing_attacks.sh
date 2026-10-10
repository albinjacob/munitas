#!/usr/bin/env bash
# Live check that the policy engine, running in bundle mode, refuses a package it should not trust, and keeps answering from the last good one.
#
# Run inside the WSL distro, with the stack in bundle mode and a signed package published (.\publish-policy.ps1):
#
#     wsl -d Munitas -- bash /mnt/c/AIProjects/ClaudeProjects/Munitas/verify/policy_signing_attacks.sh
#
# It puts four packages in turn into the policy store, as publish-policy.ps1 does, and asks the engine the same question after each:
#   a  a package nobody signed
#   b  a package signed by a different key
#   c  a package with a valid signature from the real key whose policy file was changed afterwards
#   d  the real package again, to show the engine recovers
# Packages a to c carry a policy that would open every page to everybody, so a refusal that failed to happen would show as "allow":true.
#
# Its scratch files are made in one new folder under /tmp and only that folder is removed at the end. The folder name is checked before anything is removed.

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PUB=platform/policy-bundle/published
TOKEN="${MUNITAS_OPA_TOKEN:-dev-opa-token-not-for-production}"
IMG="$(grep -E '^\s*image:\s*openpolicyagent/opa:' docker-compose.yml | head -1 | tr -d '\r' | sed -E 's/.*image:\s*//')"
KEYS="$HOME/.munitas/policy-signing"

[ -f "$PUB/policy.tar.gz" ] || { echo "No package is published. Run .\\publish-policy.ps1 first."; exit 3; }
[ "$(docker inspect -f '{{index .Config.Labels "munitas.policy-source"}}' munitas-opa-1 2>/dev/null)" = "bundle" ] || { echo "The engine is not in bundle mode."; exit 3; }

W="$(mktemp -d /tmp/signtest.XXXXXX)"
mkdir -p "$W/out" "$W/evil"

ask() {
  curl -s -H "Authorization: Bearer $TOKEN" -X POST localhost:8181/v1/data/munitas/screens/screen_decision \
    -d '{"input":{"screen":"services","roles":["analyst"]}}' | grep -o '"allow":[a-z]*'
}
publish() { bash platform/policy-bundle/store.sh upload "$1"; sleep 8; }
reason() { docker logs --since 8s munitas-opa-1 2>&1 | grep "Bundle load failed" | tail -1 | sed -E 's/.*"msg":"([^"]*)".*/\1/'; }
verdict() { # label, expected refusal wording present, answer
  if [ -n "$2" ] && [ "$3" = '"allow":false' ]; then echo "PASS  $1: refused ($2); the analyst is still told no"; FAILS=$FAILS; else echo "FAIL  $1: reason='$2' answer='$3'"; FAILS=$((FAILS+1)); fi
}
FAILS=0

cp "$PUB/policy.tar.gz" "$W/good.tar.gz"
cp platform/policy/*.rego "$W/evil/"
rm -f "$W"/evil/*_test.rego
printf 'package munitas.screens\nimport rego.v1\nmay_open if true\n' > "$W/evil/evil.rego"

# a  unsigned
docker run --rm --user 0:0 -v "$W/evil:/policy:ro" -v "$W/out:/out" "$IMG" build -b /policy -o /out/unsigned.tar.gz >/dev/null
publish "$W/out/unsigned.tar.gz"
verdict "a unsigned package" "$(reason)" "$(ask)"

# b  signed by another key
openssl genrsa -out "$W/other.pem" 3072 2>/dev/null
docker run --rm --user 0:0 -v "$W/evil:/policy:ro" -v "$W/other.pem:/k.pem:ro" -v "$ROOT/platform/policy-bundle/signing-claims.json:/c.json:ro" -v "$W/out:/out" \
  "$IMG" build -b /policy --signing-key /k.pem --signing-alg RS256 --claims-file /c.json -o /out/otherkey.tar.gz >/dev/null
publish "$W/out/otherkey.tar.gz"
verdict "b package signed by another key" "$(reason)" "$(ask)"

# c  a good signature, a policy file changed afterwards
mkdir -p "$W/tamper"
tar -xzf "$W/good.tar.gz" -C "$W/tamper"
printf '\nmay_open if true\n' >> "$W/tamper/policy/screens.rego"
(cd "$W/tamper" && tar -czf "$W/out/tampered.tar.gz" .)
publish "$W/out/tampered.tar.gz"
verdict "c policy changed after signing" "$(reason)" "$(ask)"

# d  the real package again
publish "$W/good.tar.gz"
errors="$(docker logs --since 8s munitas-opa-1 2>&1 | grep -c 'Bundle load failed')"
if [ "$errors" = "0" ] && [ "$(ask)" = '"allow":false' ]; then echo "PASS  d the real package loads again with no errors"; else echo "FAIL  d errors=$errors answer='$(ask)'"; FAILS=$((FAILS+1)); fi

case "$W" in /tmp/signtest.*) rm -rf "$W" ;; *) echo "Not removing $W: it is not a scratch folder made here." ;; esac
echo "$FAILS failed"
[ "$FAILS" = "0" ]
