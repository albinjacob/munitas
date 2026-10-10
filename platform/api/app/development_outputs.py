"""What is made from development data ends with the data it was made from.

A development sample, and whatever a run makes on a sample or on synthetic files, is for building and testing. Left alone it would pile up: copies
of real person data, derived from a sample, each one more thing to find when a person asks to be erased, and each outliving the permission that let
the input be read. So each ends, at the earlier of a number of days (7, `development_output_days` in the policy) and the end of the sample it came from.

When one ends the platform deletes its files and keeps its row. The version is sealed and never edited or deleted, so the row stays, with a
`storage_reclamation` record that says its files were freed and why (the same record the legal export reads to explain an empty version), and a
`development_output` record that says it ended. A sample's own files are only a short list and a small table: the stored files its list names
belong to the original version and are never touched, because only objects under the version's own folder are deleted.

Nothing is deleted while a legal hold stands over the organisation, or in an organisation that is retired or closing: a deletion that cannot be
brought back waits.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pgdb

from . import config, db, development_samples, logs, opa, locked_files

log = logs.get_logger("development_outputs")

BATCH = 10


def note(tenant_id: str, version_id: str, kind: str, sample_id: str | None, expires_at: datetime) -> None:
    """Record that this version was made for development, and when it ends. `kind` is 'sample' or 'output'."""
    db.execute("insert into development_output (version_id, tenant_id, kind, sample_id, expires_at) values (%s, %s, %s, %s, %s) on conflict do nothing returning version_id",
               (version_id, tenant_id, kind, sample_id, expires_at))


def note_output(tenant_id: str, pipeline_run_id: str, version_id: str) -> None:
    """A step has sealed a version. If the run that made it read development data, the version is a development output and ends with it.

    A run on real files (a version in production) is not: what it makes is held under review until a reviewer decides, and does not expire.
    """
    run = db.one("select source_version_id, triggered_by from pipeline_run where id = %s", (pipeline_run_id,))
    if not run or not run["source_version_id"]:
        return
    source = str(run["source_version_id"])
    if not development_samples.is_development_data(tenant_id, run["triggered_by"], source):
        return
    days = int(opa.read_document("munitas/access/development_output_days"))
    ends = datetime.now(timezone.utc) + timedelta(days=days)
    sample = development_samples.current_sample(tenant_id, run["triggered_by"], source) if run["triggered_by"] else None
    if sample is not None:
        ends = min(ends, sample["expires_at"])
    note(tenant_id, version_id, "output", str(sample["id"]) if sample else None, ends)


def lifecycle_of(version_id: str) -> dict | None:
    """When a development version ends, or ended, for a screen. None for a version that was not made for development."""
    row = db.one("""select o.kind, o.expires_at, o.expired_at, s.status as sample_status, s.expires_at as sample_ends
                      from development_output o left join development_sample s on s.id = o.sample_id where o.version_id = %s""", (version_id,))
    if not row:
        return None
    ends = row["expires_at"]
    if row["sample_ends"] is not None:
        ends = min(ends, row["sample_ends"])
    if row["sample_status"] is not None and row["sample_status"] != "approved":
        ends = min(ends, datetime.now(timezone.utc))
    stamp = lambda d: d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if d else None  # noqa: E731
    return {"kind": row["kind"], "ends_at": stamp(ends), "expired_at": stamp(row["expired_at"])}


# When a development version has run out: its days are over, or the sample it came from ended or ran out. One condition, used to decide what to delete
# and to tell a person that it has ended, so the page never says "no lease" about files that are about to be deleted.
_RAN_OUT = "(o.expires_at <= now() or (o.sample_id is not null and (s.status <> 'approved' or s.expires_at <= now())))"


def has_ended(version_id: str) -> bool:
    """Whether this version was made for development and has run out, whether or not its files are deleted yet."""
    return db.one(f"""select 1 as x from development_output o left join development_sample s on s.id = o.sample_id
                       where o.version_id = %s and (o.expired_at is not null or {_RAN_OUT})""", (version_id,)) is not None


def _due() -> list[dict]:
    return db.all_rows(
        f"""select o.version_id, o.tenant_id, o.kind, v.storage_prefix, v.storage_backend
             from development_output o
             join dataset_version v on v.id = o.version_id
             left join development_sample s on s.id = o.sample_id
            where o.expired_at is null and {_RAN_OUT}
            order by o.expires_at limit %s""", (BATCH,))


def _held(tenant_id: str) -> bool:
    """Whether something stands over this organisation that forbids deleting: a legal hold, or an organisation that is not open."""
    state = db.one("select tenant_hold_state(%s) as state, tenant_phase(%s) as phase", (tenant_id, tenant_id))
    return state["state"] != "none" or state["phase"] != "active"


def expire(row: dict) -> bool:
    """Delete the files under this version's own folder and record that it ended. Returns whether it did. Never touches another version's objects."""
    tenant, backend = row["tenant_id"], row["storage_backend"] or "seaweedfs"
    if _held(tenant):
        return False
    client, bucket = locked_files._storage({"backend": backend, "tenant": tenant})
    prefix = row["storage_prefix"].rstrip("/") + "/"
    objects: list[dict] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        objects.extend(page.get("Contents", []))
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
    for start in range(0, len(objects), 1000):
        client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": o["Key"]} for o in objects[start:start + 1000]], "Quiet": True})
    freed = sum(int(o["Size"]) for o in objects)
    try:
        db.execute("""insert into storage_reclamation (dataset_version_id, tenant_id, bytes_freed, object_count, reclaimed_by, reason)
                      values (%s, %s, %s, %s, 'platform', %s) on conflict do nothing returning dataset_version_id""",
                   (row["version_id"], tenant, freed, len(objects),
                    "made for development, so it ended with the sample it came from or after its days, and its files were deleted" if row["kind"] == "output"
                    else "a development sample, which ended: its list was deleted, and the files it named were not touched"))
        db.execute("update development_output set expired_at = now() where version_id = %s returning version_id", (row["version_id"],))
    except pgdb.errors.ReadOnlySqlTransaction:
        return False
    return True


def sweep() -> int:
    """End what is due. At most a few at a time, so a request that triggers it is not held up; the timer finishes the rest."""
    development_samples.sweep()
    done = 0
    for row in _due():
        try:
            done += 1 if expire(row) else 0
        except Exception:  # noqa: BLE001 - one version that cannot be freed now must not stop the others; the next pass tries it again
            log.exception("could not end a development version; trying again next time", extra={"dataset_version_id": str(row["version_id"])})
    return done


async def run_forever() -> None:
    """The timer. Everything it decides is read from the database each pass, so a restart loses nothing."""
    if config.DEVELOPMENT_SWEEP_SECONDS <= 0:
        return
    while True:
        try:
            await asyncio.to_thread(sweep)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("development sweep failed; trying again next time")
        await asyncio.sleep(config.DEVELOPMENT_SWEEP_SECONDS)
