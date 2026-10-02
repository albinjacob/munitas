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

Every row in every table that carries the organisation's id, the three tables that hold its data
through a parent (INDIRECT_TABLES), its storage buckets on SeaweedFS, and the sign-in accounts of its
people in the identity provider. A bucket on Cloudflare R2 is external storage: it is named in the
deletion record as left, for a person to remove, and never touched from here.

What stays:

  * One row in `tenant_deletion_record`: which organisation, who asked for it to close and why, the
    holds that applied (numbers and authorities, no contact details), what was removed, and when.
  * The organisation's audit rows (`access_decision`), for AUDIT_RETENTION_YEARS, and then they are
    removed too (`expire_audit`). A decision about who read what is evidence long after the data is gone.

Both are re-labelled in the same transaction, so the name can be used again. The organisation was
called `harbour`; its record and its audit rows are now filed under `harbour~deleted-20261002-a1b2`,
and the record says in `original_tenant_id` what it was. The organisation's own row is deleted, so a
new organisation may take the old name and will see none of the old audit rows.

The sign-in accounts are removed first, before any row, because the directory is what says which
accounts belong to the organisation. An account already gone counts as removed. If the identity
provider cannot be reached the purge stops before changing anything, and the next sweep tries again.

Order of work. Storage and accounts first, then the rows in one transaction. If the rows fail, nothing
was changed in the database and the next sweep finds the buckets and accounts already gone and finishes
the rest, so there is never a state in which rows exist that name a bucket which is not there and
nothing says so.
"""

from __future__ import annotations

import json
import secrets

import httpx
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

# Never deleted with the organisation: what is left behind on purpose. The audit rows are kept for
# a period of their own and removed by expire_audit, not here.
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
                  if r["table_name"] not in KEPT and not r["table_name"].startswith("access_decision"))


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


def _remove_identities(tenant_id: str) -> int:
    """Remove the sign-in accounts of this organisation's people from the identity provider.

    An account that is already gone counts as removed. Any other answer stops the purge, because a
    deleted organisation that still has working logins is worse than one that is deleted a sweep late."""
    ids = [r["kratos_identity_id"] for r in db.all_rows(
        "select kratos_identity_id from directory where tenant_id = %s and kratos_identity_id is not null",
        (tenant_id,))]
    for identity in ids:
        try:
            response = httpx.delete(f"{config.KRATOS_ADMIN_URL}/admin/identities/{identity}", timeout=10.0)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"the identity provider could not be reached to remove sign-in accounts: {exc}") from exc
        if response.status_code not in (200, 204, 404):
            raise RuntimeError(f"the identity provider refused to remove an account (HTTP {response.status_code})")
    return len(ids)


def expire_audit() -> list[dict]:
    """Remove the audit rows of deleted organisations whose retention has ended, and write the day on
    the record. Returns what was removed."""
    due = db.all_rows(
        "select id, tenant_id, original_tenant_id from tenant_deletion_record "
        "where audit_removed_at is null and audit_kept_until is not null and audit_kept_until <= now()")
    removed = []
    for record in due:
        row = db.one("with d as (delete from access_decision where tenant_id = %s returning 1) "
                     "select count(*) as n from d", (record["tenant_id"],))
        db.execute("update tenant_deletion_record set audit_removed_at = now() where id = %s", (record["id"],))
        removed.append({"tenant_id": record["tenant_id"], "original_tenant_id": record["original_tenant_id"],
                        "audit_rows_removed": row["n"]})
        log.info("audit rows of a deleted organisation removed after their retention",
                 extra={"tenant_id": record["tenant_id"], "count": row["n"]})
    return removed


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

    identities = _remove_identities(tenant_id)

    with psycopg.connect(config.PG_DSN, row_factory=dict_row) as conn:
        with conn.transaction():
            # Named for the rewrite rules, and local to this transaction.
            conn.execute("select set_config('munitas.purge_tenant', %s, true)", (tenant_id,))
            if not conn.execute("select pg_try_advisory_xact_lock(hashtext(%s)) as got",
                                ("purge:" + tenant_id,)).fetchone()["got"]:
                raise RuntimeError(f"{tenant_id} is already being purged by another process")
            if not conn.execute("select tenant_purge_allowed(%s) as ok", (tenant_id,)).fetchone()["ok"]:
                raise PermissionError(f"{tenant_id} stopped being due for deletion before the purge began")

            # The name, not the id: the person's directory row goes with the organisation, and an
            # id that points at nothing says nothing to whoever reads the record later.
            tenant = conn.execute(
                "select t.retire_reason, coalesce(d.label, t.retire_requested_by) as retire_requested_by, "
                "t.retired_at, t.closing_until from tenant t "
                "left join directory d on d.id = t.retire_requested_by where t.id = %s", (tenant_id,)).fetchone()
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

            # The name the organisation is kept under, so that the name itself is free again.
            today = conn.execute("select to_char(now(), 'YYYYMMDD') as d").fetchone()["d"]
            kept_as = f"{tenant_id}~deleted-{today}-{secrets.token_hex(2)}"
            audit = conn.execute("update access_decision set tenant_id = %s where tenant_id = %s",
                                 (kept_as, tenant_id)).rowcount
            conn.execute(
                """insert into tenant_deletion_record (tenant_id, original_tenant_id, retire_reason,
                          retire_requested_by, retired_at, closing_ended_at, purged_by, holds, rows_removed,
                          files_removed, buckets_removed, buckets_left, audit_kept_until, audit_rows_kept,
                          identities_removed)
                   values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s::jsonb,
                           now() + make_interval(years => %s), %s, %s)""",
                (kept_as, tenant_id, tenant["retire_reason"], tenant["retire_requested_by"], tenant["retired_at"],
                 tenant["closing_until"], purged_by, json.dumps(holds), json.dumps(counts), files,
                 json.dumps(removed_buckets), json.dumps(left_buckets), config.AUDIT_RETENTION_YEARS, audit,
                 identities))
            conn.execute("delete from tenant where id = %s", (tenant_id,))

    try:
        # Storage keys that named the deleted rows are dropped from the permissions document.
        grants.reconcile(trigger="manual")
    except Exception as exc:
        log.error("storage permissions could not be printed after a purge; the activator keeps trying",
                  extra={"tenant_id": tenant_id, "reason": str(exc)})
    log.info("organisation purged", extra={"tenant_id": tenant_id, "count": sum(counts.values())})
    return {"tenant_id": tenant_id, "kept_as": kept_as, "rows_removed": counts, "files_removed": files,
            "buckets_removed": removed_buckets, "buckets_left": left_buckets, "holds": holds,
            "audit_rows_kept": audit, "identities_removed": identities}
