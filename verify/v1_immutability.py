"""V1: datasets are immutable.

The claim is that a sealed dataset version cannot be altered or removed, and
that this holds against the database directly rather than only through the API.
Testing it through the API would prove the API has no update endpoint, which is
a much weaker claim and one a future commit could quietly undo.

The Iceberg half of V1, that a snapshot still resolves to the same manifest, is
not covered here. Slice 3 creates the first Iceberg table; until then there is
nothing to resolve.
"""

from __future__ import annotations

import sys

from common import check, db, fixture_contract, fixture_tenant, fixture_version, heading, require_api, skip, summary


def main() -> int:
    require_api()
    tenant = fixture_tenant()
    contract = fixture_contract(tenant)
    version = fixture_version(tenant, contract, "RAW")
    vid = version["id"]

    heading("V1: datasets are immutable")

    with db() as conn:
        before = conn.execute(
            "select * from dataset_version where id = %s", (vid,)
        ).fetchone()

        # Attempt an update. The rewrite rule turns this into a no-op, so it
        # neither errors nor changes anything. Both facts are checked.
        cur = conn.execute(
            """update dataset_version
                 set visibility_class = 'PUBLISHED', content_hash = 'tampered'
               where id = %s""",
            (vid,),
        )
        check("UPDATE on a sealed version affects zero rows", cur.rowcount == 0,
              f"rowcount={cur.rowcount}")

        after = conn.execute(
            "select * from dataset_version where id = %s", (vid,)
        ).fetchone()
        check("visibility_class unchanged after UPDATE",
              after["visibility_class"] == before["visibility_class"],
              f"{before['visibility_class']} -> {after['visibility_class']}")
        check("content_hash unchanged after UPDATE",
              after["content_hash"] == before["content_hash"],
              after["content_hash"][:16])
        check("every column unchanged after UPDATE", after == before)

        cur = conn.execute("delete from dataset_version where id = %s", (vid,))
        check("DELETE on a sealed version affects zero rows", cur.rowcount == 0,
              f"rowcount={cur.rowcount}")

        still = conn.execute(
            "select count(*) as n from dataset_version where id = %s", (vid,)
        ).fetchone()
        check("row still present after DELETE", still["n"] == 1)

        # The rule is conditioned on `sealed`, so an unsealed row must still be
        # mutable. Without this the test would also pass on a table nobody can
        # write to at all, which would prove nothing about the condition.
        conn.execute(
            """insert into dataset_version
                 (id, tenant_id, dataset_id, version, visibility_class,
                  storage_prefix, schema_id, content_hash, sealed)
               values (gen_random_uuid(), %s, %s, 999, 'RAW', 'scratch/', %s,
                       'draft', false)""",
            (tenant, version["dataset_id"], contract),
        )
        cur = conn.execute(
            """update dataset_version set content_hash = 'edited'
               where dataset_id = %s and sealed = false""",
            (version["dataset_id"],),
        )
        check("an UNSEALED version is still writable, so the rule is conditional",
              cur.rowcount == 1, f"rowcount={cur.rowcount}")

    skip("Iceberg snapshot resolves to the same manifest",
         "no Iceberg table exists until slice 3")

    return summary("V1")


if __name__ == "__main__":
    sys.exit(main())
