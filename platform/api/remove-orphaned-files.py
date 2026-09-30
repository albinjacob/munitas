"""Remove files and buckets that no record owns.

What counts as unowned is decided by app/storage_audit.py, the same function
the verification suite asks, so this and the check cannot disagree about it.
A dry run unless --apply is given, and it prints every file it would remove.

    docker compose exec -T munitas-api python /app/remove-orphaned-files.py
    docker compose exec -T munitas-api python /app/remove-orphaned-files.py --apply

Storage identities with no owner are reported, not removed here: the next
reconcile removes them (see /app/reconcile-grants.py).
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "/app")

from app import db, seaweed, storage_audit  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="remove them; without this, only report")
    args = parser.parse_args()

    db.pool.open()
    found = storage_audit.audit()
    client = seaweed._admin_boto_client()

    for o in found["objects"]:
        print(f"  file    {o['bucket']}/{o['key']}  ({o['bytes'] / 1024:.1f} KB)")
    for b in found["buckets"]:
        print(f"  bucket  {b}  (no existing organisation records it)")
    for i in found["identities"]:
        print(f"  key     {i}  (removed by the next reconcile, not here)")
    total = sum(o["bytes"] for o in found["objects"])
    print(f"\n{len(found['objects'])} file(s), {total / 1024 / 1024:.1f} MB; "
          f"{len(found['buckets'])} bucket(s); {len(found['identities'])} key(s) with no owner.")

    if not args.apply:
        print("Dry run. Nothing changed. Pass --apply to remove the files and buckets.")
        return 0

    for start in range(0, len(found["objects"]), 1000):
        chunk = found["objects"][start:start + 1000]
        by_bucket: dict[str, list[str]] = {}
        for o in chunk:
            by_bucket.setdefault(o["bucket"], []).append(o["key"])
        for bucket, keys in by_bucket.items():
            client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys]})
    for bucket in found["buckets"]:
        for start in range(0, 10**9, 1000):
            page = client.list_objects_v2(Bucket=bucket, MaxKeys=1000)
            keys = [o["Key"] for o in page.get("Contents", [])]
            if not keys:
                break
            client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys]})
        client.delete_bucket(Bucket=bucket)

    left = storage_audit.audit()
    print(f"Removed. Unowned now: {len(left['objects'])} file(s), {len(left['buckets'])} bucket(s).")
    return 0 if not left["objects"] and not left["buckets"] else 1


if __name__ == "__main__":
    sys.exit(main())
