"""Hard-delete a tenant: every row, in every table, permanently gone.

Personal, local, pre-production use only. This is not the real-world
pattern for closing a tenant; that is scripts/admin/retire-tenant.py, then
scripts/admin/reclaim-storage.py to free bytes while every row survives. This script
exists beside that pair as an explicit, separate, deliberately alarmingly
named escape hatch, because sometimes a local scratch tenant genuinely
needs to be gone rather than merely closed.

It bypasses this platform's own immutability guarantee on purpose:
dataset_version and agent_version are the only two tables in
platform/schema.sql with rewrite rules that turn a DELETE against a sealed
row into a silent no-op. Those rules are disabled, every row for the named
tenant is deleted, and the rules are re-enabled, all inside one
transaction, so any unexpected failure rolls back the entire thing,
including re-enabling the rules. There is never a window where another
tenant's data is any less immutable because this script ran.

Its SeaweedFS buckets go too, emptied and deleted once the rows are gone.
Until they did, a deleted tenant's files stayed in storage with nothing
file them, and a rebuilt tenant, which reuses the same bucket name,
inherited every previous copy. A Cloudflare R2 bucket is real external
storage and is only named, never touched.

Refuses a production-purpose tenant outright. No flag overrides this.

    python scripts/admin/nuke-tenant.py --tenant scratch-1          # dry run, changes nothing
    python scripts/admin/nuke-tenant.py --tenant scratch-1 --force  # still asks for the id typed back
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pgdb
from pgdb import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ports_config import PORTS  # noqa: E402

PG_DSN = os.environ.get(
    "MUNITAS_PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform"
)
S3_ENDPOINT = os.environ.get("MUNITAS_S3_ENDPOINT", f"http://localhost:{PORTS['seaweedfs_s3']}")
ADMIN_KEY = os.environ.get("MUNITAS_S3_ADMIN_KEY", "munitas-admin")
ADMIN_SECRET = os.environ.get("MUNITAS_S3_ADMIN_SECRET", "munitas-admin-secret")


def s3():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3", endpoint_url=S3_ENDPOINT, aws_access_key_id=ADMIN_KEY,
        aws_secret_access_key=ADMIN_SECRET, region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )


def bucket_keys(client, bucket: str) -> list[str] | None:
    """Every key in the bucket, or None if the bucket does not exist."""
    keys, token = [], None
    while True:
        kwargs = {"Bucket": bucket}
        if token:
            kwargs["ContinuationToken"] = token
        try:
            page = client.list_objects_v2(**kwargs)
        except client.exceptions.NoSuchBucket:
            return None
        keys.extend(o["Key"] for o in page.get("Contents", []))
        if not page.get("IsTruncated"):
            return keys
        token = page.get("NextContinuationToken")


def empty_and_delete_bucket(client, bucket: str) -> int:
    """Delete every object and then the bucket. Returns how many objects.

    Raises if the bucket is still there afterwards: a bucket that quietly
    survived its tenant is exactly what this exists to prevent.
    """
    keys = bucket_keys(client, bucket)
    if keys is None:
        return 0
    for start in range(0, len(keys), 1000):
        client.delete_objects(Bucket=bucket, Delete={
            "Objects": [{"Key": k} for k in keys[start:start + 1000]]})
    client.delete_bucket(Bucket=bucket)
    if bucket_keys(client, bucket) is not None:
        raise RuntimeError(f"bucket {bucket} still exists after deleting it")
    return len(keys)

# The only rules in the schema that would otherwise turn a DELETE (or an
# UPDATE, which the delete-rule alone would not stop from unsealing a row
# first) against a sealed row into a silent no-op.
PROTECTED_RULES = [
    ("dataset_version", "dataset_version_no_update"),
    ("dataset_version", "dataset_version_no_delete"),
    ("agent_version", "agent_version_no_update"),
    ("agent_version", "agent_version_no_delete"),
    # The closing records: a hold and the history of a closing are removed only with their
    # organisation, and this is how a deliberately deleted one goes.
    ("legal_hold", "legal_hold_no_delete"),
    ("lifecycle_event", "lifecycle_event_no_delete"),
    ("legal_export", "legal_export_no_delete"),
]


def tenant_scoped_tables(conn) -> list[str]:
    """Discovered, not copied from schema.sql's own trigger-installation list.

    A hand-copied second list is exactly the kind of thing that goes stale
    the next time a table is added. This can't go stale: there is nothing
    to keep in sync.

    Not the whole story: some tables hold one tenant's data without a
    tenant_id column of their own, scoped only through a foreign key to a
    table this query does find. INDIRECT_TABLES below is what covers those.
    """
    rows = conn.execute(
        "select distinct c.table_name from information_schema.columns c "
        "join information_schema.tables t "
        "  on t.table_schema = c.table_schema and t.table_name = c.table_name "
        "where c.column_name = 'tenant_id' and c.table_schema = 'public' "
        "  and t.table_type = 'BASE TABLE'"
    ).fetchall()
    # tenant_deletion_record is what a purge leaves behind on purpose and is never deleted, so it
    # is not something this script clears.
    return sorted(r["table_name"] for r in rows if r["table_name"] != "tenant_deletion_record")


# Tables carrying one tenant's data with no tenant_id column of their own:
# class_transition sits beside dataset_version rather than on it because that
# table is immutable by rewrite rule (see schema.sql), and dataset_source and
# huggingface_fetch_job are one-row-per-source records that were never given
# a tenant_id because every existing lookup reaches them through their
# dataset. table -> (foreign key column, the tenant-scoped table it points
# at). Hand-maintained, unlike tenant_scoped_tables() above: nothing in
# information_schema distinguishes "no tenant_id because unscoped" from "no
# tenant_id because scoped through a parent", so this can't be discovered.
INDIRECT_TABLES: dict[str, tuple[str, str]] = {
    "dataset_source": ("dataset_id", "dataset"),
    "class_transition": ("dataset_version_id", "dataset_version"),
    "huggingface_fetch_job": ("dataset_id", "dataset"),    # Found by asking the database for every table with no tenant_id column that points at one that has it (2026-10-09). A purge or a delete that
    # does not know a table cannot remove its parent, and stops with a foreign key error before anything is changed.
    "agent_run_attempt": ("run_id", "agent_run"),
    "external_credential": ("directory_id", "directory"),
    "gate_leak": ("gate_decision_id", "gate_decision"),
    "notebook_code_attempt": ("principal", "directory"),
    "pipeline_step_run": ("pipeline_run_id", "pipeline_run"),
    "pipeline_version": ("pipeline_id", "pipeline"),
    "table_job_part": ("job_id", "table_job"),            # the parts of an upload that is still open, which hold the organisation's file (the platform's own purge lists it too)
}


# Set by --keep-referenced-people: the people another organisation's records point at are not deleted (see people_others_rely_on).
KEEP = {"people": False}


def people_others_rely_on(conn, tenant: str) -> list[tuple[str, str]]:
    """The people of `tenant` that a record of another organisation points at, as (id, name).

    A person's directory row cannot be deleted while any record points at it, and some of those records cannot be deleted by anybody: a legal hold names
    the person who placed it. The platform administrator is one person with one row, kept in one organisation, and acts in the others; a hold placed
    in another organisation therefore stops the first organisation being deleted. Found by asking the database for every foreign key that points at
    `directory` from a table with an organisation column, and keeping the people whose rows are pointed at from a different organisation."""
    refs = conn.execute(
        """select c.conrelid::regclass::text as child, a.attname as col
             from pg_constraint c join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey)
            where c.contype = 'f' and c.confrelid = 'directory'::regclass and c.conrelid <> 'directory'::regclass
              and exists (select 1 from information_schema.columns k
                           where k.table_schema = 'public' and k.table_name = c.conrelid::regclass::text and k.column_name = 'tenant_id')
            order by 1, 2""").fetchall()
    found: dict[str, str] = {}
    for ref in refs:
        for row in conn.execute(
            f'select distinct d.id, d.label from directory d join "{ref["child"]}" x on x."{ref["col"]}" = d.id '
            "where d.tenant_id = %s and x.tenant_id <> %s", (tenant, tenant)).fetchall():
            found[row["id"]] = row["label"]
    return sorted(found.items())


def delete_clause(table: str) -> str:
    """The WHERE clause deleting all of `tenant`'s rows from `table` needs.

    Direct tables filter on their own tenant_id. Indirect tables filter
    through a subquery against the parent table named in INDIRECT_TABLES,
    which still holds the tenant's rows at the moment this runs: the parent
    can't be deleted until its children (this table, among them) are gone,
    so the subquery always sees the full set.
    """
    if table in INDIRECT_TABLES:
        fk_col, parent = INDIRECT_TABLES[table]
        return f'{fk_col} in (select id from "{parent}" where tenant_id = %s)'
    if table == "directory" and KEEP["people"]:
        return "tenant_id = %s and id not in (select id from nuke_keep)"
    return "tenant_id = %s"


def counts_for(conn, tables: list[str], tenant: str) -> dict[str, int]:
    counts = {}
    for t in tables:
        n = conn.execute(
            f'select count(*) as n from "{t}" where {delete_clause(t)}', (tenant,)
        ).fetchone()["n"]
        if n:
            counts[t] = n
    return counts


def delete_all(conn, tables: list[str], tenant: str) -> dict[str, int]:
    """Retry-pass delete.

    Attempts every table; a table still blocked by a live foreign key from
    another not-yet-cleared table is deferred to the next pass. Stops when
    a pass deletes nothing further. Replaces hand-ordering ~20 tables by
    their foreign-key dependency chain, which would be exactly as brittle
    as the hand-copied table list this function's caller already avoids.

    Each attempt runs inside its own savepoint (conn.transaction(), nested
    since the connection is already mid-transaction), so one table's
    foreign-key violation does not abort the whole surrounding transaction
    and only that one statement rolls back.
    """
    remaining = list(tables)
    deleted: dict[str, int] = {}
    while remaining:
        progressed = False
        still_remaining = []
        reasons: dict[str, str] = {}
        for t in remaining:
            try:
                with conn.transaction():
                    cur = conn.execute(
                        f'delete from "{t}" where {delete_clause(t)}', (tenant,)
                    )
                if cur.rowcount:
                    deleted[t] = cur.rowcount
                progressed = True
            except pgdb.errors.ForeignKeyViolation as exc:
                still_remaining.append(t)
                reasons[t] = f"{exc.diag.constraint_name}: {exc.diag.message_detail}"
        if not progressed and still_remaining:
            # Rows that only reference each other form a loop no one-table
            # delete can break: dataset_version names the action_run that
            # produced it, the action_run names its pipeline_run, and the
            # pipeline_run names the version it started from. A foreign key
            # is checked at the end of its statement, so deleting every stuck
            # table in one statement lets a loop go at once. Only a reference
            # from outside the loop can still refuse it.
            try:
                with conn.transaction():
                    deleted.update(_delete_together(conn, still_remaining, tenant))
                return deleted
            except pgdb.errors.ForeignKeyViolation as exc:
                raise RuntimeError(
                    "stuck: " + ", ".join(still_remaining) + " still have rows "
                    "referenced from outside them, even deleted together. "
                    f"The database's reason: {exc.diag.constraint_name}: "
                    f"{exc.diag.message_detail}. One table at a time, it said: "
                    + "; ".join(f"{t}: {r}" for t, r in reasons.items())
                ) from exc
        remaining = still_remaining
    return deleted


def _delete_together(conn, tables: list[str], tenant: str) -> dict[str, int]:
    """Delete `tenant`'s rows from every table in `tables` in one statement.

    The rows are counted first and the delete does not ask for them back (`returning`). PostgreSQL refuses a `delete ... returning`
    on a table that has a conditional `do instead` rule, and `dataset_version` has one (the immutability rule, which lets a tenant
    that declared itself disposable delete its sealed versions), so counting through `returning` failed for exactly the disposable
    tenants whose lineage loops back on itself. A data-modifying `with` runs whether or not anything reads it."""
    before = {
        t: conn.execute(
            f'select count(*) as n from "{t}" where {delete_clause(t)}', (tenant,)
        ).fetchone()["n"]
        for t in tables
    }
    parts = [f'd{i} as (delete from "{t}" where {delete_clause(t)})' for i, t in enumerate(tables)]
    conn.execute("with " + ", ".join(parts) + " select 1", [tenant] * len(tables))
    return {t: n for t, n in before.items() if n}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--keep-referenced-people", action="store_true",
                        help="keep the people that another organisation's records point at, and so the organisation's own row, instead of being refused by the database. "
                             "For a rebuild: the seed puts everything else back around them.")
    parser.add_argument("--force", action="store_true",
                        help="skip straight to the typed confirmation, for scripted use")
    args = parser.parse_args()

    with pgdb.connect(PG_DSN, row_factory=dict_row) as conn:
        row = conn.execute(
            "select purpose from tenant where id = %s", (args.tenant,)
        ).fetchone()
        if not row:
            print(f"no tenant {args.tenant!r}")
            return 1
        if row["purpose"] == "production":
            print(
                f"tenant {args.tenant!r} is production. Refusing: this script "
                "never touches a production tenant, and no flag overrides that."
            )
            return 1

        kept = people_others_rely_on(conn, args.tenant)
        if kept and args.keep_referenced_people:
            conn.execute("create temp table nuke_keep (id text primary key) on commit drop")
            for person, _name in kept:
                conn.execute("insert into nuke_keep values (%s)", (person,))
            KEEP["people"] = True

        tables = tenant_scoped_tables(conn) + list(INDIRECT_TABLES)
        counts = counts_for(conn, tables, args.tenant)

        print(f"tenant {args.tenant!r} (purpose={row['purpose']}):")
        if not counts:
            print("  no rows in any tenant-scoped table.")
        for t, n in counts.items():
            print(f"  {t}: {n} rows")
        buckets = conn.execute(
            "select bucket, backend from tenant_storage_provision where tenant_id = %s",
            (args.tenant,),
        ).fetchall()
        client = s3() if any(b["backend"] == "seaweedfs" for b in buckets) else None
        for b in buckets:
            if b["backend"] != "seaweedfs":
                print(f"  bucket {b['bucket']} ({b['backend']}): external, left for you to remove")
                continue
            keys = bucket_keys(client, b["bucket"])
            print(f"  bucket {b['bucket']}: " + ("already gone" if keys is None
                                               else f"{len(keys)} file(s), deleted with it"))

        if kept:
            who = ", ".join(f"{name} ({person})" for person, name in kept)
            if args.keep_referenced_people:
                print(f"\nKept, because records of another organisation point at them: {who}. The organisation's own row stays with them.")
            else:
                print(f"\nRecords of another organisation point at: {who}. The database will refuse to delete them, and so the organisation. "
                      "Pass --keep-referenced-people to keep them and delete everything else.")

        if not args.force:
            print(
                "\nDry run. Nothing changed. Pass --force and confirm to "
                "actually delete."
            )
            return 0

        answer = input(
            f"\nType the tenant's id ({args.tenant!r}) to permanently delete "
            "it and everything in it: "
        )
        if answer != args.tenant:
            print("Not confirmed. Nothing changed.")
            return 1

        # Packages of legal exports live in a bucket of their own, so their names are read before the rows go.
        packages = [r["package_key"] for r in conn.execute(
            "select package_key from legal_export where tenant_id = %s and package_key is not null and expired_at is null",
            (args.tenant,)).fetchall()]
        try:
            for table, rule in PROTECTED_RULES:
                conn.execute(f'alter table "{table}" disable rule "{rule}"')
            deleted = delete_all(conn, tables, args.tenant)
            # action_run_output_version_fkey is deferrable initially deferred,
            # so deleting dataset_version rows queues its FK check rather than
            # running it immediately. A table with a pending deferred trigger
            # event cannot be ALTERed, so the check must be forced now, before
            # the rules are re-enabled.
            conn.execute("set constraints all immediate")
            for table, rule in PROTECTED_RULES:
                conn.execute(f'alter table "{table}" enable rule "{rule}"')
            if not KEEP["people"]:
                conn.execute("delete from tenant where id = %s", (args.tenant,))
        except Exception as exc:
            conn.rollback()
            print(f"\nSomething went wrong, nothing was changed: {exc}")
            return 1

        conn.commit()

    print(f"\ntenant {args.tenant!r} " + ("emptied, with its row and the people named above kept," if KEEP["people"] else "deleted,") + f" across {len(deleted)} tables:")
    for t, n in deleted.items():
        print(f"  {t}: {n} rows")

    # After the commit, so a failure here cannot leave rows pointing at
    # files that are already gone. The reverse leftover, a bucket with no
    # tenant, is what verify/v74_storage_agrees.py reports.
    failed = False
    if packages:
        store = s3()
        for key in packages:
            try:
                store.delete_object(Bucket=os.environ.get("MUNITAS_LEGAL_EXPORT_BUCKET", "munitas-legal-exports"), Key=key)
            except Exception as exc:  # noqa: BLE001
                failed = True
                print(f"  package {key}: NOT removed ({exc})")
        print(f"  {len(packages)} legal export package(s) removed")
    for b in buckets:
        if b["backend"] != "seaweedfs":
            continue
        try:
            n = empty_and_delete_bucket(client, b["bucket"])
            print(f"  bucket {b['bucket']}: {n} file(s) deleted, bucket removed")
        except Exception as exc:  # noqa: BLE001 - reported, and the exit code says so
            failed = True
            print(f"  bucket {b['bucket']}: NOT removed ({exc}). Remove it with "
                  "docker compose exec -T munitas-api python /app/remove-orphaned-files.py --apply")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
