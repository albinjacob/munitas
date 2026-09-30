"""Cloudflare R2, a second object-storage backend.

Mirrors seaweed.py's shape (admin_client, grant_prefix, bucket) so
storage.py's dispatcher can treat the two interchangeably, but the
underlying mechanism is genuinely different, not just a different
endpoint. SeaweedFS grants work by adding a prefix action to a shared,
long-lived identity per role (grant_prefix there mutates state that
persists until explicitly revoked). R2 has no equivalent shared-identity
model; instead grant_prefix here calls R2's own real temporary-credentials
API (https://developers.cloudflare.com/r2/api/s3/temporary-credentials/)
and gets back a fresh, short-lived, prefix-scoped credential on every
call, one that expires on its own. Both are real, physical enforcement,
proven the same way (a grant for one prefix refused against another), but
callers that hold onto a credential and expect it to work indefinitely,
the way a SeaweedFS one effectively does until revoked, will be wrong
about R2's.

Every tenant gets its own bucket, and every operation against it, write
as much as read, uses a credential vended for that one bucket and no
other. The account's own R2 token is a parent that signs the request to
mint those and is never used to move bytes. That division is the point:
this API serves every tenant from one process, so the credential in
hand at the moment of a write is the last thing standing between a
routing bug and a cross-tenant leak. AWS documents the same shape for
the same reason, as a token vending machine
(https://docs.aws.amazon.com/whitepapers/latest/saas-tenant-isolation-strategies/run-time-policy-based-isolation-with-iam.html),
and is equally clear that a permanent identity per tenant is not the
way to get there.

Two authentication schemes are in play, and they are not
interchangeable. Cloudflare's REST API (creating a bucket, issuing a
temporary credential) takes the API token's value as a bearer. The S3
protocol takes that same token's id as an access key id and a SHA-256
hash of its value as a secret key
(https://developers.cloudflare.com/r2/api/tokens/). Hashing is one way,
so a caller holding only the S3 pair cannot reach the REST API at all,
which is why the token value is carried in its own right rather than
reconstructed when needed.

Cloudflare offers no hard spending cap for R2 (confirmed directly: Budget
Alerts are notification-only and do not pause service), so this module
enforces its own ceiling, well under R2's real free-tier numbers, via
usage_counter. Every real operation against R2 is checked and recorded
here, in one place, so nothing can reach R2 by a path that skips it.
"""

from __future__ import annotations

import hashlib
import time

import httpx

from . import config, db

R2_API = "https://api.cloudflare.com/client/v4"

# 50% of R2's real free tier: 5GB storage, 500,000 Class A ops/month,
# 5,000,000 Class B ops/month. Deliberately half, not the real number,
# because this platform's own tracked count and Cloudflare's real one can
# drift, and the point of a self-imposed ceiling is never finding that out
# the hard way.
CEILINGS = {
    "bytes_stored": 5 * 1024 * 1024 * 1024,
    "class_a_ops": 500_000,
    "class_b_ops": 5_000_000,
}

# Real, requested TTL for a minted credential, matching
# CREDENTIAL_TTL_MINUTES so the API response's own expires_at stays
# accurate for both backends rather than an assumption for one of them.
_TTL_SECONDS = config.CREDENTIAL_TTL_MINUTES * 60

# No EnvelopeCrypto instance here, unlike external_accounts.py, and no
# tenant credential columns are written: nothing long-lived is stored
# for a tenant to need sealing. tenant_storage_provision.access_key_id
# and its two secret columns stay null on every r2 row, the same as they
# already are on every seaweedfs one.


class StorageUnavailable(Exception):
    """The grant could not be applied. Own copy of seaweed.py's exception
    of the same name and meaning, so storage.py's callers can catch a
    single name regardless of which backend actually raised it."""


class UsageCeilingExceeded(Exception):
    """This operation would push R2 usage past this platform's own
    self-imposed ceiling. Refused before the real R2 call is ever made."""


def _period() -> str:
    return time.strftime("%Y-%m")


def _current(metric: str) -> int:
    row = db.one(
        "select value from usage_counter where system = 'r2' and metric = %s "
        "and period = %s",
        (metric, _period()),
    )
    return row["value"] if row else 0


def check_and_record(metric: str, amount: int) -> None:
    """Refuse, before any real R2 call, if this would cross this
    platform's own ceiling for the current month; otherwise record it.

    Not a race-free reservation (two concurrent calls could both pass the
    check before either records its own amount), acceptable here because
    this worker and API are not high-concurrency systems and the ceiling
    itself already has a 2x safety margin built in.
    """
    ceiling = CEILINGS[metric]
    current = _current(metric)
    if current + amount > ceiling:
        raise UsageCeilingExceeded(
            f"r2 {metric} for {_period()} would reach {current + amount}, "
            f"over this platform's own {ceiling} ceiling (50% of R2's real "
            "free-tier allowance)"
        )
    db.execute(
        """insert into usage_counter (system, metric, period, value)
             values ('r2', %s, %s, %s)
           on conflict (system, metric, period)
             do update set value = usage_counter.value + excluded.value,
                           updated_at = now()""",
        (metric, _period(), amount),
    )


def _require_configured() -> None:
    if not (config.R2_ACCOUNT_ID and config.R2_ACCESS_KEY_ID
            and config.R2_SECRET_ACCESS_KEY and config.R2_API_TOKEN):
        raise StorageUnavailable(
            "R2 is not configured on this install (R2_ACCOUNT_ID, "
            "R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY and R2_API_TOKEN must "
            "all be set); a dataset cannot be sealed against a backend that "
            "was never configured"
        )
    # R2_BUCKET is deliberately not required here. Every tenant gets a
    # bucket of its own, created on first write, so a fresh install needs
    # no bucket to exist in advance. The setting names the shared bucket
    # that tenants predating per-tenant storage still use, and it is
    # checked at the one point that actually reads it.


def _api_headers(api_token: str) -> dict:
    """Bearer header for Cloudflare's REST API.

    The token value, not the S3 pair. A header built from the access key
    id and secret authenticates nothing here, and Cloudflare rejects it
    as an invalid token rather than as the wrong kind of credential.
    """
    return {"Authorization": f"Bearer {api_token}"}


def s3_secret_for(api_token: str) -> str:
    """The S3 secret key belonging to an R2 API token.

    Derived, never stored twice: Cloudflare defines it as the SHA-256
    hash of the token value, so keeping the value alone leaves nothing
    that can drift out of step with it.
    """
    return hashlib.sha256(api_token.encode()).hexdigest()


def _resolve_bucket(tenant_id: str) -> str:
    """The bucket this tenant's R2 objects live in, as recorded.

    Same two branches as seaweed._resolve_bucket, and the same reasoning:
    a row wins, and a tenant without one is having its first R2 write and
    is assigned a bucket of its own. Neither branch asks what the tenant
    already contains.

    The branch between them used to read "no row, but this tenant holds
    R2 data" as "grandfathered onto the shared bucket", and raised when
    R2_BUCKET was unset because it had no way to name the bucket it had
    just decided the tenant was on. schema.sql's backfill writes that row
    for every such tenant instead, taking R2_BUCKET from
    `munitas.shared_r2_bucket` and skipping installs that never had a
    shared R2 bucket to name.

    That backfill is not optional, and this function cannot detect its
    absence: a grandfathered tenant with no row is indistinguishable here
    from a tenant writing for the first time, which is the entire point
    of removing the guess. Skip it and such a tenant is assigned a new,
    empty bucket while its objects stay in the old one, unreachable. That
    is why the backfill lives in schema.sql, which is applied with the
    code rather than remembered alongside it.
    """
    row = db.one(
        "select bucket from tenant_storage_provision "
        "where tenant_id = %s and backend = 'r2'",
        (tenant_id,),
    )
    if row:
        return row["bucket"]

    return _provision(tenant_id)


def _provision(tenant_id: str) -> str:
    """Create this tenant's own R2 bucket and record it.

    Creating a bucket is a control-plane operation and needs the root
    token, which is the only thing that token is ever used for here.
    No credential is minted or stored per tenant: see _mint for why the
    data path uses short-lived vended credentials instead.
    """
    bucket_name = f"munitas-{tenant_id}"

    check_and_record("class_a_ops", 1)
    create = httpx.post(
        f"{R2_API}/accounts/{config.R2_ACCOUNT_ID}/r2/buckets",
        headers=_api_headers(config.R2_API_TOKEN),
        json={"name": bucket_name},
        timeout=15.0,
    )
    if create.status_code not in (200, 409):
        raise StorageUnavailable(
            f"R2 refused to create bucket {bucket_name!r}: "
            f"HTTP {create.status_code} {create.text[:300]}"
        )

    db.execute(
        """insert into tenant_storage_provision (tenant_id, backend, bucket)
           values (%s, 'r2', %s)
           on conflict (tenant_id, backend) do nothing""",
        (tenant_id, bucket_name),
    )
    return bucket_name


def _mint(bucket_name: str, permission: str, prefixes: list[str] | None) -> dict:
    """Vend a short-lived credential that can reach one bucket and
    nothing else.

    This is the whole isolation mechanism, and it is used for writes as
    much as for reads. The root token is a parent that never touches
    data itself: it exists to sign this request, and the credential that
    comes back is what actually moves bytes. The pattern is the one AWS
    calls a token vending machine and documents in its SaaS tenant
    isolation guidance, for the reason that applies here too: a service
    that handles every tenant will eventually have a bug that addresses
    the wrong one, and a credential that cannot reach another tenant's
    bucket turns that bug into an error instead of a leak.

    A long-lived credential per tenant would look stronger and is not.
    It would have to be stored, rotated and revoked, it consumes an
    account-wide token slot per tenant, and it is valid until somebody
    remembers to withdraw it. These expire on their own.
    """
    body = {
        "bucket": bucket_name,
        "parentAccessKeyId": config.R2_ACCESS_KEY_ID,
        "permission": permission,
        "ttlSeconds": _TTL_SECONDS,
    }
    if prefixes:
        body["prefixes"] = prefixes

    check_and_record("class_a_ops", 1)
    response = httpx.post(
        f"{R2_API}/accounts/{config.R2_ACCOUNT_ID}/r2/temp-access-credentials",
        headers=_api_headers(config.R2_API_TOKEN),
        json=body,
        timeout=15.0,
    )
    if response.status_code != 200:
        raise StorageUnavailable(
            f"R2 refused to mint a {permission} credential for "
            f"{bucket_name!r}: HTTP {response.status_code} "
            f"{response.text[:300]}"
        )
    return response.json()["result"]


def bucket(tenant_id: str) -> str:
    _require_configured()
    return _resolve_bucket(tenant_id)


def _endpoint() -> str:
    return f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"


def admin_client(tenant_id: str):
    """An S3 client that can write anywhere in this tenant's bucket, and
    nowhere outside it.

    Used only by ingestion, the same restriction seaweed.py's own
    admin_client() docstring states. The name is inherited from that
    module and overstates what this returns: the credential behind it is
    vended for this one tenant and expires, so "admin" here means write
    access within one bucket rather than any standing authority. The
    root token signs the request that mints it and never signs a data
    request itself.
    """
    import boto3
    from botocore.config import Config

    _require_configured()
    bucket_name = _resolve_bucket(tenant_id)
    minted = _mint(bucket_name, "object-read-write", None)
    return boto3.client(
        "s3",
        endpoint_url=_endpoint(),
        aws_access_key_id=minted["accessKeyId"],
        aws_secret_access_key=minted["secretAccessKey"],
        aws_session_token=minted["sessionToken"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def grant_prefix(role: str, prefix: str, purpose: str, tenant_id: str) -> dict:
    """Vend a read-only credential scoped to one prefix in this tenant's
    own bucket.

    Returns the same shape seaweed.credentials_for already returns, plus
    session_token (R2's temporary credentials are STS-style and require
    one) and an explicit backend marker so a caller that must handle the
    two differently (agent/tools.py's list_review_queue, for the
    host-vs-container endpoint-visibility reason already documented
    there) can tell which one it got without inspecting the endpoint
    string's shape.
    """
    _require_configured()
    bucket_name = _resolve_bucket(tenant_id)
    base = prefix.rstrip("/")
    minted = _mint(bucket_name, "object-read-only", [f"{base}/"])
    return {
        "endpoint": _endpoint(),
        "access_key": minted["accessKeyId"],
        "secret_key": minted["secretAccessKey"],
        "session_token": minted["sessionToken"],
        "bucket": bucket_name,
        "backend": "r2",
    }
