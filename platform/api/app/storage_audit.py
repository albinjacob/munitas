"""Does storage agree with the register?

Two independent records of the same thing: what the database says exists, and
what is actually sitting in object storage and the storage identity document.
Each is written by a different code path, so they drift apart silently when a
path that creates something has no matching path that ends it. Three such
gaps were found on one day: pipeline runs that never ended, storage keys that
outlived their organisation, and files that outlived their dataset. Each was
invisible to every check that looked at only one side.

Ownership is read from the records that own things, never from the shape of a
key. A key is a naming convention; the rows are the facts:

  * an operator pipeline's code is owned by the pipeline_version row whose
    code_object_key names it exactly, and an agent's code the same way;
  * a dataset's files are owned by the dataset if they sit under one of its
    versions' storage_prefix, or under the prefix its next version would use
    (versions.next_version, the same function the upload path calls), which
    is where uploads wait before they are sealed.

Anything else in an organisation's bucket has no owner. So does a bucket no
existing organisation records, and a storage identity that is neither a role
from the bootstrap document nor backed by a storage_identity row.

Only SeaweedFS is read. A Cloudflare R2 bucket is real external storage and is
not listed here.
"""

from __future__ import annotations

from . import db, seaweed, versions


def _owned(tenant_id: str) -> tuple[set[str], list[str]]:
    """Exact keys and key prefixes this organisation's records own."""
    keys = {
        r["code_object_key"] for r in db.all_rows(
            """select pv.code_object_key from pipeline_version pv
                 join pipeline p on p.id = pv.pipeline_id
                where p.tenant_id = %s and pv.code_object_key is not null""",
            (tenant_id,))
    }
    keys |= {
        r["code_object_key"] for r in db.all_rows(
            "select code_object_key from agent_version "
            "where tenant_id = %s and code_object_key is not null",
            (tenant_id,))
    }
    prefixes = [r["storage_prefix"] for r in db.all_rows(
        "select storage_prefix from dataset_version where tenant_id = %s", (tenant_id,))]
    prefixes += [
        versions.next_version(tenant_id, str(r["id"]))["storage_prefix"]
        for r in db.all_rows("select id from dataset where tenant_id = %s", (tenant_id,))
    ]
    return keys, [p.rstrip("/") + "/" for p in prefixes]


def _objects(client, bucket: str) -> list[dict]:
    found, token = [], None
    while True:
        kwargs = {"Bucket": bucket}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        found.extend(page.get("Contents", []))
        if not page.get("IsTruncated"):
            return found
        token = page.get("NextContinuationToken")


def audit(*, scratch_files: bool = True) -> dict:
    """Everything in storage that no record owns.

    {"buckets": [bucket, ...],
     "objects": [{"bucket", "key", "bytes"}, ...],
     "identities": [name, ...]}

    scratch_files=False leaves out loose files inside a `scratch` tenant's
    bucket. The suite writes those on purpose (U56 and U65 put a probe object
    straight into a fresh bucket to prove who can reach it) and removes the
    tenant and its bucket whole afterwards, so while the suite runs they are
    expected rather than leaked. Their buckets are still checked either way.
    """
    client = seaweed._admin_boto_client()
    tenants = {r["id"] for r in db.all_rows("select id from tenant")}
    scratch = {r["id"] for r in db.all_rows("select id from tenant where purpose = 'scratch'")}
    provisioned = {
        r["bucket"]: r["tenant_id"] for r in db.all_rows(
            "select bucket, tenant_id from tenant_storage_provision where backend = 'seaweedfs'")
    }

    orphan_buckets, orphan_objects = [], []
    for b in (x["Name"] for x in client.list_buckets().get("Buckets", [])):
        owner = provisioned.get(b)
        if owner not in tenants:
            orphan_buckets.append(b)
            continue
        if not scratch_files and owner in scratch:
            continue
        keys, prefixes = _owned(owner)
        for o in _objects(client, b):
            k = o["Key"]
            if k in keys or any(k.startswith(p) for p in prefixes):
                continue
            orphan_objects.append({"bucket": b, "key": k, "bytes": o["Size"]})

    rows = {r["identity_name"] for r in db.all_rows("select identity_name from storage_identity")}
    rows |= {r["identity_name"] for r in db.all_rows("select identity_name from catalog_key")}
    from . import grants
    roles = grants.identity_names()
    orphan_identities = sorted(
        i["name"] for i in seaweed.load_identities().get("identities", [])
        if i["name"] not in rows and i["name"] not in roles
    )
    return {"buckets": orphan_buckets, "objects": orphan_objects,
            "identities": orphan_identities}
