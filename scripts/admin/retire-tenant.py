"""Retire a tenant: close it to further writes, keep every row readable.

The real-world-correct way to wind a tenant down. Reuses the exact
mechanism that already closed t1: one UPDATE, enforced everywhere by
refuse_write_to_retired_tenant() in platform/schema.sql, already attached
to every tenant-scoped table. This script never deletes anything; to free
the storage a retired tenant's sealed versions hold, run
scripts/admin/reclaim-storage.py --tenant <id> afterward.

    python scripts/admin/retire-tenant.py --tenant acme
    python scripts/admin/retire-tenant.py --tenant acme --force

This closes the organisation at once and gives it no dates, so the platform never
deletes it by itself: it stays readable until somebody runs reclaim-storage.py or
nuke-tenant.py. That is the right tool for a verification or throwaway organisation.
A customer organisation is closed through the console (or POST
/lifecycle/organisation/retire), which gives its people 15 days to read and cancel,
another 15 with nothing, honours any legal hold, and then deletes everything inside it
(platform/api/app/lifecycle.py).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ports_config import PORTS  # noqa: E402

PG_DSN = os.environ.get(
    "PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--force", action="store_true",
                        help="skip the typed confirmation, for scripted use")
    args = parser.parse_args()

    with psycopg.connect(PG_DSN, row_factory=dict_row, autocommit=True) as conn:
        row = conn.execute(
            "select purpose from tenant where id = %s", (args.tenant,)
        ).fetchone()
        if not row:
            print(f"no tenant {args.tenant!r}")
            return 1
        if row["purpose"] == "retired":
            print(f"tenant {args.tenant!r} is already retired")
            return 1

        print(f"tenant {args.tenant!r}: purpose={row['purpose']}")
        if not args.force:
            answer = input(
                f"Type the tenant's id ({args.tenant!r}) to retire it: "
            )
            if answer != args.tenant:
                print("Not confirmed. Nothing changed.")
                return 1

        conn.execute(
            "update tenant set purpose = 'retired' where id = %s", (args.tenant,)
        )

    print(f"tenant {args.tenant!r} retired. Reads still work; writes are refused.")
    print(f"To free its storage: python scripts/admin/reclaim-storage.py --tenant {args.tenant}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
