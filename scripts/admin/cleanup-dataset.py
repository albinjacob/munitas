"""Clear one unsealed dataset's leftover files, and only ever that.

Why this exists
----------------
Registering a dataset and bringing data into it are separate steps (see
`RegisterDataset.tsx`), and a dataset can sit registered-but-abandoned for a
while: an upload that was only ever a test, a HuggingFace fetch pointed at
the wrong repo, an integration test's leftovers. None of that becomes
visible in the console until somebody reopens the dataset and finds files
and job history that do not belong there.

Nothing existing clears this. `scripts/admin/reclaim-storage.py` only frees object bytes
for versions that are already sealed. `scripts/seed/reset-platform.ps1` wipes the entire
tenant's database. This is the tool for the space between those two: one
dataset, before it has ever been sealed.

The line this script will not cross
------------------------------------
It refuses outright if the dataset has any sealed `dataset_version` row.
That is not a permission check to relax with a flag. `class_transition`,
`lease_request`, `access_lease` and `access_decision` all carry a
`dataset_version_id` foreign key into that table, which is the lineage and
access-audit trail the platform exists to keep. `storage_reclamation` exists
as a table of its own specifically so that freeing a sealed version's bytes
never has to touch or cascade into any of that. This script does not
reinvent that boundary; it stays on the correct side of it. A dataset that
already has a sealed version needs `scripts/admin/reclaim-storage.py` instead, which frees
its storage while leaving every row, and the audit trail, intact.

There is no `DELETE` against `dataset_version`, `class_transition`,
`lease_request`, `access_lease` or `access_decision` anywhere in this file.
That is deliberate, the same way it is deliberate in `scripts/admin/reclaim-storage.py`.

What it actually deletes
-------------------------
A dataset with no sealed version has never had a version row created for
it (`versions.next_version` only reserves a prefix; sealing is what writes
the row), so nothing in Postgres references its storage yet. Everything
under `{tenant_id}/{dataset_id}/` in SeaweedFS is pre-seal data by
construction, and every `dataset_source` and `huggingface_fetch_job` row for
that `dataset_id` is exactly what this script is for. The `dataset` row
itself is left standing: registered, empty, ready for a real upload or fetch.

Usage
-----
    python scripts/admin/cleanup-dataset.py --dataset-id <uuid> --dry-run
    python scripts/admin/cleanup-dataset.py --tenant health --name raphaelmerx/openwho --dry-run
    python scripts/admin/cleanup-dataset.py --tenant health --name raphaelmerx/openwho

Reports what it found and asks for the dataset's name to be typed back
before changing anything, the same confirmation `scripts/seed/reset-platform.ps1` uses.
`--force` skips the prompt, for a support tool calling this non-interactively.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3
import psycopg
from botocore.config import Config
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ports_config import PORTS  # noqa: E402

PG_DSN = os.environ.get(
    "PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform"
)
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", f"http://localhost:{PORTS['seaweedfs_s3']}")

ADMIN_KEY = os.environ.get("S3_ADMIN_KEY", "munitas-admin")
ADMIN_SECRET = os.environ.get("S3_ADMIN_SECRET", "munitas-admin-secret")


def s3():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=ADMIN_KEY,
        aws_secret_access_key=ADMIN_SECRET,
        config=Config(signature_version="s3v4", retries={"max_attempts": 2}),
        region_name="us-east-1",
    )


def bucket_for(conn, tenant_id: str) -> str:
    """The bucket this tenant's objects are in, read from the same row the
    platform reads (platform/api/app/seaweed.py's _resolve_bucket).

    This script used to name one bucket for every tenant, which was right
    only while there was one. Deleting a dataset by listing the wrong bucket
    finds nothing, reports zero bytes freed, and leaves the objects behind
    while the rows go: the dataset looks deleted and its bytes are still
    there, unreferenced.
    """
    row = conn.execute(
        "select bucket from tenant_storage_provision "
        "where tenant_id = %s and backend = 'seaweedfs'",
        (tenant_id,),
    ).fetchone()
    if not row:
        raise SystemExit(
            f"no seaweedfs bucket recorded for tenant {tenant_id!r}; refusing "
            "to guess which bucket this dataset's objects are in"
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


def resolve_dataset(conn, dataset_id: str | None, tenant: str | None, name: str | None) -> dict:
    if dataset_id:
        row = conn.execute(
            "select id, tenant_id, name from dataset where id = %s", (dataset_id,)
        ).fetchone()
        if not row:
            sys.exit(f"no dataset {dataset_id!r}")
        return row

    if not (tenant and name):
        sys.exit("give either --dataset-id, or both --tenant and --name")

    row = conn.execute(
        "select id, tenant_id, name from dataset where tenant_id = %s and name = %s",
        (tenant, name),
    ).fetchone()
    if not row:
        sys.exit(f"no dataset {name!r} in tenant {tenant!r}")
    return row


def check_unsealed(conn, dataset_id: str) -> None:
    sealed = conn.execute(
        "select version from dataset_version where dataset_id = %s and sealed = true "
        "order by version",
        (dataset_id,),
    ).fetchall()
    if sealed:
        versions = ", ".join(f"v{r['version']}" for r in sealed)
        sys.exit(
            f"this dataset has sealed version(s) {versions}. This script only ever "
            "touches unsealed data, and will not delete a sealed version's row, "
            "lineage or access history to get at its storage. To free that "
            "version's stored bytes while keeping every row, use "
            "scripts/admin/reclaim-storage.py instead."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", help="the dataset's uuid")
    parser.add_argument("--tenant", help="tenant id, used with --name")
    parser.add_argument("--name", help="dataset name, used with --tenant")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be removed and change nothing")
    parser.add_argument("--force", action="store_true",
                        help="skip the typed confirmation, for scripted use")
    args = parser.parse_args()

    with psycopg.connect(PG_DSN, row_factory=dict_row, autocommit=True) as conn:
        dataset = resolve_dataset(conn, args.dataset_id, args.tenant, args.name)
        check_unsealed(conn, dataset["id"])

        prefix = f"{dataset['tenant_id']}/{dataset['id']}"
        bucket = bucket_for(conn, dataset["tenant_id"])
        client = s3()
        objects = objects_under(client, bucket, prefix)
        total_bytes = sum(o["Size"] for o in objects)

        sources = conn.execute(
            "select count(*) as n from dataset_source where dataset_id = %s",
            (dataset["id"],),
        ).fetchone()["n"]
        jobs = conn.execute(
            "select count(*) as n from huggingface_fetch_job where dataset_id = %s",
            (dataset["id"],),
        ).fetchone()["n"]

        print(f"{dataset['name']} [{dataset['tenant_id']}], id {dataset['id']}")
        print(f"  {len(objects)} objects, {total_bytes / 1_048_576:.1f} MB, under {prefix}/")
        print(f"  {sources} dataset_source rows")
        print(f"  {jobs} huggingface_fetch_job rows")

        if not objects and not sources and not jobs:
            print("\nAlready empty. Nothing to do.")
            return 0

        if args.dry_run:
            print("\nNothing was changed. Run again without --dry-run to clear it.")
            return 0

        if not args.force:
            print(
                f"\nThis deletes the files above and empties {dataset['name']!r}. "
                "The dataset stays registered."
            )
            answer = input(f"Type the dataset's name ({dataset['name']!r}) to continue: ")
            if answer != dataset["name"]:
                print("Nothing was changed.")
                return 0

        if objects:
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": o["Key"]} for o in objects]},
            )
        conn.execute("delete from dataset_source where dataset_id = %s", (dataset["id"],))
        conn.execute(
            "delete from huggingface_fetch_job where dataset_id = %s", (dataset["id"],)
        )

    print(
        f"\ncleared {len(objects)} objects ({total_bytes / 1_048_576:.1f} MB), "
        f"{sources} dataset_source rows, {jobs} huggingface_fetch_job rows."
    )
    print(f"{dataset['name']!r} is still registered, and now empty.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
