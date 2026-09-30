"""Prefix-scoped S3 grants.

This module is what turns an availability class from a label into an enforced
boundary. A role holds no access to a version's objects until the control plane
adds a prefix-scoped action to its identity, and SeaweedFS then returns 403 to
anything outside that prefix. V4 depends on this being real rather than advisory.

The grant is a string like `Read:munitas/t1/<dataset>/v3`. Prefixes encode
tenant, dataset and version and never the class, so promoting a version adds a
grant instead of copying bytes. That is what V3 asserts.

SeaweedFS keeps its identity document in the filer namespace at
/etc/iam/identity.json and reloads it, so the grant takes effect without a
restart.
"""

from __future__ import annotations

import json

import httpx

from . import config, db, logs

log = logs.get_logger("storage")

_IAM_PATH = "/etc/iam/identity.json"


class StorageUnavailable(Exception):
    """The grant could not be applied.

    Raised rather than returning a credential anyway. A credential handed out
    while its scope failed to apply is either useless or unbounded, and the
    caller cannot tell which.
    """


def _url() -> str:
    return f"{config.FILER_URL}{_IAM_PATH}"


def load_identities() -> dict:
    try:
        response = httpx.get(_url(), timeout=5.0)
        if response.status_code == 404:
            return {"identities": []}
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise StorageUnavailable(f"cannot read identity config: {exc}") from exc


def save_identities(doc: dict) -> None:
    try:
        response = httpx.post(
            _url(),
            files={"file": ("identity.json", json.dumps(doc, indent=2))},
            timeout=5.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise StorageUnavailable(f"cannot write identity config: {exc}") from exc


def grant_prefix(role: str, prefix: str, tenant_id: str) -> str:
    """Name the read grant a role gets on one version prefix, in this
    tenant's own bucket. grants._prefix_actions is what compiles it.

    The trailing `/*` is load-bearing. SeaweedFS treats `Read:bucket/prefix` as
    naming that one path and not the objects beneath it, so a grant without the
    wildcard reads as complete, appears in the identity document, and still
    refuses every `GetObject` under it with AccessDenied, which looks like a
    missing grant rather than a malformed one.

    That was live for as long as prefix grants have existed and went unnoticed
    because the role V4 grants to, `pipeline_action`, also holds a bucket-wide
    `Read:munitas` from the bootstrap identities, so its read succeeded on the
    broad grant and never exercised the narrow one. The first caller to hold
    only a prefix grant, the agent runtime, is what surfaced it.

    Both forms are added. The bare prefix is what a `ListObjectsV2` on the
    prefix itself matches; the wildcard is what the objects under it need.
    """
    # Nothing is written here. The grant exists once the register justifies
    # it (an allowed access_decision, or a lease) and grants.reconcile()
    # compiles the document, which the credential path does before it
    # answers. This only checks the role can hold a key and names the grant.
    if role not in config.ROLE_STORAGE_KEYS:
        raise StorageUnavailable(_no_identity_message(role))
    bucket_name = _resolve_bucket(tenant_id)
    return f"Read:{bucket_name}/{prefix.rstrip('/')}/*"


def grant_prefix_write(role: str, prefix: str, tenant_id: str) -> str:
    """Name the write grant a role gets on one version prefix, in this
    tenant's own bucket. grants._write_prefix_actions is what compiles it.

    Mirrors grant_prefix above exactly, verb for verb: nothing is written to
    SeaweedFS here either. The grant exists once the register justifies it (a
    write_grant row) and grants.reconcile() compiles the document; this only
    checks the role can hold a key and names the grant.
    """
    if role not in config.ROLE_STORAGE_KEYS:
        raise StorageUnavailable(_no_identity_message(role))
    bucket_name = _resolve_bucket(tenant_id)
    return f"Write:{bucket_name}/{prefix.rstrip('/')}/*"


# revoke_prefix used to live here: read the document, drop the action whose
# scope matched f"{config.BUCKET}/{prefix}", write it back. Deleted rather
# than repaired, for two reasons that point the same way. Nothing ever called
# it, which grants.py's own header records as one of the three faults that
# made the document a projection instead of something edited in place: "a
# lease's expiry withdrew nothing, because withdrawing is a separate act and
# revoke_prefix was never called by anything". And it named the shared bucket,
# so for any tenant with a bucket of its own it would have matched nothing
# even if something had called it. grants.reconcile() is what withdraws now,
# by recomputing the whole document from the register.


# Roles held by people rather than by workloads. They have no S3 identity, and
# that is the design rather than an omission: humans reach data through a
# workspace that holds the credential, never by holding one themselves:
# humans stay off the data plane.
#
# Named here so the refusal can say why. "No S3 identity named analyst" reads
# like a missing config entry somebody should add, which would be exactly the
# wrong fix.
HUMAN_ROLES = {"analyst", "notebook_explore"}


def _no_identity_message(role: str) -> str:
    if role in HUMAN_ROLES:
        return (
            f"the role {role!r} is held by people, and people are not issued "
            "data-plane credentials directly. Access goes through a workspace "
            "such as the annotation tool or a notebook, which holds the "
            "credential on the person's behalf"
        )
    return (
        f"no storage key for the role {role!r}. The roles that hold one are "
        "storage_roles in platform/policy/access.rego, with their keys in "
        "platform/api/app/config.py"
    )


def _resolve_bucket(tenant_id: str) -> str:
    """The bucket this tenant's SeaweedFS objects live in, as recorded.

    Two branches, both deterministic. A tenant with a row in
    tenant_storage_provision gets that exact bucket. A tenant without one
    is having its first write here and is assigned a bucket of its own,
    now, and the assignment is recorded before anything is written to it.

    There used to be a third branch between them: no row, but the tenant
    holds data on this backend, therefore it predates per-tenant buckets
    and belongs on config.BUCKET. That answer was worked out from the
    order two unrelated things happened in rather than read from
    anywhere, and it was wrong whenever a tenant acquired a
    dataset_version row before anything first asked which bucket it used.
    `finance` was pinned to the shared bucket by it weeks after per-tenant
    buckets existed, because its seed registers versions with a declared
    manifest rather than uploading. Every tenant that branch used to
    answer for now carries a row saying the same thing, backfilled by
    schema.sql, so nothing here has to infer it again.
    """
    row = db.one(
        "select bucket from tenant_storage_provision "
        "where tenant_id = %s and backend = 'seaweedfs'",
        (tenant_id,),
    )
    if row:
        return row["bucket"]

    return _provision_bucket(tenant_id)


def _provision_bucket(tenant_id: str) -> str:
    """Create this tenant's own SeaweedFS bucket and record it.

    Idempotent against a race: if the bucket already exists (another
    request provisioned it a moment ago), SeaweedFS's own
    BucketAlreadyOwnedByYou/BucketAlreadyExists is treated as success,
    and the insert's own ON CONFLICT DO NOTHING means the second writer
    never overwrites the first writer's row.
    """
    from botocore.exceptions import ClientError

    bucket_name = f"munitas-{tenant_id}"
    client = _admin_boto_client()
    try:
        client.create_bucket(Bucket=bucket_name)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise StorageUnavailable(
                f"could not create SeaweedFS bucket {bucket_name!r}: {exc}"
            ) from exc

    _reserve_volumes(tenant_id, bucket_name)

    db.execute(
        """insert into tenant_storage_provision (tenant_id, backend, bucket)
             values (%s, 'seaweedfs', %s)
           on conflict (tenant_id, backend) do nothing""",
        (tenant_id, bucket_name),
    )
    # The roles' standing access to this bucket exists once the document is
    # compiled with it in the register, which is now. A failed print is
    # recorded and retried by the activator; the caller that needs the
    # access to be in effect (admin_client) checks for itself.
    from . import grants
    try:
        grants.reconcile(trigger="provision")
    except Exception as exc:
        log.warning("bucket created, storage permissions not yet updated",
                    extra={"tenant_id": tenant_id, "bucket": bucket_name,
                           "reason": str(exc)})
    return bucket_name


def _reserve_volumes(tenant_id: str, bucket_name: str) -> None:
    """Ask the storage master for this tenant's opening volumes.

    A volume is a 1 GB file SeaweedFS appends many objects into, and volumes
    are never shared between tenant buckets, so how many a tenant opens with
    is a real decision rather than a tuning detail. The number is this
    tenant's own `initial_storage_volumes` when onboarding set one, and
    otherwise the install's default.

    A failure here degrades rather than raises, and says so. SeaweedFS
    creates volumes on the first write whether or not this call succeeds,
    so the tenant opens with the batch size from master.toml instead of the
    number asked for: a worse starting allocation, not a broken tenant.
    Failing the write that triggered provisioning would be a much larger
    consequence than the one being avoided.

    That is only a defensible trade because the warning below is now
    somewhere. Until this package had a logger the same code would have
    degraded in silence, which is the shape of every storage bug this
    platform has paid for: the reclaimer freeing nothing for a month while
    reporting success, fixtures writing to a bucket nothing read.
    """
    row = db.one(
        "select initial_storage_volumes from tenant where id = %s", (tenant_id,)
    )
    wanted = (row or {}).get("initial_storage_volumes") or config.STORAGE_VOLUMES_PER_TENANT
    try:
        response = httpx.get(
            f"{config.MASTER_URL}/vol/grow",
            params={"collection": bucket_name, "count": wanted,
                    "replication": "000"},
            timeout=20.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning(
            "could not reserve the storage volumes this tenant asked for; it "
            "opens with the batch size from master.toml instead",
            extra={"tenant_id": tenant_id, "bucket": bucket_name,
                   "volumes_wanted": wanted, "reason": str(exc)},
        )
        return
    log.info("tenant storage provisioned",
             extra={"tenant_id": tenant_id, "bucket": bucket_name,
                    "volumes_reserved": wanted})


def bucket(tenant_id: str) -> str:
    return _resolve_bucket(tenant_id)


def _admin_boto_client():
    """The actual client construction. Kept private and separate from the
    public admin_client() below so provisioning a bucket, which itself
    needs a raw admin client, does not recurse through the tenant-
    resolving public one.
    """
    import boto3
    from botocore.config import Config

    # From configuration, never read back out of the document it helps
    # to provision.
    access_key, secret_key = config.STORAGE_ADMIN

    return boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def admin_client(tenant_id: str):
    """An S3 client scoped to this tenant's own bucket.

    Provisions the bucket on first use, same as before. The client is built
    from this tenant's own standing ingest identity rather than the platform
    super-key, so a leaked credential from this path exposes one tenant's
    bucket, never another's. munitas-admin now backs only bucket creation,
    in _provision_bucket, which this function still reaches indirectly
    through _resolve_bucket.

    Minting the identity only inserts its row in Postgres; it does not exist
    to SeaweedFS until a print that started after it succeeds. So the record
    is checked first: an organisation whose key is already active, which is
    every upload after its first, goes ahead without printing anything. Only
    a key not yet active prints, and if that print fails the upload is
    refused with a message saying so, because a client handed back now would
    fail every write with InvalidAccessKeyId. The activator finishes the job,
    and the same upload succeeds once it has.
    """
    import boto3
    from botocore.config import Config

    from . import grants

    # Resolved first for what it does on a first write: creates the bucket
    # the identity below is then granted.
    _resolve_bucket(tenant_id)
    identity = grants.identity_for_tenant_ingest(tenant_id)
    if not grants.ingest_is_active(tenant_id):
        try:
            grants.reconcile(trigger="provision")
        except Exception as exc:
            raise StorageUnavailable(
                "This organisation's storage is being set up. Try again shortly."
            ) from exc
    return boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT,
        aws_access_key_id=identity["access_key"],
        aws_secret_access_key=identity["secret_key"],
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def credentials_for(role: str, tenant_id: str) -> dict:
    """Return the access key pair for a role, scoped to this tenant's bucket.

    These are the roles' static keys, from configuration, not short-lived STS tokens. SeaweedFS
    has no STS, so expiry is enforced by the control plane revoking the grant
    rather than by the credential itself going stale. The write-up must say so:
    a leaked key stays valid until its prefix grant is withdrawn.
    """
    if role not in config.ROLE_STORAGE_KEYS:
        raise StorageUnavailable(_no_identity_message(role))
    access_key, secret_key = config.ROLE_STORAGE_KEYS[role]
    return {
        "endpoint": config.S3_ENDPOINT,
        "access_key": access_key,
        "secret_key": secret_key,
        "bucket": _resolve_bucket(tenant_id),
    }
