"""Free the object storage held by a canary or retired tenant, and keep the record.

Why this exists
---------------
Verification runs accumulate. After a few weeks the platform held 81 dataset
versions whose metadata came to 9 MB and whose objects came to 502 MB, so the
bytes are the problem and the rows are not. Roughly 100 kB of rows per version
means even a hundred thousand versions would be a few hundred megabytes of
metadata.

Deleting the versions is not an option and never will be. A sealed version
cannot be removed: `delete` against one returns `DELETE 0` because a rewrite rule
in the schema turns it into a no-op. That is not an obstacle to work around, it
is the guarantee V1 verifies. A version that could be deleted by whoever could
reach the database is not an immutable version, and a lineage record that can be
quietly removed proves nothing about what happened.

So this frees the bytes and leaves everything else standing. The version row, its
lineage, its class history and every access decision ever made about it all
survive. What goes is the files.

That is the same move the platform already makes for erasure. Deleting a record
destroys its key and keeps its tombstone: the data becomes unreadable, the fact
that it existed does not. Here the data becomes unavailable and the fact that it
existed does not.

What this script cannot do
--------------------------
It contains no `delete` against any table in Postgres. That is deliberate and it
is tested: U31 reads this file and asserts the absence, because code that can
delete sealed versions is dangerous no matter how carefully it is guarded, and a
guard is one line somebody can edit.

It also cannot touch a tenant whose purpose is `production`. There are two
independent reasons it cannot: the selection query only looks at canary and
retired tenants, and a named tenant is checked before anything runs. Either alone
would be a single point of failure.

Usage
-----
    python scripts/admin/reclaim-storage.py --dry-run
    python scripts/admin/reclaim-storage.py
    python scripts/admin/reclaim-storage.py --tenant t1 --older-than 0 --reason "closing the old tenant"

Canary tenants are swept by default, oldest first, and `--older-than` keeps
recent runs intact so a failure can still be investigated. A retired tenant is
never swept: winding down a closed organisation is a decision somebody makes
once, so it has to be named with `--tenant`.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3
import httpx
import psycopg
from botocore.config import Config
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ports_config import PORTS  # noqa: E402

PG_DSN = os.environ.get(
    "PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform"
)
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", f"http://localhost:{PORTS['seaweedfs_s3']}")
# Deleting an object in SeaweedFS marks it, it does not shrink the volume the
# object sits in. The space comes back when the volume is vacuumed.
#
# This is not a detail that can be left to whoever runs the script. Without the
# vacuum, deleting 662 objects moved the data directory from 502 MB to 502 MB,
# so a script that reported "freed 613 MB" and stopped there would have been
# stating something the disk flatly contradicted.
MASTER = os.environ.get("MUNITAS_SEAWEED_MASTER", f"http://localhost:{PORTS['seaweedfs_master']}")

# The control plane's own identity. Reclamation is an administrative act on
# storage the platform owns, not a read of anybody's data, so it does not go
# through the credential path.
ADMIN_KEY = os.environ.get("S3_ADMIN_KEY", "munitas-admin")
ADMIN_SECRET = os.environ.get("S3_ADMIN_SECRET", "munitas-admin-secret")

RECLAIMABLE = ("canary", "retired")


def s3():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=ADMIN_KEY,
        aws_secret_access_key=ADMIN_SECRET,
        config=Config(signature_version="s3v4", retries={"max_attempts": 2}),
        region_name="us-east-1",
    )


def eligible(conn, tenant: str | None, older_than: int) -> list[dict]:
    """Versions whose objects may be freed.

    The tenant filter is inside the query rather than applied afterwards. A
    production tenant is not selected, filtered out and then skipped: it is never
    returned in the first place, so a bug in the loop below cannot reach one.

    A version already recorded in `storage_reclamation` is excluded, which makes
    running this twice harmless.
    """
    sql = """
        select dv.id, dv.tenant_id, dv.storage_prefix, dv.created_at,
               d.name as dataset_name, dv.version
        from dataset_version dv
        join dataset d on d.id = dv.dataset_id
        join tenant t   on t.id = dv.tenant_id
        left join storage_reclamation sr on sr.dataset_version_id = dv.id
        where t.purpose = any(%(reclaimable)s)
          and sr.dataset_version_id is null
          and dv.created_at < now() - make_interval(days => %(days)s)
          and (%(tenant)s::text is null or dv.tenant_id = %(tenant)s)
          -- A retired tenant is only ever swept when it is named. Winding down a
          -- closed organisation is somebody's decision, not a scheduled job's.
          and (t.purpose = 'canary' or %(tenant)s::text is not null)
        order by dv.created_at asc
    """
    return conn.execute(sql, {
        "reclaimable": list(RECLAIMABLE), "days": older_than, "tenant": tenant,
    }).fetchall()


def bucket_for(conn, tenant_id: str) -> str:
    """The bucket this tenant's objects are actually in.

    Reads the same row platform/api/app/seaweed.py's _resolve_bucket reads.
    Naming one bucket for every tenant, as this script used to, lists an
    empty prefix for anyone with a bucket of their own, finds nothing, and
    writes a reclamation row saying zero bytes came back. That reads as
    "already tidy" rather than as "looked in the wrong place", which is how
    it freed nothing for over a month while reporting success.
    """
    row = conn.execute(
        "select bucket from tenant_storage_provision "
        "where tenant_id = %s and backend = 'seaweedfs'",
        (tenant_id,),
    ).fetchone()
    if not row:
        raise SystemExit(
            f"no seaweedfs bucket recorded for tenant {tenant_id!r}; refusing "
            "to guess which bucket its objects are in rather than report "
            "freeing bytes that were never looked at"
        )
    return row["bucket"]


def objects_under(client, bucket: str, prefix: str) -> list[dict]:
    found: list[dict] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix.rstrip("/") + "/"}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        found.extend(page.get("Contents", []))
        if not page.get("IsTruncated"):
            return found
        token = page.get("NextContinuationToken")


def check_tenant(conn, tenant: str) -> str:
    row = conn.execute(
        "select purpose from tenant where id = %s", (tenant,)
    ).fetchone()
    if not row:
        sys.exit(f"no tenant {tenant!r}")
    if row["purpose"] not in RECLAIMABLE:
        sys.exit(
            f"tenant {tenant!r} is {row['purpose']!r}. Only canary and retired "
            "tenants can have their objects reclaimed, and changing that is not "
            "what this script is for."
        )
    return row["purpose"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", help="one tenant. Required for a retired one.")
    parser.add_argument("--older-than", type=int, default=14,
                        help="days. Recent runs stay, so a failure can be looked at.")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be freed and change nothing")
    parser.add_argument("--no-vacuum", action="store_true",
                        help="delete the objects but leave the volumes uncompacted, "
                             "so the disk does not shrink yet")
    parser.add_argument("--reason", default="canary storage reclaimed on schedule")
    parser.add_argument("--by", default=os.environ.get("USERNAME", "operator"))
    args = parser.parse_args()

    with psycopg.connect(PG_DSN, row_factory=dict_row, autocommit=True) as conn:
        if args.tenant:
            purpose = check_tenant(conn, args.tenant)
            print(f"tenant {args.tenant} ({purpose})")

        versions = eligible(conn, args.tenant, args.older_than)
        if not versions:
            print(f"Nothing to reclaim older than {args.older_than} days.")
            return 0

        client = s3()
        total_bytes = 0
        total_objects = 0
        freed_versions = 0

        for v in versions:
            bucket = bucket_for(conn, v["tenant_id"])
            found = objects_under(client, bucket, v["storage_prefix"])
            size = sum(o["Size"] for o in found)
            label = f"{v['dataset_name']} v{v['version']} [{v['tenant_id']}]"

            if not found:
                # Its objects are already gone, or it never had any. Recorded
                # anyway, so the second run does not look at it again and so the
                # log does not imply bytes came back that never existed.
                print(f"  {label}: no objects")
            else:
                print(f"  {label}: {len(found)} objects, {size / 1_048_576:.1f} MB")

            if args.dry_run:
                total_bytes += size
                total_objects += len(found)
                freed_versions += 1
                continue

            if found:
                client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": o["Key"]} for o in found]},
                )

            # The row is the only remaining evidence that these bytes existed,
            # so it is written even when nothing was found to delete.
            conn.execute(
                """insert into storage_reclamation
                     (dataset_version_id, tenant_id, bytes_freed, object_count,
                      reclaimed_by, reason)
                   values (%s, %s, %s, %s, %s, %s)
                   on conflict (dataset_version_id) do nothing""",
                (v["id"], v["tenant_id"], size, len(found), args.by, args.reason),
            )
            total_bytes += size
            total_objects += len(found)
            freed_versions += 1

    if args.dry_run:
        print(f"\nwould free {total_bytes / 1_048_576:.1f} MB across "
              f"{total_objects} objects in {freed_versions} versions.")
        print("Nothing was changed. Run again without --dry-run.")
        return 0

    print(f"\ndeleted {total_objects} objects across {freed_versions} versions, "
          f"holding {total_bytes / 1_048_576:.1f} MB.")

    if args.no_vacuum:
        print("Volumes were not vacuumed, so the disk has not shrunk yet.")
    else:
        vacuumed = vacuum()
        print("Vacuumed the volumes, which is when the space actually returns."
              if vacuumed else
              f"Could not reach the storage master at {MASTER}, so the objects "
              "are deleted but the disk has not shrunk. Run the vacuum by hand.")

    print("Every version row, its lineage and its audit history are untouched.")
    return 0


def vacuum() -> bool:
    """Compact the volumes so the deleted objects stop occupying disk.

    Reported separately from the deletion above, because they are separate
    facts. Objects can be gone while the space is still held, and saying so is
    the difference between a number that is true and one that sounds true.
    """
    try:
        r = httpx.get(f"{MASTER}/vol/vacuum",
                      params={"garbageThreshold": "0.01"}, timeout=120.0)
        return r.status_code == 200
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
