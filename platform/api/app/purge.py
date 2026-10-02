"""Deleting everything inside an organisation whose time is up.

WHEN, AND WHO DECIDES

Only `tenant_purge_allowed` in the database decides: both closing periods have
ended and no legal hold, pending or active, stands over the organisation. This
module asks it twice, once before touching storage and once inside the
transaction that deletes the rows, and does nothing if the answer is no.

HOW IT GETS PAST THE IMMUTABILITY RULES

A sealed dataset version, a sealed agent version, a legal hold and the record of
an organisation's closing cannot be deleted by anybody, and the rewrite rules that
say so are the platform's central guarantee. They are not switched off here. Each
carries one exemption, `tenant_is_being_purged(id)`, which is true only when the
transaction has named that organisation in the setting `munitas.purge_tenant` AND
the database itself says the organisation may be purged. A caller that names an
organisation that is not due, or is held, or names none, is refused exactly as
before, and verify/v100_purge.py proves each of those refusals.

WHAT IS DELETED, AND WHAT STAYS

Every row in every table that carries the organisation's id, and the three tables
that hold its data through a parent (INDIRECT_TABLES), then its storage buckets on
SeaweedFS. A bucket on Cloudflare R2 is external storage: it is named in the
deletion record as left, for a person to remove, and never touched from here.

What stays is the organisation's own row, marked purged so the name is never
reused, and one row in `tenant_deletion_record`: which organisation, who asked for it
to close and why, the holds that applied (numbers and authorities, no contact
details), what was removed, and when. The people's sign-in identities in the
identity provider are not touched either. The platform holds no administrative
credential to it by design (people.py), so each such identity simply finds no
person behind it and is refused.

Order of work. Storage is emptied first and the rows afterwards, in one
transaction. If the rows fail, nothing was changed in the database and the next
sweep finds the buckets already gone and finishes the rest, so there is never a
state in which rows exist that record a bucket which is not there and nothing says so.
"""

from __future__ import annotations

import json

import psycopg
from psycopg.rows import dict_row

from . import config, db, grants, logs, seaweed

log = logs.get_logger("purge")

# Tables that hold one organisation's data with no tenant_id column of their own,
# reached through a parent that has one. table -> (column, parent table). Kept in
# step with scripts/admin/nuke-tenant.py, and checked by verify/v100_purge.py, which
# fails if any row of a purged organisation is left anywhere.
INDIRECT_TABLES: dict[str, tuple[str, str]] = {
    "dataset_source": ("dataset_id", "dataset"),
    "class_transition": ("dataset_version_id", "dataset_version"),
    "huggingface_fetch_job": ("dataset_id", "dataset"),
}

# Never deleted with the organisation: what is left behind on purpose.
KEPT = {"tenant_deletion_record"}


def _tables(conn) -> list[str]:
    rows = conn.execute(
        "select distinct c.table_name from information_schema.columns c "
        "join information_schema.tables t "
        "  on t.table_schema = c.table_schema and t.table_name = c.table_name "
        "where c.column_name = 'tenant_id' and c.table_schema = 'public' "
        "  and t.table_type = 'BASE TABLE'"
    ).fetchall()
    # A partition is deleted through its parent.
    return sorted(r["table_name"] for r in rows
                  if r["table_name"] not in KEPT and not r["table_name"].startswith("access_decision_"))


def _clause(table: str) -> str:
    if table in INDIRECT_TABLES:
        column, parent = INDIRECT_TABLES[table]
        return f'{column} in (select id from "{parent}" where tenant_id = %s)'
    return "tenant_id = %s"


def _delete_all(conn, tables: list[str], tenant: str) -> dict[str, int]:
    """Delete in passes. A table still referenced by one not yet emptied waits for
    the next pass, which avoids ordering ~40 tables by hand. Rows that only
    reference each other are deleted together in one statement."""
    remaining, deleted = list(tables), {}
    while remaining:
        progressed, still = False, []
        for table in remaining:
            try:
                with conn.transaction():
                    cur = conn.execute(f'delete from "{table}" where {_clause(table)}', (tenant,))
                if cur.rowcount:
                    deleted[table] = cur.rowcount
                progressed = True
            except psycopg.errors.ForeignKeyViolation:
                still.append(table)
        if not progressed and still:
            parts = [f'd{i} as (delete from "{t}" where {_clause(t)} returning 1)' for i, t in enumerate(still)]
            counts = ", ".join(f"(select count(*) from d{i}) as n{i}" for i in range(len(still)))
            with conn.transaction():
                row = conn.execute("with " + ", ".join(parts) + " select " + counts,
                                   [tenant] * len(still)).fetchone()
            deleted.update({t: row[f"n{i}"] for i, t in enumerate(still) if row[f"n{i}"]})
            return deleted
        remaining = still
    return deleted


def _empty_bucket(client, bucket: str) -> int:
    """Delete every object, then the bucket. Returns how many objects. A bucket that
    is already gone counts as done, so a purge that failed halfway can be retried."""
    keys, token = [], None
    while True:
        kwargs = {"Bucket": bucket}
        if token:
            kwargs["ContinuationToken"] = token
        try:
            page = client.list_objects_v2(**kwargs)
        except client.exceptions.NoSuchBucket:
            return 0
        keys.extend(o["Key"] for o in page.get("Contents", []))
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
    for start in range(0, len(keys), 1000):
        client.delete_objects(Bucket=bucket, Delete={
            "Objects": [{"Key": k} for k in keys[start:start + 1000]]})
    client.delete_bucket(Bucket=bucket)
    return len(keys)


def purge_tenant(tenant_id: str, purged_by: str) -> dict:
    """Delete one organisation's contents and write the record that it was done.

    Raises if the organisation is not allowed to be purged, or if anything fails;
    in both cases no database row has been changed.
    """
    if not db.one("select tenant_purge_allowed(%s) as ok", (tenant_id,))["ok"]:
        raise PermissionError(f"{tenant_id} is not due for deletion, or a legal hold stands over it")

    provisions = db.all_rows(
        "select bucket, backend from tenant_storage_provision where tenant_id = %s", (tenant_id,))
    removed_buckets, left_buckets, files = [], [], 0
    client = seaweed._admin_boto_client() if any(p["backend"] == "seaweedfs" for p in provisions) else None
    for p in provisions:
        if p["backend"] == "seaweedfs":
            files += _empty_bucket(client, p["bucket"])
            removed_buckets.append(p["bucket"])
        else:
            left_buckets.append({"bucket": p["bucket"], "backend": p["backend"],
                                 "why": "external storage, for a person to remove"})

    with psycopg.connect(config.PG_DSN, row_factory=dict_row) as conn:
        with conn.transaction():
            # Named for the rewrite rules, and local to this transaction.
            conn.execute("select set_config('munitas.purge_tenant', %s, true)", (tenant_id,))
            if not conn.execute("select pg_try_advisory_xact_lock(hashtext(%s)) as got",
                                ("purge:" + tenant_id,)).fetchone()["got"]:
                raise RuntimeError(f"{tenant_id} is already being purged by another process")
            if not conn.execute("select tenant_purge_allowed(%s) as ok", (tenant_id,)).fetchone()["ok"]:
                raise PermissionError(f"{tenant_id} stopped being due for deletion before the purge began")

            tenant = conn.execute(
                "select retire_reason, retire_requested_by, retired_at, closing_until "
                "from tenant where id = %s", (tenant_id,)).fetchone()
            holds = [{
                "matter_number": h["matter_number"], "issuing_authority": h["issuing_authority"],
                "authority_reference": h["authority_reference"], "status": h["status"],
                "placed_on": h["placed_at"].date().isoformat(),
                "approved_on": h["decided_at"].date().isoformat() if h["decided_at"] else None,
                "released_on": h["released_at"].date().isoformat() if h["released_at"] else None,
                "release_reason": h["release_reason"],
            } for h in conn.execute(
                "select * from legal_hold where tenant_id = %s order by placed_at", (tenant_id,)).fetchall()]

            counts = _delete_all(conn, _tables(conn) + list(INDIRECT_TABLES), tenant_id)

            conn.execute(
                """insert into tenant_deletion_record (tenant_id, retire_reason, retire_requested_by,
                          retired_at, closing_ended_at, purged_by, holds, rows_removed, files_removed,
                          buckets_removed, buckets_left)
                   values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s::jsonb)""",
                (tenant_id, tenant["retire_reason"], tenant["retire_requested_by"], tenant["retired_at"],
                 tenant["closing_until"], purged_by, json.dumps(holds), json.dumps(counts), files,
                 json.dumps(removed_buckets), json.dumps(left_buckets)))
            conn.execute("update tenant set purged_at = now() where id = %s", (tenant_id,))

    try:
        # Storage keys that named the deleted rows are dropped from the permissions document.
        grants.reconcile(trigger="manual")
    except Exception as exc:
        log.error("storage permissions could not be printed after a purge; the activator keeps trying",
                  extra={"tenant_id": tenant_id, "reason": str(exc)})
    log.info("organisation purged", extra={"tenant_id": tenant_id, "count": sum(counts.values())})
    return {"tenant_id": tenant_id, "rows_removed": counts, "files_removed": files,
            "buckets_removed": removed_buckets, "buckets_left": left_buckets, "holds": holds}
