"""U74: storage agrees with the register.

Two records of the same thing, written by different code: what the database
says exists, and what is sitting in object storage and the storage identity
document. They drift apart silently whenever something is created by one path
and nothing ends it on the other, and on one day three such gaps turned up:
pipeline runs that never ended, storage keys that outlived their organisation,
and files that outlived their dataset. Every earlier check looked at one side
only, which is why none of them saw it.

What counts as owned is decided by platform/api/app/storage_audit.py, from the
rows that own things (a version's storage prefix, a pipeline or agent
version's exact code key), never from the shape of a key. The same function
drives /app/remove-orphaned-files.py, so the check and the cleanup cannot
disagree about what a leftover is.

Loose files inside a `scratch` tenant's bucket are left out: U56 and U65 write
them on purpose and the tenant goes whole after the run. Their buckets are
still checked.

    docker compose exec -T munitas-api python /verify/v74_storage_agrees.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, "/app")

from common import check, heading, require_api, summary  # noqa: E402


def main() -> int:
    require_api()
    from app import db as app_db  # noqa: E402
    from app import storage_audit  # noqa: E402

    app_db.pool.open()
    found = storage_audit.audit(scratch_files=False)

    heading("U74: every file in an organisation's bucket belongs to a record")
    total = sum(o["bytes"] for o in found["objects"])
    sample = ", ".join(f"{o['bucket']}/{o['key']}" for o in found["objects"][:3])
    check("no file without an owning dataset, pipeline or agent version",
          not found["objects"],
          f"{len(found['objects'])} file(s), {total / 1024 / 1024:.1f} MB, e.g. {sample}. "
          "List and remove them with /app/remove-orphaned-files.py" if found["objects"] else "")

    heading("U74: every bucket belongs to an organisation that exists")
    check("no bucket left behind by a deleted organisation", not found["buckets"],
          ", ".join(found["buckets"]))

    heading("U74: every storage key belongs to a role or a record")
    check("no key without a role or a storage_identity row", not found["identities"],
          ", ".join(found["identities"][:5]))

    return summary("U74")


if __name__ == "__main__":
    sys.exit(main())
