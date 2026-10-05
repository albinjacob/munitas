# Build context is ./platform, not ./platform/api, so the crypto package can be
# copied in alongside the API without a symlink or a published wheel.
FROM python:3.12-slim

WORKDIR /app

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/app ./app
COPY crypto ./crypto
# The library that writes a version's rows as a table: shared with the table worker, which has no database.
COPY tablewriter ./tablewriter

# The schema generator ships with the code it describes, so it can be run
# where the API's dependencies already are rather than needing a second copy
# of them installed beside the repository. It reads the app's own routes and
# writes nothing: with --stdout the caller redirects the result into
# docs/openapi.json, so the file stays owned by whoever owns the repository
# and this container never needs write access to the source tree.
COPY api/generate_openapi.py .
COPY api/reconcile-grants.py .
COPY api/remove-orphaned-files.py .
COPY api/docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Runs unprivileged. The control plane mints credentials, so it is the last
# service that should be reachable as root.
RUN useradd --create-home --uid 10001 munitas
USER munitas

# 8443 is HTTPS, started only when a certificate is mounted at /tls -- see
# docker-entrypoint.sh and infra/tls/generate-local-cert.sh. A machine with
# nothing mounted there gets exactly the single plain-HTTP process this
# image has always run.
EXPOSE 8000 8443
ENTRYPOINT ["./docker-entrypoint.sh"]
