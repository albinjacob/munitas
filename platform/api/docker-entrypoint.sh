#!/bin/sh
# Plain HTTP on 8000, always: the console and every verify/ script already
# point there, and none of what they carry is a secret worth protecting
# from anyone who can reach this container in the first place (dev-only
# posture, same as every endpoint's lack of authentication today -- see
# external_accounts.py's huggingface_secret() docstring).
#
# HTTPS on 8443, only when a certificate is mounted: the one thing that
# does cross this boundary as a real secret is what the worker fetches for
# itself (a person's HuggingFace token, the shared worker token), so that
# is the leg this exists for. See infra/tls/generate-local-cert.sh and
# RUNBOOK.md's "Local HTTPS between the worker and the API".
#
# Two uvicorn processes, not one dual-protocol listener: uvicorn does not
# serve plain and TLS on different ports from a single invocation. The
# HTTPS one runs in the background so a machine with no certificate mounted
# gets exactly today's single-process behavior, unchanged.
set -e

CERT=/tls/localhost.pem
KEY=/tls/localhost-key.pem

if [ -f "$CERT" ] && [ -f "$KEY" ]; then
  echo "docker-entrypoint: TLS cert found, also serving HTTPS on 8443"
  uvicorn app.main:app --host 0.0.0.0 --port 8443 \
    --ssl-certfile "$CERT" --ssl-keyfile "$KEY" &
else
  echo "docker-entrypoint: no TLS cert at $CERT, HTTPS on 8443 not started (see infra/tls/generate-local-cert.sh)"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
