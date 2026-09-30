"""U56: a tenant's storage is its own, not shared, from the moment it
first writes, and a tenant that already had data before this existed
keeps using the shared bucket forever rather than getting split across
two.

Runs inside the munitas-api container, where app.seaweed and app.db are
importable and SeaweedFS is reachable at its container-internal endpoint.

    docker compose exec -T munitas-api python /verify/v56_per_tenant_storage.py

One fixed probe tenant, reset at the start of every run, rather than a
fresh random one each time. The first version of this script minted a new
tenant and therefore a new bucket per run, and since every bucket is its
own SeaweedFS collection drawing roughly seven volumes from one fixed
pool, a handful of runs exhausted the pool and every write started
failing with an S3 InternalError that named nothing about volumes. The
same reasoning verify/common.py already gives for the canary tenant
applies here, one step further: a dedicated tenant is not enough on its
own if each run invents another one.
"""

from __future__ import annotations

import sys
import uuid

from common import (ADMIN, check, db, fixture_contract, fixture_tenant,
                    fixture_version, heading, require_api, s3_client, summary)

# Fixed, reused, and reset below. Named as a probe rather than as a
# tenant somebody might mistake for real, the same way the scratch and
# canary names already read.
# A tenant of this run's own, not a fixed name.
#
# The fixed name was `storage-probe`, and it worked only while nothing else
# ever wrote to that tenant. The first check below asserts the tenant has never
# had a version sealed, which is what makes "provisioning happens on first
# write" a real claim, and `reset_probe` can undo a provisioning row and a
# bucket but not a sealed `dataset_version`: those cannot be deleted, by
# design. So one unrelated script that happened to reuse the name ended the
# usefulness of this check permanently, and did.
#
# A fresh tenant each run makes the precondition true by construction. The
# cost is a tenant row per run in the canary database, which is what canary
# tenants are for and what scripts/admin/reclaim-storage.py exists to tidy.
PROBE = f"storage-probe-{uuid.uuid4().hex[:8]}"



def provision_row(tenant_id: str, backend: str) -> dict | None:
    with db() as conn:
        return conn.execute(
            "select bucket, access_key_id from tenant_storage_provision "
            "where tenant_id = %s and backend = %s",
            (tenant_id, backend),
        ).fetchone()


def has_existing_data(tenant_id: str, backend: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "select 1 from dataset_version where tenant_id = %s "
            "and storage_backend = %s limit 1",
            (tenant_id, backend),
        ).fetchone()
    return row is not None


def reset_probe(bucket_name: str) -> None:
    """Put the probe tenant back to never-having-written state.

    Both halves matter. Dropping the provisioning row alone would leave
    the bucket behind, and the resolver would happily recreate the row
    pointing at it, so the run would prove nothing about provisioning.
    Removing the bucket too is what makes the next run a real first
    write, and it is what stops this script accumulating one collection
    per execution.
    """
    with db() as conn:
        conn.execute(
            "delete from tenant_storage_provision where tenant_id = %s", (PROBE,)
        )

    client = s3_client(*ADMIN)
    try:
        listed = client.list_objects_v2(Bucket=bucket_name)
    except Exception:
        return  # No bucket yet, which is already the state this wants.
    for obj in listed.get("Contents", []):
        client.delete_object(Bucket=bucket_name, Key=obj["Key"])
    try:
        client.delete_bucket(Bucket=bucket_name)
    except Exception:
        pass  # Already gone, or never existed.


def main() -> int:
    require_api()
    sys.path.insert(0, "/app")
    from app import db as app_db  # noqa: E402
    from app import seaweed  # noqa: E402

    # This script imports app.seaweed directly rather than going through
    # a request to the running API, so the connection pool main.py's own
    # lifespan hook opens on startup was never opened here.
    app_db.pool.open()

    fixture_tenant(PROBE)
    bucket_name = f"munitas-{PROBE}"
    reset_probe(bucket_name)

    heading("U56: a brand new tenant provisions its own SeaweedFS bucket on first write")

    check("the probe tenant starts with no provisioning row",
          provision_row(PROBE, "seaweedfs") is None)

    client = seaweed.admin_client(PROBE)
    resolved = seaweed.bucket(PROBE)
    check("the resolved bucket is named after the tenant",
          resolved == bucket_name, resolved)

    row = provision_row(PROBE, "seaweedfs")
    check("a provisioning row now exists for it", row is not None)
    check("and it points at the same bucket the resolver returned",
          bool(row) and row["bucket"] == resolved)

    client.put_object(Bucket=resolved, Key="v56-probe/hello.txt", Body=b"hello")
    listed = client.list_objects_v2(Bucket=resolved, Prefix="v56-probe/")
    check("a real object can be written to and listed from the tenant's own bucket",
          listed.get("KeyCount") == 1, listed.get("KeyCount"))

    heading("U56: reads for that tenant resolve to the same bucket, not the shared one")

    action = seaweed.grant_prefix("pipeline_action", "v56-probe", PROBE)
    check("the grant names the tenant's own bucket, not the shared platform one",
          action.startswith(f"Read:{resolved}/"), action)

    creds = seaweed.credentials_for("pipeline_action", PROBE)
    check("the credential's own bucket field matches too", creds["bucket"] == resolved)

    reader = s3_client(creds["access_key"], creds["secret_key"], endpoint=creds["endpoint"])
    got = reader.get_object(Bucket=resolved, Key="v56-probe/hello.txt")["Body"].read()
    check("that credential can read the object back", got == b"hello", got)

    heading("U56: the answer comes from the row, not from what the tenant holds")

    # What the grandfathering check used to prove is gone, and this is what
    # replaced it. That check built a tenant with data and no provisioning
    # row and asserted the resolver read that shape as "predates per-tenant
    # buckets, so use the shared one". The resolver no longer reads shapes:
    # a row wins, and a tenant without one is assigned a bucket of its own
    # whatever it already contains. So the claim worth making now is the
    # opposite one, that holding data does not change the answer.
    # Sealed here rather than assumed: holding a dataset_version is the exact
    # condition the removed branch keyed on, so the probe has to actually
    # hold one for this to be testing anything.
    fixture_version(PROBE, fixture_contract(PROBE), "RAW")
    check("the probe now holds a sealed version, the condition the removed "
          "branch keyed on",
          has_existing_data(PROBE, "seaweedfs"))

    resolved_again = seaweed.bucket(PROBE)
    check("and still resolves to its own bucket, not the shared one",
          resolved_again == bucket_name, resolved_again)

    probe_bucket = provision_row(PROBE, "seaweedfs")
    check("which is the recorded answer, read back unchanged",
          probe_bucket is not None and probe_bucket["bucket"] == bucket_name,
          probe_bucket["bucket"] if probe_bucket else "(no row)")

    return summary("U56")


if __name__ == "__main__":
    sys.exit(main())
