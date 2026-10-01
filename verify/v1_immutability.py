"""V1: datasets are immutable.

The claim is that a sealed dataset version cannot be altered or removed, and
that this holds against the database directly rather than only through the API.
Testing it through the API would prove the API has no update endpoint, which is
a much weaker claim and one a future commit could quietly undo.

The Iceberg half of V1, that a snapshot resolves to the same manifest, is the
last section. It used to be a permanent skip, because no Iceberg table existed.
A tabular version is now also written as a table (iceberg.py), so it is a real
check: the snapshot the register names resolves through exactly the files the
version's own manifest lists, and tampering with the register changes neither.
"""

from __future__ import annotations

import sys

from common import (check, db, fixture_contract, fixture_tabular_version, fixture_tenant,
                    fixture_version, heading, read_table, require_api, summary)


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

    heading("V1: an Iceberg snapshot resolves to the same manifest")
    tab = fixture_tabular_version(tenant)
    with db() as conn:
        ref = conn.execute("select * from iceberg_table_ref where dataset_version_id = %s",
                           (tab["id"],)).fetchone()
        before = conn.execute(
            "select object_manifest, iceberg_snapshot_id from dataset_version where id = %s",
            (tab["id"],)).fetchone()
    check("the version has a table", ref is not None, "recorded" if ref else "no table")
    if ref:
        table = read_table(ref["metadata_location"])
        snapshot = table.snapshot_by_id(ref["snapshot_id"])
        check("the snapshot the register names exists in the table", snapshot is not None,
              str(ref["snapshot_id"]))

        def resolved_keys() -> set[str]:
            """Every file the snapshot reaches: its manifest list, its manifests and
            its data files, as the object keys the version's manifest uses."""
            files = {snapshot.manifest_list}
            files |= {m.manifest_path for m in snapshot.manifests(table.io)}
            files |= set(table.inspect.data_files().column("file_path").to_pylist())
            return {f.split("/", 3)[3] for f in files}

        reached = resolved_keys()
        listed = {m["key"] for m in before["object_manifest"]}
        check("every file the snapshot resolves through is in the version's manifest",
              reached <= listed, f"{len(reached)} reached, {len(reached - listed)} not listed")

        # The same attempts the rest of this script makes on a plain version, now on
        # one with a table: the register's rewrite rules turn them into no-ops, so
        # the snapshot and the manifest it resolves through must be untouched.
        with db() as conn:
            conn.execute("update dataset_version set iceberg_snapshot_id = 1, "
                         "object_manifest = '[]'::jsonb where id = %s", (tab["id"],))
            conn.execute("delete from dataset_version where id = %s", (tab["id"],))
            after = conn.execute(
                "select object_manifest, iceberg_snapshot_id from dataset_version where id = %s",
                (tab["id"],)).fetchone()
        check("an attempt to rewrite the snapshot id or the manifest changes nothing",
              after is not None and after["iceberg_snapshot_id"] == before["iceberg_snapshot_id"]
              and after["object_manifest"] == before["object_manifest"], "unchanged")
        check("the snapshot still resolves through the same files afterwards",
              resolved_keys() == reached, f"{len(reached)} files")

    return summary("V1")


if __name__ == "__main__":
    sys.exit(main())
