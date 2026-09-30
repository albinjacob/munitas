#!/usr/bin/env bash
# Generates a self-signed TLS certificate for the API's local HTTPS listener.
#
# Not mkcert: this cert is trusted by nobody's OS or browser, only by
# whatever explicitly points at the file (worker/config.py's API_VERIFY,
# via MUNITAS_API_CA_CERT). That is the whole point -- see RUNBOOK.md,
# "Local HTTPS between the worker and the API", for why that tradeoff was
# chosen over mkcert here.
#
# Output goes to infra/tls/out/, gitignored: a private key never belongs in
# git, and regenerating this is one command, not something worth carrying
# across machines or history.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/out"
CERT="$OUT/localhost.pem"
KEY="$OUT/localhost-key.pem"

if [[ -f "$CERT" && -f "$KEY" && "${1:-}" != "--force" ]]; then
  echo "Already have $CERT and $KEY. Pass --force to regenerate (this invalidates the old one)."
  exit 0
fi

mkdir -p "$OUT"

openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout "$KEY" -out "$CERT" \
  -subj "//CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
# The doubled leading slash on -subj is deliberate, not a typo: Git Bash's
# MSYS layer rewrites any argument starting with a single "/" that looks
# like a Unix path -- which "/CN=localhost" does -- into a Windows path
# before openssl (a native Windows binary here) ever sees it. A leading
# "//" is MSYS's own escape for "do not touch this one." Harmless on real
# Linux/WSL, where nothing rewrites arguments in the first place.

chmod 600 "$KEY"

echo "Wrote $CERT and $KEY (valid 825 days)."
echo "Restart the API (docker compose up -d --build munitas-api) to pick it up."
