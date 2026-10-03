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
# Bytes after which a table's rows continue in a new data file. 0, which is what the
# Compose file passes when nothing is set, means the platform's default below. A tool that reads one file at a time opens
# each with the key it held when it started, so many small files spread a
# long read over many separate key checks.
#
# The library counts this in uncompressed bytes held in memory, and holds that much
# before it writes a file. At its own default a large table is buffered 512 MB at a
# time, which is half of what the API process is allowed. 64 MB keeps a large write
# within a bounded share of memory, and the files come out several times smaller
# than this after compression.
ICEBERG_FILE_BYTES = int(os.environ.get("MUNITAS_ICEBERG_FILE_BYTES", "0")) or 64 * 1024 * 1024

# Closing an organisation (lifecycle.py, purge.py). Each organisation takes its
# dates from these when its retirement starts, so changing them never moves a
# countdown already running.
#
# During the first period the organisation's people can still read and may
# cancel. During the second they can do nothing, and a platform administrator
# may place a legal hold. When both end and no hold stands, everything inside the
# organisation is deleted.
RETIRING_DAYS = int(os.environ.get("MUNITAS_RETIRING_DAYS", "15"))
CLOSING_DAYS = int(os.environ.get("MUNITAS_CLOSING_DAYS", "15"))
# How long a proposed legal hold waits for a second administrator before it
# lapses and stops standing in the way of the purge, and how long an approved one
# runs before somebody has to look at it again.
HOLD_APPROVAL_DAYS = int(os.environ.get("MUNITAS_HOLD_APPROVAL_DAYS", "7"))
HOLD_REVIEW_DAYS = int(os.environ.get("MUNITAS_HOLD_REVIEW_DAYS", "90"))
# How often the platform looks for organisations whose time is up. 0 turns the
# timer off (the sweep can still be run by hand), which a verification run uses
# so a purge only happens when it asks for one.
LIFECYCLE_SWEEP_SECONDS = int(os.environ.get("MUNITAS_LIFECYCLE_SWEEP_SECONDS", "300"))

# What a purge keeps: the audit rows of a deleted organisation, for this many years, and then removes
# (purge.py). Seven is the retention period the platform was asked to meet for records of who read what.
AUDIT_RETENTION_YEARS = int(os.environ.get("MUNITAS_AUDIT_RETENTION_YEARS", "7"))
# The identity provider's admin address. A purge removes the sign-in accounts of the people of the
# organisation it deletes, and nothing else, so a deleted organisation leaves no working login behind.
KRATOS_ADMIN_URL = os.environ.get("MUNITAS_KRATOS_ADMIN_URL", "http://kratos:4434")

# Legal export (legal_export.py). A package is encrypted and signed, kept in a bucket of its own, and
# deleted after LEGAL_EXPORT_KEEP_DAYS. The signing key makes a package's manifest verifiable by whoever
# receives it: kept like the master key, and the public half is served at /legal-exports/signing-key.
SIGNING_KEY = _required("MUNITAS_SIGNING_KEY")
LEGAL_EXPORT_BUCKET = os.environ.get("MUNITAS_LEGAL_EXPORT_BUCKET", "munitas-legal-exports")
LEGAL_EXPORT_KEEP_DAYS = int(os.environ.get("MUNITAS_LEGAL_EXPORT_KEEP_DAYS", "14"))
LEGAL_EXPORT_LINK_USES = int(os.environ.get("MUNITAS_LEGAL_EXPORT_LINK_USES", "3"))
LEGAL_EXPORT_LINK_DAYS = int(os.environ.get("MUNITAS_LEGAL_EXPORT_LINK_DAYS", "7"))
# The most a package may hold. Phase one builds it on local disk, so this is a safety limit and not a design goal.
LEGAL_EXPORT_MAX_BYTES = int(os.environ.get("MUNITAS_LEGAL_EXPORT_MAX_BYTES", str(2 * 1024 ** 3)))

# Writing a version's rows as a table (iceberg.py), and what happens when that cannot be done.
#
# A caller who names a records file is asking for a table. By default a version is then NOT sealed without one when the
# table cannot be written for a reason about the data or a fault in writing it: the seal is refused with the reason, its
# version number stays unused, and nothing is left behind. A producer that would rather keep the files without a table
# says so for one seal (`table_required: false`). A table that cannot be written because the platform was told not to
# (projection switched off) or cannot yet (files on R2, a file too large) never blocks a seal.
ICEBERG_FAIL_CLOSED = os.environ.get("MUNITAS_ICEBERG_FAIL_CLOSED", "on").lower() != "off"
# The records file comes in one of three shapes, chosen by its name: a JSON list (.json), one JSON row per line (.ndjson or
# .jsonl) or a Parquet file (.parquet). A JSON list has to be read whole and held in memory, and measured here it takes about
# six times its size (a 48 MB list peaked at 586 MB, a 96 MB list at 884 MB, a 193 MB list at 1087 MB, in a process that is
# allowed 1 GB), so its limit is small. The other two are read in batches of ICEBERG_BATCH_ROWS rows and written a file at a
# time, so the memory they take does not depend on the file: 1.3 GB of rows took 131 seconds and peaked at 542 MB, and a
# Parquet file of 8 million rows peaked at 659 MB. Their limit is on time, at about 10 MB a second for JSON rows.
ICEBERG_MAX_BYTES = int(os.environ.get("MUNITAS_ICEBERG_MAX_BYTES", str(32 * 1024 * 1024)))
ICEBERG_STREAM_MAX_BYTES = int(os.environ.get("MUNITAS_ICEBERG_STREAM_MAX_BYTES", str(2 * 1024 ** 3)))
ICEBERG_BATCH_ROWS = int(os.environ.get("MUNITAS_ICEBERG_BATCH_ROWS", "20000"))
# One line of newline-delimited JSON may be no longer than this, so a file with no line breaks cannot fill memory.
ICEBERG_MAX_ROW_BYTES = int(os.environ.get("MUNITAS_ICEBERG_MAX_ROW_BYTES", str(8 * 1024 * 1024)))
# A Parquet file is read one row group at a time. A file whose row groups are larger than this (uncompressed, as its own
# footer states) is refused, because that is the amount that has to be held at once. The footer is also read with limits,
# since a hostile footer is the usual way to make a reader allocate without bound.
ICEBERG_PARQUET_MAX_ROW_GROUP_BYTES = int(os.environ.get("MUNITAS_ICEBERG_PARQUET_MAX_ROW_GROUP_BYTES", str(256 * 1024 * 1024)))
ICEBERG_PARQUET_THRIFT_STRING_BYTES = int(os.environ.get("MUNITAS_ICEBERG_PARQUET_THRIFT_STRING_BYTES", str(64 * 1024 * 1024)))
ICEBERG_PARQUET_THRIFT_CONTAINER_ITEMS = int(os.environ.get("MUNITAS_ICEBERG_PARQUET_THRIFT_CONTAINER_ITEMS", "1000000"))
# How long writing one table may take before it is given up. Every call that leaves this process needs a deadline.
ICEBERG_TIMEOUT_SECONDS = int(os.environ.get("MUNITAS_ICEBERG_TIMEOUT_SECONDS", "300"))

# How long a write grant keeps its key after the task last asked for it (grants._minted_identities). A task asks again at
# every step and every retry, so this has to outlast the longest gap between two asks and not the length of a run.
WRITE_GRANT_ACTIVE_SECONDS = int(os.environ.get("MUNITAS_WRITE_GRANT_ACTIVE_SECONDS", str(24 * 3600)))

# Table jobs (table_jobs.py). A table that is large is written by a worker, in a job, and not while a request waits.
# A seal that names a records file bigger than TABLE_JOB_INLINE_BYTES becomes a job (a caller can also ask for one, or
# refuse one, with table_mode). The limits a job works within are sent to the worker by the platform, so they are
# set here and nowhere else: a job is not tied to a request's deadline, so its size limit is far higher than the one
# a request has, and its time limit is the lifetime of its key.
TABLE_JOBS = os.environ.get("MUNITAS_TABLE_JOBS", "on").lower() != "off"
TABLE_JOB_INLINE_BYTES = int(os.environ.get("MUNITAS_TABLE_JOB_INLINE_BYTES", str(32 * 1024 * 1024)))
TABLE_JOB_MAX_BYTES = int(os.environ.get("MUNITAS_TABLE_JOB_MAX_BYTES", str(16 * 1024 ** 3)))
TABLE_JOB_TTL_SECONDS = int(os.environ.get("MUNITAS_TABLE_JOB_TTL_SECONDS", str(4 * 3600)))
# A job that has waited this long for a worker is reported on the housekeeping screen as stalled.
TABLE_JOB_STALL_SECONDS = int(os.environ.get("MUNITAS_TABLE_JOB_STALL_SECONDS", "600"))
TABLE_JOB_DISPATCH_SECONDS = int(os.environ.get("MUNITAS_TABLE_JOB_DISPATCH_SECONDS", "3"))
TABLE_SHARED_QUEUE = "munitas-table-write"


# A production start refuses the secrets this repository publishes. The storage keys the platform hands out (one per organisation, one per
# job, the catalog's rolling keys) are DERIVED from the storage administrator's secret, and the worker token is what lets a caller seal a
# version and run the platform's own endpoints. Both have a development value in this repository and in docker-compose.yml, which is
# right for a laptop and means that anybody who has read the source can compute every key if it is left in place. Set MUNITAS_ENV to
# production and the platform will not start until both are replaced. (It names the variables, never the values.)
if os.environ.get("MUNITAS_ENV", "").strip().lower() == "production":
    _published = [name for name, weak in (
        ("S3_ADMIN_SECRET", STORAGE_ADMIN[1] == "munitas-admin-secret"),
        ("MUNITAS_WORKER_TOKEN", not WORKER_TOKEN or WORKER_TOKEN == "dev-worker-token-not-for-production"),
    ) if weak]
    if _published:
        raise RuntimeError("MUNITAS_ENV is production, and these still hold the value published in this repository: "
                           + ", ".join(_published) + ". Set each to a secret of your own.")
