#!/usr/bin/env bash
# The steps of publish-policy.ps1, run inside the WSL distro. Not meant to be run by hand: the script on the Windows side works out the paths and passes them in.
#
#   REPO         this repository, as the distro sees it
#   DATA         the data folder (MUNITAS_DATA), as the distro sees it
#   IMAGE        the policy engine's image, read from docker-compose.yml, used for testing and building
#   REVISION     a label for this package
#   NEW_KEY      yes when the person asked for a new signing key pair
#
# Each step stops the script when it fails, so a policy that fails its tests or does not compile is never published.

set -eu
cd "$REPO"

KEYS="$HOME/.munitas/policy-signing"
PUBLIC=platform/policy-bundle/signing-public.pem
STORE_DIR="$DATA/policy-store"

# ---- the signing key pair: the private half stays here, the public half is committed ---------------------------------------------------------------------------
if [ ! -f "$KEYS/private.pem" ]; then
  if [ "$NEW_KEY" != yes ]; then
    echo 'There is no signing key on this machine. Run .\publish-policy.ps1 -NewSigningKey once.'
    exit 3
  fi
  mkdir -p "$KEYS"
  chmod 700 "$KEYS"
  (umask 077; openssl genrsa -out "$KEYS/private.pem" 3072 2>/dev/null)
  openssl rsa -in "$KEYS/private.pem" -pubout -out "$PUBLIC" 2>/dev/null
  echo "Made a signing key pair. The private key is $KEYS/private.pem (keep it; it is not in the repository). The public key is $PUBLIC; commit it."
elif [ "$NEW_KEY" = yes ]; then
  echo 'A signing key already exists, so -NewSigningKey was not used. Delete it first if you mean to replace it.'
  exit 3
fi
if [ "$(openssl rsa -in "$KEYS/private.pem" -pubout 2>/dev/null)" != "$(cat "$PUBLIC")" ]; then
  echo "$PUBLIC is not the public half of the private key, so the engine would refuse every package."
  exit 4
fi

# ---- the store's own read-only key: made once, kept in the data folder, never in the repository ------------------------------------------------------------------
mkdir -p "$STORE_DIR/data"
if [ ! -f "$STORE_DIR/s3.json" ] && [ ! -f "$STORE_DIR/opa.env" ]; then
  key="policy-reader-$(openssl rand -hex 6)"
  secret="$(openssl rand -hex 24)"
  (
    umask 077
    printf '{"identities":[{"name":"policy-reader","credentials":[{"accessKey":"%s","secretKey":"%s"}],"actions":["Read:policy-bundles"]}]}\n' "$key" "$secret" > "$STORE_DIR/s3.json"
    printf 'AWS_ACCESS_KEY_ID=%s\nAWS_SECRET_ACCESS_KEY=%s\nAWS_REGION=us-east-1\n' "$key" "$secret" > "$STORE_DIR/opa.env"
  )
  echo "Made the policy store's read-only key in $STORE_DIR (s3.json for the store, opa.env for the engine). Start the engine again after the store, so that it uses it."
elif [ ! -f "$STORE_DIR/s3.json" ] || [ ! -f "$STORE_DIR/opa.env" ]; then
  echo "Only one of s3.json and opa.env exists in $STORE_DIR, so the store and the engine would disagree about the key. Delete the one that is there and run this again."
  exit 6
fi

# ---- test, build and sign ---------------------------------------------------------------------------------------------------------------------------------------
mkdir -p platform/policy-bundle/published
echo "Testing the policy ($IMAGE)..."
docker run --rm -v "$PWD/platform/policy:/policy:ro" "$IMAGE" test /policy
echo 'Building and signing the package...'
rm -f platform/policy-bundle/published/policy.tar.gz.tmp
docker run --rm --user 0:0 \
  -v "$PWD/platform/policy:/policy:ro" \
  -v "$KEYS/private.pem:/keys/private.pem:ro" \
  -v "$PWD/platform/policy-bundle/signing-claims.json:/keys/claims.json:ro" \
  -v "$PWD/platform/policy-bundle/published:/out" \
  "$IMAGE" build -b /policy --ignore '*_test.rego' --signing-key /keys/private.pem --signing-alg RS256 --claims-file /keys/claims.json --revision "$REVISION" -o /out/policy.tar.gz.tmp

# ---- put it in the store ----------------------------------------------------------------------------------------------------------------------------------------
echo 'Putting it in the policy store...'
MUNITAS_DATA="$DATA" MUNITAS_HOST_WORK_DIR="" MUNITAS_OPA_POLICY_SOURCE=bundle \
  docker compose -f docker-compose.yml -f docker-compose.opa-bundle.yml up -d --no-deps policy-store
bash platform/policy-bundle/store.sh ready
bash platform/policy-bundle/store.sh upload platform/policy-bundle/published/policy.tar.gz.tmp
mv -f platform/policy-bundle/published/policy.tar.gz.tmp platform/policy-bundle/published/policy.tar.gz
bash platform/policy-bundle/store.sh status
