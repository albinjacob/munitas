"""Configuration, read once at import.

Every value fails loudly rather than defaulting to something that works. A
credential-minting service that silently starts with a development secret is the
failure mode this is written to avoid.
"""

from __future__ import annotations

import os


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


PG_DSN = _required("PG_DSN")
OPA_URL = os.environ.get("MUNITAS_OPA_URL", "http://opa:8181")
# The score store a de-identification run writes its score card to. Only
# asked whether it is up, before such a run starts (pipeline.py).
MLFLOW_URL = os.environ.get("MUNITAS_MLFLOW_URL", "http://mlflow:5000")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://seaweedfs:8333")

# The storage keys the platform's own identities use. They are configuration,
# not something read back out of the permissions document: grants.py compiles
# that document from the policy and the register, and needs the keys as an
# input. The admin and pipeline pairs share their names with what the host
# scripts (.env) and the worker (worker/config.py) already read, so each value
# is set in one place. The defaults are the development keys, not for production.
def _pair(prefix: str, key: str, secret: str) -> tuple[str, str]:
    # `or`, not a get() default: Compose passes an unset variable through as
    # an empty string, and an empty key would be accepted and then refused.
    return (os.environ.get(f"{prefix}_KEY") or key, os.environ.get(f"{prefix}_SECRET") or secret)


STORAGE_ADMIN = _pair("S3_ADMIN", 'munitas-admin', 'munitas-admin-secret')
ROLE_STORAGE_KEYS = {
    'pipeline_action': _pair('S3_PIPELINE', 'pipeline-action', 'pipeline-action-secret'),
    'training_job': _pair('S3_TRAINING_JOB', 'training-job', 'training-job-secret'),
    'annotation_tool': _pair('S3_ANNOTATION_TOOL', 'annotation-tool', 'annotation-tool-secret'),
    'notebook_explore': _pair('S3_NOTEBOOK_EXPLORE', 'notebook-explore', 'notebook-explore-secret'),
    'agent_runtime': _pair('S3_AGENT_RUNTIME', 'agent-runtime', 'agent-runtime-secret'),
}
FILER_URL = os.environ.get("MUNITAS_FILER_URL", "http://seaweedfs:8888")
MASTER_URL = os.environ.get("MUNITAS_MASTER_URL", "http://seaweedfs:9333")

# Where SeaweedFS keeps its volume files on the host, for the
# housekeeping screen to show. Display only: nothing reads or writes
# through this path, and the control plane cannot see it. Empty when
# the deployment did not say, and the screen omits it rather than
# guessing.
STORAGE_PATH = os.environ.get("MUNITAS_STORAGE_PATH", "")

# The bucket every tenant used before per-tenant buckets existed. Legacy
# only: nothing resolves to it any more, and the one place it is still read
# is schema.sql's backfill, which writes a row naming it for any tenant that
# was on it when this install upgraded. Kept so that backfill can be pointed
# at a differently named shared bucket on an install that renamed it.

# How many storage volumes a tenant's bucket is given when it first writes.
#
# A volume is a large file SeaweedFS appends many stored objects into, up to
# 1 GB, not a folder and not one file per object. Volumes are never shared
# between tenants, because each tenant bucket is its own SeaweedFS
# collection, so this number is paid per tenant rather than per byte: a
# tenant storing fifty bytes reserves as many as one storing a gigabyte.
#
# One is right for many small tenants, which is what this platform has. A
# tenant that fills its first volume is given another automatically, so the
# cost of this being too low is a brief pause, while the cost of it being
# too high is running the machine out of volumes and failing every write
# with an S3 InternalError that mentions neither volumes nor the limit.
#
# This is the default for tenants that do not name their own. One that is
# expected to be large says so at onboarding, in `initial_storage_volumes`
# (see scripts/admin/onboarding-template.json), which is recorded against the tenant and
# wins over this. The batch size SeaweedFS uses for every later top-up, once
# a volume fills, is a separate setting in infra/seaweedfs/master.toml.
STORAGE_VOLUMES_PER_TENANT = int(
    os.environ.get("MUNITAS_STORAGE_VOLUMES_PER_TENANT", "1")
)

# Cloudflare R2, a second, optional object-storage backend.
# Read from the environment only; never logged, never returned in an API
# response, never handed to a sandboxed or otherwise untrusted process,
# the same posture the SeaweedFS admin credentials above already have.
# Unset (empty string) is a valid state: an install that never configures
# R2 simply cannot seal a version against it, refused with a clear reason
# rather than a confusing failure deep inside r2.py.
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET = os.environ.get("R2_BUCKET", "")
# The same R2 API token the two values above come from, in its original
# form. Cloudflare's own REST API (creating a bucket, minting a scoped
# token, issuing a temporary credential) authenticates with the token
# value as a bearer, while the S3 protocol authenticates with the token's
# id and a hash of that value. The hash is one-way, so holding the S3
# pair is not enough to call the REST API and this fourth value has to be
# carried separately. See platform/api/app/r2.py.
R2_API_TOKEN = os.environ.get("R2_API_TOKEN", "")

TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "temporal:7233")
# Optional, not `_required`: nothing existing calls this yet, so an API that
# boots before Kratos is ready (or without it at all) must still start. See
# platform/api/app/auth.py.
KRATOS_PUBLIC_URL = os.environ.get("MUNITAS_KRATOS_PUBLIC_URL", "http://kratos:4433")
HF_INGEST_TASK_QUEUE = "munitas-hf-ingest"
AGENT_RUN_TASK_QUEUE = "munitas-agent-run"
PIPELINE_TASK_QUEUE = "munitas-pipeline"
# Derivations run on a queue of their own: the pipeline queue is one activity
# at a time, because it holds the GPU, and a query must not wait behind it.
DERIVATION_TASK_QUEUE = "munitas-derivation"

# Shared secret between this API and the host-side worker, checked by
# GET /agents/{id}/versions/{id}/code (agent_upload.py) only. Deliberately
# not `_required`: an install that never uploads and executes agent code
# should still boot. Left unset, that one endpoint refuses every request
# rather than the API failing to start: the same "fail closed on this one
# thing" trade this file otherwise avoids, made here because the feature
# itself is optional in a way a credential-minting service never is.
WORKER_TOKEN = os.environ.get("MUNITAS_WORKER_TOKEN")

# Present so the crypto module can read it. Not referenced here, which is
# deliberate: only one module should ever touch key material.
_ = _required("MUNITAS_MASTER_KEY")

# Origins allowed to call this API from a browser. Listed rather than
# wildcarded: there is no authentication in front of this service, so a wildcard
# would let any page the operator happens to visit drive the control plane.
#
# The API must stay bound to localhost for the same reason. That is not a
# deployment preference, it is the only thing standing between this and an
# unauthenticated write surface.
CONSOLE_ORIGINS = [
    o.strip() for o in os.environ.get(
        "CONSOLE_ORIGINS", "http://localhost:3000,http://localhost:5173"
    ).split(",") if o.strip()
]

# How long a minted credential lasts. Short, because the whole argument for
# leases is that access expires on its own rather than by someone remembering.
CREDENTIAL_TTL_MINUTES = int(os.environ.get("CREDENTIAL_TTL_MINUTES", "60"))

# Iceberg projection and catalog (iceberg.py, iceberg_catalog.py).
#
# "off" skips the projection entirely, so a deployment that has not given the
# API enough memory for pyarrow can still seal versions as before.
ICEBERG_PROJECTION = os.environ.get("MUNITAS_ICEBERG_PROJECTION", "on").lower() != "off"
# The storage address a person's own tool can reach. The API reaches storage as
# S3_ENDPOINT (an address inside the Compose network), which means nothing to a
# laptop, and the catalog hands out the address in the table's configuration.
PUBLIC_S3_ENDPOINT = os.environ.get("MUNITAS_PUBLIC_S3_ENDPOINT", "http://localhost:8333")
# How long a catalog token may live, at most. A token carries a person's
# purpose and nothing else; each table it opens is still decided on its own.
CATALOG_TOKEN_MAX_HOURS = int(os.environ.get("MUNITAS_CATALOG_TOKEN_MAX_HOURS", "12"))
# How long a storage key handed out by the catalog lasts, in seconds, at most.
# A key is one of a rolling series (grants.py), so it lives between half of this
# and all of it. The floor is two activator ticks per half, below which a key
# could be refused before the document that holds it is written.
CATALOG_KEY_SECONDS = max(40, int(os.environ.get("MUNITAS_CATALOG_KEY_SECONDS", "3600")))
# Rows in one Parquet row group when a table is written. 0 leaves the library's
# own default (about a million). A reader fetches one row group at a time, so a
# smaller group spreads a long scan over more, separate storage requests.
ICEBERG_ROW_GROUP_ROWS = int(os.environ.get("MUNITAS_ICEBERG_ROW_GROUP_ROWS", "0"))
# Bytes after which a table's rows continue in a new data file. 0 leaves the
# library's own default (512 MB). A tool that reads one file at a time opens
# each with the key it held when it started, so many small files spread a
# long read over many separate key checks.
ICEBERG_FILE_BYTES = int(os.environ.get("MUNITAS_ICEBERG_FILE_BYTES", "0"))
